"""迁移脚本读侧（SQLite）的回归测试，不依赖 MySQL 服务。"""

from pathlib import Path

from app.attendance.db import AttendanceStore
from app.db.migrate import ATTENDANCE_TABLES, read_rows


def test_read_rows_reads_all_tables_with_raw_values(tmp_path):
    store = AttendanceStore(tmp_path / "attendance.db")
    store.initialize()
    store.set_config("feishu_app_id", "cli_x")

    rows = read_rows(tmp_path / "attendance.db", ATTENDANCE_TABLES)

    assert set(rows) == set(ATTENDANCE_TABLES)
    assert rows["app_config"][0]["key"] == "feishu_app_id"
    assert rows["app_config"][0]["value"] == "cli_x"
    # initialize 会种默认 admin
    assert rows["account"][0]["username"] == "admin"
