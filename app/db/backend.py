"""数据库后端抽象：把考勤 / 候选人存储层与具体数据库解耦。

后端只负责连接、执行与事务；JSON 字段反序列化、布尔转换等业务映射仍留在各 store。
当前提供 SQLite 实现（保持单机行为不变），MySQL 实现后续加入。
"""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Sequence


class BackendTransaction(ABC):
    """单连接事务句柄，在 `Database.transaction()` 上下文中使用。

    具体实现同时是上下文管理器：`__enter__` 开启连接，`__exit__` 提交或回滚并关闭。
    """

    @abstractmethod
    def execute(self, sql: str, params: Sequence = ()) -> int:
        """执行写语句，返回 lastrowid。"""

    @abstractmethod
    def query(self, sql: str, params: Sequence = ()) -> list[dict[str, Any]]:
        """执行查询，返回字典行列表。"""

    @abstractmethod
    def query_one(self, sql: str, params: Sequence = ()) -> dict[str, Any] | None:
        """执行查询，返回一行或 None。"""


class Database(ABC):
    # 后端方言标识："sqlite" 或 "mysql"，store 用它选择建表脚本
    dialect: str = ""

    @abstractmethod
    def execute_script(self, sql: str) -> None:
        """执行多语句建表脚本（幂等）。"""

    @abstractmethod
    def table_columns(self, table: str) -> set[str]:
        """返回某表当前列名集合，用于幂等补列。"""

    @abstractmethod
    def add_column_if_missing(self, table: str, column: str, definition: str) -> None:
        """列不存在时追加该列。"""

    @abstractmethod
    def query(self, sql: str, params: Sequence = ()) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def query_one(self, sql: str, params: Sequence = ()) -> dict[str, Any] | None:
        ...

    @abstractmethod
    def execute(self, sql: str, params: Sequence = ()) -> int:
        """执行写语句并提交，返回 lastrowid。"""

    @abstractmethod
    def executemany(self, sql: str, params_list: Sequence[Sequence]) -> None:
        """批量执行写语句并提交。"""

    @abstractmethod
    def transaction(self) -> BackendTransaction:
        """返回一个尚未开启的事务句柄（在 `with` 块内开启连接）。"""


class _SqliteTransaction(BackendTransaction):
    def __init__(self, database: "SqliteDatabase") -> None:
        self._database = database
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> "_SqliteTransaction":
        self._conn = self._database._connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._conn is None:
                return
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def execute(self, sql: str, params: Sequence = ()) -> int:
        cur = self._conn.execute(sql, params)
        return int(cur.lastrowid or 0)

    def query(self, sql: str, params: Sequence = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: Sequence = ()) -> dict[str, Any] | None:
        row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None


class SqliteDatabase(Database):
    """SQLite 后端：每次读写新建连接并在结束时关闭（与原单机实现一致）。"""

    dialect = "sqlite"

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def execute_script(self, sql: str) -> None:
        conn = self._connect()
        try:
            conn.executescript(sql)
            conn.commit()
        finally:
            conn.close()

    def table_columns(self, table: str) -> set[str]:
        conn = self._connect()
        try:
            return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        finally:
            conn.close()

    def add_column_if_missing(self, table: str, column: str, definition: str) -> None:
        if column in self.table_columns(table):
            return
        self.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")

    def query(self, sql: str, params: Sequence = ()) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def query_one(self, sql: str, params: Sequence = ()) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def execute(self, sql: str, params: Sequence = ()) -> int:
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

    def executemany(self, sql: str, params_list: Sequence[Sequence]) -> None:
        conn = self._connect()
        try:
            conn.executemany(sql, params_list)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def transaction(self) -> BackendTransaction:
        return _SqliteTransaction(self)
