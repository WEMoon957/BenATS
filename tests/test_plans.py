"""招聘作业（Plan）的单元测试：数据层 CRUD 与执行前检查。"""

from app.recruitment.db import RecruitmentStore
from app.recruitment.plans import (
    PLAN_STATE_DRAFT,
    PLAN_STATE_RUNNING,
    PLAN_STATE_STOPPED,
    check_plan,
    create_plan,
    get_plan,
    list_plans,
    set_plan_state,
)


def build_store(tmp_path):
    store = RecruitmentStore(tmp_path / "recruitment.db")
    store.initialize()
    return store


class FakeBoss:
    def __init__(self, positions=None, error=None):
        self.positions = positions or []
        self.error = error

    def list_positions(self):
        if self.error:
            raise self.error
        return self.positions


def check_by_key(checks, key):
    return next(c for c in checks if c["key"] == key)


def test_create_and_list_plans(tmp_path):
    store = build_store(tmp_path)
    plan = create_plan(store, "前端工程师")
    assert plan["state"] == PLAN_STATE_DRAFT
    assert plan["job_keyword"] == "前端工程师"
    assert plan["mode"] == "passive"
    assert [p["id"] for p in list_plans(store)] == [plan["id"]]


def test_set_plan_state_and_get(tmp_path):
    store = build_store(tmp_path)
    plan = create_plan(store, "前端工程师")
    updated = set_plan_state(store, plan["id"], PLAN_STATE_RUNNING)
    assert updated["state"] == PLAN_STATE_RUNNING
    assert get_plan(store, plan["id"])["state"] == PLAN_STATE_RUNNING
    set_plan_state(store, plan["id"], PLAN_STATE_STOPPED)
    assert get_plan(store, plan["id"])["state"] == PLAN_STATE_STOPPED


def test_check_plan_all_pass(tmp_path):
    boss = FakeBoss([{"name": "前端工程师"}])
    checks = check_plan(boss, True, {"job_keyword": "前端工程师"})
    assert all(c["ok"] for c in checks)


def test_check_plan_missing_position(tmp_path):
    boss = FakeBoss([{"name": "后端工程师"}])
    checks = check_plan(boss, True, {"job_keyword": "前端工程师"})
    assert check_by_key(checks, "position")["ok"] is False


def test_check_plan_cli_error(tmp_path):
    boss = FakeBoss(error=RuntimeError("boss-cli 不可用"))
    checks = check_plan(boss, True, {"job_keyword": "前端工程师"})
    assert check_by_key(checks, "cli")["ok"] is False
    assert check_by_key(checks, "position")["ok"] is False


def test_check_plan_model_not_ready(tmp_path):
    boss = FakeBoss([{"name": "前端工程师"}])
    checks = check_plan(boss, False, {"job_keyword": "前端工程师"})
    assert check_by_key(checks, "model")["ok"] is False


def test_plan_api_create_checks_and_start_blocked(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    class FakeConnector:
        def list_positions(self):
            return [{"name": "前端工程师", "status": "开放中"}]

    def make_store():
        store = RecruitmentStore(tmp_path / "recruitment.db")
        store.initialize()
        return store

    monkeypatch.setattr("app.main.BossCliConnector", lambda: FakeConnector())
    monkeypatch.setattr("app.main.get_recruitment_store", make_store)
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    headers = {"X-App-Token": app.state.app_token}

    created = client.post("/api/recruitment/plans", json={"job_keyword": "前端工程师"}, headers=headers)
    assert created.status_code == 200
    plan = created.json()
    assert plan["state"] == "draft"
    assert plan["mode"] == "passive"

    listed = client.get("/api/recruitment/plans", headers=headers)
    assert [p["id"] for p in listed.json()["plans"]] == [plan["id"]]

    checks = client.get(f"/api/recruitment/plans/{plan['id']}/checks", headers=headers)
    assert checks.status_code == 200
    items = checks.json()["checks"]
    assert check_by_key(items, "position")["ok"] is True
    assert check_by_key(items, "model")["ok"] is False

    started = client.post(f"/api/recruitment/plans/{plan['id']}/start", headers=headers)
    assert started.status_code == 409
