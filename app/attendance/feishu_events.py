"""飞书事件订阅（长连接）：实时接收通讯录成员变更，自动维护在职状态与入离职事件。

本机部署拿不到公网回调地址，自建应用只能用长连接方式订阅事件：应用主动向飞书建立
WebSocket 连接，事件由飞书推送到该连接，无需对外暴露端口。前置条件是在飞书开放平台
把「事件订阅」方式设为长连接，并订阅通讯录用户创建 / 更新 / 删除事件。

只要配置了飞书应用凭证，长连接随应用启动自动建立（无需额外开关）：这也是飞书控制台
「验证」长连接能否成功的前提——应用侧没有连接时，控制台校验必然失败。
收到的成员变更会映射到本地员工档案：刷新姓名、部门、岗位、手机号、入职日期与在职状态，
并在状态真实变化时写入一条入离职事件（来源 `feishu`）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time

from .db import AttendanceStore, _now
from .feishu_contacts import (
    FeishuContactsClient,
    _is_employed,
    _join_date,
)
from .lifecycle import EVENT_OFFBOARD, EVENT_ONBOARD, SOURCE_FEISHU, record_event, today_cn
from .sync import CONFIG_APP_ID, CONFIG_APP_SECRET

logger = logging.getLogger(__name__)

# 事件订阅开关与最近一次事件回执，均存 app_config。
# 默认开启（有凭证即连接）；显式写入 "0" 可关闭，用于排障。
CONFIG_ENABLED = "feishu_events_enabled"
CONFIG_LAST_EVENT = "feishu_events_last"

# 部门名缓存时长（秒）：事件里只有 department_ids，需查一次部门名
DEPARTMENT_TTL = 600

# 连接建立宽限期（秒）；超过仍无连接给出可执行提示
CONNECT_GRACE_SECONDS = 20

CONNECT_HINT = (
    "事件长连接未建立：请在飞书开放平台把「事件订阅」方式设为「长连接」并订阅"
    "通讯录用户创建 / 更新 / 删除事件，重新发布应用后重试"
)


def _status_dict(status) -> dict:
    if status is None:
        return {}
    return {
        key: bool(getattr(status, key, False))
        for key in ("is_frozen", "is_resigned", "is_activated", "is_exited", "is_unjoin")
    }


def _member_from_event(obj) -> dict:
    """把飞书用户事件对象映射为与通讯录同步一致的员工字段。"""
    return {
        "employee_no": str(getattr(obj, "employee_no", "") or "").strip(),
        "feishu_user_id": str(getattr(obj, "user_id", "") or "").strip(),
        "feishu_open_id": str(getattr(obj, "open_id", "") or "").strip(),
        "name": str(getattr(obj, "name", "") or "").strip(),
        "position": str(getattr(obj, "job_title", "") or "").strip(),
        "phone": str(getattr(obj, "mobile", "") or "").strip(),
        "join_date": _join_date(getattr(obj, "join_time", None)),
        "department_ids": [str(value) for value in (getattr(obj, "department_ids", None) or [])],
        "active": _is_employed(_status_dict(getattr(obj, "status", None))),
    }


def _find_employee(store: AttendanceStore, info: dict, key: str):
    for candidate in (key, info["feishu_user_id"], info["feishu_open_id"]):
        if not candidate:
            continue
        row = store.query_one(
            "SELECT id, active, employment_status FROM employee WHERE employee_no = ?", (candidate,)
        )
        if row:
            return row
    return None


def apply_member_change(store: AttendanceStore, info: dict, *, deleted: bool = False) -> dict:
    """把一条成员变更落到员工档案与事件流，返回本次动作摘要。

    返回形如 `{"action": "onboard"|"offboard"|"updated"|"skipped", "name": ...}`。
    """
    key = info["employee_no"] or info["feishu_user_id"] or info["feishu_open_id"]
    if not key or not info["name"]:
        return {"action": "skipped", "reason": "缺少工号或姓名"}
    is_active = False if deleted else bool(info["active"])
    existing = _find_employee(store, info, key)
    was_active = bool(existing["active"]) if existing else False
    status = "left" if not is_active else (
        "regular" if not existing or existing["employment_status"] in ("", "left") else existing["employment_status"]
    )
    if existing:
        store.execute(
            "UPDATE employee SET employee_no = ?, name = ?, department = COALESCE(NULLIF(?, ''), department), "
            "position = ?, phone = ?, join_date = COALESCE(?, join_date), employment_status = ?, active = ?, "
            "feishu_user_id = ?, feishu_open_id = ?, updated_at = ? WHERE id = ?",
            (
                key,
                info["name"],
                info["department"],
                info["position"],
                info["phone"],
                info["join_date"],
                status,
                1 if is_active else 0,
                info["feishu_user_id"],
                info["feishu_open_id"],
                _now(),
                existing["id"],
            ),
        )
        employee_id = existing["id"]
    else:
        employee_id = store.execute(
            "INSERT INTO employee (employee_no, name, aliases, department, position, join_date, "
            "employment_status, active, phone, feishu_user_id, feishu_open_id, created_at, updated_at) "
            "VALUES (?, ?, '[]', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                key,
                info["name"],
                info["department"],
                info["position"],
                info["join_date"],
                status,
                1 if is_active else 0,
                info["phone"],
                info["feishu_user_id"],
                info["feishu_open_id"],
                _now(),
                _now(),
            ),
        )

    employee = {
        "id": employee_id,
        "employee_no": key,
        "name": info["name"],
        "department": info["department"],
    }
    action = "updated"
    if is_active and not was_active:
        record_event(
            store,
            employee,
            event_type=EVENT_ONBOARD,
            effective_date=info["join_date"] or today_cn().isoformat(),
            source=SOURCE_FEISHU,
            reason="飞书事件订阅",
        )
        action = "onboard"
    elif was_active and not is_active:
        record_event(
            store,
            employee,
            event_type=EVENT_OFFBOARD,
            effective_date=today_cn().isoformat(),
            source=SOURCE_FEISHU,
            reason="飞书事件订阅",
        )
        action = "offboard"
    return {"action": action, "name": info["name"]}


class FeishuEventEngine:
    """长连接事件引擎：在后台线程里运行 lark-oapi 的 WebSocket 客户端。

    有飞书应用凭证即自动启动；`running` 表示工作线程存活，`connected` 表示握过手。
    """

    def __init__(self, store: AttendanceStore) -> None:
        self.store = store
        self._thread: threading.Thread | None = None
        self._ws = None
        self._contacts: FeishuContactsClient | None = None
        self._dept_cache: tuple[float, dict[str, str]] | None = None
        self.last_error = ""
        self.stats = {"onboard": 0, "offboard": 0, "updated": 0, "skipped": 0}

    # ---- 状态 ----

    @property
    def configured(self) -> bool:
        return bool(self.store.get_config(CONFIG_APP_ID) and self.store.get_config(CONFIG_APP_SECRET))

    @property
    def enabled(self) -> bool:
        """有凭证时默认开启；显式写入 "0" 才关闭，用于排障。"""
        return self.configured and self.store.get_config(CONFIG_ENABLED, "1") == "1"

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def connected(self) -> bool:
        """lark 客户端握手成功后写入 `_conn_id`，断开时清空。"""
        return bool(self._ws is not None and getattr(self._ws, "_conn_id", ""))

    def last_event(self) -> dict | None:
        raw = self.store.get_config(CONFIG_LAST_EVENT)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    # ---- 生命周期 ----

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self.configured or not self.enabled:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="feishu-events")
        self._thread.start()

    def stop(self) -> None:
        """关闭长连接：先关自动重连，再在客户端自己的事件循环上断开并停循环。"""
        ws_client, self._ws = self._ws, None
        self._thread = None
        if ws_client is None:
            return
        try:
            from lark_oapi.ws import client as ws_module

            loop = getattr(ws_module, "loop", None)
            if loop is not None and loop.is_running():
                ws_client._auto_reconnect = False
                asyncio.run_coroutine_threadsafe(ws_client._disconnect(), loop).result(timeout=5)
                loop.call_soon_threadsafe(loop.stop)
        except Exception:  # noqa: BLE001
            logger.warning("关闭飞书事件长连接失败", exc_info=True)

    def _run(self) -> None:
        app_id = self.store.get_config(CONFIG_APP_ID)
        app_secret = self.store.get_config(CONFIG_APP_SECRET)
        try:
            import lark_oapi as lark
            from lark_oapi.ws import Client as WsClient
        except ImportError:
            self.last_error = "缺少 lark-oapi 依赖，无法建立事件长连接"
            logger.warning(self.last_error)
            return

        self._contacts = FeishuContactsClient(app_id, app_secret)
        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_contact_user_created_v3(self._on_created)
            .register_p2_contact_user_updated_v3(self._on_updated)
            .register_p2_contact_user_deleted_v3(self._on_deleted)
            .build()
        )
        ws_client = WsClient(app_id, app_secret, event_handler=handler)
        self._ws = ws_client
        threading.Thread(target=self._watch_connect, daemon=True, name="feishu-events-watch").start()
        try:
            self.last_error = ""
            ws_client.start()
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"事件长连接建立失败：{exc}；{CONNECT_HINT}"
            logger.exception("飞书事件长连接异常退出")

    def _watch_connect(self) -> None:
        """宽限期内没连上就给出可执行提示，避免界面只显示「未连接」而无从下手。"""
        deadline = time.time() + CONNECT_GRACE_SECONDS
        while time.time() < deadline:
            if self.connected:
                self.last_error = ""
                return
            time.sleep(0.5)
        if not self.connected and not self.last_error:
            self.last_error = CONNECT_HINT

    # ---- 事件处理 ----

    def _on_created(self, event):  # noqa: ANN001
        self._dispatch(event, deleted=False)

    def _on_updated(self, event):  # noqa: ANN001
        self._dispatch(event, deleted=False)

    def _on_deleted(self, event):  # noqa: ANN001
        self._dispatch(event, deleted=True)

    def _dispatch(self, event, *, deleted: bool) -> None:  # noqa: ANN001
        try:
            obj = getattr(getattr(event, "event", None), "object", None)
            if obj is None:
                return
            info = _member_from_event(obj)
            info["department"] = self._department_name(info["department_ids"])
            result = apply_member_change(self.store, info, deleted=deleted)
            action = result.get("action", "updated")
            if action in self.stats:
                self.stats[action] += 1
            self.store.set_config(
                CONFIG_LAST_EVENT,
                json.dumps(
                    {"at": _now(), "action": action, "name": result.get("name", "")},
                    ensure_ascii=False,
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception("处理飞书成员事件失败")

    def _department_name(self, department_ids: list[str]) -> str:
        if not department_ids:
            return ""
        names = self._department_map()
        for department_id in department_ids:
            name = names.get(str(department_id))
            if name:
                return name
        return ""

    def _department_map(self) -> dict[str, str]:
        cached = self._dept_cache
        if cached and time.time() < cached[0]:
            return cached[1]
        mapping: dict[str, str] = {}
        if self._contacts is not None:
            try:
                mapping = {
                    item["department_id"]: item["name"] for item in self._contacts.list_departments()
                }
            except Exception:  # noqa: BLE001
                logger.warning("读取飞书部门名失败，本次事件不带部门", exc_info=True)
                mapping = cached[1] if cached else {}
        self._dept_cache = (time.time() + DEPARTMENT_TTL, mapping)
        return mapping
