"""人事中台看板与员工生命周期：聚合员工、考勤与招聘摘要，并承载离职表单流程。

看板数据全部来自本地存储（考勤 attendance.db 与招聘 recruitment.db）；
「从飞书同步员工」与「飞书事件订阅」是员工档案的写入来源，离职表单流程由此发起。
公开表单接口（/api/resignation/*）不校验登录会话，仅依赖应用内置令牌，供员工在
浏览器打开链接后填写。
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .attendance.feishu_contacts import (
    FeishuContactsClient,
    FeishuContactsError,
    last_sync,
    sync_employees,
)
from .attendance.lifecycle import (
    RESIGN_STATUS_LABELS,
    employee_flow_dashboard,
    list_events,
)
from .attendance.resignation import (
    CONFIG_PUBLIC_BASE_URL,
    REASON_CATEGORIES,
    confirm_request,
    create_request,
    deliver_request,
    get_by_token,
    list_requests,
    reject_request,
    resolve_base_url,
    submit_request,
)
from .attendance.routes import require_account
from .attendance.sync import CONFIG_APP_ID, CONFIG_APP_SECRET, CONFIG_ENABLED, FEISHU_SYNC_HASH
from .recruitment.db import STAGE_LABELS


class ResignationCreateInput(BaseModel):
    employee_id: int


class ResignationConfirmInput(BaseModel):
    last_working_day: str | None = None


class ResignationRejectInput(BaseModel):
    note: str = ""


class LifecycleConfigInput(BaseModel):
    public_base_url: str = ""


class ResignationSubmitInput(BaseModel):
    last_working_day: str
    reason_category: str = ""
    reason_detail: str = Field(default="", max_length=2000)
    handover_to: str = Field(default="", max_length=200)
    handover_note: str = Field(default="", max_length=2000)
    contact_after: str = Field(default="", max_length=200)


def register_hr_routes(
    app: FastAPI,
    attendance_store,
    recruitment_store,
    repository,
    feishu_sync,
    event_engine=None,
    lifecycle_engine=None,
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

        # ---- 人员流动（入离职事件流聚合） ----
        lifecycle = employee_flow_dashboard(attendance_store)
        lifecycle["ops"] = {
            "public_base_url": attendance_store.get_config(CONFIG_PUBLIC_BASE_URL),
            "reason_categories": REASON_CATEGORIES,
            "events": {
                "configured": bool(event_engine and event_engine.configured),
                "enabled": bool(event_engine and event_engine.enabled),
                "running": bool(event_engine and event_engine.running),
                "connected": bool(event_engine and event_engine.connected),
                "last_error": event_engine.last_error if event_engine else "",
                "last_event": event_engine.last_event() if event_engine else None,
                "stats": event_engine.stats if event_engine else {},
            },
            "offboard": {
                "running": bool(lifecycle_engine and lifecycle_engine.running),
                "last_error": lifecycle_engine.last_error if lifecycle_engine else "",
                "last_result": lifecycle_engine.last_result if lifecycle_engine else None,
            },
        }

        # ---- 招聘进展 ----
        candidates = recruitment_store.query("SELECT COUNT(*) AS c FROM candidate")[0]["c"]
        stage_rows = recruitment_store.query("SELECT stage, COUNT(*) AS c FROM candidate GROUP BY stage")
        stages = [
            {"stage": row["stage"], "label": STAGE_LABELS.get(row["stage"], row["stage"]), "count": row["c"]}
            for row in stage_rows
        ]
        # 全量未归档任务数：list_recent 默认封顶 20，只适合最近列表，不能用来计数
        jobs = len(repository.list_jobs(archived=False))

        return {
            "employees": {"total": total, "active": active, "departments": departments},
            "attendance": attendance,
            "lifecycle": lifecycle,
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

    # ---- 人员流动事件流 ----

    @app.get("/api/hr/lifecycle/events")
    async def lifecycle_events(request: Request):
        require_account(request, attendance_store)
        events = list_events(attendance_store, limit=200)
        return {"events": events, "status_labels": RESIGN_STATUS_LABELS}

    @app.put("/api/hr/lifecycle/config")
    async def save_lifecycle_config(request: Request, payload: LifecycleConfigInput):
        require_account(request, attendance_store, write=True)
        attendance_store.set_config(CONFIG_PUBLIC_BASE_URL, payload.public_base_url.strip().rstrip("/"))
        # 事件长连接有凭证即自动建立；保存后顺带确保已启动
        if event_engine is not None:
            await run_in_threadpool(event_engine.start)
        return {"ok": True}

    @app.post("/api/hr/lifecycle/run-offboards")
    async def run_offboards(request: Request):
        require_account(request, attendance_store, write=True)
        if lifecycle_engine is None:
            raise HTTPException(status_code=503, detail="离职定时引擎未初始化")
        return await run_in_threadpool(lifecycle_engine.run_once)

    # ---- 离职申请（HR 侧） ----

    @app.get("/api/hr/resignations")
    async def resignations(request: Request):
        require_account(request, attendance_store)
        return {"requests": list_requests(attendance_store)}

    @app.post("/api/hr/resignations")
    async def create_resignation(request: Request, payload: ResignationCreateInput):
        account = require_account(request, attendance_store, write=True)
        try:
            record = await run_in_threadpool(
                create_request,
                attendance_store,
                employee_id=payload.employee_id,
                created_by_id=account["id"],
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        base_url = resolve_base_url(attendance_store, str(request.base_url))
        delivery = await run_in_threadpool(_deliver_safely, attendance_store, record, base_url)
        return {"request": _reload(attendance_store, record["id"]), "delivery": delivery}

    @app.post("/api/hr/resignations/{request_id}/confirm")
    async def confirm_resignation(request: Request, request_id: int, payload: ResignationConfirmInput):
        account = require_account(request, attendance_store, write=True)
        try:
            record = await run_in_threadpool(
                confirm_request,
                attendance_store,
                request_id,
                account_id=account["id"],
                last_working_day=payload.last_working_day,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"request": record}

    @app.post("/api/hr/resignations/{request_id}/reject")
    async def reject_resignation(request: Request, request_id: int, payload: ResignationRejectInput):
        require_account(request, attendance_store, write=True)
        try:
            record = await run_in_threadpool(
                reject_request, attendance_store, request_id, note=payload.note
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"request": record}

    @app.post("/api/hr/resignations/{request_id}/resend")
    async def resend_resignation(request: Request, request_id: int):
        require_account(request, attendance_store, write=True)
        record = attendance_store.query_one(
            "SELECT * FROM resignation_request WHERE id = ?", (request_id,)
        )
        if not record:
            raise HTTPException(status_code=404, detail="离职申请不存在")
        base_url = resolve_base_url(attendance_store, str(request.base_url))
        delivery = await run_in_threadpool(_deliver_safely, attendance_store, record, base_url)
        return {"request": _reload(attendance_store, request_id), "delivery": delivery}

    # ---- 公开离职表单（员工侧，凭令牌访问） ----

    @app.get("/api/resignation/{token}")
    async def public_form(token: str):
        record = get_by_token(attendance_store, token)
        if not record:
            raise HTTPException(status_code=404, detail="表单链接无效或已失效")
        return {
            "request": {
                "employee_name": record["employee_name"],
                "employee_no": record["employee_no"],
                "department": record["department"],
                "position": record["position"],
                "status": record["status"],
                "last_working_day": record["last_working_day"],
                "reason_category": record["reason_category"],
                "reason_detail": record["reason_detail"],
                "handover_to": record["handover_to"],
                "handover_note": record["handover_note"],
                "contact_after": record["contact_after"],
            },
            "reason_categories": REASON_CATEGORIES,
        }

    @app.post("/api/resignation/{token}")
    async def public_submit(token: str, payload: ResignationSubmitInput):
        try:
            record = await run_in_threadpool(
                submit_request,
                attendance_store,
                token,
                last_working_day=payload.last_working_day,
                reason_category=payload.reason_category,
                reason_detail=payload.reason_detail,
                handover_to=payload.handover_to,
                handover_note=payload.handover_note,
                contact_after=payload.contact_after,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "status": record["status"], "last_working_day": record["last_working_day"]}


def _reload(store, request_id: int) -> dict:
    return store.query_one("SELECT * FROM resignation_request WHERE id = ?", (request_id,))


def _deliver_safely(store, record: dict, base_url: str) -> dict:
    """下发是外部调用，失败不能让已建好的申请整体报错，统一转成可读的送达结果。"""
    try:
        return deliver_request(store, record, base_url=base_url)
    except Exception as exc:  # noqa: BLE001
        return {"delivered": False, "manual": True, "link": "", "detail": f"下发表单失败：{exc}"}
