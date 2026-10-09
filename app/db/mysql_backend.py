"""MySQL 后端：连接、执行、事务，并把 SQLite 方言的 SQL 翻译成 MySQL。

建表脚本由各 store 提供 MySQL 版（store 按 `dialect` 选择），所以 `execute_script`
不做方言翻译；DML（query/execute/executemany/transaction）与单列定义（add_column_if_missing）
则走 `translate` / `translate_column_definition` 翻译。
"""

from __future__ import annotations

import re
from typing import Any, Sequence

import pymysql

from .backend import BackendTransaction, Database
from .sql_translate import translate

# 幂等建库时忽略的 MySQL 错误码
_TABLE_EXISTS = 1050
_DUPLICATE_COLUMN = 1060
_DUPLICATE_KEY_NAME = 1061


def translate_column_definition(definition: str) -> str:
    """把 SQLite 单列定义翻译成 MySQL：INTEGER→INT、REAL→DOUBLE、TEXT 默认值加括号。"""
    definition = re.sub(r"\bINTEGER\b", "INT", definition)
    definition = re.sub(r"\bREAL\b", "DOUBLE", definition)
    definition = re.sub(
        r"(TEXT\s+NOT\s+NULL\s+DEFAULT\s+)('(?:[^']|'')*')", r"\1(\2)", definition
    )
    return definition


def _is_duplicate_definition(exc: Exception) -> bool:
    code = exc.args[0] if exc.args else 0
    return code in (_TABLE_EXISTS, _DUPLICATE_COLUMN, _DUPLICATE_KEY_NAME)


class _MySqlTransaction(BackendTransaction):
    def __init__(self, database: "MySqlDatabase") -> None:
        self._database = database
        self._conn = None

    def __enter__(self) -> "_MySqlTransaction":
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

    def _run(self, sql: str, params: Sequence):
        cur = self._conn.cursor()
        cur.execute(translate(sql), tuple(params))
        return cur

    def execute(self, sql: str, params: Sequence = ()) -> int:
        return int(self._run(sql, params).lastrowid or 0)

    def query(self, sql: str, params: Sequence = ()) -> list[dict[str, Any]]:
        return self._run(sql, params).fetchall()

    def query_one(self, sql: str, params: Sequence = ()) -> dict[str, Any] | None:
        return self._run(sql, params).fetchone()


class MySqlDatabase(Database):
    dialect = "mysql"

    def __init__(self, host: str, port: int, user: str, password: str, database: str) -> None:
        self._params = {
            "host": host,
            "port": int(port),
            "user": user,
            "password": password,
            "database": database,
        }

    def _connect(self):
        return pymysql.connect(
            **self._params,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )

    def execute_script(self, sql: str) -> None:
        conn = self._connect()
        try:
            for statement in sql.split(";"):
                statement = statement.strip()
                if not statement:
                    continue
                try:
                    conn.cursor().execute(statement)
                except Exception as exc:  # noqa: BLE001
                    if _is_duplicate_definition(exc):
                        continue
                    raise
            conn.commit()
        finally:
            conn.close()

    def table_columns(self, table: str) -> set[str]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT COLUMN_NAME AS name FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                (table,),
            )
            return {row["name"] for row in cur.fetchall()}
        finally:
            conn.close()

    def add_column_if_missing(self, table: str, column: str, definition: str) -> None:
        if column in self.table_columns(table):
            return
        self.execute(f"ALTER TABLE {table} ADD COLUMN {translate_column_definition(definition)}")

    def query(self, sql: str, params: Sequence = ()) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(translate(sql), tuple(params))
            return list(cur.fetchall())
        finally:
            conn.close()

    def query_one(self, sql: str, params: Sequence = ()) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(translate(sql), tuple(params))
            return cur.fetchone()
        finally:
            conn.close()

    def execute(self, sql: str, params: Sequence = ()) -> int:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(translate(sql), tuple(params))
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
            cur = conn.cursor()
            cur.executemany(translate(sql), [tuple(p) for p in params_list])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def transaction(self) -> BackendTransaction:
        return _MySqlTransaction(self)
