"""候选人预评分服务：拿到简历后，用岗位 JD 与附件简历文本快速判断匹配档位。

预评分是粗筛：评分合格的进入筛选，不合格的标记淘汰。
数据源为已下载的附件简历（PDF/DOCX 等），不再使用在线简历预览。
"""

from __future__ import annotations

import logging

from ..llm import LLMError, OpenAICompatibleClient
from ..pipeline import extract_document
from ..rubric.models import WeightedRubric
from ..rubric.service import generate_rubric, score_candidate
from .db import (
    GREETABLE_SCORES,
    STAGE_REJECTED,
    STAGE_SCREENING,
    RecruitmentStore,
    _now,
)
from .scoring import pre_score

logger = logging.getLogger(__name__)


def _get_jd(candidate: dict, repository, boss) -> str:
    job_id = candidate.get("job_id") or ""
    if job_id:
        try:
            job = repository.get(job_id)
            jd_file = job.get("jd_file") or ""
            if jd_file:
                path = repository.job_dir(job_id) / "jd" / jd_file
                if path.is_file():
                    return path.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    keyword = candidate.get("job_keyword") or ""
    if keyword:
        try:
            return boss.fetch_jd(keyword)
        except Exception:  # noqa: BLE001
            pass
    return ""


def _get_resume_text(candidate: dict, repository, settings) -> str:
    """从任务目录读取已下载的附件简历并解析为文本。"""
    job_id = candidate.get("job_id") or ""
    if not job_id:
        return ""
    try:
        job = repository.get(job_id)
    except FileNotFoundError:
        return ""
    resume_files = job.get("resume_files") or []
    if not resume_files:
        return ""
    try:
        path = repository.resume_path(job_id, resume_files[-1])
        result = extract_document(path, settings, resume=True)
        return (result.get("text") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def score_candidates(store: RecruitmentStore, boss, repository, settings_store, candidate_ids=None) -> dict:
    if candidate_ids:
        candidates = [
            store.query_one("SELECT * FROM candidate WHERE id = ?", (cid,))
            for cid in candidate_ids
        ]
        candidates = [c for c in candidates if c]
    else:
        candidates = store.query(
            "SELECT * FROM candidate WHERE stage = 'resume_received' ORDER BY created_at"
        )

    settings = settings_store.load()
    if not settings.is_ready:
        return {"ok": False, "detail": "模型配置不完整，请先配置模型 API。"}

    scored = 0
    passed = 0
    rejected = 0
    failed = 0
    with OpenAICompatibleClient(settings) as client:
        for candidate in candidates:
            try:
                jd_text = _get_jd(candidate, repository, boss)
                resume_text = _get_resume_text(candidate, repository, settings)
                result = pre_score(client, jd_text, resume_text)
                if result.conclusion in GREETABLE_SCORES:
                    stage = STAGE_SCREENING
                    passed += 1
                else:
                    stage = STAGE_REJECTED
                    rejected += 1
                store.execute(
                    "UPDATE candidate SET pre_score = ?, pre_score_reason = ?, pre_scored_at = ?, "
                    "stage = ?, updated_at = ? WHERE id = ?",
                    (result.conclusion, result.reason, _now(), stage, _now(), candidate["id"]),
                )
                scored += 1
            except LLMError as exc:
                failed += 1
                logger.warning("候选人「%s」预评分失败：%s", candidate.get("name"), exc)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.warning("候选人「%s」读取简历或评分失败：%s", candidate.get("name"), exc)

    return {
        "ok": True,
        "total": len(candidates),
        "scored": scored,
        "passed": passed,
        "rejected": rejected,
        "failed": failed,
    }


def _get_or_generate_rubric(store, client, job_keyword: str, sample_candidate, repository, boss):
    """获取岗位专属加权评分标准：优先读缓存，缺失则 AI 生成并缓存。"""
    import json

    cached = store.get_rubric(job_keyword)
    if cached:
        try:
            return WeightedRubric.model_validate(json.loads(cached))
        except Exception:  # noqa: BLE001
            pass
    jd_text = _get_jd(sample_candidate, repository, boss) if sample_candidate else ""
    if not jd_text.strip():
        return None
    rubric = generate_rubric(client, jd_text)
    store.set_rubric(job_keyword, json.dumps(rubric.model_dump(), ensure_ascii=False))
    return rubric


def _stage_from_grade(grade: str) -> str:
    if grade in {"S", "A", "B"}:
        return STAGE_SCREENING
    return STAGE_REJECTED


def auto_score_candidates(store, repository, settings_store, boss=None, candidate_ids=None) -> dict:
    """简历到手后自动评分：按岗位专属加权标准逐份打分，结果写入候选人。"""
    if candidate_ids:
        candidates = [store.query_one("SELECT * FROM candidate WHERE id = ?", (cid,)) for cid in candidate_ids]
        candidates = [c for c in candidates if c]
    else:
        candidates = store.query("SELECT * FROM candidate WHERE stage = 'resume_received'")

    if not candidates:
        return {"ok": True, "total": 0, "scored": 0, "failed": 0}

    settings = settings_store.load()
    if not settings.is_ready:
        return {"ok": False, "detail": "模型配置不完整，请先配置模型 API。"}

    by_job: dict[str, list] = {}
    for c in candidates:
        key = c.get("job_keyword") or c.get("job_id") or ""
        by_job.setdefault(key, []).append(c)

    scored = 0
    failed = 0
    with OpenAICompatibleClient(settings) as client:
        for job_keyword, group in by_job.items():
            rubric = _get_or_generate_rubric(store, client, job_keyword, group[0], repository, boss)
            if rubric is None:
                failed += len(group)
                continue
            for c in group:
                try:
                    resume_text = _get_resume_text(c, repository, settings)
                    result = score_candidate(client, rubric, resume_text, c["name"])
                    stage = _stage_from_grade(result.grade)
                    import json

                    store.execute(
                        "UPDATE candidate SET pre_score = ?, pre_score_reason = ?, pre_scored_at = ?, "
                        "score_detail = ?, stage = ?, updated_at = ? WHERE id = ?",
                        (result.grade, result.highlight, _now(), json.dumps(result.model_dump(), ensure_ascii=False), stage, _now(), c["id"]),
                    )
                    scored += 1
                except LLMError as exc:
                    failed += 1
                    logger.warning("候选人「%s」自动评分失败：%s", c.get("name"), exc)
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    logger.warning("候选人「%s」读取简历或评分失败：%s", c.get("name"), exc)

    return {"ok": True, "total": len(candidates), "scored": scored, "failed": failed}
