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
import threading
from pathlib import Path
from typing import Any

from ..config import app_data_dir
from ..db.backend import Database, SqliteDatabase
from ..db.factory import build_database

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
    feishu_user_id TEXT NOT NULL DEFAULT '',
    feishu_open_id TEXT NOT NULL DEFAULT '',
    leave_date TEXT,
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

CREATE TABLE IF NOT EXISTS employee_lifecycle_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employee(id) ON DELETE CASCADE,
    employee_no TEXT NOT NULL DEFAULT '',
    employee_name TEXT NOT NULL DEFAULT '',
    department TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'feishu',
    reason TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (employee_id, event_type, effective_date, source)
);

CREATE TABLE IF NOT EXISTS resignation_request (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL UNIQUE,
    employee_id INTEGER NOT NULL REFERENCES employee(id) ON DELETE CASCADE,
    employee_no TEXT NOT NULL DEFAULT '',
    employee_name TEXT NOT NULL DEFAULT '',
    department TEXT NOT NULL DEFAULT '',
    position TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'sent',
    last_working_day TEXT,
    reason_category TEXT NOT NULL DEFAULT '',
    reason_detail TEXT NOT NULL DEFAULT '',
    handover_to TEXT NOT NULL DEFAULT '',
    handover_note TEXT NOT NULL DEFAULT '',
    contact_after TEXT NOT NULL DEFAULT '',
    submitted_at TEXT,
    confirmed_by_id INTEGER REFERENCES account(id) ON DELETE SET NULL,
    confirmed_at TEXT,
    deliver_status TEXT NOT NULL DEFAULT '',
    deliver_error TEXT NOT NULL DEFAULT '',
    created_by_id INTEGER REFERENCES account(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lifecycle_employee ON employee_lifecycle_event(employee_id);
CREATE INDEX IF NOT EXISTS idx_lifecycle_date ON employee_lifecycle_event(effective_date);
CREATE INDEX IF NOT EXISTS idx_resignation_status ON resignation_request(status);
"""

# MySQL 版建表脚本：与 _SCHEMA 表/列一一对应，仅类型与方言差异不同。
# 注意：外键列类型必须与被引用列（BIGINT）一致；UNIQUE/PK 的 TEXT 列改用 VARCHAR；
# TEXT 默认值在 MySQL 8 需写成表达式 DEFAULT ('x')；CREATE INDEX 无 IF NOT EXISTS，
# 幂等由后端 execute_script 容错重复索引实现。
_SCHEMA_MYSQL = """
CREATE TABLE IF NOT EXISTS account (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    username VARCHAR(255) NOT NULL UNIQUE COMMENT '登录用户名',
    password_hash TEXT NOT NULL COMMENT '密码哈希（PBKDF2）',
    role VARCHAR(64) NOT NULL DEFAULT 'viewer' COMMENT '角色：admin/hr/supervisor/viewer',
    department VARCHAR(255) NOT NULL DEFAULT '' COMMENT '所属部门',
    is_active INT NOT NULL DEFAULT 1 COMMENT '是否启用（1 启用 / 0 停用）',
    must_change_password INT NOT NULL DEFAULT 0 COMMENT '是否强制改密（1 是 / 0 否）',
    created_at TEXT NOT NULL COMMENT '创建时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='登录账号与角色';

CREATE TABLE IF NOT EXISTS employee_tag (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    name VARCHAR(255) NOT NULL UNIQUE COMMENT '标签名',
    color TEXT NOT NULL DEFAULT ('#64748B') COMMENT '标签颜色',
    description TEXT NOT NULL DEFAULT ('') COMMENT '标签说明'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='员工标签';

CREATE TABLE IF NOT EXISTS attendance_policy (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    code VARCHAR(255) NOT NULL UNIQUE COMMENT '策略编码',
    name TEXT NOT NULL COMMENT '策略名称',
    mode TEXT NOT NULL DEFAULT ('standard') COMMENT '模式：standard/flexible/exempt/part_time/shift',
    start_time TEXT COMMENT '上班时间',
    end_time TEXT COMMENT '下班时间',
    grace_minutes INT NOT NULL DEFAULT 0 COMMENT '宽限分钟数',
    cross_day_cutoff_minutes INT NOT NULL DEFAULT 180 COMMENT '跨日切分阈值（分钟）',
    description TEXT NOT NULL DEFAULT ('') COMMENT '策略说明',
    active INT NOT NULL DEFAULT 1 COMMENT '是否启用'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='考勤策略';

CREATE TABLE IF NOT EXISTS employee (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    employee_no VARCHAR(255) NOT NULL UNIQUE COMMENT '工号（或飞书用户 ID）',
    name TEXT NOT NULL COMMENT '姓名',
    aliases TEXT NOT NULL DEFAULT ('[]') COMMENT '别名（JSON 数组）',
    department TEXT NOT NULL DEFAULT ('') COMMENT '部门',
    position TEXT NOT NULL DEFAULT ('') COMMENT '岗位',
    join_date TEXT COMMENT '入职日期',
    employment_status TEXT NOT NULL DEFAULT ('regular') COMMENT '用工状态：probation/regular/founder/part_time/left',
    active INT NOT NULL DEFAULT 1 COMMENT '是否在职（1 在职 / 0 离职）',
    attendance_policy_id BIGINT NULL COMMENT '考勤策略 id',
    expected_days_override DOUBLE COMMENT '应出勤天数覆盖值',
    phone TEXT NOT NULL DEFAULT ('') COMMENT '手机号',
    feishu_user_id TEXT NOT NULL DEFAULT ('') COMMENT '飞书用户 ID',
    feishu_open_id TEXT NOT NULL DEFAULT ('') COMMENT '飞书 open_id',
    leave_date TEXT COMMENT '离职生效日期',
    created_at TEXT NOT NULL COMMENT '创建时间',
    updated_at TEXT NOT NULL COMMENT '更新时间',
    CONSTRAINT fk_employee_policy FOREIGN KEY (attendance_policy_id) REFERENCES attendance_policy(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='员工档案';

CREATE TABLE IF NOT EXISTS employee_tags (
    employee_id BIGINT NOT NULL COMMENT '员工 id',
    tag_id BIGINT NOT NULL COMMENT '标签 id',
    PRIMARY KEY (employee_id, tag_id),
    CONSTRAINT fk_employee_tags_employee FOREIGN KEY (employee_id) REFERENCES employee(id) ON DELETE CASCADE,
    CONSTRAINT fk_employee_tags_tag FOREIGN KEY (tag_id) REFERENCES employee_tag(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='员工-标签关联';

CREATE TABLE IF NOT EXISTS import_batch (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    original_filename TEXT NOT NULL COMMENT '上传的原始文件名',
    source_file_path TEXT NOT NULL COMMENT '源文件路径',
    file_sha256 TEXT NOT NULL COMMENT '文件 SHA256',
    year INT NOT NULL COMMENT '考勤年',
    month INT NOT NULL COMMENT '考勤月',
    default_expected_days DOUBLE NOT NULL DEFAULT 25 COMMENT '默认应出勤天数',
    status TEXT NOT NULL DEFAULT ('pending') COMMENT '批次状态',
    total_rows INT NOT NULL DEFAULT 0 COMMENT '总行数',
    matched_rows INT NOT NULL DEFAULT 0 COMMENT '匹配行数',
    unmatched_rows INT NOT NULL DEFAULT 0 COMMENT '未匹配行数',
    suspicion_count INT NOT NULL DEFAULT 0 COMMENT '疑似数',
    error_message TEXT NOT NULL DEFAULT ('') COMMENT '错误信息',
    uploaded_by_id BIGINT NULL COMMENT '上传人账号 id',
    created_at TEXT NOT NULL COMMENT '创建时间',
    completed_at TEXT COMMENT '完成时间',
    CONSTRAINT fk_import_batch_uploader FOREIGN KEY (uploaded_by_id) REFERENCES account(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='考勤导入批次';

CREATE TABLE IF NOT EXISTS raw_punch_day (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    batch_id BIGINT NOT NULL COMMENT '所属导入批次 id',
    employee_id BIGINT NULL COMMENT '匹配到的员工 id',
    source_row INT NOT NULL COMMENT '来源行号',
    employee_no TEXT NOT NULL DEFAULT ('') COMMENT '来源工号',
    source_name TEXT NOT NULL COMMENT '来源姓名',
    organization TEXT NOT NULL DEFAULT ('') COMMENT '来源组织',
    attendance_rule TEXT NOT NULL DEFAULT ('') COMMENT '来源考勤规则',
    work_date VARCHAR(16) NOT NULL COMMENT '日期',
    raw_value TEXT NOT NULL DEFAULT ('') COMMENT '原始打卡值',
    punches TEXT NOT NULL DEFAULT ('[]') COMMENT '打卡明细（JSON）',
    has_punch INT NOT NULL DEFAULT 0 COMMENT '是否有打卡',
    effective_has_punch INT NOT NULL DEFAULT 0 COMMENT '核算口径是否算打卡',
    match_status TEXT NOT NULL DEFAULT ('unmatched') COMMENT '匹配状态',
    is_cross_day_suspicion INT NOT NULL DEFAULT 0 COMMENT '是否跨日疑似',
    UNIQUE KEY uq_raw_day (batch_id, source_row, work_date),
    CONSTRAINT fk_raw_batch FOREIGN KEY (batch_id) REFERENCES import_batch(id) ON DELETE CASCADE,
    CONSTRAINT fk_raw_employee FOREIGN KEY (employee_id) REFERENCES employee(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='原始打卡日记录';

CREATE TABLE IF NOT EXISTS attendance_result (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    batch_id BIGINT NOT NULL COMMENT '所属导入批次 id',
    employee_id BIGINT NOT NULL COMMENT '员工 id',
    punch_days DOUBLE NOT NULL DEFAULT 0 COMMENT '出勤天数',
    due_days DOUBLE NOT NULL DEFAULT 0 COMMENT '应出勤天数',
    rest_days DOUBLE NOT NULL DEFAULT 0 COMMENT '休息天数',
    leave_days DOUBLE NOT NULL DEFAULT 0 COMMENT '请假天数',
    overtime_days DOUBLE NOT NULL DEFAULT 0 COMMENT '加班天数',
    overtime_hours DOUBLE NOT NULL DEFAULT 0 COMMENT '加班小时',
    adjustment_days DOUBLE NOT NULL DEFAULT 0 COMMENT '人工调整天数',
    adjustment_hours DOUBLE NOT NULL DEFAULT 0 COMMENT '人工调整小时',
    actual_days DOUBLE NOT NULL DEFAULT 0 COMMENT '实际出勤天数',
    late_count INT NOT NULL DEFAULT 0 COMMENT '迟到次数',
    absence_count INT NOT NULL DEFAULT 0 COMMENT '缺勤次数',
    missing_punch_count INT NOT NULL DEFAULT 0 COMMENT '缺卡次数',
    deduction DOUBLE NOT NULL DEFAULT 0 COMMENT '扣款/扣分',
    status TEXT NOT NULL DEFAULT ('review') COMMENT '状态：review/confirmed',
    note TEXT NOT NULL DEFAULT ('') COMMENT '备注',
    rule_trace TEXT NOT NULL DEFAULT ('{}') COMMENT '规则计算轨迹（JSON）',
    reviewed_by_id BIGINT NULL COMMENT '复核人账号 id',
    reviewed_at TEXT COMMENT '复核时间',
    updated_at TEXT NOT NULL COMMENT '更新时间',
    UNIQUE KEY uq_result_employee (batch_id, employee_id),
    CONSTRAINT fk_result_batch FOREIGN KEY (batch_id) REFERENCES import_batch(id) ON DELETE CASCADE,
    CONSTRAINT fk_result_employee FOREIGN KEY (employee_id) REFERENCES employee(id) ON DELETE CASCADE,
    CONSTRAINT fk_result_reviewer FOREIGN KEY (reviewed_by_id) REFERENCES account(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='考勤核算结果';

CREATE TABLE IF NOT EXISTS cross_day_suspicion (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    batch_id BIGINT NOT NULL COMMENT '所属导入批次 id',
    raw_day_id BIGINT NOT NULL UNIQUE COMMENT '原始打卡记录 id',
    employee_id BIGINT NULL COMMENT '员工 id',
    previous_date TEXT NOT NULL COMMENT '前一日日期',
    work_date TEXT NOT NULL COMMENT '当日日期',
    punch_text TEXT NOT NULL COMMENT '打卡文本',
    reason TEXT NOT NULL COMMENT '疑似原因',
    status TEXT NOT NULL DEFAULT ('pending') COMMENT '状态：pending/resolved',
    reviewed_by_id BIGINT NULL COMMENT '复核人账号 id',
    reviewed_at TEXT COMMENT '复核时间',
    created_at TEXT NOT NULL COMMENT '创建时间',
    CONSTRAINT fk_suspicion_batch FOREIGN KEY (batch_id) REFERENCES import_batch(id) ON DELETE CASCADE,
    CONSTRAINT fk_suspicion_raw FOREIGN KEY (raw_day_id) REFERENCES raw_punch_day(id) ON DELETE CASCADE,
    CONSTRAINT fk_suspicion_employee FOREIGN KEY (employee_id) REFERENCES employee(id) ON DELETE SET NULL,
    CONSTRAINT fk_suspicion_reviewer FOREIGN KEY (reviewed_by_id) REFERENCES account(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='跨日打卡疑似（待人工审核）';

CREATE TABLE IF NOT EXISTS app_config (
    `key` VARCHAR(255) NOT NULL PRIMARY KEY COMMENT '配置键',
    `value` TEXT NOT NULL COMMENT '配置值'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='应用配置键值（飞书凭证等）';

CREATE TABLE IF NOT EXISTS employee_lifecycle_event (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    employee_id BIGINT NOT NULL COMMENT '员工 id',
    employee_no TEXT NOT NULL DEFAULT ('') COMMENT '工号快照',
    employee_name TEXT NOT NULL DEFAULT ('') COMMENT '姓名快照',
    department TEXT NOT NULL DEFAULT ('') COMMENT '部门快照',
    event_type VARCHAR(32) NOT NULL COMMENT '事件类型：onboard/offboard',
    effective_date VARCHAR(16) NOT NULL COMMENT '生效日期',
    source VARCHAR(32) NOT NULL DEFAULT ('feishu') COMMENT '来源：feishu/resignation/manual',
    reason TEXT NOT NULL DEFAULT ('') COMMENT '原因',
    detail TEXT NOT NULL DEFAULT ('{}') COMMENT '明细（JSON）',
    created_at TEXT NOT NULL COMMENT '创建时间',
    UNIQUE KEY uq_lifecycle (employee_id, event_type, effective_date, source),
    CONSTRAINT fk_lifecycle_employee FOREIGN KEY (employee_id) REFERENCES employee(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='员工入离职事件流';

CREATE TABLE IF NOT EXISTS resignation_request (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
    token VARCHAR(64) NOT NULL UNIQUE COMMENT '表单访问令牌',
    employee_id BIGINT NOT NULL COMMENT '员工 id',
    employee_no TEXT NOT NULL DEFAULT ('') COMMENT '工号快照',
    employee_name TEXT NOT NULL DEFAULT ('') COMMENT '姓名快照',
    department TEXT NOT NULL DEFAULT ('') COMMENT '部门快照',
    position TEXT NOT NULL DEFAULT ('') COMMENT '岗位快照',
    status VARCHAR(32) NOT NULL DEFAULT ('sent') COMMENT '状态：sent/submitted/confirmed/rejected/completed',
    last_working_day VARCHAR(16) COMMENT '最后工作日',
    reason_category TEXT NOT NULL DEFAULT ('') COMMENT '离职原因分类',
    reason_detail TEXT NOT NULL DEFAULT ('') COMMENT '离职原因说明',
    handover_to TEXT NOT NULL DEFAULT ('') COMMENT '交接人',
    handover_note TEXT NOT NULL DEFAULT ('') COMMENT '交接说明',
    contact_after TEXT NOT NULL DEFAULT ('') COMMENT '离职后联系方式',
    submitted_at TEXT COMMENT '提交时间',
    confirmed_by_id BIGINT NULL COMMENT '确认人账号 id',
    confirmed_at TEXT COMMENT '确认时间',
    deliver_status VARCHAR(32) NOT NULL DEFAULT ('') COMMENT '下发状态',
    deliver_error TEXT NOT NULL DEFAULT ('') COMMENT '下发错误',
    created_by_id BIGINT NULL COMMENT '发起人账号 id',
    created_at TEXT NOT NULL COMMENT '创建时间',
    updated_at TEXT NOT NULL COMMENT '更新时间',
    CONSTRAINT fk_resignation_employee FOREIGN KEY (employee_id) REFERENCES employee(id) ON DELETE CASCADE,
    CONSTRAINT fk_resignation_confirmer FOREIGN KEY (confirmed_by_id) REFERENCES account(id) ON DELETE SET NULL,
    CONSTRAINT fk_resignation_creator FOREIGN KEY (created_by_id) REFERENCES account(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='离职申请表';

CREATE INDEX idx_lifecycle_employee ON employee_lifecycle_event(employee_id);
CREATE INDEX idx_lifecycle_date ON employee_lifecycle_event(effective_date);
CREATE INDEX idx_resignation_status ON resignation_request(status);

CREATE INDEX idx_raw_day_batch ON raw_punch_day(batch_id);
CREATE INDEX idx_raw_day_employee ON raw_punch_day(employee_id);
CREATE INDEX idx_result_batch ON attendance_result(batch_id);
CREATE INDEX idx_result_employee ON attendance_result(employee_id);
CREATE INDEX idx_suspicion_batch ON cross_day_suspicion(batch_id);
CREATE INDEX idx_suspicion_employee ON cross_day_suspicion(employee_id);
"""

# 需要从 JSON TEXT 反序列化的字段（表名 -> 字段集合）
_JSON_FIELDS = {
    "employee": {"aliases"},
    "raw_punch_day": {"punches"},
    "attendance_result": {"rule_trace"},
    "employee_lifecycle_event": {"detail"},
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


def row_to_dict(table: str, row: dict[str, Any]) -> dict[str, Any]:
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
    """考勤数据库访问层。全部读写经 _lock 串行化，具体数据库由 backend 决定。"""

    def __init__(self, db_path: Path | None = None, *, backend: Database | None = None) -> None:
        self.db_path = db_path or (app_data_dir() / DB_FILENAME)
        self._backend = backend or SqliteDatabase(self.db_path)
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self._backend.execute_script(_SCHEMA if self._backend.dialect != "mysql" else _SCHEMA_MYSQL)
        # 既有库补列：CREATE TABLE IF NOT EXISTS 不会改动已存在的表
        self._backend.add_column_if_missing(
            "account", "must_change_password", "must_change_password INTEGER NOT NULL DEFAULT 0"
        )
        self._backend.add_column_if_missing(
            "employee", "feishu_user_id", "feishu_user_id TEXT NOT NULL DEFAULT ''"
        )
        self._backend.add_column_if_missing(
            "employee", "feishu_open_id", "feishu_open_id TEXT NOT NULL DEFAULT ''"
        )
        self._backend.add_column_if_missing("employee", "leave_date", "leave_date TEXT")
        self._ensure_default_admin()

    def _ensure_default_admin(self) -> None:
        row = self._backend.query_one("SELECT COUNT(*) AS c FROM account")
        if not row or row["c"] == 0:
            self._backend.execute(
                "INSERT INTO account (username, password_hash, role, department, is_active, "
                "must_change_password, created_at) VALUES (?, ?, ?, ?, 1, 1, ?)",
                ("admin", hash_password("admin"), "admin", "", _now()),
            )

    # ---- 通用查询辅助 ----

    def query(self, sql: str, params: tuple = (), table: str = "") -> list[dict[str, Any]]:
        with self._lock:
            rows = self._backend.query(sql, params)
        return [row_to_dict(table, row) for row in rows] if table else rows

    def query_one(self, sql: str, params: tuple = (), table: str = "") -> dict[str, Any] | None:
        with self._lock:
            row = self._backend.query_one(sql, params)
        if row is None:
            return None
        return row_to_dict(table, row) if table else row

    def execute(self, sql: str, params: tuple = ()) -> int:
        """执行写操作，返回 lastrowid。"""
        with self._lock:
            return self._backend.execute(sql, params)

    def executemany(self, sql: str, params_list: list[tuple]) -> None:
        with self._lock:
            self._backend.executemany(sql, params_list)

    def transaction(self) -> "_Transaction":
        """返回一个事务上下文，供多步写入原子提交。"""
        return _Transaction(self)

    def _cfg_key(self) -> str:
        # MySQL 里 KEY 是保留字，列名需反引号；SQLite 保持 key
        return "`key`" if self._backend.dialect == "mysql" else "key"

    def get_config(self, key: str, default: str = "") -> str:
        cfg_key = self._cfg_key()
        row = self.query_one(f"SELECT value FROM app_config WHERE {cfg_key} = ?", (key,))
        return str(row["value"]) if row else default

    def set_config(self, key: str, value: str) -> None:
        cfg_key = self._cfg_key()
        self.execute(
            f"INSERT INTO app_config ({cfg_key}, value) VALUES (?, ?) "
            f"ON CONFLICT({cfg_key}) DO UPDATE SET value = excluded.value",
            (key, value),
        )


class _Transaction:
    def __init__(self, store: AttendanceStore) -> None:
        self._store = store
        self._tx = None

    def __enter__(self) -> "_Transaction":
        self._store._lock.acquire()
        self._tx = self._store._backend.transaction()
        self._tx.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self._tx.__exit__(exc_type, exc, tb)
        finally:
            self._store._lock.release()

    def execute(self, sql: str, params: tuple = ()) -> int:
        return self._tx.execute(sql, params)

    def query(self, sql: str, params: tuple = (), table: str = "") -> list[dict[str, Any]]:
        rows = self._tx.query(sql, params)
        return [row_to_dict(table, row) for row in rows] if table else rows

    def query_one(self, sql: str, params: tuple = (), table: str = "") -> dict[str, Any] | None:
        row = self._tx.query_one(sql, params)
        if row is None:
            return None
        return row_to_dict(table, row) if table else row


_store: AttendanceStore | None = None
_store_lock = threading.Lock()


def get_store() -> AttendanceStore:
    """返回进程级单例（惰性初始化 schema）。"""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = AttendanceStore(backend=build_database(app_data_dir() / DB_FILENAME))
                _store.initialize()
    return _store
