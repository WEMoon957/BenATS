"""从 BOSS 直聘导入职位与候选人到 Talent Hub 任务的编排。"""

from __future__ import annotations

import hashlib

from .boss_cli import BossCliConnector, BossCliError
from ..repository import JobRepository


def _fingerprint_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def import_position(
    repository: JobRepository,
    connector: BossCliConnector,
    position_name: str,
    *,
    job_keyword: str | None = None,
) -> dict:
    """拉取职位 JD 与推荐候选人（及已可下载的附件简历），写入一个新的 Talent Hub 任务。

    返回 ``{"job_id", "jd_file", "candidates_total", "resumes_imported", "errors"}``。
    未附带附件简历的候选人会被记入 ``errors``，不阻塞其余候选人导入。
    """
    jd_text = connector.fetch_jd(position_name)
    candidates = connector.list_recommend(job_keyword or position_name)

    job = repository.create(title=position_name)
    job_id = job["id"]

    reserved_jd, jd_path = repository.reserve_upload(job_id, "jd", "岗位JD.txt")
    jd_path.write_text(jd_text, encoding="utf-8")
    repository.update(job_id, jd_file=reserved_jd, stage="JD 已就绪")

    resume_files: list[str] = []
    resume_hashes: dict[str, str] = {}
    errors: list[str] = []
    for candidate in candidates:
        name = candidate["name"]
        try:
            source = connector.download_resume(name)
            reserved, target = repository.reserve_upload(job_id, "resumes", source.name)
            target.write_bytes(source.read_bytes())
            resume_hashes[reserved] = _fingerprint_bytes(target.read_bytes())
            resume_files.append(reserved)
        except BossCliError as exc:
            errors.append(f"{name}：{exc}")

    repository.update(
        job_id,
        resume_files=resume_files,
        resume_hashes=resume_hashes,
        total=len(resume_files),
        stage=f"已导入 {len(resume_files)} 份附件简历",
    )

    return {
        "job_id": job_id,
        "jd_file": reserved_jd,
        "candidates_total": len(candidates),
        "resumes_imported": len(resume_files),
        "errors": errors,
    }