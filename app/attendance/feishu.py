"""飞书考勤打卡 API 客户端。

封装 tenant_access_token 获取与缓存、以及「获取打卡结果」接口（user_tasks/query）。
考勤机打卡、GPS/Wi-Fi 打卡等均通过飞书考勤系统回流，本客户端统一按工号拉取。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
USER_TASKS_URL = "https://open.feishu.cn/open-apis/attendance/v1/user_tasks/query"

CHINA_TZ = timezone(timedelta(hours=8))
# 每批最多查询的员工数（飞书接口限制 50）
BATCH_SIZE = 50
# token 提前多少秒视为过期，强制刷新
TOKEN_REFRESH_MARGIN = 120


class FeishuAttendanceError(Exception):
    def __init__(self, code, msg) -> None:
        super().__init__(f"飞书考勤错误（{code}）：{msg}")
        self.code = code
        self.msg = msg


class FeishuAttendanceClient:
    def __init__(self, app_id: str, app_secret: str) -> None:
        self.app_id = app_id.strip()
        self.app_secret = app_secret.strip()
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
                response = httpx.post(
                    TOKEN_URL,
                    json={"app_id": self.app_id, "app_secret": self.app_secret},
                    timeout=10,
                )
            except httpx.HTTPError as exc:
                raise FeishuAttendanceError("network", f"获取 tenant_access_token 失败：{exc}") from exc
            data = _json(response)
            if data.get("code") != 0:
                raise FeishuAttendanceError(data.get("code"), data.get("msg", "获取 token 失败"))
            self._token = data.get("tenant_access_token", "")
            self._token_expire_at = time.time() + int(data.get("expire", 7200))
            return self._token

    def query_user_tasks(self, user_ids: list[str], date_from: str, date_to: str) -> list[dict]:
        """按工号批量查询打卡结果，返回 user_task_results 列表（已合并分页）。"""
        results: list[dict] = []
        for start in range(0, len(user_ids), BATCH_SIZE):
            batch = user_ids[start:start + BATCH_SIZE]
            token = self._get_token()
            try:
                response = httpx.post(
                    USER_TASKS_URL,
                    params={
                        "employee_type": "employee_no",
                        "ignore_invalid_users": "true",
                        "include_terminated_user": "false",
                    },
                    json={
                        "user_ids": batch,
                        "check_date_from": int(date_from),
                        "check_date_to": int(date_to),
                        "need_overtime_result": False,
                    },
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=30,
                )
            except httpx.HTTPError as exc:
                raise FeishuAttendanceError("network", f"查询打卡结果失败：{exc}") from exc
            data = _json(response)
            if data.get("code") != 0:
                raise FeishuAttendanceError(data.get("code"), data.get("msg", "查询打卡结果失败"))
            chunk = data.get("data", {}).get("user_task_results") or []
            results.extend(chunk)
        return results


def _json(response: httpx.Response) -> dict:
    try:
        return response.json()
    except ValueError as exc:
        raise FeishuAttendanceError("parse", f"飞书响应不是合法 JSON（HTTP {response.status_code}）") from exc


def timestamp_to_punch(ts: int, work_day: int) -> dict:
    """把打卡秒时间戳转成 punches 元素，work_day 为飞书返回的工作日（yyyyMMdd）。

    跨天打卡（时间戳落在次日）标记 next_day 并增加 1440 分钟，复用现有跨日检测口径。
    """
    dt = datetime.fromtimestamp(int(ts), tz=CHINA_TZ)
    clock_minutes = dt.hour * 60 + dt.minute
    next_day = dt.strftime("%Y%m%d") != str(work_day)
    minutes = clock_minutes + (1440 if next_day else 0)
    text = ("次日" if next_day else "") + f"{dt.hour:02d}:{dt.minute:02d}"
    return {"text": text, "minutes": minutes, "clock_minutes": clock_minutes, "next_day": next_day, "valid": True}
