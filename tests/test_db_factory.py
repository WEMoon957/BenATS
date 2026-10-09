"""后端选择工厂的单元测试。"""

from pathlib import Path

from app.db.backend import SqliteDatabase
from app.db.factory import build_database
from app.db.mysql_backend import MySqlDatabase


def test_defaults_to_sqlite(monkeypatch):
    monkeypatch.delenv("TALENT_HUB_DB", raising=False)
    monkeypatch.delenv("MYSQL_DATABASE", raising=False)

    assert isinstance(build_database(Path("/tmp/x.db")), SqliteDatabase)


def test_mysql_when_talent_hub_db_is_mysql(monkeypatch):
    monkeypatch.delenv("MYSQL_DATABASE", raising=False)
    monkeypatch.setenv("TALENT_HUB_DB", "mysql")
    monkeypatch.setenv("MYSQL_HOST", "db.example.com")
    monkeypatch.setenv("MYSQL_PORT", "3307")
    monkeypatch.setenv("MYSQL_USER", "u")
    monkeypatch.setenv("MYSQL_PASSWORD", "pw")
    monkeypatch.setenv("MYSQL_DATABASE", "benats")

    db = build_database(Path("/tmp/x.db"))

    assert isinstance(db, MySqlDatabase)
    assert db._params == {
        "host": "db.example.com",
        "port": 3307,
        "user": "u",
        "password": "pw",
        "database": "benats",
    }


def test_mysql_when_database_env_set(monkeypatch):
    monkeypatch.delenv("TALENT_HUB_DB", raising=False)
    monkeypatch.setenv("MYSQL_DATABASE", "benats")
    monkeypatch.setenv("MYSQL_PASSWORD", "pw")

    assert isinstance(build_database(Path("/tmp/x.db")), MySqlDatabase)
