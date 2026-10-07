"""考勤管理数据层：SQLite 连接、schema 初始化、密码哈希与各表 CRUD。

设计约定：
- 数据库文件位于数据目录下的 attendance.db（与 settings.json 同级）。
- 单机单进程：每次读写新建连接并在结束时关闭，全部读写经 _lock 串行化。
- 布尔值存 INTEGER(0/1)，JSON 字段存 TEXT，日期存 ISO 字符串，时间存 HH:MM。
- 天数、扣款、覆盖天数等小数字段存 REAL，计算与比较统一 round 到 2 位小数。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
from pathlib import Path
from typing import Any

from ..config import app_data_dir

DB_FILENAME = "attendance.db"

ROLE_LABELS = {
    "admin": "系统管理员",
    "hr": "HR",
    "supervisor": "部门主管",
    "viewer": "只读",
}

# 有写权限的角色（admin / hr）；supervisor、viewer 只读。
WRITE_ROLES = {"admin", "hr"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer',
    department TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS employee_tag (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL DEFAULT '#64748B',
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS attendance_policy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'standard',
    start_time TEXT,
    end_time TEXT,
    grace_minutes INTEGER NOT NULL DEFAULT 0,
    cross_day_cutoff_minutes INTEGER NOT NULL DEFAULT 180,
    description TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS employee (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_no TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]',
    department TEXT NOT NULL DEFAULT '',
    position TEXT NOT NULL DEFAULT '',
    join_date TEXT,
    employment_status TEXT NOT NULL DEFAULT 'regular',
    active INTEGER NOT NULL DEFAULT 1,
    attendance_policy_id INTEGER REFERENCES attendance_policy(id) ON DELETE SET NULL,
    expected_days_override REAL,
    phone TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS employee_tags (
    employee_id INTEGER NOT NULL REFERENCES employee(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES employee_tag(id) ON DELETE CASCADE,
    PRIMARY KEY (employee_id, tag_id)
);

CREATE TABLE IF NOT EXISTS import_batch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_filename TEXT NOT NULL,
    source_file_path TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    default_expected_days REAL NOT NULL DEFAULT 25,
    status TEXT NOT NULL DEFAULT 'pending',
    total_rows INTEGER NOT NULL DEFAULT 0,
    matched_rows INTEGER NOT NULL DEFAULT 0,
    unmatched_rows INTEGER NOT NULL DEFAULT 0,
    suspicion_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT NOT NULL DEFAULT '',
    uploaded_by_id INTEGER REFERENCES account(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS raw_punch_day (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES import_batch(id) ON DELETE CASCADE,
    employee_id INTEGER REFERENCES employee(id) ON DELETE SET NULL,
    source_row INTEGER NOT NULL,
    employee_no TEXT NOT NULL DEFAULT '',
    source_name TEXT NOT NULL,
    organization TEXT NOT NULL DEFAULT '',
    attendance_rule TEXT NOT NULL DEFAULT '',
    work_date TEXT NOT NULL,
    raw_value TEXT NOT NULL DEFAULT '',
    punches TEXT NOT NULL DEFAULT '[]',
    has_punch INTEGER NOT NULL DEFAULT 0,
    effective_has_punch INTEGER NOT NULL DEFAULT 0,
    match_status TEXT NOT NULL DEFAULT 'unmatched',
    is_cross_day_suspicion INTEGER NOT NULL DEFAULT 0,
    UNIQUE (batch_id, source_row, work_date)
);

CREATE TABLE IF NOT EXISTS attendance_result (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES import_batch(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL REFERENCES employee(id) ON DELETE CASCADE,
    punch_days REAL NOT NULL DEFAULT 0,
    due_days REAL NOT NULL DEFAULT 0,
    rest_days REAL NOT NULL DEFAULT 0,
    leave_days REAL NOT NULL DEFAULT 0,
    overtime_days REAL NOT NULL DEFAULT 0,
    overtime_hours REAL NOT NULL DEFAULT 0,
    adjustment_days REAL NOT NULL DEFAULT 0,
    adjustment_hours REAL NOT NULL DEFAULT 0,
    actual_days REAL NOT NULL DEFAULT 0,
    late_count INTEGER NOT NULL DEFAULT 0,
    absence_count INTEGER NOT NULL DEFAULT 0,
    missing_punch_count INTEGER NOT NULL DEFAULT 0,
    deduction REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'review',
    note TEXT NOT NULL DEFAULT '',
    rule_trace TEXT NOT NULL DEFAULT '{}',
    reviewed_by_id INTEGER REFERENCES account(id) ON DELETE SET NULL,
    reviewed_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (batch_id, employee_id)
);

CREATE TABLE IF NOT EXISTS cross_day_suspicion (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL REFERENCES import_batch(id) ON DELETE CASCADE,
    raw_day_id INTEGER NOT NULL UNIQUE REFERENCES raw_punch_day(id) ON DELETE CASCADE,
    employee_id INTEGER REFERENCES employee(id) ON DELETE SET NULL,
    previous_date TEXT NOT NULL,
    work_date TEXT NOT NULL,
    punch_text TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    reviewed_by_id INTEGER REFERENCES account(id) ON DELETE SET NULL,
    reviewed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_day_batch ON raw_punch_day(batch_id);
CREATE INDEX IF NOT EXISTS idx_raw_day_employee ON raw_punch_day(employee_id);
CREATE INDEX IF NOT EXISTS idx_result_batch ON attendance_result(batch_id);
CREATE INDEX IF NOT EXISTS idx_result_employee ON attendance_result(employee_id);
CREATE INDEX IF NOT EXISTS idx_suspicion_batch ON cross_day_suspicion(batch_id);
CREATE INDEX IF NOT EXISTS idx_suspicion_employee ON cross_day_suspicion(employee_id);

CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# 需要从 JSON TEXT 反序列化的字段（表名 -> 字段集合）
_JSON_FIELDS = {
    "employee": {"aliases"},
    "raw_punch_day": {"punches"},
    "attendance_result": {"rule_trace"},
}

# 需要从 INTEGER 转 bool 的字段
_BOOL_FIELDS = {
    "raw_punch_day": {"has_punch", "effective_has_punch", "is_cross_day_suspicion"},
}


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 260_000
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
    ).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _algo, iterations, salt, digest = encoded.split("$")
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations)
        ).hex()
        return secrets.compare_digest(candidate, digest)
    except (ValueError, TypeError):
        return False


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def row_to_dict(table: str, row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for field in _JSON_FIELDS.get(table, set()):
        raw = data.get(field)
        if isinstance(raw, str):
            try:
                data[field] = json.loads(raw)
            except json.JSONDecodeError:
                data[field] = [] if field != "rule_trace" else {}
    for field in _BOOL_FIELDS.get(table, set()):
        data[field] = bool(data.get(field))
    return data


class AttendanceStore:
    """考勤数据库访问层。所有操作通过 _lock 串行化。"""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or (app_data_dir() / DB_FILENAME)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            # 既有库补列：CREATE TABLE IF NOT EXISTS 不会改动已存在的表
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(account)")}
            if "must_change_password" not in columns:
                conn.execute(
                    "ALTER TABLE account ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0"
                )
            conn.commit()
            self._ensure_default_admin(conn)
        finally:
            self._release(conn)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _release(self, conn: sqlite3.Connection) -> None:
        conn.close()

    def _ensure_default_admin(self, conn: sqlite3.Connection) -> None:
        count = conn.execute("SELECT COUNT(*) FROM account").fetchone()[0]
        if count == 0:
            conn.execute(
                "INSERT INTO account (username, password_hash, role, department, is_active, "
                "must_change_password, created_at) VALUES (?, ?, ?, ?, 1, 1, ?)",
                ("admin", hash_password("admin"), "admin", "", _now()),
            )
            conn.commit()

    # ---- 通用查询辅助 ----

    def query(self, sql: str, params: tuple = (), table: str = "") -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                self._release(conn)
        return [row_to_dict(table, row) for row in rows] if table else [dict(row) for row in rows]

    def query_one(self, sql: str, params: tuple = (), table: str = "") -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(sql, params).fetchone()
            finally:
                self._release(conn)
        if row is None:
            return None
        return row_to_dict(table, row) if table else dict(row)

    def execute(self, sql: str, params: tuple = ()) -> int:
        """执行写操作，返回 lastrowid。"""
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
                self._release(conn)

    def executemany(self, sql: str, params_list: list[tuple]) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executemany(sql, params_list)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                self._release(conn)

    def transaction(self) -> "_Transaction":
        """返回一个事务上下文，供多步写入原子提交。"""
        return _Transaction(self)

    def get_config(self, key: str, default: str = "") -> str:
        row = self.query_one("SELECT value FROM app_config WHERE key = ?", (key,))
        return str(row["value"]) if row else default

    def set_config(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO app_config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


class _Transaction:
    def __init__(self, store: AttendanceStore) -> None:
        self.store = store
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> "_Transaction":
        self.store._lock.acquire()
        self.conn = self.store._connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.conn is None:
                return
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            if self.conn is not None:
                self.store._release(self.conn)
            self.store._lock.release()

    def execute(self, sql: str, params: tuple = ()) -> int:
        cur = self.conn.execute(sql, params)
        return int(cur.lastrowid or 0)

    def query(self, sql: str, params: tuple = (), table: str = "") -> list[dict[str, Any]]:
        rows = self.conn.execute(sql, params).fetchall()
        return [row_to_dict(table, row) for row in rows] if table else [dict(row) for row in rows]

    def query_one(self, sql: str, params: tuple = (), table: str = "") -> dict[str, Any] | None:
        row = self.conn.execute(sql, params).fetchone()
        if row is None:
            return None
        return row_to_dict(table, row) if table else dict(row)


_store: AttendanceStore | None = None
_store_lock = threading.Lock()


def get_store() -> AttendanceStore:
    """返回进程级单例（惰性初始化 schema）。"""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = AttendanceStore()
                _store.initialize()
    return _store
