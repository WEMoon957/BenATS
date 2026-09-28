"""自动化引擎的单元测试：用假 boss 客户端与假引擎，验证状态推进与去重。"""

import json

from app.connectors.automation import AutomationEngine, AutomationStore
from app.connectors.boss_cli import BossCliError
from app.connectors.outreach import OutreachStore
from app.repository import JobRepository


class FakeBoss:
    def __init__(self, positions, jds, candidates, resumes, inbound=None):
        self.positions = positions
        self.jds = jds
        self.candidates = candidates
        self.resumes = resumes
        self.inbound = inbound or []

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


def build(tmp_path, boss, engine, call_repository=None):
    repository = JobRepository(tmp_path)
    outreaches = OutreachStore(tmp_path)
    store = AutomationStore(tmp_path)
    automation = AutomationEngine(repository, engine, boss, outreaches, store, call_repository)
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
    assert response.json() == {"sent": 2, "failed": 0}
    assert len(fake.calls) == 2

    # 全部已发送后重复调用不重复执行
    response = client.post("/api/boss/outreaches/approve-all", headers=headers)
    assert response.json() == {"sent": 0, "failed": 0}
    assert len(fake.calls) == 2


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


def test_inbound_resume_request_creates_agree_draft_and_returns_to_download(tmp_path):
    boss = FakeBoss(
        [{"name": "前端工程师", "status": "开放中"}],
        {"前端工程师": "# 前端\n\n## 职位描述\n负责前端。"},
        {},
        {},
        inbound=[{"name": "王五", "job": "前端工程师", "message": "对方想发送附件简历给您，您是否同意"}],
    )
    engine = FakeEngine()
    repository, outreaches, store, automation = build(tmp_path, boss, engine)
    automation.run_once()

    job = repository.list_jobs(archived=False)[0]
    agrees = [
        d for d in outreaches.list_records(archived=False)
        if d.get("action", {}).get("command") == "agree-resume"
    ]
    assert len(agrees) == 1
    assert agrees[0]["action"]["target"] == "王五"
    assert agrees[0]["action"]["job_keyword"] == "前端工程师"
    assert store.candidate_status(job["id"], "王五") == "agree_pending"

    # 去重：下一轮不再重复生成
    automation.run_once()
    agrees = [
        d for d in outreaches.list_records(archived=False)
        if d.get("action", {}).get("command") == "agree-resume"
    ]
    assert len(agrees) == 1

    # 同意发送后回到下载探测（对方发来附件即可自动入库）
    outreaches.update(agrees[0]["id"], status="sent", result="ok", error="")
    summary = automation.run_once()
    assert summary["outreaches_reconciled"] == 1
    assert store.candidate_status(job["id"], "王五") == "outreached"