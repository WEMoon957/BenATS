"""按环境变量选择数据库后端。"""

from __future__ import annotations

import os
from pathlib import Path

from .backend import Database, SqliteDatabase
from .json_backend import JsonMetadataBackend, MySqlJsonBackend
from .mysql_backend import MySqlDatabase


def build_database(db_path: Path) -> Database:
    """`TALENT_HUB_DB=mysql` 或设置了 `MYSQL_DATABASE` 时用 MySQL，否则用 SQLite。"""
    if os.getenv("TALENT_HUB_DB") == "mysql" or os.getenv("MYSQL_DATABASE"):
        return MySqlDatabase(
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "benats"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DATABASE", "benats"),
        )
    return SqliteDatabase(db_path)


def build_json_backend(db_path: Path) -> JsonMetadataBackend | None:
    """MySQL 时返回共享的 MySQL JSON 后端，否则返回 None（各 store 用文件后端）。"""
    database = build_database(db_path)
    if isinstance(database, MySqlDatabase):
        return MySqlJsonBackend(database)
    return None
