"""自动化引擎的单元测试：用假 boss 客户端与假引擎，验证状态推进与去重。"""

from app.connectors.automation import AutomationEngine, AutomationStore
from app.connectors.boss_cli import BossCliError
from app.connectors.outreach import OutreachStore
from app.repository import JobRepository


class FakeBoss:
    def __init__(self, positions, jds, candidates, resumes):
        self.positions = positions
        self.jds = jds
        self.candidates = candidates
        self.resumes = resumes

    def list_positions(self):
        return self.positions

    def fetch_jd(self, name):
        return self.jds[name]

    def list_recommend(self, keyword):
        return self.candidates.get(keyword, [])

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


def build(tmp_path, boss, engine):
    repository = JobRepository(tmp_path)
    outreaches = OutreachStore(tmp_path)
    store = AutomationStore(tmp_path)
    automation = AutomationEngine(repository, engine, boss, outreaches, store)
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