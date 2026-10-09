"""SQLite → MySQL 数据迁移 CLI。

用法：
    python scripts/migrate_sqlite_to_mysql.py \
        --attendance-sqlite ~/.local/share/TalentHub/attendance.db \
        --recruitment-sqlite ~/.local/share/TalentHub/recruitment.db \
        --data-dir ~/.local/share/TalentHub \
        --mysql-host 127.0.0.1 --mysql-user benats --mysql-password 密码 --mysql-database benats
（密码也可经 MYSQL_PASSWORD 环境变量提供；目标库需为空且已建表。）
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.attendance.db import AttendanceStore  # noqa: E402
from app.config import app_data_dir  # noqa: E402
from app.db.json_backend import MySqlJsonBackend  # noqa: E402
from app.db.migrate import (  # noqa: E402
    ATTENDANCE_TABLES,
    RECRUITMENT_TABLES,
    connect,
    migrate,
    migrate_json_records,
)
from app.db.mysql_backend import MySqlDatabase  # noqa: E402
from app.recruitment.db import RecruitmentStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite → MySQL 数据迁移")
    parser.add_argument("--attendance-sqlite", type=Path)
    parser.add_argument("--recruitment-sqlite", type=Path)
    parser.add_argument(
        "--data-dir", type=Path, default=app_data_dir(),
        help="任务/触达元数据所在的数据目录（默认取 TALENT_HUB_DATA_DIR 或系统默认）",
    )
    parser.add_argument("--mysql-host", default=os.getenv("MYSQL_HOST", "127.0.0.1"))
    parser.add_argument("--mysql-port", type=int, default=int(os.getenv("MYSQL_PORT", "3306")))
    parser.add_argument("--mysql-user", default=os.getenv("MYSQL_USER", "benats"))
    parser.add_argument("--mysql-password", default=os.getenv("MYSQL_PASSWORD", ""))
    parser.add_argument("--mysql-database", default=os.getenv("MYSQL_DATABASE", "benats"))
    args = parser.parse_args()

    if not args.mysql_password:
        parser.error("缺少 MySQL 密码：请用 --mysql-password 或环境变量 MYSQL_PASSWORD 提供")
    if not args.attendance_sqlite and not args.recruitment_sqlite:
        parser.error("请至少提供 --attendance-sqlite 或 --recruitment-sqlite")

    database = MySqlDatabase(
        args.mysql_host, args.mysql_port, args.mysql_user, args.mysql_password,
        args.mysql_database,
    )
    # 建表（幂等）：考勤/招聘表 + json_records
    AttendanceStore(backend=database).initialize()
    RecruitmentStore(backend=database).initialize()
    json_backend = MySqlJsonBackend(database)

    conn = connect(
        args.mysql_host, args.mysql_port, args.mysql_user, args.mysql_password, args.mysql_database
    )
    try:
        total = 0
        if args.attendance_sqlite:
            total += migrate(conn, args.attendance_sqlite, ATTENDANCE_TABLES)
        if args.recruitment_sqlite:
            total += migrate(conn, args.recruitment_sqlite, RECRUITMENT_TABLES)
        conn.commit()
        # 任务/触达元数据（jobs/calls/outreaches）迁到 json_records 表
        if args.data_dir.exists():
            total += migrate_json_records(args.data_dir, json_backend)
        print(f"迁移完成，共写入 {total} 行")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
