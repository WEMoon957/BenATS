"""候选人数据层：SQLite 存储、阶段状态机与 CRUD。

阶段从「发现」到「录用/淘汰」一条漏斗：
discovered → scored → greeting_pending → greeted → resume_received → screening → screened → interviewing → offered/rejected
（另有 skipped 表示预评分不合格被跳过）。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from ..config import app_data_dir
from ..db.backend import Database, SqliteDatabase
from ..db.factory import build_database

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
    score_detail TEXT NOT NULL DEFAULT '',
    resume_file TEXT NOT NULL DEFAULT '',
    call_score TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (name, job_keyword)
);

CREATE INDEX IF NOT EXISTS idx_candidate_stage ON candidate(stage);
CREATE INDEX IF NOT EXISTS idx_candidate_job ON candidate(job_keyword);

CREATE TABLE IF NOT EXISTS job_rubric (
    job_keyword TEXT PRIMARY KEY,
    rubric TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_keyword TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'passive',
    state TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plan_state ON plan(state);
"""

# MySQL 版建表脚本：与 _SCHEMA 表/列一一对应，仅类型与方言差异不同。
# 索引列（stage/state/job_keyword）与 UNIQUE 列（name/job_keyword）必须是 VARCHAR，
# MySQL 不能对 TEXT 直接建索引/唯一键；TEXT 默认值写成 DEFAULT ('x')。
_SCHEMA_MYSQL = """
CREATE TABLE IF NOT EXISTS candidate (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    name VARCHAR(255) NOT NULL COMMENT '候选人姓名',
    job_keyword VARCHAR(255) NOT NULL DEFAULT '' COMMENT '岗位关键词',
    job_id TEXT NOT NULL DEFAULT ('') COMMENT '关联岗位任务 id',
    source TEXT NOT NULL DEFAULT ('boss') COMMENT '来源：boss/zhaopin',
    stage VARCHAR(32) NOT NULL DEFAULT ('discovered') COMMENT '跟进阶段',
    pre_score TEXT NOT NULL DEFAULT ('') COMMENT 'AI 预评分档位（S/A/B/C）',
    pre_score_reason TEXT NOT NULL DEFAULT ('') COMMENT '预评分理由',
    pre_scored_at TEXT COMMENT '预评分时间',
    score_detail TEXT NOT NULL DEFAULT ('') COMMENT '筛选评分详情（JSON）',
    resume_file TEXT NOT NULL DEFAULT ('') COMMENT '简历文件名',
    call_score TEXT NOT NULL DEFAULT ('') COMMENT '电话评分（JSON）',
    phone TEXT NOT NULL DEFAULT ('') COMMENT '手机号',
    note TEXT NOT NULL DEFAULT ('') COMMENT '备注',
    created_at TEXT NOT NULL COMMENT '创建时间',
    updated_at TEXT NOT NULL COMMENT '更新时间',
    UNIQUE KEY uq_candidate (name, job_keyword)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='候选人跟进';

CREATE INDEX idx_candidate_stage ON candidate(stage);
CREATE INDEX idx_candidate_job ON candidate(job_keyword);

CREATE TABLE IF NOT EXISTS job_rubric (
    job_keyword VARCHAR(255) NOT NULL PRIMARY KEY COMMENT '岗位关键词',
    rubric TEXT NOT NULL COMMENT '评分标准全文',
    updated_at TEXT NOT NULL COMMENT '更新时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='岗位评分标准';

CREATE TABLE IF NOT EXISTS plan (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    job_keyword TEXT NOT NULL COMMENT '岗位关键词',
    mode TEXT NOT NULL DEFAULT ('passive') COMMENT '作业模式',
    state VARCHAR(32) NOT NULL DEFAULT ('draft') COMMENT '作业状态',
    created_at TEXT NOT NULL COMMENT '创建时间',
    updated_at TEXT NOT NULL COMMENT '更新时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='招聘作业计划';

CREATE INDEX idx_plan_state ON plan(state);
"""


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RecruitmentStore:
    def __init__(self, db_path: Path | None = None, *, backend: Database | None = None) -> None:
        self.db_path = db_path or (app_data_dir() / DB_FILENAME)
        self._backend = backend or SqliteDatabase(self.db_path)
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self._backend.execute_script(_SCHEMA if self._backend.dialect != "mysql" else _SCHEMA_MYSQL)
        # 既有库补列：CREATE TABLE IF NOT EXISTS 不会改动已存在的表
        self._backend.add_column_if_missing(
            "candidate", "score_detail", "score_detail TEXT NOT NULL DEFAULT ''"
        )
        self._backend.add_column_if_missing(
            "candidate", "resume_file", "resume_file TEXT NOT NULL DEFAULT ''"
        )
        self._backend.add_column_if_missing(
            "candidate", "call_score", "call_score TEXT NOT NULL DEFAULT ''"
        )

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return self._backend.query(sql, params)

    def query_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            return self._backend.query_one(sql, params)

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            return self._backend.execute(sql, params)

    def get_rubric(self, job_keyword: str) -> str:
        row = self.query_one("SELECT rubric FROM job_rubric WHERE job_keyword = ?", (job_keyword,))
        return str(row["rubric"]) if row else ""

    def set_rubric(self, job_keyword: str, rubric: str) -> None:
        self.execute(
            "INSERT INTO job_rubric (job_keyword, rubric, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(job_keyword) DO UPDATE SET rubric = excluded.rubric, updated_at = excluded.updated_at",
            (job_keyword, rubric, _now()),
        )


_store: RecruitmentStore | None = None
_store_lock = threading.Lock()


def get_store() -> RecruitmentStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = RecruitmentStore(backend=build_database(app_data_dir() / DB_FILENAME))
                _store.initialize()
    return _store
