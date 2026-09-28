"""BOSS 直聘接入的自动化引擎。

后台定时轮询，自动推进「拉职位 → 拉候选人 → 生成触达草稿（待 HR 审核）→
下载附件简历 → 启动筛选」。对外触达一律先生成待审核草稿，只有 HR 审核通过后才
真正调用 boss-cli 发送；已处理过的职位与候选人会被持久化记录，不重复走流程。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path

from .boss_cli import BossCliConnector, BossCliError
from .outreach import OutreachAction, OutreachStore
from ..repository import JobRepository
from ..pipeline import EvaluationEngine

logger = logging.getLogger(__name__)

# 下载失败达到该轮数后生成一次「求简历」触达草稿
RESUME_REQUEST_FAILURE_THRESHOLD = 3

# 对主动发来消息的候选人，回复并附带求简历的默认话术
INBOUND_REPLY_TEXT = "您好，方便发一份您的简历吗？"


def _fingerprint_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class AutomationStore:
    """持久化自动化处理状态（进程内缓存 + JSON 落盘，锁内并发安全）。

    - ``positions``：职位名 → 已创建的任务 id（去重）。
    - ``candidates``：``<job_id>:<name>`` → 处理状态（去重 + 状态推进）。
    """

    def __init__(self, root: Path) -> None:
        self._path = root / "automation.json"
        self._lock = threading.RLock()
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._data = {}
        if not isinstance(self._data, dict):
            self._data = {}
        self._data.setdefault("positions", {})
        self._data.setdefault("candidates", {})

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    def has_position(self, name: str) -> bool:
        return name in self._data["positions"]

    def mark_position(self, name: str, job_id: str) -> None:
        with self._lock:
            self._data["positions"][name] = {"job_id": job_id, "s_call_synced": False}
            self._save()

    def position_s_calls_synced(self, name: str) -> bool:
        return bool(self._data["positions"].get(name, {}).get("s_call_synced"))

    def mark_position_s_calls_synced(self, name: str) -> None:
        with self._lock:
            info = self._data["positions"].setdefault(name, {})
            info["s_call_synced"] = True
            self._save()

    def list_positions(self) -> dict:
        return dict(self._data["positions"])

    @staticmethod
    def _candidate_key(job_id: str, name: str) -> str:
        return f"{job_id}:{name}"

    def candidate_status(self, job_id: str, name: str) -> str | None:
        record = self._data["candidates"].get(self._candidate_key(job_id, name))
        return record.get("status") if record else None

    def mark_candidate(self, job_id: str, name: str, status: str, **fields) -> None:
        with self._lock:
            record = {"status": status, **fields}
            self._data["candidates"][self._candidate_key(job_id, name)] = record
            self._save()

    def increment_download_failures(self, job_id: str, name: str) -> int:
        """下载失败计数 +1 并返回新计数；状态必须为 outreached 才计数。"""
        with self._lock:
            key = self._candidate_key(job_id, name)
            record = self._data["candidates"].get(key)
            if not record or record.get("status") != "outreached":
                return -1
            count = int(record.get("download_failures", 0)) + 1
            record["download_failures"] = count
            self._save()
            return count

    def candidates_by_status(self, status: str) -> list[dict]:
        result: list[dict] = []
        for key, value in self._data["candidates"].items():
            if value.get("status") != status:
                continue
            job_id, _, name = key.partition(":")
            result.append({"job_id": job_id, "name": name})
        return result


def _match_position(job_text: str, positions: dict) -> str | None:
    """把沟通列表中的意向岗位文本匹配到已处理的职位名；无法匹配返回 None。"""
    if not job_text:
        return None
    if job_text in positions:
        return job_text
    return next((name for name in positions if name in job_text or job_text in name), None)


class AutomationEngine:
    """后台轮询引擎：按固定间隔推进自动化流程。"""

    def __init__(
        self,
        repository: JobRepository,
        engine: EvaluationEngine,
        boss: BossCliConnector,
        outreaches: OutreachStore,
        store: AutomationStore,
        call_repository=None,
        recruitment_store=None,
        *,
        interval_seconds: int = 300,
    ) -> None:
        self.repository = repository
        self.engine = engine
        self.boss = boss
        self.outreaches = outreaches
        self.store = store
        self.call_repository = call_repository
        self.recruitment_store = recruitment_store
        self.interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="boss-automation")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("自动化引擎一轮执行失败")
            self._stop.wait(self.interval)

    def run_once(self) -> dict:
        summary = {
            "positions_created": self.sync_positions(),
            "outreaches_created": self.sync_candidates(),
            "inbound_created": self.sync_inbound(),
            "outreaches_reconciled": self.sync_outreach_results(),
            "resumes_downloaded": self.sync_resumes(),
            "jobs_started": self.start_ready_jobs(),
            "s_calls_synced": self.sync_s_calls(),
        }
        return summary

    def sync_positions(self) -> int:
        """拉取职位列表，为未处理过的职位创建 Talent Hub 任务并写入 JD。"""
        created = 0
        try:
            positions = self.boss.list_positions()
        except BossCliError as exc:
            logger.warning("拉取职位失败：%s", exc)
            return 0
        for position in positions:
            name = position["name"]
            if not name or self.store.has_position(name):
                continue
            try:
                jd_text = self.boss.fetch_jd(name)
                job = self.repository.create(title=name)
                reserved, jd_path = self.repository.reserve_upload(job["id"], "jd", "岗位JD.txt")
                jd_path.write_text(jd_text, encoding="utf-8")
                self.repository.update(job["id"], jd_file=reserved, stage="JD 已就绪")
                self.store.mark_position(name, job["id"])
                created += 1
            except BossCliError as exc:
                logger.warning("导入职位「%s」失败：%s", name, exc)
        return created

    def sync_candidates(self) -> int:
        """拉取每个职位的推荐候选人，写入候选人库（待预评分，不再直接生成打招呼草稿）。"""
        created = 0
        for name, info in self.store.list_positions().items():
            job_id = info["job_id"]
            try:
                candidates = self.boss.list_recommend(name)
            except BossCliError as exc:
                logger.warning("拉取职位「%s」候选人失败：%s", name, exc)
                continue
            for candidate in candidates:
                candidate_name = candidate["name"]
                if not candidate_name or self.store.candidate_status(job_id, candidate_name) is not None:
                    continue
                self._upsert_candidate(candidate_name, name, job_id)
                self.store.mark_candidate(job_id, candidate_name, "discovered")
                created += 1
        return created

    def _upsert_candidate(self, name: str, job_keyword: str, job_id: str) -> None:
        """把候选人写入候选人库（同岗位同名去重），无 recruitment_store 时跳过。"""
        if self.recruitment_store is None:
            return
        existing = self.recruitment_store.query_one(
            "SELECT id FROM candidate WHERE name = ? AND job_keyword = ?", (name, job_keyword)
        )
        if existing:
            return
        from ..recruitment.db import _now

        self.recruitment_store.execute(
            "INSERT INTO candidate (name, job_keyword, job_id, source, stage, created_at, updated_at) "
            "VALUES (?, ?, ?, 'boss', 'discovered', ?, ?)",
            (name, job_keyword, job_id, _now(), _now()),
        )

    def sync_inbound(self) -> int:
        """读取未读沟通列表（对方主动发来消息的人），生成两类草稿：

        - 消息为「请求发送附件简历」→ 「同意接收」草稿（kind=action, command=agree-resume）；
        - 其他消息 → 「回复+求简历」草稿（kind=send, request_resume=True）。
        """
        created = 0
        try:
            contacts = self.boss.list_unread_contacts()
        except BossCliError as exc:
            logger.warning("读取未读候选人失败：%s", exc)
            return 0
        positions = {name: info for name, info in self.store.list_positions().items() if name}
        for contact in contacts:
            contact_name = contact.get("name", "")
            if not contact_name:
                continue
            message = (contact.get("message") or "").strip()
            matched = _match_position((contact.get("job") or "").strip(), positions)
            job_id = positions[matched]["job_id"] if matched else ""
            if self.store.candidate_status(job_id, contact_name) is not None:
                continue
            if "附件简历" in message:
                self.outreaches.create(
                    action=OutreachAction(
                        kind="action",
                        command="agree-resume",
                        target=contact_name,
                        job_keyword=matched or "",
                    ),
                )
                self.store.mark_candidate(job_id, contact_name, "agree_pending")
                created += 1
                continue
            self.outreaches.create(
                action=OutreachAction(
                    kind="send",
                    target=contact_name,
                    text=INBOUND_REPLY_TEXT,
                    request_resume=True,
                    job_keyword=matched or "",
                ),
            )
            self.store.mark_candidate(job_id, contact_name, "inbound_pending")
            created += 1
        return created

    def sync_outreach_results(self) -> int:
        """把已审核并发送的触达回写候选人状态：打招呼 → 已触达；回复求简历 → 已触达；求简历 → 回到下载探测。"""
        reconciled = 0
        positions = self.store.list_positions()
        for record in self.outreaches.list_records(archived=False):
            if record.get("status") != "sent":
                continue
            action = record.get("action", {})
            target = action.get("target", "")
            job_keyword = action.get("job_keyword", "")
            info = positions.get(job_keyword)
            if not info or not target:
                continue
            job_id = info["job_id"]
            if (
                action.get("kind") == "action"
                and action.get("command") == "request-attachment-resume"
                and self.store.candidate_status(job_id, target) == "resume_requested"
            ):
                self.store.mark_candidate(job_id, target, "outreached")
                reconciled += 1
                continue
            if (
                action.get("kind") == "action"
                and action.get("command") == "agree-resume"
                and self.store.candidate_status(job_id, target) == "agree_pending"
            ):
                self.store.mark_candidate(job_id, target, "outreached")
                reconciled += 1
                continue
            if (
                action.get("kind") == "send"
                and self.store.candidate_status(job_id, target) == "inbound_pending"
            ):
                self.store.mark_candidate(job_id, target, "outreached")
                reconciled += 1
                continue
            if self.store.candidate_status(job_id, target) == "outreach_pending":
                self.store.mark_candidate(job_id, target, "outreached")
                reconciled += 1
        return reconciled

    def sync_resumes(self) -> int:
        """对已触达候选人尝试下载附件简历，成功则写入对应任务；连续失败达阈值后生成一次求简历草稿。"""
        downloaded = 0
        positions_by_job = {
            info["job_id"]: name for name, info in self.store.list_positions().items()
        }
        for candidate in self.store.candidates_by_status("outreached"):
            job_id = candidate["job_id"]
            name = candidate["name"]
            try:
                source = self.boss.download_resume(name)
            except BossCliError as exc:
                failures = self.store.increment_download_failures(job_id, name)
                if failures >= RESUME_REQUEST_FAILURE_THRESHOLD:
                    job_keyword = positions_by_job.get(job_id, "")
                    self.outreaches.create(
                        action=OutreachAction(
                            kind="action",
                            command="request-attachment-resume",
                            target=name,
                            job_keyword=job_keyword,
                        ),
                    )
                    self.store.mark_candidate(job_id, name, "resume_requested")
                    logger.info("候选人「%s」连续 %s 轮未下载到简历，已生成求简历草稿：%s", name, failures, exc)
                continue
            try:
                job = self.repository.get(job_id)
                reserved, target = self.repository.reserve_upload(job_id, "resumes", source.name)
                target.write_bytes(source.read_bytes())
                resume_hashes = dict(job.get("resume_hashes", {}))
                resume_hashes[reserved] = _fingerprint_bytes(target.read_bytes())
                files = [*job.get("resume_files", []), reserved]
                self.repository.update(
                    job_id, resume_files=files, resume_hashes=resume_hashes,
                    total=len(files), stage=f"已导入 {len(files)} 份附件简历",
                )
                self.store.mark_candidate(job_id, name, "resume_downloaded")
                downloaded += 1
            except (FileNotFoundError, ValueError) as exc:
                logger.warning("写入候选人「%s」简历失败：%s", name, exc)
        return downloaded

    def start_ready_jobs(self) -> int:
        """对已有简历、尚未运行的任务启动筛选。"""
        started = 0
        for info in self.store.list_positions().values():
            job_id = info["job_id"]
            try:
                job = self.repository.get(job_id)
            except FileNotFoundError:
                continue
            if job.get("status") in {"queued", "running"}:
                continue
            if not job.get("jd_file") or not job.get("resume_files"):
                continue
            if job.get("status") not in {"draft", "waiting"}:
                continue
            try:
                self.engine.start(job_id)
                started += 1
            except RuntimeError as exc:
                logger.warning("启动筛选失败 %s：%s", job_id, exc)
        return started

    def sync_s_calls(self) -> int:
        """筛选完成后，为 S 级候选人创建电话确认任务（含名单），每个任务只同步一次。"""
        if self.call_repository is None:
            return 0
        created = 0
        for name, info in self.store.list_positions().items():
            if self.store.position_s_calls_synced(name):
                continue
            job_id = info["job_id"]
            try:
                job = self.repository.get(job_id)
            except FileNotFoundError:
                continue
            if job.get("status") != "completed":
                continue
            results_path = self.repository.job_dir(job_id) / "评估结果.json"
            try:
                evaluations = json.loads(results_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            roster = [
                {
                    "candidate_name": item.get("candidate_name", ""),
                    "conclusion": item.get("conclusion", ""),
                    "one_line": item.get("one_line", ""),
                    "source_file": item.get("source_file", ""),
                }
                for item in evaluations
                if item.get("conclusion") == "S电话沟通"
            ]
            if roster:
                self.call_repository.create(job_title=name, job_id=job_id, roster=roster)
                created += 1
            self.store.mark_position_s_calls_synced(name)
        return created