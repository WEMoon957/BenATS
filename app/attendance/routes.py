"""考勤管理 API 路由与认证。

认证模型：考勤模块有独立的账号体系（admin/hr/supervisor/viewer）。
登录成功后签发内存会话 token，前端通过 X-Attendance-Token 头携带；
后端据此解析账号并做写权限校验（admin/hr 可写，supervisor/viewer 只读）。
"""

from __future__ import annotations

import secrets
import threading
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..config import app_data_dir
from .db import WRITE_ROLES, AttendanceStore, hash_password, verify_password
from .exporter import build_summary_workbook
from .services import (
    process_import_batch,
    recalculate_result,
    resolve_cross_day,
    file_sha256,
)
from .sync import CONFIG_APP_ID, CONFIG_APP_SECRET, CONFIG_ENABLED, FeishuSyncEngine

_sessions: dict[str, int] = {}
_sessions_lock = threading.Lock()

MAX_IMPORT_BYTES = 10 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def account_payload(account: dict) -> dict:
    return {
        "id": account["id"],
        "username": account["username"],
        "role": account["role"],
        "department": account["department"],
        "is_active": bool(account["is_active"]),
    }


def require_account(request: Request, store: AttendanceStore, write: bool = False) -> dict:
    token = request.headers.get("X-Attendance-Token", "")
    with _sessions_lock:
        account_id = _sessions.get(token)
    if account_id is None:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    account = store.query_one("SELECT * FROM account WHERE id = ?", (account_id,), table="account")
    if not account or not account["is_active"]:
        raise HTTPException(status_code=401, detail="账号不存在或已停用")
    if write and account["role"] not in WRITE_ROLES:
        raise HTTPException(status_code=403, detail="当前角色没有写权限")
    return account


# ---- 输入模型 ----

class LoginInput(BaseModel):
    username: str
    password: str


class ChangePasswordInput(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6, max_length=128)


class PolicyInput(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    mode: str = "standard"
    start_time: str | None = None
    end_time: str | None = None
    grace_minutes: int = 0
    cross_day_cutoff_minutes: int = 180
    description: str = ""
    active: bool = True


class TagInput(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    color: str = "#64748B"
    description: str = ""


class EmployeeInput(BaseModel):
    employee_no: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    aliases: list[str] = []
    department: str = ""
    position: str = ""
    join_date: str | None = None
    employment_status: str = "regular"
    active: bool = True
    attendance_policy_id: int | None = None
    expected_days_override: float | None = None
    phone: str = ""
    bank_name: str = ""
    bank_account_holder: str = ""
    bank_province: str = ""
    bank_branch: str = ""
    bank_card_number: str = ""
    alipay_account: str = ""
    tag_ids: list[int] = []


class ResultPatchInput(BaseModel):
    leave_days: float | None = None
    overtime_days: float | None = None
    overtime_hours: float | None = None
    adjustment_days: float | None = None
    adjustment_hours: float | None = None
    late_count: int | None = None
    absence_count: int | None = None
    missing_punch_count: int | None = None
    deduction: float | None = None
    note: str | None = None


class ResolveInput(BaseModel):
    resolution: str


class FeishuConfigInput(BaseModel):
    app_id: str = ""
    app_secret: str = ""
    enabled: bool = False


# ---- 序列化辅助 ----

def _tags_for_employee(store: AttendanceStore, employee_id: int) -> list[dict]:
    return store.query(
        "SELECT t.id, t.name, t.color FROM employee_tag t "
        "JOIN employee_tags et ON et.tag_id = t.id WHERE et.employee_id = ? ORDER BY t.name",
        (employee_id,),
    )


def _employee_payload(store: AttendanceStore, employee: dict) -> dict:
    payload = dict(employee)
    payload["tags"] = _tags_for_employee(store, employee["id"])
    policy_id = employee.get("attendance_policy_id")
    if policy_id:
        policy = store.query_one(
            "SELECT id, code, name, mode FROM attendance_policy WHERE id = ?", (policy_id,)
        )
        payload["attendance_policy"] = policy
    return payload


def _policy_payload(policy: dict) -> dict:
    return {
        "id": policy["id"],
        "code": policy["code"],
        "name": policy["name"],
        "mode": policy["mode"],
        "start_time": policy["start_time"],
        "end_time": policy["end_time"],
        "grace_minutes": policy["grace_minutes"],
        "cross_day_cutoff_minutes": policy["cross_day_cutoff_minutes"],
        "description": policy["description"],
        "active": bool(policy["active"]),
    }


def _batch_payload(batch: dict) -> dict:
    return {
        "id": batch["id"],
        "original_filename": batch["original_filename"],
        "file_sha256": batch["file_sha256"],
        "year": batch["year"],
        "month": batch["month"],
        "default_expected_days": batch["default_expected_days"],
        "status": batch["status"],
        "total_rows": batch["total_rows"],
        "matched_rows": batch["matched_rows"],
        "unmatched_rows": batch["unmatched_rows"],
        "suspicion_count": batch["suspicion_count"],
        "error_message": batch["error_message"],
        "created_at": batch["created_at"],
        "completed_at": batch["completed_at"],
    }


def _result_payload(result: dict) -> dict:
    payload = dict(result)
    payload["rule_trace"] = result.get("rule_trace") or {}
    return payload


def _suspicion_payload(suspicion: dict) -> dict:
    return dict(suspicion)


def register_routes(app: FastAPI, store: AttendanceStore, sync_engine: FeishuSyncEngine | None = None) -> None:
    # ---- 认证 ----

    @app.post("/api/attendance/login")
    async def login(payload: LoginInput):
        account = store.query_one(
            "SELECT * FROM account WHERE username = ?", (payload.username.strip(),), table="account"
        )
        if not account or not verify_password(payload.password, account["password_hash"]):
            raise HTTPException(status_code=400, detail="账号或密码错误")
        if not account["is_active"]:
            raise HTTPException(status_code=403, detail="账号已停用")
        token = secrets.token_urlsafe(32)
        with _sessions_lock:
            _sessions[token] = account["id"]
        return {"token": token, "account": account_payload(account)}

    @app.post("/api/attendance/logout")
    async def logout(request: Request):
        token = request.headers.get("X-Attendance-Token", "")
        with _sessions_lock:
            _sessions.pop(token, None)
        return {"ok": True}

    @app.get("/api/attendance/me")
    async def me(request: Request):
        account = require_account(request, store)
        return account_payload(account)

    @app.post("/api/attendance/change-password")
    async def change_password(request: Request, payload: ChangePasswordInput):
        account = require_account(request, store)
        if not verify_password(payload.current_password, account["password_hash"]):
            raise HTTPException(status_code=400, detail="当前密码错误")
        store.execute(
            "UPDATE account SET password_hash = ? WHERE id = ?",
            (hash_password(payload.new_password), account["id"]),
        )
        return {"ok": True}

    # ---- 考勤策略 ----

    @app.get("/api/attendance/policies")
    async def list_policies(_request: Request):
        require_account(_request, store)
        rows = store.query("SELECT * FROM attendance_policy ORDER BY name")
        return {"policies": [_policy_payload(row) for row in rows]}

    @app.post("/api/attendance/policies")
    async def create_policy(request: Request, payload: PolicyInput):
        require_account(request, store, write=True)
        try:
            policy_id = store.execute(
                "INSERT INTO attendance_policy (code, name, mode, start_time, end_time, grace_minutes, "
                "cross_day_cutoff_minutes, description, active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    payload.code, payload.name, payload.mode, payload.start_time, payload.end_time,
                    payload.grace_minutes, payload.cross_day_cutoff_minutes, payload.description,
                    1 if payload.active else 0,
                ),
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"策略代码可能已存在：{exc}") from exc
        row = store.query_one("SELECT * FROM attendance_policy WHERE id = ?", (policy_id,))
        return _policy_payload(row)

    @app.put("/api/attendance/policies/{policy_id}")
    async def update_policy(request: Request, policy_id: int, payload: PolicyInput):
        require_account(request, store, write=True)
        store.execute(
            "UPDATE attendance_policy SET code = ?, name = ?, mode = ?, start_time = ?, end_time = ?, "
            "grace_minutes = ?, cross_day_cutoff_minutes = ?, description = ?, active = ? WHERE id = ?",
            (
                payload.code, payload.name, payload.mode, payload.start_time, payload.end_time,
                payload.grace_minutes, payload.cross_day_cutoff_minutes, payload.description,
                1 if payload.active else 0, policy_id,
            ),
        )
        row = store.query_one("SELECT * FROM attendance_policy WHERE id = ?", (policy_id,))
        return _policy_payload(row)

    @app.delete("/api/attendance/policies/{policy_id}")
    async def delete_policy(request: Request, policy_id: int):
        require_account(request, store, write=True)
        store.execute("DELETE FROM attendance_policy WHERE id = ?", (policy_id,))
        return {"ok": True}

    # ---- 标签 ----

    @app.get("/api/attendance/tags")
    async def list_tags(_request: Request):
        require_account(_request, store)
        rows = store.query("SELECT * FROM employee_tag ORDER BY name")
        return {"tags": [dict(row) for row in rows]}

    @app.post("/api/attendance/tags")
    async def create_tag(request: Request, payload: TagInput):
        require_account(request, store, write=True)
        try:
            tag_id = store.execute(
                "INSERT INTO employee_tag (name, color, description) VALUES (?, ?, ?)",
                (payload.name, payload.color, payload.description),
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"标签名称可能已存在：{exc}") from exc
        return dict(store.query_one("SELECT * FROM employee_tag WHERE id = ?", (tag_id,)))

    @app.put("/api/attendance/tags/{tag_id}")
    async def update_tag(request: Request, tag_id: int, payload: TagInput):
        require_account(request, store, write=True)
        store.execute(
            "UPDATE employee_tag SET name = ?, color = ?, description = ? WHERE id = ?",
            (payload.name, payload.color, payload.description, tag_id),
        )
        return dict(store.query_one("SELECT * FROM employee_tag WHERE id = ?", (tag_id,)))

    @app.delete("/api/attendance/tags/{tag_id}")
    async def delete_tag(request: Request, tag_id: int):
        require_account(request, store, write=True)
        store.execute("DELETE FROM employee_tag WHERE id = ?", (tag_id,))
        return {"ok": True}

    # ---- 人员档案 ----

    @app.get("/api/attendance/employees")
    async def list_employees(
        request: Request,
        q: str = Query(default=""),
        active: str | None = Query(default=None),
        mode: str | None = Query(default=None),
        tag: str | None = Query(default=None),
    ):
        require_account(request, store)
        sql = (
            "SELECT e.*, p.mode AS policy_mode FROM employee e "
            "LEFT JOIN attendance_policy p ON p.id = e.attendance_policy_id WHERE 1=1"
        )
        params: list = []
        if q:
            sql += " AND (e.name LIKE ? OR e.employee_no LIKE ? OR e.department LIKE ? OR e.position LIKE ?)"
            like = f"%{q}%"
            params += [like, like, like, like]
        if active in {"true", "false"}:
            sql += " AND e.active = ?"
            params.append(1 if active == "true" else 0)
        if mode:
            sql += " AND p.mode = ?"
            params.append(mode)
        if tag:
            sql += " AND e.id IN (SELECT employee_id FROM employee_tags WHERE tag_id = ?)"
            params.append(int(tag))
        sql += " ORDER BY e.department, e.employee_no"
        rows = store.query(sql, tuple(params), table="employee")
        return {"employees": [_employee_payload(store, row) for row in rows]}

    @app.post("/api/attendance/employees")
    async def create_employee(request: Request, payload: EmployeeInput):
        require_account(request, store, write=True)
        import json as _json

        try:
            employee_id = store.execute(
                "INSERT INTO employee (employee_no, name, aliases, department, position, join_date, "
                "employment_status, active, attendance_policy_id, expected_days_override, phone, bank_name, "
                "bank_account_holder, bank_province, bank_branch, bank_card_number, alipay_account, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    payload.employee_no, payload.name, _json.dumps(payload.aliases, ensure_ascii=False),
                    payload.department, payload.position, payload.join_date, payload.employment_status,
                    1 if payload.active else 0, payload.attendance_policy_id, payload.expected_days_override,
                    payload.phone, payload.bank_name, payload.bank_account_holder, payload.bank_province,
                    payload.bank_branch, payload.bank_card_number, payload.alipay_account, _now(), _now(),
                ),
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"工号可能已存在：{exc}") from exc
        _set_employee_tags(store, employee_id, payload.tag_ids)
        row = store.query_one(
            "SELECT e.*, p.mode AS policy_mode FROM employee e LEFT JOIN attendance_policy p "
            "ON p.id = e.attendance_policy_id WHERE e.id = ?",
            (employee_id,), table="employee",
        )
        return _employee_payload(store, row)

    @app.put("/api/attendance/employees/{employee_id}")
    async def update_employee(request: Request, employee_id: int, payload: EmployeeInput):
        require_account(request, store, write=True)
        import json as _json

        store.execute(
            "UPDATE employee SET employee_no = ?, name = ?, aliases = ?, department = ?, position = ?, "
            "join_date = ?, employment_status = ?, active = ?, attendance_policy_id = ?, "
            "expected_days_override = ?, phone = ?, bank_name = ?, bank_account_holder = ?, bank_province = ?, "
            "bank_branch = ?, bank_card_number = ?, alipay_account = ?, updated_at = ? WHERE id = ?",
            (
                payload.employee_no, payload.name, _json.dumps(payload.aliases, ensure_ascii=False),
                payload.department, payload.position, payload.join_date, payload.employment_status,
                1 if payload.active else 0, payload.attendance_policy_id, payload.expected_days_override,
                payload.phone, payload.bank_name, payload.bank_account_holder, payload.bank_province,
                payload.bank_branch, payload.bank_card_number, payload.alipay_account, _now(), employee_id,
            ),
        )
        _set_employee_tags(store, employee_id, payload.tag_ids)
        row = store.query_one(
            "SELECT e.*, p.mode AS policy_mode FROM employee e LEFT JOIN attendance_policy p "
            "ON p.id = e.attendance_policy_id WHERE e.id = ?",
            (employee_id,), table="employee",
        )
        return _employee_payload(store, row)

    @app.delete("/api/attendance/employees/{employee_id}")
    async def delete_employee(request: Request, employee_id: int):
        require_account(request, store, write=True)
        store.execute("DELETE FROM employee WHERE id = ?", (employee_id,))
        return {"ok": True}

    # ---- 导入 ----

    @app.get("/api/attendance/imports")
    async def list_imports(request: Request):
        require_account(request, store)
        rows = store.query("SELECT * FROM import_batch ORDER BY created_at DESC")
        return {"imports": [_batch_payload(row) for row in rows]}

    @app.post("/api/attendance/imports")
    async def create_import(
        request: Request,
        file: UploadFile = File(...),
        year: int = Query(...),
        month: int = Query(...),
        default_expected_days: float = Query(default=25),
    ):
        require_account(request, store, write=True)
        filename = file.filename or ""
        if not filename.lower().endswith(".xlsx"):
            raise HTTPException(status_code=400, detail="第一版仅支持 .xlsx 文件")
        raw = await file.read()
        if len(raw) > MAX_IMPORT_BYTES:
            raise HTTPException(status_code=400, detail="文件不能超过 10MB")
        if not 1 <= month <= 12:
            raise HTTPException(status_code=400, detail="月份必须在 1-12 之间")

        digest = file_sha256(raw)
        duplicate = store.query_one(
            "SELECT * FROM import_batch WHERE file_sha256 = ? AND year = ? AND month = ?",
            (digest, year, month), table="import_batch",
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="同一个文件已导入过")

        import_dir = app_data_dir() / "attendance_imports" / str(year) / f"{month:02d}"
        import_dir.mkdir(parents=True, exist_ok=True)
        file_path = import_dir / filename
        file_path.write_bytes(raw)

        batch_id = store.execute(
            "INSERT INTO import_batch (original_filename, source_file_path, file_sha256, year, month, "
            "default_expected_days, status, uploaded_by_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (filename, str(file_path), digest, year, month, default_expected_days, "pending", None, _now()),
        )
        try:
            batch = process_import_batch(store, batch_id)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"导入失败：{exc}") from exc
        return _batch_payload(batch)

    @app.get("/api/attendance/imports/{batch_id}")
    async def get_import(request: Request, batch_id: int):
        require_account(request, store)
        batch = store.query_one("SELECT * FROM import_batch WHERE id = ?", (batch_id,), table="import_batch")
        if not batch:
            raise HTTPException(status_code=404, detail="导入批次不存在")
        return _batch_payload(batch)

    @app.get("/api/attendance/imports/{batch_id}/export")
    async def export_import(request: Request, batch_id: int):
        require_account(request, store)
        batch = store.query_one("SELECT * FROM import_batch WHERE id = ?", (batch_id,), table="import_batch")
        if not batch:
            raise HTTPException(status_code=404, detail="导入批次不存在")
        if batch["status"] != "completed":
            raise HTTPException(status_code=400, detail="只有处理完成的批次可以导出")
        stream = build_summary_workbook(store, batch)
        filename = f"{batch['year']}.{batch['month']}月考勤汇总.xlsx"
        return StreamingResponse(
            stream,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        )

    # ---- 考勤结果 ----

    @app.get("/api/attendance/results")
    async def list_results(
        request: Request,
        batch: int | None = Query(default=None),
        q: str = Query(default=""),
        status: str | None = Query(default=None),
    ):
        require_account(request, store)
        sql = (
            "SELECT r.*, e.employee_no AS emp_no, e.name AS emp_name, e.department AS emp_department, "
            "e.position AS emp_position, p.mode AS policy_mode "
            "FROM attendance_result r "
            "JOIN employee e ON e.id = r.employee_id "
            "LEFT JOIN attendance_policy p ON p.id = e.attendance_policy_id WHERE 1=1"
        )
        params: list = []
        if batch:
            sql += " AND r.batch_id = ?"
            params.append(batch)
        if q:
            sql += " AND (e.name LIKE ? OR e.employee_no LIKE ? OR e.department LIKE ?)"
            like = f"%{q}%"
            params += [like, like, like]
        if status:
            sql += " AND r.status = ?"
            params.append(status)
        sql += " ORDER BY e.department, e.employee_no"
        rows = store.query(sql, tuple(params), table="attendance_result")
        return {"results": [_result_payload(row) for row in rows]}

    @app.patch("/api/attendance/results/{result_id}")
    async def update_result(request: Request, result_id: int, payload: ResultPatchInput):
        require_account(request, store, write=True)
        result = store.query_one("SELECT * FROM attendance_result WHERE id = ?", (result_id,))
        if not result:
            raise HTTPException(status_code=404, detail="考勤结果不存在")
        updates = payload.model_dump(exclude_none=True)
        if updates:
            assignments = ", ".join(f"{key} = ?" for key in updates)
            store.execute(
                f"UPDATE attendance_result SET {assignments}, updated_at = ? WHERE id = ?",
                (*updates.values(), _now(), result_id),
            )
        updated = recalculate_result(store, result_id)
        return _result_payload(updated)

    @app.post("/api/attendance/results/{result_id}/approve")
    async def approve_result(request: Request, result_id: int):
        account = require_account(request, store, write=True)
        result = store.query_one("SELECT * FROM attendance_result WHERE id = ?", (result_id,))
        if not result:
            raise HTTPException(status_code=404, detail="考勤结果不存在")
        store.execute(
            "UPDATE attendance_result SET status = 'approved', reviewed_by_id = ?, reviewed_at = ? WHERE id = ?",
            (account["id"], _now(), result_id),
        )
        row = store.query_one(
            "SELECT r.*, e.employee_no AS emp_no, e.name AS emp_name, e.department AS emp_department, "
            "e.position AS emp_position, p.mode AS policy_mode FROM attendance_result r "
            "JOIN employee e ON e.id = r.employee_id LEFT JOIN attendance_policy p ON p.id = e.attendance_policy_id "
            "WHERE r.id = ?",
            (result_id,), table="attendance_result",
        )
        return _result_payload(row)

    # ---- 跨日疑似 ----

    @app.get("/api/attendance/suspicions")
    async def list_suspicions(
        request: Request,
        batch: int | None = Query(default=None),
        status: str | None = Query(default=None),
    ):
        require_account(request, store)
        sql = (
            "SELECT s.*, e.name AS emp_name, e.employee_no AS emp_no "
            "FROM cross_day_suspicion s LEFT JOIN employee e ON e.id = s.employee_id WHERE 1=1"
        )
        params: list = []
        if batch:
            sql += " AND s.batch_id = ?"
            params.append(batch)
        if status:
            sql += " AND s.status = ?"
            params.append(status)
        sql += " ORDER BY s.created_at DESC"
        rows = store.query(sql, tuple(params), table="cross_day_suspicion")
        return {"suspicions": [_suspicion_payload(row) for row in rows]}

    @app.post("/api/attendance/suspicions/{suspicion_id}/resolve")
    async def resolve_suspicion(request: Request, suspicion_id: int, payload: ResolveInput):
        account = require_account(request, store, write=True)
        try:
            suspicion = resolve_cross_day(store, suspicion_id, payload.resolution, account["id"])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _suspicion_payload(suspicion)

    # ---- 看板 ----

    @app.get("/api/attendance/dashboard")
    async def dashboard(
        request: Request,
        frm: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
        department: str = Query(default=""),
    ):
        require_account(request, store)
        completed = store.query(
            "SELECT * FROM import_batch WHERE status = 'completed' ORDER BY year DESC, month DESC, created_at DESC",
            table="import_batch",
        )
        latest_by_month: dict[tuple[int, int], dict] = {}
        for item in completed:
            latest_by_month.setdefault((item["year"], item["month"]), item)
        available_batches = list(latest_by_month.values())
        available_periods = [
            {"value": f"{item['year']:04d}-{item['month']:02d}", "label": f"{item['year']} 年 {item['month']} 月",
             "batch_id": item["id"]}
            for item in available_batches
        ]

        def parse_month(value):
            try:
                parsed = datetime.strptime(value, "%Y-%m")
            except (TypeError, ValueError):
                return None
            return parsed.year, parsed.month

        if frm or to:
            start_period = parse_month(frm or to)
            end_period = parse_month(to or frm)
            if not start_period or not end_period:
                raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM")
            if start_period > end_period:
                raise HTTPException(status_code=400, detail="起始月份不能晚于结束月份")
        elif available_batches:
            start_period = end_period = (available_batches[0]["year"], available_batches[0]["month"])
        else:
            start_period = end_period = None

        selected_batches = []
        if start_period and end_period:
            selected_batches = [
                item for item in available_batches
                if start_period <= (item["year"], item["month"]) <= end_period
            ]
            selected_batches.sort(key=lambda item: (item["year"], item["month"], item["created_at"]))

        period = {
            "from": f"{start_period[0]:04d}-{start_period[1]:02d}" if start_period else None,
            "to": f"{end_period[0]:04d}-{end_period[1]:02d}" if end_period else None,
            "batch_count": len(selected_batches),
        }
        empty = {
            "batch": None, "batches": [], "period": period, "available_periods": available_periods,
            "selected_department": department.strip(), "available_departments": [],
            "kpis": {"employees": 0, "attendance_rate": 0, "review_count": 0, "pending_cross_day": 0, "unmatched_rows": 0},
            "summary": {"total_rows": 0, "matched_rows": 0, "unmatched_rows": 0, "suspicion_count": 0},
            "daily": [], "departments": [],
        }
        if not selected_batches:
            return empty

        batch_ids = [item["id"] for item in selected_batches]
        placeholders = ",".join("?" for _ in batch_ids)
        results = store.query(
            f"SELECT r.*, e.department AS dept FROM attendance_result r JOIN employee e ON e.id = r.employee_id "
            f"WHERE r.batch_id IN ({placeholders})",
            tuple(batch_ids), table="attendance_result",
        )
        available_departments = sorted({row["dept"] or "未分组" for row in results})
        selected_department = department.strip()
        filtered = results
        if selected_department:
            filtered = [
                row for row in results
                if (row["dept"] or "未分组") == selected_department
            ]

        actual_total = sum(float(row["actual_days"]) for row in filtered)
        due_total = sum(float(row["due_days"]) for row in filtered)
        attendance_rate = min(round(actual_total / due_total * 100, 1), 100) if due_total else 0
        employee_count = len({row["employee_id"] for row in filtered})

        review_count = sum(1 for row in filtered if row["status"] == "review")
        pending_cross_day = store.query(
            f"SELECT COUNT(*) AS c FROM cross_day_suspicion WHERE batch_id IN ({placeholders}) AND status = 'pending'",
            tuple(batch_ids),
        )[0]["c"]
        if selected_department:
            dept_employees = {e["id"] for e in store.query(
                "SELECT id FROM employee WHERE department = ?", ("",) if selected_department == "未分组" else (selected_department,)
            )}
            # 跨日疑似按部门过滤：仅统计属于该部门的员工
            pending_cross_day = store.query(
                f"SELECT COUNT(*) AS c FROM cross_day_suspicion s JOIN employee e ON e.id = s.employee_id "
                f"WHERE s.batch_id IN ({placeholders}) AND s.status = 'pending' AND e.department = ?",
                tuple(batch_ids) + (("",) if selected_department == "未分组" else (selected_department,)),
            )[0]["c"]

        # 每日出勤
        raw_days = store.query(
            f"SELECT r.work_date, r.batch_id, r.employee_id, e.department AS dept FROM raw_punch_day r "
            f"JOIN employee e ON e.id = r.employee_id WHERE r.batch_id IN ({placeholders}) AND r.effective_has_punch = 1",
            tuple(batch_ids),
        )
        if selected_department:
            raw_days = [row for row in raw_days if (row["dept"] or "未分组") == selected_department]
        employee_counts_by_batch: dict[int, int] = {}
        for row in filtered:
            employee_counts_by_batch[row["batch_id"]] = employee_counts_by_batch.get(row["batch_id"], 0) + 1
        daily_map: dict[tuple[str, int], set] = {}
        for row in raw_days:
            daily_map.setdefault((row["work_date"], row["batch_id"]), set()).add(row["employee_id"])
        daily = [
            {"date": date_str, "count": len(ids),
             "rate": round(len(ids) / employee_counts_by_batch.get(batch_id, 1) * 100, 1)}
            for (date_str, batch_id), ids in sorted(daily_map.items(), key=lambda item: item[0][0])
        ]

        # 部门对比
        dept_agg: dict[str, dict] = {}
        for row in results:
            dept = row["dept"] or "未分组"
            agg = dept_agg.setdefault(dept, {"employees": set(), "actual": 0.0, "due": 0.0, "review": set()})
            agg["employees"].add(row["employee_id"])
            agg["actual"] += float(row["actual_days"])
            agg["due"] += float(row["due_days"])
            if row["status"] == "review":
                agg["review"].add(row["employee_id"])
        departments = []
        for dept, agg in dept_agg.items():
            departments.append({
                "department": dept,
                "employees": len(agg["employees"]),
                "attendance_rate": round(min(agg["actual"] / agg["due"] * 100, 100), 1) if agg["due"] else 0,
                "review_count": len(agg["review"]),
            })
        departments.sort(key=lambda item: item["department"])

        summary = {
            "total_rows": sum(item["total_rows"] for item in selected_batches),
            "matched_rows": sum(item["matched_rows"] for item in selected_batches),
            "unmatched_rows": sum(item["unmatched_rows"] for item in selected_batches),
            "suspicion_count": sum(item["suspicion_count"] for item in selected_batches),
        }
        latest_batch = selected_batches[-1]
        return {
            "batch": _batch_payload(latest_batch),
            "batches": [_batch_payload(item) for item in selected_batches],
            "period": period,
            "available_periods": available_periods,
            "kpis": {
                "employees": employee_count,
                "attendance_rate": attendance_rate,
                "review_count": review_count,
                "pending_cross_day": pending_cross_day,
                "unmatched_rows": summary["unmatched_rows"],
            },
            "summary": summary,
            "selected_department": selected_department,
            "available_departments": available_departments,
            "daily": daily,
            "departments": departments,
        }

    # ---- 飞书考勤自动同步 ----

    @app.get("/api/attendance/feishu-config")
    async def get_feishu_config(request: Request):
        require_account(request, store)
        app_secret = store.get_config(CONFIG_APP_SECRET)
        return {
            "enabled": store.get_config(CONFIG_ENABLED) == "1",
            "app_id": store.get_config(CONFIG_APP_ID),
            "app_secret_configured": bool(app_secret),
            "app_secret_tail": app_secret[-4:] if app_secret else "",
            "sync_running": bool(sync_engine and sync_engine.running),
            "last_error": sync_engine.last_error if sync_engine else "",
        }

    @app.put("/api/attendance/feishu-config")
    async def save_feishu_config(request: Request, payload: FeishuConfigInput):
        require_account(request, store, write=True)
        store.set_config(CONFIG_APP_ID, payload.app_id.strip())
        store.set_config(CONFIG_ENABLED, "1" if payload.enabled else "0")
        if payload.app_secret.strip():
            store.set_config(CONFIG_APP_SECRET, payload.app_secret.strip())
        return {"ok": True}

    @app.post("/api/attendance/feishu/sync")
    async def sync_feishu_now(request: Request):
        require_account(request, store, write=True)
        if not sync_engine:
            raise HTTPException(status_code=503, detail="同步引擎未初始化")
        return sync_engine.sync_once()

    @app.get("/api/attendance/feishu/status")
    async def feishu_status(request: Request):
        require_account(request, store)
        return {
            "running": bool(sync_engine and sync_engine.running),
            "last_error": sync_engine.last_error if sync_engine else "",
        }


def _set_employee_tags(store: AttendanceStore, employee_id: int, tag_ids: list[int]) -> None:
    store.execute("DELETE FROM employee_tags WHERE employee_id = ?", (employee_id,))
    for tag_id in tag_ids:
        store.execute(
            "INSERT OR IGNORE INTO employee_tags (employee_id, tag_id) VALUES (?, ?)",
            (employee_id, tag_id),
        )
