"""离职表单流程：申请状态机、飞书下发降级与前后端接口契约。

外部调用（飞书 token / 发消息）用 httpx.MockTransport 打桩，不依赖真实租户。
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.attendance import resignation as R
from app.attendance.db import AttendanceStore
from app.attendance.lifecycle import (
    STATUS_COMPLETED,
    STATUS_CONFIRMED,
    STATUS_REJECTED,
    STATUS_SENT,
    STATUS_SUBMITTED,
)
from app.main import create_app
from app.recruitment.db import RecruitmentStore

TOKEN_PATH = "/open-apis/auth/v3/tenant_access_token/internal"
MESSAGE_PATH = "/open-apis/im/v1/messages"


def _store(tmp_path) -> AttendanceStore:
    store = AttendanceStore(tmp_path / "attendance.db")
    store.initialize()
    return store


def _employee(store, **overrides) -> int:
    values = {
        "employee_no": "E001",
        "name": "张三",
        "department": "技术部",
        "position": "后端",
        "active": 1,
        "feishu_open_id": "ou-1",
    }
    values.update(overrides)
    return store.execute(
        "INSERT INTO employee (employee_no, name, aliases, department, position, employment_status, "
        "active, feishu_open_id, created_at, updated_at) VALUES (?, ?, '[]', ?, ?, 'regular', ?, ?, 't', 't')",
        (
            values["employee_no"],
            values["name"],
            values["department"],
            values["position"],
            values["active"],
            values["feishu_open_id"],
        ),
    )


# ---- 状态机 ----


def test_create_request_snapshots_employee_and_is_reusable(tmp_path):
    store = _store(tmp_path)
    employee_id = _employee(store)

    record = R.create_request(store, employee_id=employee_id, created_by_id=None)
    assert record["status"] == STATUS_SENT
    assert record["employee_name"] == "张三"
    assert record["department"] == "技术部"
    assert record["token"]

    again = R.create_request(store, employee_id=employee_id, created_by_id=None)
    assert again["id"] == record["id"]


def test_create_request_rejects_inactive_employee(tmp_path):
    store = _store(tmp_path)
    employee_id = _employee(store, active=0)

    with pytest.raises(ValueError):
        R.create_request(store, employee_id=employee_id)


def test_submit_request_requires_valid_date_and_blocks_resubmit(tmp_path):
    store = _store(tmp_path)
    record = R.create_request(store, employee_id=_employee(store))

    with pytest.raises(ValueError):
        R.submit_request(store, record["token"], last_working_day="2026/10/20")

    submitted = R.submit_request(
        store, record["token"], last_working_day="2026-10-20", reason_category="个人发展"
    )
    assert submitted["status"] == STATUS_SUBMITTED
    assert submitted["last_working_day"] == "2026-10-20"

    with pytest.raises(ValueError):
        R.submit_request(store, record["token"], last_working_day="2026-10-21")


def test_submit_request_rejects_unknown_token(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        R.submit_request(store, "nope", last_working_day="2026-10-20")


def test_confirm_request_defaults_last_working_day_and_blocks_completed(tmp_path):
    store = _store(tmp_path)
    record = R.create_request(store, employee_id=_employee(store))

    confirmed = R.confirm_request(store, record["id"], account_id=None, last_working_day="2026-10-20")
    assert confirmed["status"] == STATUS_CONFIRMED

    with pytest.raises(ValueError):
        R.confirm_request(store, record["id"], account_id=None)


def test_reject_request_keeps_note(tmp_path):
    store = _store(tmp_path)
    record = R.create_request(store, employee_id=_employee(store))

    rejected = R.reject_request(store, record["id"], note="先谈留任")
    assert rejected["status"] == STATUS_REJECTED
    assert "先谈留任" in rejected["reason_detail"]


def test_list_requests_orders_pending_first(tmp_path):
    store = _store(tmp_path)
    first = R.create_request(store, employee_id=_employee(store, employee_no="E001", name="甲"))
    second = R.create_request(store, employee_id=_employee(store, employee_no="E002", name="乙"))
    R.submit_request(store, second["token"], last_working_day="2026-10-20")

    listed = R.list_requests(store)

    assert [item["status"] for item in listed] == [STATUS_SUBMITTED, STATUS_SENT]
    assert listed[0]["status_label"] == "待 HR 确认"


# ---- 飞书下发降级 ----


def _delivery_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_deliver_without_base_url_falls_back_to_manual(tmp_path):
    store = _store(tmp_path)
    record = R.create_request(store, employee_id=_employee(store))

    result = R.deliver_request(store, record, base_url="")

    assert result["delivered"] is False
    assert result["manual"] is True
    assert "/resign/" in result["link"]
    assert "未配置对外访问地址" in result["detail"]


def test_deliver_without_feishu_credentials_falls_back_to_manual(tmp_path):
    store = _store(tmp_path)
    record = R.create_request(store, employee_id=_employee(store))

    result = R.deliver_request(store, record, base_url="https://hr.example.com")

    assert result["manual"] is True
    assert "未配置飞书应用凭证" in result["detail"]


def test_deliver_sends_card_and_records_status(tmp_path):
    store = _store(tmp_path)
    store.set_config("feishu_app_id", "cli_test")
    store.set_config("feishu_app_secret", "secret")
    record = R.create_request(store, employee_id=_employee(store))
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        if request.url.path == TOKEN_PATH:
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tk", "expire": 7200})
        return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_1"}})

    result = R.deliver_request(
        store, record, base_url="https://hr.example.com", client=_delivery_client(handler)
    )

    assert result["delivered"] is True
    assert result["link"] == f"https://hr.example.com/resign/{record['token']}"
    assert seen[-1][0] == MESSAGE_PATH
    assert seen[-1][1]["receive_id_type"] == "open_id"
    assert store.query_one("SELECT deliver_status FROM resignation_request")["deliver_status"] == "sent"


def test_deliver_reports_missing_message_scope(tmp_path):
    store = _store(tmp_path)
    store.set_config("feishu_app_id", "cli_test")
    store.set_config("feishu_app_secret", "secret")
    record = R.create_request(store, employee_id=_employee(store))
    # 飞书的 msg 里带开通入口与所需权限清单，应原样透传给 HR
    feishu_msg = (
        "Access denied. One of the following scopes is required: "
        "[im:message:send, im:message, im:message:send_as_bot]，点击链接申请并开通任一权限即可"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == TOKEN_PATH:
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "tk", "expire": 7200})
        return httpx.Response(400, json={"code": 99991672, "msg": feishu_msg})

    result = R.deliver_request(
        store, record, base_url="https://hr.example.com", client=_delivery_client(handler)
    )

    assert result["delivered"] is False
    assert "未开通发消息权限" in result["detail"]
    assert "im:message:send_as_bot" in result["detail"]
    assert store.query_one("SELECT deliver_status FROM resignation_request")["deliver_status"] == "failed"


# ---- 接口契约 ----


def _boot(tmp_path, monkeypatch):
    monkeypatch.setenv("TALENT_HUB_DATA_DIR", str(tmp_path))
    store = AttendanceStore(tmp_path / "attendance.db")
    store.initialize()
    # 默认管理员首次登录强制改密会拦住写操作，测试直接放行
    store.execute("UPDATE account SET must_change_password = 0 WHERE username = 'admin'")
    recruitment = RecruitmentStore(tmp_path / "recruitment.db")
    recruitment.initialize()
    monkeypatch.setattr("app.main.get_attendance_store", lambda: store)
    monkeypatch.setattr("app.main.get_recruitment_store", lambda: recruitment)
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    headers = {"X-App-Token": app.state.app_token}
    token = client.post(
        "/api/attendance/login", json={"username": "admin", "password": "admin"}
    ).json()["token"]
    headers["X-Attendance-Token"] = token
    return client, headers, store


def test_dashboard_exposes_lifecycle_board(tmp_path, monkeypatch):
    client, headers, store = _boot(tmp_path, monkeypatch)
    _employee(store)

    body = client.get("/api/hr/dashboard", headers=headers).json()

    assert body["lifecycle"]["active"] == 1
    assert body["lifecycle"]["current"]["headcount"] == 1
    assert len(body["lifecycle"]["months"]) == 6
    assert "ops" in body["lifecycle"]
    assert body["lifecycle"]["ops"]["reason_categories"]
    # 未配置飞书凭证：既不启动长连接，也不显示为「已开启」
    events = body["lifecycle"]["ops"]["events"]
    assert events["configured"] is False
    assert events["enabled"] is False
    assert events["connected"] is False


def test_resignation_flow_over_http(tmp_path, monkeypatch):
    client, headers, store = _boot(tmp_path, monkeypatch)
    employee_id = _employee(store)

    created = client.post("/api/hr/resignations", headers=headers, json={"employee_id": employee_id})
    assert created.status_code == 200
    payload = created.json()
    assert payload["request"]["status"] == STATUS_SENT
    # 未配置对外地址与飞书凭证时降级为手工转发
    assert payload["delivery"]["manual"] is True
    token = payload["request"]["token"]
    request_id = payload["request"]["id"]

    form = client.get(f"/api/resignation/{token}", headers=headers)
    assert form.status_code == 200
    assert form.json()["request"]["employee_name"] == "张三"

    submitted = client.post(
        f"/api/resignation/{token}",
        headers=headers,
        json={"last_working_day": "2026-10-20", "reason_category": "个人发展"},
    )
    assert submitted.status_code == 200
    assert submitted.json()["status"] == STATUS_SUBMITTED

    confirmed = client.post(
        f"/api/hr/resignations/{request_id}/confirm",
        headers=headers,
        json={"last_working_day": "2026-10-20"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["request"]["status"] == STATUS_CONFIRMED

    listed = client.get("/api/hr/resignations", headers=headers).json()["requests"]
    assert listed[0]["status_label"] == "待到岗离职日"

    offboarded = client.post("/api/hr/lifecycle/run-offboards", headers=headers)
    assert offboarded.status_code == 200


def test_public_form_rejects_unknown_token(tmp_path, monkeypatch):
    client, headers, _store_instance = _boot(tmp_path, monkeypatch)

    assert client.get("/api/resignation/nope", headers=headers).status_code == 404


def test_resignation_routes_require_login(tmp_path, monkeypatch):
    client, _headers, store = _boot(tmp_path, monkeypatch)
    employee_id = _employee(store)

    # 仅带应用令牌、未登录会话：写操作应被拒绝
    response = client.post(
        "/api/hr/resignations",
        headers={"X-App-Token": client.app.state.app_token},
        json={"employee_id": employee_id},
    )

    assert response.status_code == 401


def test_completed_request_cannot_be_rejected(tmp_path, monkeypatch):
    client, headers, store = _boot(tmp_path, monkeypatch)
    employee_id = _employee(store)
    record = client.post(
        "/api/hr/resignations", headers=headers, json={"employee_id": employee_id}
    ).json()["request"]
    store.execute(
        "UPDATE resignation_request SET status = ? WHERE id = ?", (STATUS_COMPLETED, record["id"])
    )

    response = client.post(
        f"/api/hr/resignations/{record['id']}/reject", headers=headers, json={"note": ""}
    )

    assert response.status_code == 400
    assert "已完成离职" in response.json()["detail"]
