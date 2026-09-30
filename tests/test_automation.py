"""自动化引擎的单元测试：用假 boss 客户端与假引擎，验证状态推进与去重。"""

import json

import pytest

from app.config import AppSettings, SettingsStore
from app.connectors.automation import AutomationEngine, AutomationStore
from app.connectors.boss_cli import BossCliError
from app.connectors.outreach import (
    ZHAOPIN_PLATFORM,
    OutreachAction,
    OutreachStore,
    execute_outreach,
)
from app.recruitment import services
from app.recruitment.db import RecruitmentStore
from app.repository import JobRepository


class FakeBoss:
    def __init__(self, positions, jds, candidates, resumes, inbound=None):
        self.positions = positions
        self.jds = jds
        self.candidates = candidates
        self.resumes = resumes
        self.inbound = inbound or []
        self.calls = []

    def run(self, *args, **kwargs):
        self.calls.append(args)
        return "ok"

    def list_positions(self):
        return self.positions

    def fetch_jd(self, name):
        return self.jds[name]

    def list_recommend(self, keyword):
        return self.candidates.get(keyword, [])

    def list_unread_contacts(self):
        return self.inbound

    def download_resume(self, name):
        if name in self.resumes:
            return self.resumes[name]
        raise BossCliError("暂无附件")


class FakeZhaopin:
    """假智联连接器：只提供自动化流程用到的读取与命令执行能力。"""

    def __init__(self, positions, candidates):
        self.positions = positions
        self.candidates = candidates
        self.calls = []

    def run(self, *args, **kwargs):
        self.calls.append(args)
        return "ok"

    def list_positions(self, keyword=None):
        return self.positions

    def list_recommend(self, keyword=None):
        return self.candidates.get(keyword, [])


class FakeEngine:
    def __init__(self):
        self.started = []

    def start(self, job_id):
        self.started.append(job_id)
        return {}


class FakeCallRepository:
    def __init__(self):
        self.created = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "call-id"}


def build(
    tmp_path,
    boss,
    engine,
    call_repository=None,
    target_job="",
    zhaopin=None,
    zhaopin_target_job="",
):
    repository = JobRepository(tmp_path)
    outreaches = OutreachStore(tmp_path)
    store = AutomationStore(tmp_path)
    settings_store = None
    if target_job or zhaopin_target_job:
        settings_store = SettingsStore(tmp_path)
        settings_store.save(
            AppSettings(boss_target_job=target_job, zhaopin_target_job=zhaopin_target_job)
        )
    automation = AutomationEngine(
        repository, engine, boss, outreaches, store,
        call_repository, None, settings_store, zhaopin,
    )
    return repository, outreaches, store, automation


def test_run_once_creates_job_and_outreaches_and_dedupes(tmp_path):
    boss = FakeBoss(
        [{"name": "前端工程师", "status": "开放中"}],
        {"前端工程师": "# 前端\n\n## 职位描述\n负责前端。"},
        {"前端工程师": [{"name": "张三"}, {"name": "李四"}]},
        {},
    )
    engine = FakeEngine()
    repository, outreaches, store, automation = build(tmp_path, boss, engine)

    first = automation.run_once()
    assert first["positions_created"] == 1
    assert first["outreaches_created"] == 2
    assert len(repository.list_jobs(archived=False)) == 1
    assert len(outreaches.list_records(archived=False)) == 2

    second = automation.run_once()
    assert second["positions_created"] == 0
    assert second["outreaches_created"] == 0
    assert len(repository.list_jobs(archived=False)) == 1
    assert len(outreaches.list_records(archived=False)) == 2


def test_approve_then_download_resume_and_start(tmp_path):
    resume = tmp_path / "张三.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    boss = FakeBoss(
        [{"name": "前端工程师", "status": "开放中"}],
        {"前端工程师": "# 前端"},
        {"前端工程师": [{"name": "张三"}]},
        {"张三": resume},
    )
    engine = FakeEngine()
    repository, outreaches, store, automation = build(tmp_path, boss, engine)
    automation.run_once()

    # 模拟 HR 审核通过（端点里的 approve 逻辑等价于这里更新状态）
    record = outreaches.list_records(archived=False)[0]
    outreaches.update(record["id"], status="sent", result="ok", error="")

    summary = automation.run_once()
    assert summary["outreaches_reconciled"] == 1
    assert summary["resumes_downloaded"] == 1

    job = repository.list_jobs(archived=False)[0]
    assert len(job["resume_files"]) == 1
    assert store.candidate_status(job["id"], "张三") == "resume_downloaded"
    assert engine.started == [job["id"]]


def approve_greet(outreaches):
    record = outreaches.list_records(archived=False)[0]
    outreaches.update(record["id"], status="sent", result="ok", error="")


def test_download_failures_generate_resume_request_once(tmp_path):
    boss = FakeBoss(
        [{"name": "前端工程师", "status": "开放中"}],
        {"前端工程师": "# 前端"},
        {"前端工程师": [{"name": "张三"}]},
        {},
    )
    engine = FakeEngine()
    repository, outreaches, store, automation = build(tmp_path, boss, engine)
    automation.run_once()
    approve_greet(outreaches)
    automation.run_once()  # 触达回写 → outreached，下载失败 1
    automation.run_once()  # 下载失败 2
    automation.run_once()  # 下载失败 3 → 生成求简历草稿

    job = repository.list_jobs(archived=False)[0]
    assert store.candidate_status(job["id"], "张三") == "resume_requested"
    requests = [
        r for r in outreaches.list_records(archived=False)
        if r.get("action", {}).get("command") == "request-attachment-resume"
    ]
    assert len(requests) == 1

    automation.run_once()  # 状态已切换，不再生成第二条求简历草稿
    requests = [
        r for r in outreaches.list_records(archived=False)
        if r.get("action", {}).get("command") == "request-attachment-resume"
    ]
    assert len(requests) == 1


def test_resume_request_sent_returns_candidate_to_download_probe(tmp_path):
    boss = FakeBoss(
        [{"name": "前端工程师", "status": "开放中"}],
        {"前端工程师": "# 前端"},
        {"前端工程师": [{"name": "张三"}]},
        {},
    )
    engine = FakeEngine()
    repository, outreaches, store, automation = build(tmp_path, boss, engine)
    automation.run_once()
    approve_greet(outreaches)
    for _ in range(3):
        automation.run_once()

    job = repository.list_jobs(archived=False)[0]
    assert store.candidate_status(job["id"], "张三") == "resume_requested"

    request = [
        r for r in outreaches.list_records(archived=False)
        if r.get("action", {}).get("command") == "request-attachment-resume"
    ][0]
    outreaches.update(request["id"], status="sent", result="已发送", error="")

    summary = automation.run_once()
    assert summary["outreaches_reconciled"] == 1
    assert store.candidate_status(job["id"], "张三") == "outreached"


def test_s_calls_created_once_from_completed_job(tmp_path):
    boss = FakeBoss(
        [{"name": "前端工程师", "status": "开放中"}],
        {"前端工程师": "# 前端\n\n## 职位描述\n负责前端。"},
        {"前端工程师": [{"name": "张三"}]},
        {},
    )
    engine = FakeEngine()
    call_repository = FakeCallRepository()
    repository, outreaches, store, automation = build(tmp_path, boss, engine, call_repository)
    automation.run_once()

    job = repository.list_jobs(archived=False)[0]
    repository.update(job["id"], status="completed")
    results_path = repository.job_dir(job["id"]) / "评估结果.json"
    results_path.write_text(json.dumps([
        {"candidate_name": "张三", "conclusion": "S电话沟通", "one_line": "强匹配", "source_file": "张三.pdf"},
        {"candidate_name": "李四", "conclusion": "A优先约面", "one_line": "匹配", "source_file": "李四.pdf"},
    ], ensure_ascii=False), encoding="utf-8")

    first = automation.run_once()
    assert first["s_calls_synced"] == 1
    assert len(call_repository.created) == 1
    created = call_repository.created[0]
    assert created["job_title"] == "前端工程师"
    assert [r["candidate_name"] for r in created["roster"]] == ["张三"]

    second = automation.run_once()
    assert second["s_calls_synced"] == 0
    assert len(call_repository.created) == 1


def test_approve_all_sends_every_pending_outreach(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    class FakeConnector:
        def __init__(self):
            self.calls = []

        def run(self, *args, **kwargs):
            self.calls.append(args)
            return "ok"

    fake = FakeConnector()
    monkeypatch.setattr("app.main.BossCliConnector", lambda: fake)
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    headers = {"X-App-Token": app.state.app_token}

    for name in ("张三", "李四"):
        response = client.post(
            "/api/boss/outreaches", json={"kind": "greet", "target": name}, headers=headers,
        )
        assert response.status_code == 200

    response = client.post("/api/boss/outreaches/approve-all", headers=headers)
    assert response.json() == {"sent": 2, "failed": 0, "skipped": 0, "target_job": ""}
    assert len(fake.calls) == 2

    # 全部已发送后重复调用不重复执行
    response = client.post("/api/boss/outreaches/approve-all", headers=headers)
    assert response.json() == {"sent": 0, "failed": 0, "skipped": 0, "target_job": ""}
    assert len(fake.calls) == 2


def test_approve_all_scoped_to_target_job(tmp_path, monkeypatch):
    """一键发送只发送当前目标岗位的草稿，其它岗位的历史草稿一律跳过。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    class FakeConnector:
        def __init__(self):
            self.calls = []

        def run(self, *args, **kwargs):
            self.calls.append(args)
            return "ok"

    fake = FakeConnector()
    monkeypatch.setattr("app.main.BossCliConnector", lambda: fake)
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    headers = {"X-App-Token": app.state.app_token}

    client.post("/api/boss/target-job", json={"job": "招聘专员"}, headers=headers)
    for job in ("招聘专员", "新媒体运营总监"):
        response = client.post(
            "/api/boss/outreaches",
            json={"kind": "greet", "target": f"{job}候选人", "job_keyword": job},
            headers=headers,
        )
        assert response.status_code == 200

    response = client.post("/api/boss/outreaches/approve-all", headers=headers)
    assert response.json() == {"sent": 1, "failed": 0, "skipped": 1, "target_job": "招聘专员"}


def test_run_once_skips_when_another_round_is_running(tmp_path):
    """同一时刻只允许一轮：已有轮次在执行时并发调用直接跳过，避免两轮同时驱动浏览器。"""
    boss = FakeBoss([], {}, {}, {})
    _, _, _, automation = build(tmp_path, boss, FakeEngine())

    assert automation._round_lock.acquire(blocking=False)
    try:
        assert automation.run_once() == {"skipped": "busy"}
    finally:
        automation._round_lock.release()


def test_stop_interrupts_resume_download_loop(tmp_path):
    """收到停止请求后，轮内不再继续逐个候选人下载简历。"""
    boss = FakeBoss([], {}, {}, {})
    _, _, store, automation = build(tmp_path, boss, FakeEngine())
    store.mark_position("招聘专员", "job-1")
    for name in ("甲", "乙", "丙"):
        store.mark_candidate("job-1", name, "outreached")

    attempted: list[str] = []
    automation._download_resume_to_job = lambda name, job_id: attempted.append(name) or None

    automation.stop()
    automation.sync_resumes()
    assert attempted == []


def test_inbound_creates_reply_draft_for_matched_position_and_dedupes(tmp_path):
    boss = FakeBoss(
        [{"name": "前端工程师", "status": "开放中"}],
        {"前端工程师": "# 前端\n\n## 职位描述\n负责前端。"},
        {},
        {},
        inbound=[{"name": "王五", "job": "前端工程师"}, {"name": "赵六", "job": "设计师"}],
    )
    engine = FakeEngine()
    repository, outreaches, store, automation = build(tmp_path, boss, engine)
    automation.run_once()

    job = repository.list_jobs(archived=False)[0]
    prints = [d.get("action", {}) for d in outreaches.list_records(archived=False)]
    sends = [a for a in prints if a.get("kind") == "send"]
    assert len(sends) == 2
    matched = next(a for a in sends if a["target"] == "王五")
    assert matched["text"] == "您好，方便发一份您的简历吗？"
    assert matched["request_resume"] is True
    assert matched["job_keyword"] == "前端工程师"
    unmatched = next(a for a in sends if a["target"] == "赵六")
    assert unmatched["job_keyword"] == ""
    assert store.candidate_status(job["id"], "赵六") is None

    automation.run_once()
    sends = [
        d for d in outreaches.list_records(archived=False)
        if d.get("action", {}).get("kind") == "send"
    ]
    assert len(sends) == 2
    assert store.candidate_status(job["id"], "王五") == "inbound_pending"

    matched_draft = next(
        d for d in outreaches.list_records(archived=False)
        if d.get("action", {}).get("target") == "王五"
    )
    outreaches.update(matched_draft["id"], status="sent", result="ok", error="")
    summary = automation.run_once()
    assert summary["outreaches_reconciled"] == 1
    assert store.candidate_status(job["id"], "王五") == "outreached"


def test_inbound_resume_request_auto_accepts_and_returns_to_download(tmp_path):
    boss = FakeBoss(
        [{"name": "前端工程师", "status": "开放中"}],
        {"前端工程师": "# 前端\n\n## 职位描述\n负责前端。"},
        {},
        {},
        inbound=[{"name": "王五", "job": "前端工程师", "message": "对方想发送附件简历给您，您是否同意"}],
    )
    engine = FakeEngine()
    repository, outreaches, store, automation = build(tmp_path, boss, engine)
    summary = automation.run_once()

    job = repository.list_jobs(archived=False)[0]
    agrees = [
        d for d in outreaches.list_records(archived=False)
        if d.get("action", {}).get("command") == "agree-resume"
    ]
    assert len(agrees) == 1
    assert agrees[0]["action"]["target"] == "王五"
    assert agrees[0]["action"]["job_keyword"] == "前端工程师"
    # 同意接收自动执行（非对外触达，无需 HR 审核），直接回到下载探测
    assert agrees[0]["status"] == "sent"
    assert summary["resume_requests_accepted"] == 1
    assert store.candidate_status(job["id"], "王五") == "outreached"

    # 去重：下一轮不再重复生成
    automation.run_once()
    agrees = [
        d for d in outreaches.list_records(archived=False)
        if d.get("action", {}).get("command") == "agree-resume"
    ]
    assert len(agrees) == 1


def test_inbound_resume_file_downloads_without_requesting_resume(tmp_path):
    """对方已发来附件简历时不再求简历，直接下载并把候选人推进到已收简历。"""
    resume = tmp_path / "王五.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    boss = FakeBoss(
        [{"name": "前端工程师", "status": "开放中"}],
        {"前端工程师": "# 前端\n\n## 职位描述\n负责前端。"},
        {},
        {"王五": resume},
        inbound=[{"name": "王五", "job": "前端工程师", "message": "王五简历.pdf"}],
    )
    engine = FakeEngine()
    repository, outreaches, store, automation = build(tmp_path, boss, engine)
    summary = automation.run_once()

    job = repository.list_jobs(archived=False)[0]
    sends = [
        d for d in outreaches.list_records(archived=False)
        if d.get("action", {}).get("kind") == "send"
    ]
    assert sends == []
    assert summary["resumes_downloaded"] == 1
    assert store.candidate_status(job["id"], "王五") == "resume_downloaded"


def test_inbound_scoped_to_configured_target_job(tmp_path):
    """配置目标岗位后，其他岗位的往来消息不进入流程。"""
    boss = FakeBoss(
        [{"name": "前端工程师", "status": "开放中"}, {"name": "设计师", "status": "开放中"}],
        {"前端工程师": "# 前端", "设计师": "# 设计"},
        {},
        {},
        inbound=[
            {"name": "王五", "job": "前端工程师"},
            {"name": "赵六", "job": "设计师"},
        ],
    )
    engine = FakeEngine()
    repository, outreaches, store, automation = build(
        tmp_path, boss, engine, target_job="前端工程师"
    )
    automation.run_once()

    assert len(repository.list_jobs(archived=False)) == 1
    targets = [d["action"]["target"] for d in outreaches.list_records(archived=False)]
    assert targets == ["王五"]


def test_inbound_resume_file_supersedes_pending_resume_request(tmp_path):
    """对方随后发来附件简历时，作废此前待审核的求简历草稿。"""
    resume = tmp_path / "王五.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    boss = FakeBoss(
        [{"name": "前端工程师", "status": "开放中"}],
        {"前端工程师": "# 前端"},
        {},
        {"王五": resume},
        inbound=[{"name": "王五", "job": "前端工程师", "message": "你好"}],
    )
    engine = FakeEngine()
    repository, outreaches, store, automation = build(tmp_path, boss, engine)
    automation.run_once()
    assert [d["status"] for d in outreaches.list_records(archived=False)] == ["pending"]

    boss.inbound[0]["message"] = "王五简历.pdf"
    summary = automation.run_once()

    job = repository.list_jobs(archived=False)[0]
    assert [d["status"] for d in outreaches.list_records(archived=False)] == ["rejected"]
    assert summary["resumes_downloaded"] == 1
    assert store.candidate_status(job["id"], "王五") == "resume_downloaded"


def test_downloaded_resume_is_recorded_on_candidate(tmp_path):
    """下载成功后把本人简历文件名登记到候选人库，供自动打分读取。"""
    resume = tmp_path / "王五.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    boss = FakeBoss(
        [{"name": "前端工程师", "status": "开放中"}],
        {"前端工程师": "# 前端"},
        {},
        {"王五": resume},
        inbound=[{"name": "王五", "job": "前端工程师", "message": "王五简历.pdf"}],
    )
    repository = JobRepository(tmp_path)
    recruitment = RecruitmentStore(tmp_path / "recruitment.db")
    recruitment.initialize()
    automation = AutomationEngine(
        repository, FakeEngine(), boss, OutreachStore(tmp_path), AutomationStore(tmp_path),
        None, recruitment, None,
    )
    automation.run_once()

    job = repository.list_jobs(archived=False)[0]
    row = recruitment.query_one(
        "SELECT stage, resume_file FROM candidate WHERE name = ? AND job_keyword = ?",
        ("王五", "前端工程师"),
    )
    assert row["stage"] == "resume_received"
    assert row["resume_file"] in (job["resume_files"] or [])


def test_get_resume_text_reads_candidate_own_resume(tmp_path, monkeypatch):
    """同一任务下多位候选人各读本人的简历，而不是任务里最后一份。"""
    repository = JobRepository(tmp_path)
    job = repository.create(title="新媒体运营总监")
    stored_names = []
    for label in ("甲", "乙"):
        stored, target = repository.reserve_upload(job["id"], "resumes", f"{label}.pdf")
        target.write_bytes(b"%PDF-1.4")
        stored_names.append(stored)
    repository.update(job["id"], resume_files=stored_names)

    monkeypatch.setattr(
        services, "extract_document",
        lambda path, settings, resume=True: {"text": path.stem},
    )

    def read(name: str, resume_file: str) -> str:
        return services._get_resume_text(
            {"name": name, "job_id": job["id"], "resume_file": resume_file}, repository, None
        )

    assert read("甲", stored_names[0]) == "甲"
    assert read("乙", stored_names[1]) == "乙"


def test_get_resume_text_rejects_candidate_without_resume(tmp_path):
    """尚未登记本人简历文件时不打分，避免用空文本或别人的简历得出评分。"""
    repository = JobRepository(tmp_path)
    job = repository.create(title="新媒体运营总监")
    with pytest.raises(FileNotFoundError):
        services._get_resume_text(
            {"name": "甲", "job_id": job["id"], "resume_file": ""}, repository, None
        )


def test_target_job_survives_settings_save(tmp_path):
    """目标岗位在「招聘接入」面板单独设置，保存其他设置时不应被清空。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    headers = {"X-App-Token": app.state.app_token}

    response = client.post("/api/boss/target-job", json={"job": "新媒体运营总监"}, headers=headers)
    assert response.json() == {"target_job": "新媒体运营总监"}

    # 设置弹窗保存时不携带 boss_target_job，应保持原值
    response = client.put(
        "/api/settings",
        json={"base_url": "https://api.deepseek.com", "model": "deepseek-flash"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["boss_target_job"] == "新媒体运营总监"

    response = client.post("/api/boss/target-job", json={"job": ""}, headers=headers)
    assert response.json() == {"target_job": ""}


def test_target_job_selector_payload(tmp_path):
    """目标岗位选择器读本地缓存即返回岗位列表，不调 boss-cli，且不与 /api/boss/positions 撞路由。

    背景：`GET /api/boss/positions` 曾被注册两次，先注册的实时职位接口遮蔽了选择器接口，
    导致前端拿不到 positions、下拉框被 disabled。
    """
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(data_dir=tmp_path)
    app.state.automation.store.remember_available(["招聘专员", "新媒体运营总监"])
    client = TestClient(app)
    headers = {"X-App-Token": app.state.app_token}

    response = client.get("/api/boss/target-job", headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "target_job": "",
        "positions": ["招聘专员", "新媒体运营总监"],
    }
    # 两个端点必须各自独立存在：实时职位表与选择器数据不可合并为同一路径
    paths = {route.path for route in app.routes}
    assert {"/api/boss/positions", "/api/boss/target-job"} <= paths


def test_score_reason_matches_grade():
    """一句话结论与档位一致：命中否决项列否决项，D 档给风险，通过档给亮点。"""
    from app.rubric.models import CandidateScore

    def score(grade: str, **kwargs) -> CandidateScore:
        return CandidateScore(candidate_name="甲", item_scores=[], grade=grade, **kwargs)

    veto = score("D", veto_hits=["赛道不符", "经验不足"], risk="风险A", highlight="亮点A")
    assert services._score_reason(veto) == "命中否决项：赛道不符、经验不足"

    low = score("D", risk="风险B", highlight="亮点B")
    assert services._score_reason(low) == "风险B"

    passed = score("A", risk="风险C", highlight="亮点C")
    assert services._score_reason(passed) == "亮点C"


def _two_group_rubric():
    from app.rubric.models import WeightedRubric

    return WeightedRubric.model_validate({
        "job_title": "岗位",
        "groups": [
            {"name": "A 组", "items": [{"id": "①", "name": "aa", "weight": 60, "rubric": "5分…"}]},
            {"name": "B 组", "items": [{"id": "①", "name": "bb", "weight": 40, "rubric": "5分…"}]},
        ],
    })


def test_rubric_item_keys_are_globally_unique():
    """评分项 id 只在组内唯一，全局 key 必须带组字母，否则跨组撞键。"""
    assert list(_two_group_rubric().item_map().keys()) == ["A①", "B①"]


def test_compute_score_matches_model_item_ids():
    """模型按「组字母 + 项 id」返回时必须能对上，并按权重算出总分。"""
    from app.rubric.models import CandidateScore, compute_score

    result = compute_score(
        CandidateScore(candidate_name="甲", item_scores=[
            {"item_id": "A①", "score": 5, "evidence": "依据一"},
            {"item_id": "B①", "score": 3, "evidence": "依据二"},
        ]),
        _two_group_rubric(),
    )
    # 60×5/5 + 40×3/5 = 84
    assert result.base_score == 84.0
    assert result.total == 84.0
    assert result.grade == "A"


def test_compute_score_rejects_unmatched_item_ids():
    """逐项得分对不上评分标准时报错，不能当成 0 分把候选人误判为淘汰。"""
    from app.rubric.models import CandidateScore, compute_score

    rubric = _two_group_rubric()
    with pytest.raises(ValueError, match="无法对应评分标准"):
        compute_score(
            CandidateScore(candidate_name="甲", item_scores=[{"item_id": "Z9", "score": 5}]),
            rubric,
        )
    with pytest.raises(ValueError, match="缺少逐项得分"):
        compute_score(CandidateScore(candidate_name="甲", item_scores=[]), rubric)


def test_zhaopin_sync_registers_positions_and_drafts_greetings(tmp_path):
    """智联职位与推荐候选人进入流程：登记岗位、只给目标岗位生成打招呼草稿，且标记为智联平台。"""
    boss = FakeBoss([], {}, {}, {})
    zhaopin = FakeZhaopin(
        [{"name": "服务员"}, {"name": "店长"}],
        {"服务员": [{"name": "高女士"}, {"name": "龙女士"}]},
    )
    _, outreaches, store, automation = build(
        tmp_path, boss, FakeEngine(), zhaopin=zhaopin, zhaopin_target_job="服务员"
    )

    summary = automation.run_once()
    assert summary["zhaopin_positions_created"] == 2
    assert summary["zhaopin_outreaches_created"] == 2

    drafts = outreaches.list_records(archived=False)
    assert sorted(record["action"]["target"] for record in drafts) == ["高女士", "龙女士"]
    assert all(record["action"]["platform"] == ZHAOPIN_PLATFORM for record in drafts)
    assert all(record["action"]["job_keyword"] == "服务员" for record in drafts)
    assert store.list_available(ZHAOPIN_PLATFORM) == ["店长", "服务员"]

    # 再跑一轮不重复登记岗位，也不重复生成草稿
    again = automation.run_once()
    assert again["zhaopin_positions_created"] == 0
    assert again["zhaopin_outreaches_created"] == 0
    assert len(outreaches.list_records(archived=False)) == 2


def test_zhaopin_greet_switches_job_then_greets():
    """智联打招呼先切到目标岗位的推荐页，再按姓名打招呼。"""
    connector = FakeZhaopin([], {})
    action = OutreachAction(
        kind="greet", target="高女士", job_keyword="服务员", platform=ZHAOPIN_PLATFORM
    )

    assert execute_outreach(connector, action) == "ok"
    assert connector.calls == [("recommend", "服务员"), ("greet", "高女士")]


def test_zhaopin_resume_request_uses_request_command():
    """智联索要附件简历由 request 命令自行打开聊天框完成。"""
    connector = FakeZhaopin([], {})
    action = OutreachAction(
        kind="action",
        command="request-attachment-resume",
        target="高女士",
        platform=ZHAOPIN_PLATFORM,
    )

    assert execute_outreach(connector, action) == "ok"
    assert connector.calls == [("request", "高女士", "resume")]