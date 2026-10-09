"""MySqlDatabase 的接线单元测试：用假连接替换 pymysql.connect，不依赖 MySQL 服务。

只验证「翻译被应用、参数透传、提交/容错、information_schema 查询」这些接线逻辑；
SQL 是否被 MySQL 接受需真实实例验证。
"""

import pymysql

from app.db import mysql_backend
from app.db.mysql_backend import MySqlDatabase, translate_column_definition


class _FakeCursor:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.lastrowid = 0
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params
        return self

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def executemany(self, sql, params_list):
        self.sql = sql
        self.params = params_list
        return self


class _FakeConn:
    def __init__(self):
        self.committed = 0
        self.rolled_back = 0
        self.closed = False
        self.cursor_obj = _FakeCursor()

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True


def _backend(monkeypatch, conn):
    monkeypatch.setattr(mysql_backend.pymysql, "connect", lambda **kw: conn)
    return MySqlDatabase("host", 3306, "user", "pass", "benats")


def test_execute_translates_sql_and_commits(monkeypatch):
    conn = _FakeConn()
    db = _backend(monkeypatch, conn)

    db.execute("INSERT INTO t (a) VALUES (?) ON CONFLICT(a) DO UPDATE SET a = excluded.a", (1,))

    assert conn.cursor_obj.sql == "INSERT INTO t (a) VALUES (%s) ON DUPLICATE KEY UPDATE a = VALUES(a)"
    assert conn.cursor_obj.params == (1,)
    assert conn.committed == 1
    assert conn.closed


def test_query_translates_sql(monkeypatch):
    conn = _FakeConn()
    conn.cursor_obj = _FakeCursor(rows=[{"a": 1}])
    db = _backend(monkeypatch, conn)

    rows = db.query("SELECT * FROM t WHERE a = ?", (1,))

    assert conn.cursor_obj.sql == "SELECT * FROM t WHERE a = %s"
    assert rows == [{"a": 1}]


def test_execute_script_tolerates_duplicate_index(monkeypatch):
    class _Cursor(_FakeCursor):
        def execute(self, sql, params=None):
            if sql.strip().upper().startswith("CREATE INDEX"):
                raise pymysql.err.InternalError(1061, "Duplicate key name 'idx'")
            return self

    conn = _FakeConn()
    conn.cursor_obj = _Cursor()
    db = _backend(monkeypatch, conn)

    # 重复建索引不抛错，其余语句正常执行
    db.execute_script("CREATE TABLE IF NOT EXISTS t (id INT); CREATE INDEX idx ON t(id);")

    assert conn.committed == 1


def test_table_columns_queries_information_schema(monkeypatch):
    conn = _FakeConn()
    conn.cursor_obj = _FakeCursor(rows=[{"name": "a"}, {"name": "b"}])
    db = _backend(monkeypatch, conn)

    columns = db.table_columns("t")

    assert columns == {"a", "b"}
    assert "information_schema.COLUMNS" in conn.cursor_obj.sql


def test_add_column_if_missing_translates_text_default(monkeypatch):
    conn = _FakeConn()
    conn.cursor_obj = _FakeCursor(rows=[])  # 尚无该列
    db = _backend(monkeypatch, conn)

    db.add_column_if_missing("candidate", "score_detail", "score_detail TEXT NOT NULL DEFAULT ''")

    assert conn.cursor_obj.sql == (
        "ALTER TABLE candidate ADD COLUMN score_detail TEXT NOT NULL DEFAULT ('')"
    )


def test_translate_column_definition():
    assert translate_column_definition("x INTEGER NOT NULL DEFAULT 0") == "x INT NOT NULL DEFAULT 0"
    assert translate_column_definition("x TEXT NOT NULL DEFAULT ''") == "x TEXT NOT NULL DEFAULT ('')"
    assert translate_column_definition("x REAL NOT NULL DEFAULT 0") == "x DOUBLE NOT NULL DEFAULT 0"
