"""员工生命周期：入离职事件流、离职执行与人员流动看板。

事件流（employee_lifecycle_event）是入离职的事实记录；employee 表上的
active / employment_status / leave_date 是当前状态的物化结果。两者由本模块
统一维护，避免多处各写一套口径：

- 来源 `feishu`：通讯录同步或事件订阅发现的在职状态变化。
- 来源 `resignation`：离职表单流程确认并到期后执行。
- 来源 `manual`：人工在界面调整。

事件按 (员工, 类型, 生效日期, 来源) 幂等，重复触发不会产生重复行。
"""

from __future__ import annotations

import calendar
import json
import logging
import threading
from datetime import date, datetime, timedelta

from .db import AttendanceStore, _now
from .feishu import CHINA_TZ

logger = logging.getLogger(__name__)

EVENT_ONBOARD = "onboard"
EVENT_OFFBOARD = "offboard"
EVENT_LABELS = {EVENT_ONBOARD: "入职", EVENT_OFFBOARD: "离职"}

SOURCE_FEISHU = "feishu"
SOURCE_RESIGNATION = "resignation"
SOURCE_MANUAL = "manual"

STATUS_SENT = "sent"
STATUS_SUBMITTED = "submitted"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"
STATUS_COMPLETED = "completed"

RESIGN_STATUS_LABELS = {
    STATUS_SENT: "待员工填写",
    STATUS_SUBMITTED: "待 HR 确认",
    STATUS_CONFIRMED: "待到岗离职日",
    STATUS_COMPLETED: "已离职",
    STATUS_REJECTED: "已驳回",
}


def today_cn() -> date:
    return datetime.now(CHINA_TZ).date()


def record_event(
    store: AttendanceStore,
    employee: dict | None,
    *,
    event_type: str,
    effective_date: str,
    source: str,
    reason: str = "",
    detail: dict | None = None,
) -> bool:
    """幂等写入一条入离职事件，重复返回 False。"""
    if not employee or not effective_date:
        return False
    exists = store.query_one(
        "SELECT id FROM employee_lifecycle_event WHERE employee_id = ? AND event_type = ? "
        "AND effective_date = ? AND source = ?",
        (employee["id"], event_type, effective_date, source),
    )
    if exists:
        return False
    store.execute(
        "INSERT INTO employee_lifecycle_event (employee_id, employee_no, employee_name, department, "
        "event_type, effective_date, source, reason, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            employee["id"],
            employee.get("employee_no", ""),
            employee.get("name", ""),
            employee.get("department", ""),
            event_type,
            effective_date,
            source,
            reason,
            json.dumps(detail or {}, ensure_ascii=False),
            _now(),
        ),
    )
    return True


def onboard_employee(
    store: AttendanceStore,
    employee_id: int,
    *,
    effective_date: str,
    source: str = SOURCE_FEISHU,
    reason: str = "",
) -> bool:
    """把员工置为在职并记录入职事件。"""
    employee = store.query_one("SELECT * FROM employee WHERE id = ?", (employee_id,), table="employee")
    if not employee:
        return False
    store.execute(
        "UPDATE employee SET active = 1, employment_status = CASE WHEN employment_status = 'left' "
        "THEN 'regular' ELSE employment_status END, leave_date = NULL, updated_at = ? WHERE id = ?",
        (_now(), employee_id),
    )
    return record_event(
        store, employee, event_type=EVENT_ONBOARD, effective_date=effective_date, source=source, reason=reason
    )


def offboard_employee(
    store: AttendanceStore,
    employee_id: int,
    *,
    effective_date: str,
    source: str = SOURCE_MANUAL,
    reason: str = "",
) -> bool:
    """把员工置为离职并记录离职事件。"""
    employee = store.query_one("SELECT * FROM employee WHERE id = ?", (employee_id,), table="employee")
    if not employee:
        return False
    store.execute(
        "UPDATE employee SET active = 0, employment_status = 'left', leave_date = ?, updated_at = ? WHERE id = ?",
        (effective_date, _now(), employee_id),
    )
    return record_event(
        store, employee, event_type=EVENT_OFFBOARD, effective_date=effective_date, source=source, reason=reason
    )


def derive_events_from_sync(store: AttendanceStore, transitions: list[dict]) -> int:
    """根据通讯录同步前后的状态差异写入事件。

    `transitions` 每项形如
    `{"employee": {...}, "was_active": bool, "is_active": bool, "join_date": str | None}`。
    只处理真实变化：新入职、离职、重新入职。
    """
    written = 0
    for item in transitions:
        employee = item.get("employee")
        was_active = bool(item.get("was_active"))
        is_active = bool(item.get("is_active"))
        if not employee:
            continue
        if not was_active and is_active:
            effective = item.get("join_date") or today_cn().isoformat()
            written += int(
                record_event(
                    store,
                    employee,
                    event_type=EVENT_ONBOARD,
                    effective_date=effective,
                    source=SOURCE_FEISHU,
                    reason="飞书通讯录同步",
                )
            )
        elif was_active and not is_active:
            written += int(
                record_event(
                    store,
                    employee,
                    event_type=EVENT_OFFBOARD,
                    effective_date=today_cn().isoformat(),
                    source=SOURCE_FEISHU,
                    reason="飞书通讯录同步",
                )
            )
    return written


def process_due_offboards(store: AttendanceStore, *, on_date: date | None = None) -> dict:
    """把已确认且到最后工作日的离职申请执行掉，返回本轮处理结果。"""
    target = (on_date or today_cn()).isoformat()
    rows = store.query(
        "SELECT * FROM resignation_request WHERE status = ? AND last_working_day IS NOT NULL "
        "AND last_working_day <= ?",
        (STATUS_CONFIRMED, target),
    )
    processed = 0
    for row in rows:
        employee = store.query_one(
            "SELECT * FROM employee WHERE id = ?", (row["employee_id"],), table="employee"
        )
        if not employee:
            store.execute(
                "UPDATE resignation_request SET status = ?, updated_at = ? WHERE id = ?",
                (STATUS_REJECTED, _now(), row["id"]),
            )
            continue
        reason = row.get("reason_category") or "离职申请"
        if employee["active"]:
            offboard_employee(
                store,
                employee["id"],
                effective_date=row["last_working_day"],
                source=SOURCE_RESIGNATION,
                reason=reason,
            )
        else:
            # 飞书已先一步把成员置为离职：补记一条表单来源的事件，不改动已离职状态
            record_event(
                store,
                employee,
                event_type=EVENT_OFFBOARD,
                effective_date=row["last_working_day"],
                source=SOURCE_RESIGNATION,
                reason=reason,
            )
        store.execute(
            "UPDATE resignation_request SET status = ?, updated_at = ? WHERE id = ?",
            (STATUS_COMPLETED, _now(), row["id"]),
        )
        processed += 1
    return {"processed": processed, "date": target}


def list_events(
    store: AttendanceStore, *, event_type: str | None = None, limit: int = 100
) -> list[dict]:
    sql = "SELECT * FROM employee_lifecycle_event WHERE 1=1"
    params: list = []
    if event_type:
        sql += " AND event_type = ?"
        params.append(event_type)
    sql += " ORDER BY effective_date DESC, id DESC LIMIT ?"
    params.append(limit)
    return store.query(sql, tuple(params), table="employee_lifecycle_event")


def _month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def employee_flow_dashboard(
    store: AttendanceStore, *, months: int = 6, on_date: date | None = None
) -> dict:
    """人员流动看板数据：按月入离职、在职与离职率、原因分布、部门流动与离职待办。"""
    today = on_date or today_cn()
    active = store.query("SELECT COUNT(*) AS c FROM employee WHERE active = 1")[0]["c"]
    total = store.query("SELECT COUNT(*) AS c FROM employee")[0]["c"]

    events = store.query(
        "SELECT employee_id, event_type, effective_date, department FROM employee_lifecycle_event",
        table="employee_lifecycle_event",
    )
    onboard_events = [e for e in events if e["event_type"] == EVENT_ONBOARD and e["effective_date"]]
    offboard_events = [e for e in events if e["event_type"] == EVENT_OFFBOARD and e["effective_date"]]

    def count_after(items: list[dict], cutoff: str) -> int:
        return sum(1 for item in items if item["effective_date"] > cutoff)

    def headcount_at(cutoff: str) -> int:
        # 从当前在职数倒推月末人数：加回之后离职的，减去之后入职的
        return active + count_after(offboard_events, cutoff) - count_after(onboard_events, cutoff)

    window = [_shift_month(today.year, today.month, offset) for offset in range(-(months - 1), 1)]
    series = []
    for year, month in window:
        start = date(year, month, 1)
        end_of_month = date(year, month, calendar.monthrange(year, month)[1])
        start_iso = start.isoformat()
        end_iso = end_of_month.isoformat()
        onboard = sum(1 for e in onboard_events if start_iso <= e["effective_date"] <= end_iso)
        offboard = sum(1 for e in offboard_events if start_iso <= e["effective_date"] <= end_iso)
        headcount = headcount_at(end_iso)
        start_headcount = headcount_at((start - timedelta(days=1)).isoformat())
        series.append(
            {
                "month": _month_key(year, month),
                "onboard": onboard,
                "offboard": offboard,
                "headcount": headcount,
                "turnover_rate": round(offboard / start_headcount * 100, 1) if start_headcount else 0,
            }
        )

    current = series[-1]
    reasons = store.query(
        "SELECT CASE WHEN reason_category = '' THEN '未填写' ELSE reason_category END AS category, "
        "COUNT(*) AS c FROM resignation_request WHERE status IN (?, ?, ?) "
        "GROUP BY category ORDER BY c DESC",
        (STATUS_SUBMITTED, STATUS_CONFIRMED, STATUS_COMPLETED),
    )
    departments = store.query(
        "SELECT CASE WHEN department = '' THEN '未分组' ELSE department END AS department, "
        "COUNT(*) AS c FROM employee WHERE active = 1 GROUP BY department ORDER BY department"
    )
    dept_onboard: dict[str, int] = {}
    dept_offboard: dict[str, int] = {}
    window_start = _month_key(*window[0]) + "-01"
    for item in onboard_events:
        if item["effective_date"] >= window_start:
            key = item["department"] or "未分组"
            dept_onboard[key] = dept_onboard.get(key, 0) + 1
    for item in offboard_events:
        if item["effective_date"] >= window_start:
            key = item["department"] or "未分组"
            dept_offboard[key] = dept_offboard.get(key, 0) + 1

    pending_rows = store.query(
        "SELECT status, COUNT(*) AS c FROM resignation_request GROUP BY status"
    )
    pending_by_status = {row["status"]: row["c"] for row in pending_rows}

    recent = store.query(
        "SELECT id, employee_name, department, event_type, effective_date, source, reason "
        "FROM employee_lifecycle_event ORDER BY effective_date DESC, id DESC LIMIT 8",
        table="employee_lifecycle_event",
    )

    return {
        "active": active,
        "total": total,
        "current": {
            "onboard": current["onboard"],
            "offboard": current["offboard"],
            "turnover_rate": current["turnover_rate"],
            "headcount": current["headcount"],
        },
        "months": series,
        "reasons": [{"category": row["category"], "count": row["c"]} for row in reasons],
        "departments": [
            {
                "department": row["department"],
                "active": row["c"],
                "onboard": dept_onboard.get(row["department"], 0),
                "offboard": dept_offboard.get(row["department"], 0),
            }
            for row in departments
        ],
        "pending": {
            "sent": pending_by_status.get(STATUS_SENT, 0),
            "submitted": pending_by_status.get(STATUS_SUBMITTED, 0),
            "confirmed": pending_by_status.get(STATUS_CONFIRMED, 0),
            "completed": pending_by_status.get(STATUS_COMPLETED, 0),
        },
        "recent_events": [
            {
                "id": row["id"],
                "employee_name": row["employee_name"],
                "department": row["department"],
                "event_type": row["event_type"],
                "event_label": EVENT_LABELS.get(row["event_type"], row["event_type"]),
                "effective_date": row["effective_date"],
                "source": row["source"],
                "reason": row["reason"],
            }
            for row in recent
        ],
    }


class LifecycleEngine:
    """周期执行到期离职的定时引擎，模式与飞书考勤同步引擎一致。"""

    def __init__(self, store: AttendanceStore, *, interval_seconds: int = 3600) -> None:
        self.store = store
        self.interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error = ""
        self.last_result: dict | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="employee-lifecycle")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        self._stop.wait(5)
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self.interval)

    def run_once(self) -> dict:
        try:
            result = process_due_offboards(self.store)
            self.last_result = result
            self.last_error = ""
            return result
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            logger.exception("到期离职处理失败")
            return {"processed": 0, "error": self.last_error}
