"""人事中台接入飞书：通讯录员工同步与考勤同步状态的接口契约。

通讯录网络层用 httpx.MockTransport 打桩，考勤客户端按模块替换，均不依赖真实飞书租户。
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.attendance.db import AttendanceStore
from app.attendance.feishu_contacts import (
    FeishuContactsClient,
    FeishuContactsError,
    last_sync,
    sync_employees,
)
from app.main import create_app
from app.recruitment.db import RecruitmentStore

TOKEN_PATH = "/open-apis/auth/v3/tenant_access_token/internal"
# 部门 ID 是路径参数：/departments/:department_id/children
CHILDREN_PREFIX = "/open-apis/contact/v3/departments/"
CHILDREN_SUFFIX = "/children"
USERS_PATH = "/open-apis/contact/v3/users/find_by_department"

ROOT = "0"
TECH = "od-tech"
MARKET = "od-market"

DEPARTMENTS = {
    ROOT: [{"open_department_id": TECH, "name": "技术部"}, {"open_department_id": MARKET, "name": "市场部"}],
    TECH: [],
    MARKET: [],
}

USERS = {
    ROOT: [],
    TECH: [
        {
            "open_id": "ou-1",
            "name": "张三",
            "employee_no": "E001",
            "job_title": "后端工程师",
            "mobile": "13800000000",
            "join_time": 1704067200,
            "department_ids": [TECH],
            "status": {"is_activated": True},
        }
    ],
    MARKET: [
        {"open_id": "ou-2", "name": "李四", "employee_no": "", "department_ids": [MARKET], "status": {}},
        {
            "open_id": "ou-3",
            "name": "王五",
            "employee_no": "E003",
            "department_ids": [MARKET],
            "status": {"is_exited": True},
        },
    ],
}


def _token_response() -> httpx.Response:
    """token 接口把凭证放在响应顶层，与业务接口的 data 信封不同。"""
    return httpx.Response(
        200,
        json={"code": 0, "msg": "ok", "tenant_access_token": "tenant-token", "expire": 7200},
    )


def _children_department(path: str) -> str | None:
    """从子部门接口的路径里取出部门 ID，取不到返回 None。"""
    if path.startswith(CHILDREN_PREFIX) and path.endswith(CHILDREN_SUFFIX):
        return path[len(CHILDREN_PREFIX) : -len(CHILDREN_SUFFIX)]
    return None


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == TOKEN_PATH:
        return _token_response()
    department = _children_department(request.url.path)
    if department is not None:
        items = DEPARTMENTS.get(department, [])
        return httpx.Response(200, json={"code": 0, "data": {"items": items, "has_more": False}})
    if request.url.path == USERS_PATH:
        department = request.url.params.get("department_id", "")
        items = USERS.get(department, [])
        return httpx.Response(200, json={"code": 0, "data": {"items": items, "has_more": False}})
    return httpx.Response(404, json={"code": 404, "msg": "unexpected path"})


def _client(handler=_handler) -> FeishuContactsClient:
    return FeishuContactsClient(
        "cli_test", "secret", client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def _store(tmp_path) -> AttendanceStore:
    store = AttendanceStore(tmp_path / "attendance.db")
    store.initialize()
    return store


def _insert_employee(store, **overrides) -> None:
    values = {
        "employee_no": "E001",
        "name": "旧名",
        "aliases": "[]",
        "department": "旧部门",
        "position": "旧岗位",
        "join_date": None,
        "employment_status": "regular",
        "active": 1,
        "attendance_policy_id": None,
        "expected_days_override": None,
        "phone": "",
    }
    values.update(overrides)
    store.execute(
        "INSERT INTO employee (employee_no, name, aliases, department, position, join_date, "
        "employment_status, active, attendance_policy_id, expected_days_override, phone, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 't', 't')",
        (
            values["employee_no"],
            values["name"],
            values["aliases"],
            values["department"],
            values["position"],
            values["join_date"],
            values["employment_status"],
            values["active"],
            values["attendance_policy_id"],
            values["expected_days_override"],
            values["phone"],
        ),
    )


# ---- 客户端抓取 ----


def test_list_departments_walks_the_tree():
    names = {item["name"] for item in _client().list_departments()}
    assert names == {"技术部", "市场部"}


def test_fetch_employees_maps_feishu_fields():
    by_no = {item["employee_no"]: item for item in _client().fetch_employees() if item["employee_no"]}
    assert by_no["E001"] == {
        "employee_no": "E001",
        "name": "张三",
        "department": "技术部",
        "position": "后端工程师",
        "phone": "13800000000",
        "join_date": "2024-01-01",
        "active": True,
    }


def test_fetch_employees_marks_exited_member_inactive():
    by_no = {item["employee_no"]: item for item in _client().fetch_employees()}
    assert by_no["E003"]["active"] is False


@pytest.mark.parametrize("status", [{"is_exited": True}, {"is_resigned": True}, {"is_unjoin": True}])
def test_fetch_employees_excludes_members_who_are_not_employed(status):
    """离职、主动退出与尚未加入企业都不计入在职。"""
    member = {"open_id": "ou-9", "name": "赵六", "employee_no": "E009", "department_ids": [], "status": status}

    def handler(request):
        if request.url.path == TOKEN_PATH:
            return _token_response()
        if _children_department(request.url.path) is not None:
            return httpx.Response(200, json={"code": 0, "data": {"items": [], "has_more": False}})
        return httpx.Response(200, json={"code": 0, "data": {"items": [member], "has_more": False}})

    assert _client(handler).fetch_employees()[0]["active"] is False


def test_fetch_employees_keeps_frozen_member_employed():
    """账号暂停仍是在职员工，只是账号被冻结。"""
    member = {"open_id": "ou-9", "name": "赵六", "employee_no": "E009", "department_ids": [], "status": {"is_frozen": True}}

    def handler(request):
        if request.url.path == TOKEN_PATH:
            return _token_response()
        if _children_department(request.url.path) is not None:
            return httpx.Response(200, json={"code": 0, "data": {"items": [], "has_more": False}})
        return httpx.Response(200, json={"code": 0, "data": {"items": [member], "has_more": False}})

    assert _client(handler).fetch_employees()[0]["active"] is True


def test_fetch_employees_dedupes_member_seen_in_two_departments():
    member = {"open_id": "ou-1", "name": "张三", "employee_no": "E001", "department_ids": [TECH], "status": {}}

    def handler(request):
        if request.url.path == TOKEN_PATH:
            return _token_response()
        department = _children_department(request.url.path)
        if department is not None:
            items = [{"open_department_id": TECH, "name": "技术部"}] if department == ROOT else []
            return httpx.Response(200, json={"code": 0, "data": {"items": items, "has_more": False}})
        return httpx.Response(200, json={"code": 0, "data": {"items": [member], "has_more": False}})

    assert len(_client(handler).fetch_employees()) == 1


def test_fetch_employees_follows_pagination():
    first = {"open_id": "ou-1", "name": "张三", "employee_no": "E001", "department_ids": [], "status": {}}
    second = {"open_id": "ou-2", "name": "李四", "employee_no": "E002", "department_ids": [], "status": {}}

    def handler(request):
        if request.url.path == TOKEN_PATH:
            return _token_response()
        if _children_department(request.url.path) is not None:
            return httpx.Response(200, json={"code": 0, "data": {"items": [], "has_more": False}})
        if not request.url.params.get("page_token"):
            return httpx.Response(
                200, json={"code": 0, "data": {"items": [first], "has_more": True, "page_token": "p2"}}
            )
        return httpx.Response(200, json={"code": 0, "data": {"items": [second], "has_more": False}})

    employee_nos = {item["employee_no"] for item in _client(handler).fetch_employees()}
    assert employee_nos == {"E001", "E002"}


def test_requests_use_documented_paths_and_params():
    """钉住官方接口形状：部门 ID 是路径参数，用户列表接口用查询参数。"""
    seen: list[tuple[str, dict]] = []

    def handler(request):
        seen.append((request.url.path, dict(request.url.params)))
        if request.url.path == TOKEN_PATH:
            return _token_response()
        return httpx.Response(200, json={"code": 0, "data": {"items": [], "has_more": False}})

    _client(handler).fetch_employees()

    children = [item for item in seen if item[0].endswith(CHILDREN_SUFFIX)]
    assert children[0][0] == "/open-apis/contact/v3/departments/0/children"
    assert children[0][1]["department_id_type"] == "open_department_id"
    assert "department_id" not in children[0][1]

    users = [item for item in seen if item[0] == USERS_PATH]
    assert users[0][1]["department_id"] == "0"
    assert users[0][1]["department_id_type"] == "open_department_id"


def test_business_request_carries_tenant_token_from_top_level():
    authorizations = []

    def handler(request):
        authorizations.append(request.headers.get("Authorization"))
        if request.url.path == TOKEN_PATH:
            return _token_response()
        return httpx.Response(200, json={"code": 0, "data": {"items": [], "has_more": False}})

    _client(handler).list_departments()

    # token 请求本身不带 Authorization；业务请求必须带上顶层返回的 token
    assert authorizations[0] is None
    assert authorizations[-1] == "Bearer tenant-token"


def test_scope_denied_surfaces_actionable_error():
    def handler(request):
        if request.url.path == TOKEN_PATH:
            return _token_response()
        return httpx.Response(403, json={"code": 99991672, "msg": "Access denied"})

    with pytest.raises(FeishuContactsError) as excinfo:
        _client(handler).list_departments()
    assert excinfo.value.code == 99991672
    assert "contact:contact:readonly" in str(excinfo.value)
    assert "全部成员" in str(excinfo.value)


@pytest.mark.parametrize("code", [40004, 40014])
def test_scope_range_denied_explains_permission_range(code):
    def handler(request):
        if request.url.path == TOKEN_PATH:
            return _token_response()
        return httpx.Response(403, json={"code": code, "msg": "no dept authority error"})

    with pytest.raises(FeishuContactsError) as excinfo:
        _client(handler).list_departments()
    assert "通讯录权限范围" in str(excinfo.value)


# ---- 员工档案同步 ----


def test_sync_employees_inserts_new_members_and_skips_missing_employee_no(tmp_path):
    store = _store(tmp_path)
    summary = sync_employees(store, _client())

    assert summary["total"] == 3
    assert summary["inserted"] == 2
    assert summary["updated"] == 0
    assert summary["skipped"] == 1
    assert last_sync(store) == summary

    rows = {row["employee_no"]: row for row in store.query("SELECT * FROM employee")}
    assert set(rows) == {"E001", "E003"}
    assert rows["E001"]["department"] == "技术部"
    assert rows["E001"]["position"] == "后端工程师"
    assert rows["E001"]["phone"] == "13800000000"
    assert rows["E001"]["join_date"] == "2024-01-01"
    assert rows["E001"]["active"] == 1
    assert rows["E003"]["active"] == 0
    assert rows["E003"]["employment_status"] == "left"


def test_sync_employees_reports_when_every_member_lacks_employee_no(tmp_path):
    """成员全缺工号时不写空摘要，而是提示字段权限与数据本身都要检查。"""
    store = _store(tmp_path)
    members = [{"open_id": "ou-1", "name": "张三", "employee_no": "", "department_ids": [], "status": {}}]

    def handler(request):
        if request.url.path == TOKEN_PATH:
            return _token_response()
        if _children_department(request.url.path) is not None:
            return httpx.Response(200, json={"code": 0, "data": {"items": [], "has_more": False}})
        return httpx.Response(200, json={"code": 0, "data": {"items": members, "has_more": False}})

    with pytest.raises(FeishuContactsError) as excinfo:
        sync_employees(store, _client(handler))

    assert "contact:contact:readonly" in str(excinfo.value)
    assert store.query("SELECT * FROM employee") == []
    assert last_sync(store) is None


def test_sync_employees_refreshes_feishu_fields_and_keeps_local_only_fields(tmp_path):
    store = _store(tmp_path)
    policy_id = store.execute(
        "INSERT INTO attendance_policy (code, name, mode) VALUES ('std', '标准', 'standard')"
    )
    _insert_employee(
        store,
        name="旧名",
        aliases='["小张"]',
        department="旧部门",
        position="旧岗位",
        join_date="2020-01-01",
        employment_status="probation",
        attendance_policy_id=policy_id,
        expected_days_override=21.5,
        phone="13900000000",
    )

    summary = sync_employees(store, _client())
    assert summary["inserted"] == 1
    assert summary["updated"] == 1

    row = store.query_one("SELECT * FROM employee WHERE employee_no = 'E001'")
    # 飞书是人事事实来源：姓名、部门、岗位、手机号、入职日期被刷新
    assert row["name"] == "张三"
    assert row["department"] == "技术部"
    assert row["position"] == "后端工程师"
    assert row["phone"] == "13800000000"
    assert row["join_date"] == "2024-01-01"
    # 本地专有字段与人工判断保持原值
    assert row["aliases"] == '["小张"]'
    assert row["attendance_policy_id"] == policy_id
    assert row["expected_days_override"] == 21.5
    assert row["employment_status"] == "probation"


def test_sync_employees_reactivates_member_active_in_feishu(tmp_path):
    store = _store(tmp_path)
    _insert_employee(store, employment_status="left", active=0)

    sync_employees(store, _client())

    row = store.query_one("SELECT * FROM employee WHERE employee_no = 'E001'")
    assert row["active"] == 1
    assert row["employment_status"] == "regular"


# ---- 人事中台接口 ----


def _boot(tmp_path, monkeypatch):
    monkeypatch.setenv("TALENT_HUB_DATA_DIR", str(tmp_path))
    store = AttendanceStore(tmp_path / "attendance.db")
    store.initialize()
    recruitment = RecruitmentStore(tmp_path / "recruitment.db")
    recruitment.initialize()
    monkeypatch.setattr("app.main.get_attendance_store", lambda: store)
    monkeypatch.setattr("app.main.get_recruitment_store", lambda: recruitment)
    app = create_app(data_dir=tmp_path)
    return TestClient(app), {"X-App-Token": app.state.app_token}, store


class _StubClient:
    def __init__(self, employees):
        self._employees = employees

    def fetch_employees(self):
        return list(self._employees)


def test_dashboard_reports_feishu_state(tmp_path, monkeypatch):
    client, headers, store = _boot(tmp_path, monkeypatch)

    before = client.get("/api/hr/dashboard", headers=headers)
    assert before.status_code == 200
    assert before.json()["feishu"] == {"credentials_configured": False, "employee_sync": None}

    store.set_config("feishu_app_id", "cli_test")
    store.set_config("feishu_app_secret", "secret")
    monkeypatch.setattr(
        "app.hr.FeishuContactsClient",
        lambda app_id, app_secret: _StubClient(
            [
                {"employee_no": "E001", "name": "张三", "department": "技术部", "position": "",
                 "phone": "", "join_date": None, "active": True}
            ]
        ),
    )
    synced = client.post("/api/hr/feishu-employees/sync", headers=headers)
    assert synced.status_code == 200
    assert synced.json()["inserted"] == 1

    after = client.get("/api/hr/dashboard", headers=headers)
    body = after.json()
    assert body["feishu"]["credentials_configured"] is True
    assert body["feishu"]["employee_sync"]["inserted"] == 1
    assert body["employees"] == {
        "total": 1,
        "active": 1,
        "departments": [{"department": "技术部", "count": 1}],
    }


def test_sync_endpoint_requires_feishu_credentials(tmp_path, monkeypatch):
    client, headers, _store_instance = _boot(tmp_path, monkeypatch)

    response = client.post("/api/hr/feishu-employees/sync", headers=headers)

    assert response.status_code == 400
    assert "App ID" in response.json()["detail"]


def test_sync_endpoint_reports_feishu_error_as_bad_gateway(tmp_path, monkeypatch):
    client, headers, store = _boot(tmp_path, monkeypatch)
    store.set_config("feishu_app_id", "cli_test")
    store.set_config("feishu_app_secret", "secret")

    class _DeniedClient:
        def fetch_employees(self):
            raise FeishuContactsError(99991672, "Access denied")

    monkeypatch.setattr("app.hr.FeishuContactsClient", lambda app_id, app_secret: _DeniedClient())
    response = client.post("/api/hr/feishu-employees/sync", headers=headers)

    assert response.status_code == 502
    assert "99991672" in response.json()["detail"]


# ---- 飞书考勤同步 ----


class _StubAttendanceClient:
    """替换考勤客户端，避免测试触网；返回空打卡即可走完一轮同步。"""

    def __init__(self, app_id, app_secret) -> None:
        self.app_id = app_id
        self.app_secret = app_secret

    def query_user_tasks(self, user_ids, date_from, date_to):
        return []


def _enable_feishu_attendance(store) -> None:
    store.set_config("feishu_app_id", "cli_test")
    store.set_config("feishu_app_secret", "secret")
    store.set_config("feishu_enabled", "1")


def test_dashboard_reports_attendance_feishu_state(tmp_path, monkeypatch):
    client, headers, _store_instance = _boot(tmp_path, monkeypatch)

    body = client.get("/api/hr/dashboard", headers=headers).json()

    assert body["attendance"]["feishu"] == {
        "enabled": False,
        "running": False,
        "last_error": "",
        "last_batch": None,
    }


def test_hr_attendance_sync_runs_engine_and_records_batch(tmp_path, monkeypatch):
    client, headers, store = _boot(tmp_path, monkeypatch)
    _enable_feishu_attendance(store)
    _insert_employee(store)
    monkeypatch.setattr("app.attendance.sync.FeishuAttendanceClient", _StubAttendanceClient)

    response = client.post("/api/hr/feishu-sync", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["employees"] == 1

    state = client.get("/api/hr/dashboard", headers=headers).json()["attendance"]["feishu"]
    assert state["enabled"] is True
    assert state["last_error"] == ""
    assert state["last_batch"]["period"] == body["period"]
    assert state["last_batch"]["status"] == "completed"
    assert state["last_batch"]["completed_at"]


def test_hr_attendance_sync_reports_missing_config_without_error(tmp_path, monkeypatch):
    client, headers, _store_instance = _boot(tmp_path, monkeypatch)

    response = client.post("/api/hr/feishu-sync", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"ok": False, "detail": "飞书考勤未配置或未启用"}


def test_hr_attendance_sync_reports_missing_employees(tmp_path, monkeypatch):
    client, headers, store = _boot(tmp_path, monkeypatch)
    _enable_feishu_attendance(store)

    response = client.post("/api/hr/feishu-sync", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"ok": False, "detail": "没有可同步的在职员工"}
