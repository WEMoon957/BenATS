"""SQLite 与 MySQL 建表脚本的列一致性校验：两套 schema 不得漂移。"""

import re

from app.attendance.db import _SCHEMA as ATT_SQLITE, _SCHEMA_MYSQL as ATT_MYSQL
from app.recruitment.db import _SCHEMA as REC_SQLITE, _SCHEMA_MYSQL as REC_MYSQL

_TYPES = r"(?:INT(?:EGER)?|BIGINT|VARCHAR|TEXT|DOUBLE|REAL)"
_COL_RE = re.compile(rf"^\s{{4}}([`\w]+)\s+{_TYPES}\b")


def _columns(schema: str) -> dict[str, list[str]]:
    """按 4 空格缩进的「列名 类型」行抽取每张表的列名。"""
    tables: dict[str, list[str]] = {}
    current: str | None = None
    for line in schema.splitlines():
        matched = re.match(r"CREATE TABLE IF NOT EXISTS (\w+)", line)
        if matched:
            current = matched.group(1)
            tables[current] = []
            continue
        if current is not None:
            column = _COL_RE.match(line)
            if column:
                tables[current].append(column.group(1).strip("`"))
    return {table: sorted(columns) for table, columns in tables.items()}


def test_attendance_schema_parity():
    assert _columns(ATT_SQLITE) == _columns(ATT_MYSQL)


def test_recruitment_schema_parity():
    assert _columns(REC_SQLITE) == _columns(REC_MYSQL)
