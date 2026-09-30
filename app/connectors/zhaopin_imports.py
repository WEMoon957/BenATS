"""从智联招聘导入候选人与在线简历到 Talent Hub 任务的编排。

与 BOSS 直聘的导入不同，智联候选人拿到的是页面上的在线简历正文，
这里把它按 Markdown 文本落盘为简历文件，交给 Talent Hub 既有的筛选流水线处理。
"""

from __future__ import annotations

import hashlib

from .zhaopin_cli import ZhaopinCliConnector, ZhaopinCliError
from ..repository import JobRepository


def _fingerprint_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def import_candidates(
    repository: JobRepository,
    connector: ZhaopinCliConnector,
    position_name: str,
    *,
    job_keyword: str | None = None,
    jd_text: str = "",
    limit: int = 20,
) -> dict:
    """读取智联推荐候选人，把在线简历写入一个新的 Talent Hub 任务。

    - ``position_name``：任务标题，同时也是切换岗位的关键词。
    - ``jd_text``：岗位说明；留空时只导入候选人，JD 可在任务里补填。
    - ``limit``：最多导入的候选人数。读取一位候选人就要打开一次详情页，数量越大耗时越长。

    返回 ``{"job_id", "jd_file", "candidates_total", "resumes_imported", "errors"}``。
    单个候选人的简历读取失败只记入 ``errors``，不阻塞其余候选人。
    """
    candidates = connector.list_recommend(job_keyword or position_name)

    job = repository.create(title=position_name)
    job_id = job["id"]

    jd_file = ""
    if jd_text.strip():
        jd_file, jd_path = repository.reserve_upload(job_id, "jd", "岗位JD.txt")
        jd_path.write_text(jd_text, encoding="utf-8")
        repository.update(job_id, jd_file=jd_file, stage="JD 已就绪")

    resume_files: list[str] = []
    resume_hashes: dict[str, str] = {}
    errors: list[str] = []

    for candidate in candidates[: max(0, limit)]:
        name = candidate["name"]
        try:
            detail = connector.open_detail(name)
        except ZhaopinCliError as exc:
            errors.append(f"{name}：{exc}")
            continue

        reserved, target = repository.reserve_upload(job_id, "resumes", f"{name}.md")
        target.write_text(detail, encoding="utf-8")
        resume_hashes[reserved] = _fingerprint_bytes(target.read_bytes())
        resume_files.append(reserved)

    repository.update(
        job_id,
        resume_files=resume_files,
        resume_hashes=resume_hashes,
        total=len(resume_files),
        stage=f"已导入 {len(resume_files)} 份在线简历",
    )

    return {
        "job_id": job_id,
        "jd_file": jd_file,
        "candidates_total": len(candidates),
        "resumes_imported": len(resume_files),
        "errors": errors,
    }
