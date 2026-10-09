"""SQLite → MySQL 数据迁移。

读取 SQLite 各表全部行（保留原始 TEXT，不经过 store 的 JSON 反序列化），
只写入目标 MySQL 建表脚本里存在的列——源库里按约定已废弃的列（如支付字段）随迁移丢弃。
在单个 MySQL 连接里关闭外键检查后逐表写入，显式保留 id 以便外键关系不变。
"""

from __future__ import annotations

from pathlib import Path

import pymysql

from .backend import SqliteDatabase
from .json_backend import FileJsonBackend, MySqlJsonBackend

ATTENDANCE_TABLES = [
    "account",
    "employee_tag",
    "attendance_policy",
    "employee",
    "employee_tags",
    "import_batch",
    "raw_punch_day",
    "attendance_result",
    "cross_day_suspicion",
    "app_config",
]

RECRUITMENT_TABLES = ["candidate", "job_rubric", "plan"]

# 文件后端的 JsonStore 种类：kind、子目录、元数据文件名
_JSON_KINDS = [
    ("job", "jobs", "job.json"),
    ("call", "calls", "record.json"),
    ("outreach", "outreaches", "outreach.json"),
]


def read_rows(sqlite_path: Path, tables: list[str]) -> dict[str, list[dict]]:
    """读取 SQLite 各表全部行，返回 {表名: 行列表}。"""
    source = SqliteDatabase(sqlite_path)
    return {table: source.query(f"SELECT * FROM {table}") for table in tables}


def table_columns(conn, table: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COLUMN_NAME AS name FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (table,),
        )
        return {row["name"] for row in cur.fetchall()}


def clear_tables(conn, tables: list[str]) -> None:
    """清空目标表（关闭外键检查），避免与源数据主键冲突。"""
    with conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in tables:
            cur.execute(f"DELETE FROM {table}")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")


def write_rows(conn, rows_by_table: dict[str, list[dict]], target_columns: dict[str, set[str]]) -> int:
    """在单个 MySQL 连接里写入全部行（关闭外键检查），返回写入行数。"""
    count = 0
    with conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table, rows in rows_by_table.items():
            keep = target_columns.get(table, set())
            for row in rows:
                cols = [col for col in row if col in keep]
                placeholders = ", ".join(["%s"] * len(cols))
                quoted = ", ".join(f"`{col}`" for col in cols)
                cur.execute(
                    f"INSERT INTO {table} ({quoted}) VALUES ({placeholders})",
                    tuple(row[col] for col in cols),
                )
                count += 1
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    return count


def migrate(conn, sqlite_path: Path, tables: list[str]) -> int:
    clear_tables(conn, tables)
    target_columns = {table: table_columns(conn, table) for table in tables}
    rows_by_table = read_rows(sqlite_path, tables)
    expected = sum(len(rows) for rows in rows_by_table.values())
    written = write_rows(conn, rows_by_table, target_columns)
    if written != expected:
        raise RuntimeError(f"写入行数 {written} 与读取行数 {expected} 不一致")
    return written


def migrate_json_records(data_root: Path, target: MySqlJsonBackend) -> int:
    """把文件后端的 job/call/outreach 元数据迁到 MySQL json_records。"""
    total = 0
    for kind, subdir, metadata_name in _JSON_KINDS:
        source = FileJsonBackend(data_root / subdir, metadata_name, f"{kind}-")
        for archived in (False, True):
            for record in source.list(kind, archived=archived, limit=None, offset=0):
                target.save(kind, record)
                total += 1
    return total


def connect(host: str, port: int, user: str, password: str, database: str):
    return pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
