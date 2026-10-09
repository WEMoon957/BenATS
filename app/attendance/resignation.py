"""离职表单流程：HR 发起 → 飞书下发填报表单 → 员工提交 → HR 确认 → 到期自动离职。

与 lifecycle 的分工：本模块只负责「申请」的状态机与送达，真正把员工置为离职由
`lifecycle.offboard_employee` / `process_due_offboards` 执行，保证离职口径唯一。

送达方式：把带令牌的表单链接通过飞书应用消息发给员工本人；员工需能在浏览器打开
应用地址，因此链接基于可配置的对外地址（app_config 的 `public_base_url`）。应用
只有本机地址时无法直接送达，此时保留链接由 HR 手工转发。
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import date
from urllib.parse import quote

import httpx

from .db import AttendanceStore, _now
from .lifecycle import (
    RESIGN_STATUS_LABELS,
    STATUS_COMPLETED,
    STATUS_CONFIRMED,
    STATUS_REJECTED,
    STATUS_SENT,
    STATUS_SUBMITTED,
    today_cn,
)

logger = logging.getLogger(__name__)

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages"

CONFIG_PUBLIC_BASE_URL = "public_base_url"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SCOPE_DENIED_CODE = 99991672

REASON_CATEGORIES = ["个人发展", "薪酬福利", "工作强度", "家庭原因", "团队管理", "身体原因", "岗位调整", "其他"]


def resolve_base_url(store: AttendanceStore, request_base: str = "") -> str:
    """表单链接的宿主地址：优先用配置的对外地址，否则回退当前请求地址。"""
    configured = store.get_config(CONFIG_PUBLIC_BASE_URL).strip().rstrip("/")
    if configured:
        return configured
    return (request_base or "").rstrip("/")


def build_form_url(base_url: str, token: str) -> str:
    return f"{base_url}/resign/{quote(token)}"


# ---- 申请状态机 ----


def create_request(
    store: AttendanceStore, *, employee_id: int, created_by_id: int | None = None
) -> dict:
    """为在职员工创建一份离职申请，已存在未结束的申请时直接返回它。"""
    employee = store.query_one("SELECT * FROM employee WHERE id = ?", (employee_id,), table="employee")
    if not employee:
        raise ValueError("员工不存在")
    if not employee["active"]:
        raise ValueError("该员工已离职，无需再发起离职流程")
    pending = store.query_one(
        "SELECT * FROM resignation_request WHERE employee_id = ? AND status IN (?, ?, ?) "
        "ORDER BY id DESC",
        (employee_id, STATUS_SENT, STATUS_SUBMITTED, STATUS_CONFIRMED),
    )
    if pending:
        return pending
    token = secrets.token_urlsafe(24)
    request_id = store.execute(
        "INSERT INTO resignation_request (token, employee_id, employee_no, employee_name, department, "
        "position, status, created_by_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            token,
            employee_id,
            employee.get("employee_no", ""),
            employee.get("name", ""),
            employee.get("department", ""),
            employee.get("position", ""),
            STATUS_SENT,
            created_by_id,
            _now(),
            _now(),
        ),
    )
    return store.query_one("SELECT * FROM resignation_request WHERE id = ?", (request_id,))


def list_requests(store: AttendanceStore, *, limit: int = 100) -> list[dict]:
    rows = store.query(
        "SELECT * FROM resignation_request ORDER BY "
        "CASE status WHEN 'submitted' THEN 0 WHEN 'sent' THEN 1 WHEN 'confirmed' THEN 2 ELSE 3 END, "
        "id DESC LIMIT ?",
        (limit,),
    )
    return [_request_payload(row) for row in rows]


def get_by_token(store: AttendanceStore, token: str) -> dict | None:
    return store.query_one("SELECT * FROM resignation_request WHERE token = ?", (token,))


def submit_request(
    store: AttendanceStore,
    token: str,
    *,
    last_working_day: str,
    reason_category: str = "",
    reason_detail: str = "",
    handover_to: str = "",
    handover_note: str = "",
    contact_after: str = "",
) -> dict:
    """员工提交离职信息。仅待填写状态可提交，重复提交会被拒绝。"""
    request = get_by_token(store, token)
    if not request:
        raise ValueError("表单链接无效或已失效")
    if request["status"] != STATUS_SENT:
        raise ValueError("该离职表单已提交，无需重复填写")
    if not last_working_day or not DATE_RE.match(last_working_day):
        raise ValueError("请填写有效的最后工作日（YYYY-MM-DD）")
    store.execute(
        "UPDATE resignation_request SET status = ?, last_working_day = ?, reason_category = ?, "
        "reason_detail = ?, handover_to = ?, handover_note = ?, contact_after = ?, submitted_at = ?, "
        "updated_at = ? WHERE id = ?",
        (
            STATUS_SUBMITTED,
            last_working_day,
            reason_category.strip(),
            reason_detail.strip(),
            handover_to.strip(),
            handover_note.strip(),
            contact_after.strip(),
            _now(),
            _now(),
            request["id"],
        ),
    )
    return store.query_one("SELECT * FROM resignation_request WHERE id = ?", (request["id"],))


def confirm_request(
    store: AttendanceStore, request_id: int, *, account_id: int | None, last_working_day: str | None = None
) -> dict:
    """HR 确认离职申请：确认后到期由 lifecycle 自动执行离职。"""
    request = store.query_one("SELECT * FROM resignation_request WHERE id = ?", (request_id,))
    if not request:
        raise ValueError("离职申请不存在")
    if request["status"] not in (STATUS_SENT, STATUS_SUBMITTED):
        raise ValueError("该申请当前状态不可确认")
    effective = (last_working_day or request["last_working_day"] or today_cn().isoformat()).strip()
    if not DATE_RE.match(effective):
        raise ValueError("最后工作日格式应为 YYYY-MM-DD")
    store.execute(
        "UPDATE resignation_request SET status = ?, last_working_day = ?, confirmed_by_id = ?, "
        "confirmed_at = ?, updated_at = ? WHERE id = ?",
        (STATUS_CONFIRMED, effective, account_id, _now(), _now(), request_id),
    )
    return store.query_one("SELECT * FROM resignation_request WHERE id = ?", (request_id,))


def reject_request(store: AttendanceStore, request_id: int, *, note: str = "") -> dict:
    request = store.query_one("SELECT * FROM resignation_request WHERE id = ?", (request_id,))
    if not request:
        raise ValueError("离职申请不存在")
    if request["status"] == STATUS_COMPLETED:
        raise ValueError("该申请已完成离职，无法驳回")
    reason = request.get("reason_detail") or ""
    merged = (reason + ("\n" if reason and note else "") + note).strip()
    store.execute(
        "UPDATE resignation_request SET status = ?, reason_detail = ?, updated_at = ? WHERE id = ?",
        (STATUS_REJECTED, merged, _now(), request_id),
    )
    return store.query_one("SELECT * FROM resignation_request WHERE id = ?", (request_id,))


def _request_payload(row: dict) -> dict:
    payload = dict(row)
    payload["status_label"] = RESIGN_STATUS_LABELS.get(row["status"], row["status"])
    payload["employee_active"] = None
    return payload


# ---- 飞书下发 ----


class ResignationDeliveryError(Exception):
    pass


def _get_token(app_id: str, app_secret: str, *, client: httpx.Client) -> str:
    try:
        response = client.post(TOKEN_URL, json={"app_id": app_id, "app_secret": app_secret})
    except httpx.HTTPError as exc:
        raise ResignationDeliveryError(f"获取飞书 tenant_access_token 失败：{exc}") from exc
    data = response.json()
    if data.get("code") != 0:
        raise ResignationDeliveryError(f"获取飞书 tenant_access_token 失败：{data.get('msg', '未知错误')}")
    return str(data.get("tenant_access_token") or "")


def deliver_request(
    store: AttendanceStore,
    request: dict,
    *,
    base_url: str,
    client: httpx.Client | None = None,
) -> dict:
    """把表单链接通过飞书应用消息发给员工本人，返回送达结果并写回记录。"""
    app_id = store.get_config("feishu_app_id")
    app_secret = store.get_config("feishu_app_secret")
    employee = store.query_one(
        "SELECT feishu_open_id, feishu_user_id FROM employee WHERE id = ?", (request["employee_id"],)
    )
    open_id = (employee or {}).get("feishu_open_id") or ""
    user_id = (employee or {}).get("feishu_user_id") or ""
    receive_id, receive_type = (open_id, "open_id") if open_id else (user_id, "user_id")
    link = build_form_url(base_url, request["token"])

    if not base_url or not receive_id or not app_id or not app_secret:
        if not base_url:
            reason = "未配置对外访问地址"
        elif not receive_id:
            reason = "员工缺少飞书用户标识"
        else:
            reason = "未配置飞书应用凭证"
        store.execute(
            "UPDATE resignation_request SET deliver_status = 'manual', deliver_error = ?, updated_at = ? WHERE id = ?",
            (f"{reason}，请手工转发链接：{link}", _now(), request["id"]),
        )
        return {"delivered": False, "manual": True, "link": link, "detail": reason}

    http = client or httpx.Client(timeout=15)
    try:
        token = _get_token(app_id, app_secret, client=http)
        card = _build_card(request, link)
        response = http.post(
            MESSAGE_URL,
            params={"receive_id_type": receive_type},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"receive_id": receive_id, "msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False)},
        )
        payload = response.json()
    except (httpx.HTTPError, ResignationDeliveryError, ValueError) as exc:
        result = {"ok": False, "detail": f"飞书消息发送失败：{exc}"}
    else:
        code = payload.get("code")
        if code == 0:
            result = {"ok": True, "detail": ""}
        elif code == SCOPE_DENIED_CODE:
            # 飞书自己的 msg 里带开通入口与所需权限清单，直接透传比自造文案更有用
            result = {
                "ok": False,
                "detail": f"飞书应用未开通发消息权限：{payload.get('msg', '')}".strip(),
            }
        else:
            result = {"ok": False, "detail": f"飞书消息发送失败（{code}）：{payload.get('msg', '未知错误')}"}
    finally:
        if client is None:
            http.close()

    if result["ok"]:
        store.execute(
            "UPDATE resignation_request SET deliver_status = 'sent', deliver_error = '', updated_at = ? WHERE id = ?",
            (_now(), request["id"]),
        )
        return {"delivered": True, "manual": False, "link": link, "detail": ""}
    store.execute(
        "UPDATE resignation_request SET deliver_status = 'failed', deliver_error = ?, updated_at = ? WHERE id = ?",
        (result["detail"], _now(), request["id"]),
    )
    return {"delivered": False, "manual": False, "link": link, "detail": result["detail"]}


def _build_card(request: dict, link: str) -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "orange", "title": {"tag": "plain_text", "content": "离职信息填写"}},
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"**{request['employee_name']}** 你好，请在线填写离职信息（最后工作日、"
                        "离职原因与工作交接），提交后由 HR 确认。"
                    ),
                },
            },
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "填写离职信息"},
                        "type": "primary",
                        "url": link,
                    }
                ],
            },
            {"tag": "note", "elements": [{"tag": "plain_text", "content": "该链接仅你本人可用，请勿转发"}]},
        ],
    }
