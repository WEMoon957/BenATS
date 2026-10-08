"""人事中台看板：聚合员工、考勤与招聘三类摘要，供前端一览页渲染。

看板数据全部来自本地存储（考勤 attendance.db 与招聘 recruitment.db），
不涉及考勤独立账号体系；「从飞书同步员工」是唯一写入口，按飞书通讯录刷新员工档案。
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool

from .attendance.feishu_contacts import (
    FeishuContactsClient,
    FeishuContactsError,
    last_sync,
    sync_employees,
)
from .attendance.sync import CONFIG_APP_ID, CONFIG_APP_SECRET, CONFIG_ENABLED, FEISHU_SYNC_HASH
from .recruitment.db import STAGE_LABELS


def register_hr_routes(
    app: FastAPI, attendance_store, recruitment_store, repository, feishu_sync
) -> None:
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

        # ---- 飞书员工同步状态 ----
        feishu = {
            "credentials_configured": bool(
                attendance_store.get_config(CONFIG_APP_ID) and attendance_store.get_config(CONFIG_APP_SECRET)
            ),
            "employee_sync": last_sync(attendance_store),
        }

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

        # ---- 飞书考勤同步状态（最新一个飞书同步批次） ----
        last_feishu_batch = attendance_store.query_one(
            "SELECT year, month, status, matched_rows, completed_at FROM import_batch "
            "WHERE file_sha256 = ? ORDER BY year DESC, month DESC, created_at DESC",
            (FEISHU_SYNC_HASH,),
        )
        attendance["feishu"] = {
            "enabled": attendance_store.get_config(CONFIG_ENABLED) == "1",
            "running": bool(feishu_sync and feishu_sync.running),
            "last_error": feishu_sync.last_error if feishu_sync else "",
            "last_batch": (
                {
                    "period": f"{last_feishu_batch['year']:04d}-{last_feishu_batch['month']:02d}",
                    "status": last_feishu_batch["status"],
                    "matched_rows": last_feishu_batch["matched_rows"] or 0,
                    "completed_at": last_feishu_batch["completed_at"],
                }
                if last_feishu_batch
                else None
            ),
        }

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
            "feishu": feishu,
        }

    @app.post("/api/hr/feishu-employees/sync")
    async def sync_feishu_employees():
        app_id = attendance_store.get_config(CONFIG_APP_ID)
        app_secret = attendance_store.get_config(CONFIG_APP_SECRET)
        if not app_id or not app_secret:
            raise HTTPException(
                status_code=400,
                detail="尚未配置飞书应用凭证，请先在「考勤管理 → 设置」中填写 App ID 与 App Secret",
            )
        client = FeishuContactsClient(app_id, app_secret)
        try:
            return await run_in_threadpool(sync_employees, attendance_store, client)
        except FeishuContactsError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/hr/feishu-sync")
    async def sync_feishu_attendance():
        if not feishu_sync:
            raise HTTPException(status_code=503, detail="飞书考勤同步引擎未初始化")
        return await run_in_threadpool(feishu_sync.sync_once)
