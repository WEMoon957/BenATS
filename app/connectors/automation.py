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
from .outreach import (
    BOSS_PLATFORM,
    ZHAOPIN_PLATFORM,
    OutreachAction,
    OutreachStore,
    execute_outreach,
)
from .zhaopin_cli import ZhaopinCliConnector, ZhaopinCliError
from ..config import AppSettings
from ..repository import JobRepository
from ..pipeline import EvaluationEngine

logger = logging.getLogger(__name__)

# 下载失败达到该轮数后生成一次「求简历」触达草稿
RESUME_REQUEST_FAILURE_THRESHOLD = 3

# 对主动发来消息的候选人，回复并附带求简历的默认话术
INBOUND_REPLY_TEXT = "您好，方便发一份您的简历吗？"

# 简历类附件后缀：未读消息里对方发来的附件简历显示为文件名，用于判断对方是否已发简历
RESUME_FILE_SUFFIXES = (".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png", ".zip")

# 附件简历已到手的候选人状态：无需再次向对方索要简历
RESUME_RECEIVED_STATUSES = frozenset({"resume_ready", "resume_downloaded"})


def _is_target_job(name: str, target: str) -> bool:
    """判断职位是否为目标岗位；目标岗位为空时视为全部处理。"""
    return not target or name.strip() == target or target in name


def _is_resume_card(message: str) -> bool:
    """对方发来的「请求发送附件简历」确认卡片（需先同意接收）。"""
    return "附件简历" in message


def _is_resume_file(message: str) -> bool:
    """对方发来的附件简历本身：聊天列表里显示为文件名（如「张三简历.pdf」）。"""
    return message.strip().lower().endswith(RESUME_FILE_SUFFIXES)


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
        if not isinstance(self._data.get("available_positions"), dict):
            self._data["available_positions"] = {}

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    def has_position(self, name: str) -> bool:
        return name in self._data["positions"]

    def mark_position(self, name: str, job_id: str, platform: str = BOSS_PLATFORM) -> None:
        with self._lock:
            self._data["positions"][name] = {
                "job_id": job_id,
                "platform": platform,
                "s_call_synced": False,
            }
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

    def remember_available(self, names: list[str], platform: str = BOSS_PLATFORM) -> None:
        """记录最近一次拉到的全部职位名（按平台分存），供界面选择目标岗位。"""
        with self._lock:
            by_platform = self._data["available_positions"]
            by_platform[platform] = sorted({name for name in names if name})
            self._save()

    def list_available(self, platform: str = BOSS_PLATFORM) -> list[str]:
        """返回指定平台最近一次拉到的职位名。"""
        return list(self._data["available_positions"].get(platform, []))

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
        settings_store=None,
        zhaopin: ZhaopinCliConnector | None = None,
        *,
        interval_seconds: int = 300,
    ) -> None:
        self.repository = repository
        self.engine = engine
        self.boss = boss
        self.zhaopin = zhaopin
        self.outreaches = outreaches
        self.store = store
        self.call_repository = call_repository
        self.recruitment_store = recruitment_store
        self.settings_store = settings_store
        self.interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # 当前轮次的取消标记：与 _stop 分开，因为「立即运行一轮」在引擎停止时仍应完整执行一轮
        self._abort_round = threading.Event()
        # 同一时刻只允许一轮：后台轮询与「运行一轮」并发会同时驱动浏览器，互相抢会话锁
        self._round_lock = threading.Lock()

    @property
    def stopping(self) -> bool:
        """当前轮次是否已被要求取消；轮内各步骤据此在边界处收尾，停止后不再启动新步骤。"""
        return self._abort_round.is_set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._abort_round.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="boss-automation")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._abort_round.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def target_job(self) -> str:
        """当前生效的 BOSS 目标岗位：每轮实时读取设置项，未配置时回退到环境变量与默认岗位。"""
        if self.settings_store is None:
            return AppSettings().effective_boss_target_job
        return self.settings_store.load().effective_boss_target_job

    def zhaopin_target_job(self) -> str:
        """当前生效的智联目标岗位：每轮实时读取设置项，未配置时回退到环境变量。"""
        if self.settings_store is None:
            return AppSettings().effective_zhaopin_target_job
        return self.settings_store.load().effective_zhaopin_target_job

    def _connector_for(self, platform: str):
        """按平台返回对应的 CLI 连接器；智联未配置时返回 None。"""
        if platform == ZHAOPIN_PLATFORM:
            return self.zhaopin
        return self.boss

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("自动化引擎一轮执行失败")
            self._stop.wait(self.interval)

    def run_once(self, *, manual: bool = False) -> dict:
        if not self._round_lock.acquire(blocking=False):
            logger.info("已有自动化轮次在执行，本次跳过")
            return {"skipped": "busy"}
        if manual:
            # 手动触发的一轮是显式意图：清掉此前的取消标记，让它完整执行
            self._abort_round.clear()
        try:
            def step(fn):
                # 每个步骤都会驱动本机浏览器。停止请求到达后不再启动新步骤；
                # 只靠方法内部的循环检查会让后续步骤继续逐个驱动浏览器。
                return 0 if self.stopping else fn()

            summary = {
                "positions_created": step(self.sync_positions),
                "outreaches_created": step(self.sync_candidates),
                "zhaopin_positions_created": step(self.sync_zhaopin_positions),
                "zhaopin_outreaches_created": step(self.sync_zhaopin_candidates),
                "inbound_created": step(self.sync_inbound),
                "resume_requests_accepted": step(self.auto_accept_resumes),
                "outreaches_reconciled": step(self.sync_outreach_results),
                "resumes_downloaded": step(self.sync_resumes),
                "candidates_scored": step(self.sync_scoring),
                "jobs_started": step(self.start_ready_jobs),
                "s_calls_synced": step(self.sync_s_calls),
            }
        finally:
            self._round_lock.release()
        return summary

    def sync_scoring(self) -> int:
        """简历到手后自动评分：按岗位专属加权标准逐份打分，结果写入候选人。"""
        if self.recruitment_store is None or self.settings_store is None:
            return 0
        from ..recruitment.services import auto_score_candidates

        try:
            result = auto_score_candidates(
                self.recruitment_store, self.repository, self.settings_store, self.boss
            )
            if not result.get("ok"):
                logger.warning("自动评分未执行：%s", result.get("detail") or "未知原因")
                return 0
            return int(result.get("scored", 0))
        except Exception as exc:  # noqa: BLE001
            logger.warning("自动评分一轮失败：%s", exc)
            return 0

    def sync_positions(self) -> int:
        """拉取职位列表，为未处理过的职位创建 Talent Hub 任务并写入 JD。"""
        created = 0
        try:
            positions = self.boss.list_positions()
        except BossCliError as exc:
            logger.warning("拉取职位失败：%s", exc)
            return 0
        target = self.target_job()
        self.store.remember_available([p["name"] for p in positions])
        for position in positions:
            if self.stopping:
                break
            name = position["name"]
            if not name or self.store.has_position(name):
                continue
            if not _is_target_job(name, target):
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
        """拉取目标岗位的推荐候选人，写入候选人库并生成打招呼草稿（待 HR 审核）。"""
        created = 0
        target = self.target_job()
        for name, info in self.store.list_positions().items():
            if self.stopping:
                break
            if not _is_target_job(name, target):
                continue
            job_id = info["job_id"]
            try:
                candidates = self.boss.list_recommend(name)
            except BossCliError as exc:
                logger.warning("拉取职位「%s」候选人失败：%s", name, exc)
                continue
            for candidate in candidates:
                candidate_name = candidate["name"]
                if not candidate_name:
                    continue
                status = self.store.candidate_status(job_id, candidate_name)
                if status is not None and status != "discovered":
                    continue
                self._upsert_candidate(candidate_name, name, job_id)
                self.outreaches.create_unique(
                    action=OutreachAction(kind="greet", target=candidate_name, job_keyword=name),
                )
                self.store.mark_candidate(job_id, candidate_name, "outreach_pending")
                created += 1
        return created

    def sync_zhaopin_positions(self) -> int:
        """登记智联职位列表里的岗位。

        智联当前没有 JD 命令，因此只记录岗位名与平台归属；
        候选人与草稿以岗位名为关键字关联，不创建 Talent Hub 任务。
        """
        if self.zhaopin is None:
            return 0
        try:
            positions = self.zhaopin.list_positions()
        except ZhaopinCliError as exc:
            logger.warning("拉取智联职位失败：%s", exc)
            return 0
        names = [item["name"] for item in positions if item.get("name")]
        self.store.remember_available(names, ZHAOPIN_PLATFORM)
        created = 0
        for name in names:
            if self.stopping:
                break
            if self.store.has_position(name):
                continue
            # 智联没有 JD 任务，用一个稳定且可区分平台的编号，供候选人状态做键
            self.store.mark_position(name, f"{ZHAOPIN_PLATFORM}:{name}", ZHAOPIN_PLATFORM)
            created += 1
        return created

    def sync_zhaopin_candidates(self) -> int:
        """拉取智联目标岗位的推荐候选人，写入候选人库并生成打招呼草稿（待 HR 审核）。"""
        if self.zhaopin is None:
            return 0
        created = 0
        target = self.zhaopin_target_job()
        for name, info in self.store.list_positions().items():
            if self.stopping:
                break
            if info.get("platform") != ZHAOPIN_PLATFORM:
                continue
            if not _is_target_job(name, target):
                continue
            job_id = info.get("job_id", "")
            try:
                candidates = self.zhaopin.list_recommend(name)
            except ZhaopinCliError as exc:
                logger.warning("拉取智联职位「%s」候选人失败：%s", name, exc)
                continue
            for candidate in candidates:
                candidate_name = candidate["name"]
                if not candidate_name:
                    continue
                status = self.store.candidate_status(job_id, candidate_name)
                if status is not None and status != "discovered":
                    continue
                self._upsert_candidate(
                    candidate_name, name, job_id, platform=ZHAOPIN_PLATFORM
                )
                self.outreaches.create_unique(
                    action=OutreachAction(
                        kind="greet",
                        target=candidate_name,
                        job_keyword=name,
                        platform=ZHAOPIN_PLATFORM,
                    ),
                )
                self.store.mark_candidate(job_id, candidate_name, "outreach_pending")
                created += 1
        return created

    def _upsert_candidate(
        self,
        name: str,
        job_keyword: str,
        job_id: str,
        stage: str = "discovered",
        platform: str = BOSS_PLATFORM,
    ) -> None:
        """把候选人写入候选人库（同岗位同名去重，已存在则跳过），无 recruitment_store 时跳过。"""
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
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, job_keyword, job_id, platform, stage, _now(), _now()),
        )

    def _set_candidate_stage(
        self,
        name: str,
        job_keyword: str,
        job_id: str,
        stage: str,
        resume_file: str | None = None,
        platform: str = BOSS_PLATFORM,
    ) -> None:
        """把候选人阶段推进到指定值（不存在则新增），可同时登记附件简历文件名。"""
        if self.recruitment_store is None:
            return
        from ..recruitment.db import _now

        existing = self.recruitment_store.query_one(
            "SELECT id FROM candidate WHERE name = ? AND job_keyword = ?", (name, job_keyword)
        )
        now = _now()
        if existing:
            if resume_file is None:
                self.recruitment_store.execute(
                    "UPDATE candidate SET stage = ?, updated_at = ? WHERE id = ?",
                    (stage, now, existing["id"]),
                )
            else:
                self.recruitment_store.execute(
                    "UPDATE candidate SET stage = ?, resume_file = ?, updated_at = ? WHERE id = ?",
                    (stage, resume_file, now, existing["id"]),
                )
        else:
            self.recruitment_store.execute(
                "INSERT INTO candidate (name, job_keyword, job_id, source, stage, resume_file, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (name, job_keyword, job_id, platform, stage, resume_file or "", now, now),
            )

    def sync_inbound(self) -> int:
        """读取未读沟通列表（对方主动发来消息的人），只处理目标岗位，按消息类型生成草稿：

        - 附件简历确认卡片 → 「同意接收」草稿（kind=action, command=agree-resume）；
        - 对方已发来的附件简历 → 标记待下载，不发送任何消息；
        - 其他消息 → 「回复+求简历」草稿（kind=send, request_resume=True）。
        """
        created = 0
        try:
            contacts = self.boss.list_unread_contacts()
        except BossCliError as exc:
            logger.warning("读取未读候选人失败：%s", exc)
            return 0
        target = self.target_job()
        positions = {
            name: info
            for name, info in self.store.list_positions().items()
            if name and _is_target_job(name, target)
        }
        for contact in contacts:
            contact_name = contact.get("name", "")
            if not contact_name:
                continue
            matched = _match_position((contact.get("job") or "").strip(), positions)
            if not matched and target:
                # 已选择目标岗位时，其他岗位的往来消息不进入流程
                continue
            job_id = positions[matched]["job_id"] if matched else ""
            keyword = matched or ""
            message = (contact.get("message") or "").strip()
            status = self.store.candidate_status(job_id, contact_name)
            # 对方主动联系，自动写入候选人库（待打招呼）
            self._upsert_candidate(contact_name, keyword, job_id, stage="greeting_pending")
            if _is_resume_card(message):
                if status is not None:
                    continue
                self.outreaches.create_unique(
                    action=OutreachAction(
                        kind="action",
                        command="agree-resume",
                        target=contact_name,
                        job_keyword=keyword,
                    ),
                )
                self.store.mark_candidate(job_id, contact_name, "agree_pending")
                created += 1
                continue
            if _is_resume_file(message):
                if status in RESUME_RECEIVED_STATUSES:
                    continue
                self._cancel_resume_requests(contact_name)
                self.store.mark_candidate(job_id, contact_name, "resume_ready", source_file=message)
                created += 1
                continue
            if status is not None:
                continue
            self.outreaches.create_unique(
                action=OutreachAction(
                    kind="send",
                    target=contact_name,
                    text=INBOUND_REPLY_TEXT,
                    request_resume=True,
                    job_keyword=keyword,
                ),
            )
            self.store.mark_candidate(job_id, contact_name, "inbound_pending")
            created += 1
        return created

    def _cancel_resume_requests(self, name: str) -> None:
        """对方已发来简历时，作废该候选人尚未发送的求简历草稿，避免重复索要。"""
        for record in self.outreaches.list_records(archived=False):
            if record.get("status") != "pending":
                continue
            action = record.get("action", {})
            if action.get("target") == name and action.get("request_resume"):
                self.outreaches.update(
                    record["id"], status="rejected", result="对方已发来附件简历，自动作废",
                )

    def auto_accept_resumes(self) -> int:
        """自动执行「同意接收附件简历」草稿。

        同意接收只是接受对方发来的文件，不向候选人发送任何消息，属于非对外触达，
        因此无需 HR 审核，检测到后即自动执行；下载仍在后续轮次由 ``sync_resumes`` 完成。
        """
        executed = 0
        for record in self.outreaches.list_records(archived=False):
            if self.stopping:
                break
            if record.get("status") != "pending":
                continue
            action = record.get("action", {})
            if action.get("kind") != "action" or action.get("command") != "agree-resume":
                continue
            connector = self._connector_for(action.get("platform", BOSS_PLATFORM))
            if connector is None:
                continue
            try:
                result = execute_outreach(connector, OutreachAction(**action))
            except (BossCliError, ZhaopinCliError) as exc:
                self.outreaches.update(record["id"], status="failed", error=str(exc))
            else:
                self.outreaches.update(record["id"], status="sent", result=result, error="")
                executed += 1
        return executed

    def sync_outreach_results(self) -> int:
        """把已审核并发送的触达回写候选人状态，并同步候选人库阶段为已打招呼。"""
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
            if not self._should_reconcile(action, self.store.candidate_status(job_id, target)):
                continue
            self.store.mark_candidate(job_id, target, "outreached")
            self._set_candidate_stage(
                target, job_keyword, job_id, "greeted",
                platform=info.get("platform", BOSS_PLATFORM),
            )
            reconciled += 1
        return reconciled

    @staticmethod
    def _should_reconcile(action: dict, status: str | None) -> bool:
        """判断已发送草稿是否应把候选人回写到已触达。"""
        if status == "outreach_pending":
            return True
        kind = action.get("kind")
        command = action.get("command", "")
        if kind == "action" and command == "request-attachment-resume":
            return status == "resume_requested"
        if kind == "action" and command == "agree-resume":
            return status == "agree_pending"
        if kind == "send":
            return status == "inbound_pending"
        return False

    def sync_resumes(self) -> int:
        """下载候选人的附件简历，成功则写入任务并推进候选人阶段。

        覆盖两类候选人：已触达（outreached）的，以及对方已发来简历（resume_ready）的。
        """
        downloaded = 0
        positions = self.store.list_positions()
        positions_by_job = {
            info["job_id"]: name for name, info in positions.items() if info.get("job_id")
        }
        platforms_by_job = {
            info["job_id"]: info.get("platform", BOSS_PLATFORM) for info in positions.values()
        }
        pending = [
            *self.store.candidates_by_status("outreached"),
            *self.store.candidates_by_status("resume_ready"),
        ]
        for candidate in pending:
            if self.stopping:
                break
            job_id = candidate["job_id"]
            name = candidate["name"]
            job_keyword = positions_by_job.get(job_id, "")
            if platforms_by_job.get(job_id) == ZHAOPIN_PLATFORM:
                # 智联没有下载附件简历的命令，直接生成一条「索要附件简历」草稿交给 HR 审核
                self.outreaches.create_unique(
                    action=OutreachAction(
                        kind="action",
                        command="request-attachment-resume",
                        target=name,
                        job_keyword=job_keyword,
                        platform=ZHAOPIN_PLATFORM,
                    ),
                )
                self.store.mark_candidate(job_id, name, "resume_requested")
                continue
            stored = self._download_resume_to_job(name, job_id)
            if stored:
                self.store.mark_candidate(job_id, name, "resume_downloaded")
                self._set_candidate_stage(
                    name, job_keyword, job_id, "resume_received", resume_file=stored,
                )
                downloaded += 1
                continue
            failures = self.store.increment_download_failures(job_id, name)
            if failures >= RESUME_REQUEST_FAILURE_THRESHOLD:
                self.outreaches.create_unique(
                    action=OutreachAction(
                        kind="action",
                        command="request-attachment-resume",
                        target=name,
                        job_keyword=job_keyword,
                    ),
                )
                self.store.mark_candidate(job_id, name, "resume_requested")
                logger.info("候选人「%s」连续 %s 轮未下载到简历，已生成求简历草稿", name, failures)

        # 候选人库中已打招呼（greeted）的候选人，自动下载附件简历
        if self.recruitment_store:
            greeted = self.recruitment_store.query(
                "SELECT * FROM candidate WHERE stage = 'greeted' AND job_id != ''"
            )
            for c in greeted:
                if self.stopping:
                    break
                if platforms_by_job.get(c["job_id"]) == ZHAOPIN_PLATFORM:
                    continue
                stored = self._download_resume_to_job(c["name"], c["job_id"])
                if stored:
                    self._set_candidate_stage(
                        c["name"], c["job_keyword"], c["job_id"], "resume_received", resume_file=stored,
                    )
                    downloaded += 1
        return downloaded

    def _download_resume_to_job(self, name: str, job_id: str) -> str | None:
        """下载候选人附件简历并写入对应任务，成功返回写入的文件名。"""
        try:
            source = self.boss.download_resume(name)
        except BossCliError:
            return None
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
            return reserved
        except (FileNotFoundError, ValueError) as exc:
            logger.warning("写入候选人「%s」简历失败：%s", name, exc)
            return None

    def start_ready_jobs(self) -> int:
        """对已有简历、尚未运行的任务启动筛选。"""
        started = 0
        for info in self.store.list_positions().values():
            job_id = info.get("job_id", "")
            if not job_id:
                continue
            try:
                job = self.repository.get(job_id)
            except (FileNotFoundError, ValueError):
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
            job_id = info.get("job_id", "")
            if not job_id:
                continue
            try:
                job = self.repository.get(job_id)
            except (FileNotFoundError, ValueError):
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