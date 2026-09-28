"""候选人管理 API：列表、增删改、阶段推进、批量打招呼。"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .db import (
    STAGES,
    STAGE_GREETED,
    STAGE_GREETING_PENDING,
    STAGE_LABELS,
    RecruitmentStore,
    _now,
)


class CandidateInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    job_keyword: str = Field(default="", max_length=200)
    source: str = Field(default="boss", max_length=40)
    phone: str = Field(default="", max_length=30)
    note: str = Field(default="", max_length=2000)


class CandidatePatchInput(BaseModel):
    stage: str | None = None
    job_keyword: str | None = None
    phone: str | None = None
    note: str | None = None


class GreetInput(BaseModel):
    ids: list[int] = Field(min_length=1)


def candidate_payload(row: dict) -> dict:
    return {
        **row,
        "stage_label": STAGE_LABELS.get(row["stage"], row["stage"]),
    }


def register_routes(app: FastAPI, store: RecruitmentStore) -> None:
    @app.get("/api/recruitment/stages")
    async def list_stages():
        return {"stages": [{"value": s, "label": STAGE_LABELS[s]} for s in STAGES]}

    @app.get("/api/recruitment/candidates")
    async def list_candidates(
        stage: str | None = Query(default=None),
        job: str | None = Query(default=None),
        q: str = Query(default=""),
        limit: int = Query(default=200, ge=1, le=1000),
    ):
        sql = "SELECT * FROM candidate WHERE 1=1"
        params: list = []
        if stage:
            sql += " AND stage = ?"
            params.append(stage)
        if job:
            sql += " AND job_keyword = ?"
            params.append(job)
        if q:
            sql += " AND (name LIKE ? OR job_keyword LIKE ?)"
            like = f"%{q}%"
            params += [like, like]
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = store.query(sql, tuple(params))
        return {"candidates": [candidate_payload(row) for row in rows]}

    @app.post("/api/recruitment/candidates")
    async def create_candidate(payload: CandidateInput):
        try:
            candidate_id = store.execute(
                "INSERT INTO candidate (name, job_keyword, source, stage, phone, note, created_at, updated_at) "
                "VALUES (?, ?, ?, 'discovered', ?, ?, ?, ?)",
                (payload.name, payload.job_keyword, payload.source, payload.phone, payload.note, _now(), _now()),
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail=f"候选人可能已存在：{exc}") from exc
        row = store.query_one("SELECT * FROM candidate WHERE id = ?", (candidate_id,))
        return candidate_payload(row)

    @app.patch("/api/recruitment/candidates/{candidate_id}")
    async def update_candidate(candidate_id: int, payload: CandidatePatchInput):
        row = store.query_one("SELECT * FROM candidate WHERE id = ?", (candidate_id,))
        if not row:
            raise HTTPException(status_code=404, detail="候选人不存在")
        updates = payload.model_dump(exclude_none=True)
        if "stage" in updates and updates["stage"] not in STAGES:
            raise HTTPException(status_code=400, detail="无效的候选人阶段")
        if updates:
            assignments = ", ".join(f"{key} = ?" for key in updates)
            store.execute(
                f"UPDATE candidate SET {assignments}, updated_at = ? WHERE id = ?",
                (*updates.values(), _now(), candidate_id),
            )
        row = store.query_one("SELECT * FROM candidate WHERE id = ?", (candidate_id,))
        return candidate_payload(row)

    @app.delete("/api/recruitment/candidates/{candidate_id}")
    async def delete_candidate(candidate_id: int):
        store.execute("DELETE FROM candidate WHERE id = ?", (candidate_id,))
        return {"ok": True}

    @app.post("/api/recruitment/candidates/greet")
    async def greet_candidates(payload: GreetInput):
        """把勾选的待打招呼候选人标记为已打招呼（实际 boss 动作由自动化引擎执行）。"""
        greeted = 0
        for candidate_id in payload.ids:
            row = store.query_one("SELECT * FROM candidate WHERE id = ?", (candidate_id,))
            if not row or row["stage"] != STAGE_GREETING_PENDING:
                continue
            store.execute(
                "UPDATE candidate SET stage = ?, updated_at = ? WHERE id = ?",
                (STAGE_GREETED, _now(), candidate_id),
            )
            greeted += 1
        return {"greeted": greeted}
