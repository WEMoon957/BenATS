"""飞书通讯录：按部门树拉取成员并落到考勤模块的员工档案。

供「人事中台」用一次同步把飞书的人事事实（姓名、部门、岗位、手机号、入职日期、
在职状态）写入本地 employee 表。本地专有字段不在同步范围内：别名、标签、考勤策略
与应出勤天数覆盖保持原值。
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Iterator

import httpx

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
# 部门 ID 是路径参数，子部门列表不接受 department_id 查询参数
DEPARTMENTS_CHILDREN_URL = (
    "https://open.feishu.cn/open-apis/contact/v3/departments/{department_id}/children"
)
DEPARTMENT_USERS_URL = "https://open.feishu.cn/open-apis/contact/v3/users/find_by_department"
# 读取应用当前的通讯录授权范围，用于把无权限报错说清楚
SCOPES_URL = "https://open.feishu.cn/open-apis/contact/v3/scopes"

CHINA_TZ = timezone(timedelta(hours=8))
PAGE_SIZE = 50
ROOT_DEPARTMENT_ID = "0"
DEPARTMENT_ID_TYPE = "open_department_id"
TOKEN_REFRESH_MARGIN = 120

# 飞书拒绝未开通的权限时返回的业务码，用于把报错翻成可执行的提示
SCOPE_DENIED_CODE = 99991672
# 部门不在应用通讯录权限范围内；查询根部门要求权限范围为全部成员
SCOPE_RANGE_CODES = {40004, 40014}
# 既覆盖通讯录接口权限，也覆盖工号、姓名、部门、入职时间与职务等字段权限
CONTACTS_SCOPE = "contact:contact:readonly"
# 手机号是独立的字段权限，不随通讯录权限一并返回
PHONE_SCOPE = "contact:user.phone:readonly"

# 最近一次员工同步的结果，存成 app_config 的一行 JSON
CONFIG_LAST_SYNC = "feishu_contacts_last_sync"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FeishuContactsError(Exception):
    def __init__(self, code, msg) -> None:
        super().__init__(f"飞书通讯录错误（{code}）：{msg}")
        self.code = code
        self.msg = msg


class FeishuContactsClient:
    """飞书通讯录客户端。`client` 可注入，便于测试替身。"""

    def __init__(self, app_id: str, app_secret: str, *, client: httpx.Client | None = None) -> None:
        self.app_id = app_id.strip()
        self.app_secret = app_secret.strip()
        self._client = client or httpx.Client(timeout=30)
        self._token: str | None = None
        self._token_expire_at = 0.0
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.app_secret)

    def _get_token(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token_expire_at - TOKEN_REFRESH_MARGIN:
                return self._token
            try:
                response = self._client.post(
                    TOKEN_URL,
                    json={"app_id": self.app_id, "app_secret": self.app_secret},
                )
            except httpx.HTTPError as exc:
                raise FeishuContactsError("network", f"获取 tenant_access_token 失败：{exc}") from exc
            data = self._payload(response)
            if data.get("code") != 0:
                raise FeishuContactsError(data.get("code"), data.get("msg", "获取 token 失败"))
            # token 接口把凭证放在响应顶层，与业务接口的 data 信封不同
            self._token = str(data.get("tenant_access_token") or "")
            self._token_expire_at = time.time() + int(data.get("expire", 7200))
            return self._token

    def _payload(self, response: httpx.Response) -> dict:
        try:
            return response.json()
        except ValueError as exc:
            raise FeishuContactsError("parse", f"飞书响应不是合法 JSON（HTTP {response.status_code}）") from exc

    def _request(self, url: str, params: dict) -> dict:
        try:
            response = self._client.get(
                url, params=params, headers={"Authorization": f"Bearer {self._get_token()}"}
            )
        except httpx.HTTPError as exc:
            raise FeishuContactsError("network", f"请求飞书通讯录失败：{exc}") from exc
        payload = self._payload(response)
        code = payload.get("code")
        if code != 0:
            raise FeishuContactsError(code, self._message(code, payload.get("msg", "请求失败")))
        return payload.get("data") or {}

    def _message(self, code, msg: str) -> str:
        if code == SCOPE_DENIED_CODE:
            return (
                f"飞书应用缺少通讯录权限：请开通 {CONTACTS_SCOPE}"
                f"（需要手机号时另加 {PHONE_SCOPE}），并把通讯录权限范围设为全部成员后重新发布应用"
            )
        if code in SCOPE_RANGE_CODES:
            return (
                "飞书应用的通讯录权限范围不包含该部门：请在开放平台把通讯录权限范围设为全部成员"
                "（查询根部门下的子部门要求全员范围），重新发布后重试"
            )
        return str(msg)

    def _pages(self, url: str, params: dict) -> Iterator[dict]:
        page_token = ""
        while True:
            query = dict(params, page_size=PAGE_SIZE)
            if page_token:
                query["page_token"] = page_token
            data = self._request(url, query)
            for item in data.get("items") or []:
                yield item
            page_token = str(data.get("page_token") or "")
            if not data.get("has_more") or not page_token:
                return

    def list_departments(self) -> list[dict]:
        """从根部门递归收集全部子部门，返回 `{department_id, name}` 列表。

        权限范围不含根部门时补上当前实际授权范围，让「该改哪个设置」不用靠猜。
        """
        try:
            return self._walk_departments()
        except FeishuContactsError as exc:
            if exc.code in SCOPE_RANGE_CODES:
                raise FeishuContactsError(
                    exc.code, f"{exc.msg}；应用当前的通讯录权限范围：{self.scope_summary()}"
                ) from exc
            raise

    def _walk_departments(self) -> list[dict]:
        departments: list[dict] = []
        queue = [ROOT_DEPARTMENT_ID]
        visited = {ROOT_DEPARTMENT_ID}
        while queue:
            parent = queue.pop(0)
            for item in self._pages(
                DEPARTMENTS_CHILDREN_URL.format(department_id=parent),
                {"department_id_type": DEPARTMENT_ID_TYPE},
            ):
                department_id = str(item.get("open_department_id") or item.get("department_id") or "")
                if not department_id or department_id in visited:
                    continue
                visited.add(department_id)
                departments.append({"department_id": department_id, "name": str(item.get("name") or "")})
                queue.append(department_id)
        return departments

    def authorized_scope(self) -> dict:
        """查询应用当前的通讯录授权范围（部门与用户）。"""
        data = self._request(SCOPES_URL, {})
        return {
            "department_ids": [str(value) for value in data.get("department_ids") or []],
            "user_ids": [str(value) for value in data.get("user_ids") or []],
        }

    def scope_summary(self) -> str:
        """把授权范围压成一句话；读取失败时不掩盖原本的报错。"""
        try:
            scope = self.authorized_scope()
        except FeishuContactsError:
            return "无法读取"
        return f"{len(scope['department_ids'])} 个部门、{len(scope['user_ids'])} 个用户"

    def list_users(self, department_id: str) -> list[dict]:
        """按部门拉取成员，已合并分页。"""
        return list(
            self._pages(
                DEPARTMENT_USERS_URL,
                {"department_id": department_id, "department_id_type": DEPARTMENT_ID_TYPE},
            )
        )

    def fetch_employees(self) -> list[dict]:
        """遍历部门树收集成员，按人员维度去重后映射为员工档案字段。"""
        departments = self.list_departments()
        department_names = {item["department_id"]: item["name"] for item in departments}
        employees: dict[str, dict] = {}
        for department_id in [ROOT_DEPARTMENT_ID, *(item["department_id"] for item in departments)]:
            for user in self.list_users(department_id):
                key = str(user.get("open_id") or user.get("user_id") or user.get("employee_no") or "")
                if not key or key in employees:
                    continue
                employees[key] = _to_employee(user, department_names)
        return list(employees.values())


def _is_employed(status: dict) -> bool:
    """离职、主动退出与尚未加入企业都不计入在职；账号暂停仍视为在职员工。"""
    return not (status.get("is_exited") or status.get("is_resigned") or status.get("is_unjoin"))


def _to_employee(user: dict, department_names: dict[str, str]) -> dict:
    status = user.get("status") or {}
    department = ""
    for department_id in user.get("department_ids") or []:
        name = department_names.get(str(department_id))
        if name:
            department = name
            break
    return {
        "employee_no": str(user.get("employee_no") or "").strip(),
        "name": str(user.get("name") or "").strip(),
        "department": department,
        "position": str(user.get("job_title") or "").strip(),
        "phone": str(user.get("mobile") or "").strip(),
        "join_date": _join_date(user.get("join_time")),
        "active": _is_employed(status),
    }


def _join_date(join_time) -> str | None:
    if not join_time:
        return None
    return datetime.fromtimestamp(int(join_time), CHINA_TZ).strftime("%Y-%m-%d")


def _employment_status(active: bool, current: str) -> str:
    """飞书通讯录不区分试用期与已转正，仅在离职时改写状态，其余保留本地判断。"""
    if not active:
        return "left"
    return current if current and current != "left" else "regular"


def sync_employees(store, client: FeishuContactsClient) -> dict:
    """把飞书成员写入 employee 表并按工号归档，返回本次同步的计数摘要。"""
    employees = client.fetch_employees()
    inserted = 0
    updated = 0
    skipped = 0
    for item in employees:
        employee_no = item["employee_no"]
        # 工号是 employee 表的唯一键，也是飞书考勤按工号取数的前提，缺则不入库
        if not employee_no or not item["name"]:
            skipped += 1
            continue
        existing = store.query_one(
            "SELECT id, employment_status FROM employee WHERE employee_no = ?", (employee_no,)
        )
        status = _employment_status(item["active"], str((existing or {}).get("employment_status") or ""))
        if existing:
            store.execute(
                "UPDATE employee SET name = ?, department = ?, position = ?, phone = ?, "
                "join_date = COALESCE(?, join_date), employment_status = ?, active = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    item["name"],
                    item["department"],
                    item["position"],
                    item["phone"],
                    item["join_date"],
                    status,
                    1 if item["active"] else 0,
                    _now(),
                    existing["id"],
                ),
            )
            updated += 1
        else:
            store.execute(
                "INSERT INTO employee (employee_no, name, aliases, department, position, join_date, "
                "employment_status, active, phone, created_at, updated_at) "
                "VALUES (?, ?, '[]', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    employee_no,
                    item["name"],
                    item["department"],
                    item["position"],
                    item["join_date"],
                    status,
                    1 if item["active"] else 0,
                    item["phone"],
                    _now(),
                    _now(),
                ),
            )
            inserted += 1
    # 成员全部缺工号或姓名时基本是字段权限没开齐，直接给出可执行的提示而不是记一条空摘要
    if employees and not inserted and not updated:
        raise FeishuContactsError(
            "no_employee_no",
            f"飞书返回的 {len(employees)} 名成员都没有工号或姓名，无法按工号建档："
            f"请在飞书补齐成员的工号与姓名，并确认应用已开通 {CONTACTS_SCOPE}（工号与姓名属于字段权限）",
        )
    summary = {
        "at": _now(),
        "total": len(employees),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
    }
    store.set_config(CONFIG_LAST_SYNC, json.dumps(summary, ensure_ascii=False))
    return summary


def last_sync(store) -> dict | None:
    """读取最近一次员工同步的摘要，未同步过时返回 None。"""
    raw = store.get_config(CONFIG_LAST_SYNC)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None
