"""全局会话：整站共用的账号登录态。

会话为进程内内存存储（token → account_id）。账号体系沿用考勤模块的 account 表
（admin/hr/supervisor/viewer），登录签发、登出吊销、请求解析都走这里。
"""

from __future__ import annotations

import secrets
import threading

_sessions: dict[str, int] = {}
_sessions_lock = threading.Lock()


def create_session(account_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with _sessions_lock:
        _sessions[token] = account_id
    return token


def account_id_for(token: str) -> int | None:
    with _sessions_lock:
        return _sessions.get(token)


def revoke_session(token: str) -> None:
    with _sessions_lock:
        _sessions.pop(token, None)
