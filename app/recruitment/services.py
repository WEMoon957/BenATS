"""候选人预评分服务：拉取在线简历 + JD，调用模型评分并写回候选人。

预评分是粗筛，评分合格的进入「待打招呼」清单，不合格的标记跳过。
"""

from __future__ import annotations

import logging

from ..llm import LLMError, OpenAICompatibleClient
from .db import (
    GREETABLE_SCORES,
    STAGE_GREETING_PENDING,
    STAGE_SCORED,
    STAGE_SKIPPED,
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


def score_candidates(store: RecruitmentStore, boss, repository, settings_store, candidate_ids=None) -> dict:
    if candidate_ids:
        candidates = [
            store.query_one("SELECT * FROM candidate WHERE id = ?", (cid,))
            for cid in candidate_ids
        ]
        candidates = [c for c in candidates if c]
    else:
        candidates = store.query(
            "SELECT * FROM candidate WHERE stage = 'discovered' ORDER BY created_at"
        )

    settings = settings_store.load()
    if not settings.is_ready:
        return {"ok": False, "detail": "模型配置不完整，请先配置模型 API。"}

    scored = 0
    skipped = 0
    failed = 0
    with OpenAICompatibleClient(settings) as client:
        for candidate in candidates:
            try:
                jd_text = _get_jd(candidate, repository, boss)
                resume_text = boss.preview_resume(candidate["name"])
                result = pre_score(client, jd_text, resume_text)
                if result.conclusion in GREETABLE_SCORES:
                    stage = STAGE_GREETING_PENDING
                else:
                    stage = STAGE_SKIPPED
                    skipped += 1
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
                logger.warning("候选人「%s」拉取简历或评分失败：%s", candidate.get("name"), exc)

    return {"ok": True, "total": len(candidates), "scored": scored, "skipped": skipped, "failed": failed}
