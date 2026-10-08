"""飞书考勤自动同步：定时拉取打卡结果 → 转换 → 复用核算逻辑。

同步口径：
- 以当前月为单位，每次同步重建该月的「飞书自动同步」批次（幂等覆盖）。
- 飞书返回的 user_task_results 按 user_id（工号）/employee_name 匹配本地员工。
- 无打卡的工作日记为空（休息），有打卡的按班次上下班时间转成 punches。
- 跨日疑似检测与结果重算复用 services 的既有逻辑，人工仅需审核跨日与确认结果。
"""

from __future__ import annotations

import calendar
import logging
import threading
from datetime import date, datetime

from .db import AttendanceStore, _now
from .feishu import (
    CHINA_TZ,
    EMPLOYEE_ID,
    FeishuAttendanceClient,
    FeishuAttendanceError,
    timestamp_to_punch,
)
from .services import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    _cutoff_for,
    _employee_maps,
    json_dumps,
    match_employee,
    recalculate_batch,
)

logger = logging.getLogger(__name__)

# 飞书自动同步批次用固定哈希标记，便于幂等识别
FEISHU_SYNC_HASH = "feishu-sync"
CONFIG_ENABLED = "feishu_enabled"
CONFIG_APP_ID = "feishu_app_id"
CONFIG_APP_SECRET = "feishu_app_secret"


def _default_expected_days() -> float:
    return float(calendar.monthrange(date.today().year, date.today().month)[1])


class FeishuSyncEngine:
    def __init__(self, store: AttendanceStore, *, interval_seconds: int = 3600) -> None:
        self.store = store
        self.interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="feishu-attendance-sync")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        # 启动即尝试一轮（配置就绪才真正同步）
        self._stop.wait(3)
        while not self._stop.is_set():
            try:
                self.sync_once()
            except Exception:  # noqa: BLE001
                logger.exception("飞书考勤同步一轮执行失败")
            self._stop.wait(self.interval)

    def sync_once(self) -> dict:
        app_id = self.store.get_config(CONFIG_APP_ID)
        app_secret = self.store.get_config(CONFIG_APP_SECRET)
        enabled = self.store.get_config(CONFIG_ENABLED) == "1"
        if not enabled or not app_id or not app_secret:
            return {"ok": False, "detail": "飞书考勤未配置或未启用"}

        client = FeishuAttendanceClient(app_id, app_secret)
        now = datetime.now(CHINA_TZ)
        year, month = now.year, now.month
        days_in_month = calendar.monthrange(year, month)[1]

        employees, by_no, by_name, by_alias = _employee_maps(self.store)
        employee_nos = [e["employee_no"] for e in employees if e.get("employee_no", "").strip()]
        if not employee_nos:
            return {"ok": False, "detail": "没有可同步的在职员工"}

        date_from = f"{year}{month:02d}01"
        date_to = f"{year}{month:02d}{days_in_month:02d}"
        try:
            results, invalid = client.query_user_tasks(employee_nos, date_from, date_to)
            # 员工键可能是工号，也可能是飞书用户 ID：先按工号查，再把飞书判为无效的键按用户 ID 查一次
            if invalid:
                fallback, still_invalid = client.query_user_tasks(
                    invalid, date_from, date_to, employee_type=EMPLOYEE_ID
                )
                results.extend(fallback)
                # 两种类型都不认这些键，且一条打卡都没取到时：那是档案里的标识飞书不认，
                # 不能当成「当月没人打卡」记一批全零。个别成员标识无效时只要有人取到数据就继续。
                if not results and not fallback and len(still_invalid) == len(invalid):
                    raise FeishuAttendanceError(
                        "invalid_employee_ids",
                        f"飞书考勤不认这 {len(invalid)} 个员工标识（工号与用户 ID 都试过）："
                        "请为成员填写飞书里的工号或用户 ID，或确认「通讯录权限范围」已覆盖他们后重试",
                    )
        except FeishuAttendanceError as exc:
            self.last_error = str(exc)
            return {"ok": False, "detail": self.last_error}

        # 按 (工号或姓名匹配的员工id, 日期) 组织打卡
        punches_by_day: dict[tuple[int, date], list[dict]] = {}
        matched_employees = 0
        for item in results:
            employee, _status = match_employee(
                item.get("user_id", ""), item.get("employee_name", ""), by_no, by_name, by_alias
            )
            if not employee:
                continue
            matched_employees += 1
            day = int(item.get("day", 0))
            if not day:
                continue
            work_date = date(day // 10000, (day // 100) % 100, day % 100)
            punches: list[dict] = []
            for record in item.get("records") or []:
                check_in = record.get("check_in_record") or {}
                check_out = record.get("check_out_record") or {}
                for punch_record in (check_in, check_out):
                    ts = punch_record.get("check_time")
                    if ts:
                        punches.append(timestamp_to_punch(int(ts), day))
            punches_by_day[(employee["id"], work_date)] = punches

        # 幂等：删除该月已有的飞书同步批次后重建
        old = self.store.query_one(
            "SELECT id FROM import_batch WHERE file_sha256 = ? AND year = ? AND month = ?",
            (FEISHU_SYNC_HASH, year, month),
        )
        if old:
            self.store.execute("DELETE FROM import_batch WHERE id = ?", (old["id"],))

        batch_id = self.store.execute(
            "INSERT INTO import_batch (original_filename, source_file_path, file_sha256, year, month, "
            "default_expected_days, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"飞书自动同步 {year}-{month:02d}",
                "",
                FEISHU_SYNC_HASH,
                year,
                month,
                _default_expected_days(),
                STATUS_PENDING,
                _now(),
            ),
        )

        try:
            total_rows = 0
            matched_rows = 0
            unmatched_rows = 0
            suspicion_count = 0
            days_for_rows: list[dict[int, dict]] = []

            with self.store.transaction() as tx:
                for employee in employees:
                    for day in range(1, days_in_month + 1):
                        work_date = date(year, month, day)
                        punches = punches_by_day.get((employee["id"], work_date), [])
                        total_rows += 1
                        if employee["employee_no"]:
                            matched_rows += 1
                        else:
                            unmatched_rows += 1
                        raw_id = tx.execute(
                            "INSERT INTO raw_punch_day (batch_id, employee_id, source_row, employee_no, "
                            "source_name, organization, attendance_rule, work_date, raw_value, punches, "
                            "has_punch, effective_has_punch, match_status, is_cross_day_suspicion) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                            (
                                batch_id,
                                employee["id"],
                                total_rows,
                                employee.get("employee_no", ""),
                                employee.get("name", ""),
                                employee.get("department", ""),
                                "",
                                work_date.isoformat(),
                                _punches_text(punches),
                                json_dumps(punches),
                                1 if punches else 0,
                                1 if punches else 0,
                                "employee_no" if employee["employee_no"] else "unmatched",
                            ),
                        )
                        days_for_rows.append({day: {
                            "id": raw_id,
                            "employee_id": employee["id"],
                            "employee": employee,
                            "work_date": work_date,
                            "punches": punches,
                        }})

                # 跨日疑似检测（复用既有口径）
                for days_for_row in days_for_rows:
                    for day in sorted(days_for_row):
                        if day <= 1:
                            continue
                        current = days_for_row[day]
                        previous = days_for_row.get(day - 1)
                        if not previous or len(current["punches"]) != 1 or not previous["punches"]:
                            continue
                        punch = current["punches"][0]
                        if not punch.get("valid") or punch.get("next_day"):
                            continue
                        cutoff = _cutoff_for(current.get("employee"))
                        if punch.get("clock_minutes", 10_000) > cutoff:
                            continue
                        previous_has_late_punch = any(
                            p.get("valid") and not p.get("next_day") and p.get("clock_minutes", 0) >= 12 * 60
                            for p in previous["punches"]
                        )
                        if not previous_has_late_punch:
                            continue
                        tx.execute(
                            "UPDATE raw_punch_day SET is_cross_day_suspicion = 1, effective_has_punch = 0 WHERE id = ?",
                            (current["id"],),
                        )
                        tx.execute(
                            "INSERT INTO cross_day_suspicion (batch_id, raw_day_id, employee_id, previous_date, "
                            "work_date, punch_text, reason, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                batch_id,
                                current["id"],
                                current.get("employee_id"),
                                previous["work_date"].isoformat(),
                                current["work_date"].isoformat(),
                                punch["text"],
                                f"当天仅一条凌晨打卡，且前一天存在下午或晚间打卡（截止 {cutoff // 60:02d}:{cutoff % 60:02d}）",
                                STATUS_PENDING,
                                _now(),
                            ),
                        )
                        suspicion_count += 1

            recalculate_batch(self.store, batch_id)
            self.store.execute(
                "UPDATE import_batch SET status = ?, total_rows = ?, matched_rows = ?, unmatched_rows = ?, "
                "suspicion_count = ?, completed_at = ? WHERE id = ?",
                (STATUS_COMPLETED, total_rows, matched_rows, unmatched_rows, suspicion_count, _now(), batch_id),
            )
            self.last_error = ""
            return {
                "ok": True,
                "batch_id": batch_id,
                "period": f"{year}-{month:02d}",
                "employees": len(employees),
                "matched_employees": matched_employees,
                "suspicion_count": suspicion_count,
            }
        except Exception as exc:
            self.store.execute(
                "UPDATE import_batch SET status = ?, error_message = ? WHERE id = ?",
                (STATUS_FAILED, str(exc), batch_id),
            )
            self.last_error = str(exc)
            return {"ok": False, "detail": f"同步失败：{exc}"}


def _punches_text(punches: list[dict]) -> str:
    """把 punches 列表转成可读文本（供 raw_value 展示），无打卡返回 '-'。"""
    if not punches:
        return "-"
    return "\n".join(p.get("text", "") for p in punches)
