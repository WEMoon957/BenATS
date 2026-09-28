"""候选人数据层：SQLite 存储、阶段状态机与 CRUD。

阶段从「发现」到「录用/淘汰」一条漏斗：
discovered → scored → greeting_pending → greeted → resume_received → screening → screened → interviewing → offered/rejected
（另有 skipped 表示预评分不合格被跳过）。
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from ..config import app_data_dir

DB_FILENAME = "recruitment.db"

# 阶段常量与中文标签
STAGE_DISCOVERED = "discovered"
STAGE_SCORED = "scored"
STAGE_GREETING_PENDING = "greeting_pending"
STAGE_GREETED = "greeted"
STAGE_RESUME_RECEIVED = "resume_received"
STAGE_SCREENING = "screening"
STAGE_SCREENED = "screened"
STAGE_INTERVIEWING = "interviewing"
STAGE_OFFERED = "offered"
STAGE_REJECTED = "rejected"
STAGE_SKIPPED = "skipped"

STAGES = [
    STAGE_DISCOVERED,
    STAGE_SCORED,
    STAGE_GREETING_PENDING,
    STAGE_GREETED,
    STAGE_RESUME_RECEIVED,
    STAGE_SCREENING,
    STAGE_SCREENED,
    STAGE_INTERVIEWING,
    STAGE_OFFERED,
    STAGE_REJECTED,
    STAGE_SKIPPED,
]

STAGE_LABELS = {
    STAGE_DISCOVERED: "新发现",
    STAGE_SCORED: "已评分",
    STAGE_GREETING_PENDING: "待打招呼",
    STAGE_GREETED: "已打招呼",
    STAGE_RESUME_RECEIVED: "已收简历",
    STAGE_SCREENING: "筛选中",
    STAGE_SCREENED: "已筛完",
    STAGE_INTERVIEWING: "面试中",
    STAGE_OFFERED: "已录用",
    STAGE_REJECTED: "已淘汰",
    STAGE_SKIPPED: "已跳过",
}

# 预评分档位（与正式筛选结论一致）
PRE_SCORE_S = "S电话沟通"
PRE_SCORE_A = "A优先约面"
PRE_SCORE_B = "B电话确认"
PRE_SCORE_C = "C不推进"

PRE_SCORE_LABELS = {
    PRE_SCORE_S: "特优",
    PRE_SCORE_A: "优先约面",
    PRE_SCORE_B: "电话确认",
    PRE_SCORE_C: "不推进",
}

# 评分合格（进入待打招呼清单）的档位
GREETABLE_SCORES = {PRE_SCORE_S, PRE_SCORE_A, PRE_SCORE_B}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidate (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    job_keyword TEXT NOT NULL DEFAULT '',
    job_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'boss',
    stage TEXT NOT NULL DEFAULT 'discovered',
    pre_score TEXT NOT NULL DEFAULT '',
    pre_score_reason TEXT NOT NULL DEFAULT '',
    pre_scored_at TEXT,
    phone TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (name, job_keyword)
);

CREATE INDEX IF NOT EXISTS idx_candidate_stage ON candidate(stage);
CREATE INDEX IF NOT EXISTS idx_candidate_job ON candidate(job_keyword);
"""


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RecruitmentStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (app_data_dir() / DB_FILENAME)
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
        return [dict(row) for row in rows]

    def query_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(sql, params).fetchone()
            finally:
                conn.close()
        return dict(row) if row else None

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(sql, params)
                conn.commit()
                return int(cur.lastrowid or 0)
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()


_store: RecruitmentStore | None = None
_store_lock = threading.Lock()


def get_store() -> RecruitmentStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = RecruitmentStore()
                _store.initialize()
    return _store
