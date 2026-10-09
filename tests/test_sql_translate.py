"""SQLite → MySQL 方言翻译的单元测试（纯函数，不依赖 MySQL 服务）。"""

from app.db.sql_translate import translate


def test_placeholders_replaced():
    assert translate("SELECT * FROM t WHERE a = ? AND b = ?") == (
        "SELECT * FROM t WHERE a = %s AND b = %s"
    )


def test_question_mark_inside_literal_is_preserved():
    assert translate("SELECT '?' AS q WHERE a = ?") == "SELECT '?' AS q WHERE a = %s"


def test_escaped_quote_inside_literal():
    assert translate("SELECT 'it''s ?' AS q WHERE a = ?") == "SELECT 'it''s ?' AS q WHERE a = %s"


def test_insert_or_ignore():
    assert translate("INSERT OR IGNORE INTO t (a) VALUES (?)") == "INSERT IGNORE INTO t (a) VALUES (%s)"


def test_on_conflict_single_column():
    sql = "INSERT INTO app_config (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value"
    assert translate(sql) == (
        "INSERT INTO app_config (key, value) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE value = VALUES(value)"
    )


def test_on_conflict_multiple_columns():
    sql = (
        "INSERT INTO job_rubric (job_keyword, rubric, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(job_keyword) DO UPDATE SET rubric = excluded.rubric, updated_at = excluded.updated_at"
    )
    assert translate(sql) == (
        "INSERT INTO job_rubric (job_keyword, rubric, updated_at) VALUES (%s, %s, %s) "
        "ON DUPLICATE KEY UPDATE rubric = VALUES(rubric), updated_at = VALUES(updated_at)"
    )


def test_plain_query_untouched_except_placeholders():
    assert translate("SELECT * FROM t WHERE a = ? AND b LIKE ?") == (
        "SELECT * FROM t WHERE a = %s AND b LIKE %s"
    )
