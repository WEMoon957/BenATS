"""人事中台看板：聚合员工、考勤与招聘三类摘要，供前端一览页渲染。

数据全部来自本地存储（考勤 attendance.db 与招聘 recruitment.db），
不依赖飞书运行时同步；看板是只读概览，不涉及考勤独立账号体系。
"""

from __future__ import annotations

from fastapi import FastAPI

from .recruitment.db import STAGE_LABELS


def register_hr_routes(app: FastAPI, attendance_store, recruitment_store, repository) -> None:
    @app.get("/api/hr/dashboard")
    async def hr_dashboard():
        # ---- 员工信息 ----
        total = attendance_store.query("SELECT COUNT(*) AS c FROM employee")[0]["c"]
        active = attendance_store.query("SELECT COUNT(*) AS c FROM employee WHERE active = 1")[0]["c"]
        dept_rows = attendance_store.query(
            "SELECT CASE WHEN department = '' THEN '未分组' ELSE department END AS department, "
            "COUNT(*) AS c FROM employee GROUP BY department ORDER BY department"
        )
        departments = [{"department": row["department"], "count": row["c"]} for row in dept_rows]

        # ---- 考勤汇总（最近一个已完成批次） ----
        latest = attendance_store.query_one(
            "SELECT * FROM import_batch WHERE status = 'completed' "
            "ORDER BY year DESC, month DESC, created_at DESC",
            table="import_batch",
        )
        attendance = {
            "latest_period": None,
            "attendance_rate": 0,
            "review_count": 0,
            "pending_cross_day": 0,
        }
        if latest:
            attendance["latest_period"] = f"{latest['year']:04d}-{latest['month']:02d}"
            batch_id = latest["id"]
            totals = attendance_store.query_one(
                "SELECT SUM(actual_days) AS actual, SUM(due_days) AS due "
                "FROM attendance_result WHERE batch_id = ?",
                (batch_id,),
            )
            actual = float(totals["actual"] or 0)
            due = float(totals["due"] or 0)
            attendance["attendance_rate"] = round(min(actual / due * 100, 100), 1) if due else 0
            attendance["review_count"] = attendance_store.query(
                "SELECT COUNT(*) AS c FROM attendance_result WHERE batch_id = ? AND status = 'review'",
                (batch_id,),
            )[0]["c"]
            attendance["pending_cross_day"] = attendance_store.query(
                "SELECT COUNT(*) AS c FROM cross_day_suspicion WHERE batch_id = ? AND status = 'pending'",
                (batch_id,),
            )[0]["c"]

        # ---- 招聘进展 ----
        candidates = recruitment_store.query("SELECT COUNT(*) AS c FROM candidate")[0]["c"]
        stage_rows = recruitment_store.query("SELECT stage, COUNT(*) AS c FROM candidate GROUP BY stage")
        stages = [
            {"stage": row["stage"], "label": STAGE_LABELS.get(row["stage"], row["stage"]), "count": row["c"]}
            for row in stage_rows
        ]
        jobs = len(repository.list_recent())

        return {
            "employees": {"total": total, "active": active, "departments": departments},
            "attendance": attendance,
            "recruitment": {"candidates": candidates, "jobs": jobs, "stages": stages},
        }
