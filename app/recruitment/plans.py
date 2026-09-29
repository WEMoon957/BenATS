"""招聘作业（Plan）：四步向导发起一次招聘作业的配置与状态。

P0 阶段只支持被动咨询（mode=passive），作业记录岗位、模式与状态；
执行前检查与启动触发在 main.py 的路由层完成，这里只放纯数据逻辑。
"""

from __future__ import annotations

from .db import RecruitmentStore, _now

PLAN_STATE_DRAFT = "draft"
PLAN_STATE_RUNNING = "running"
PLAN_STATE_STOPPED = "stopped"

PLAN_STATES = [PLAN_STATE_DRAFT, PLAN_STATE_RUNNING, PLAN_STATE_STOPPED]

PLAN_MODE_PASSIVE = "passive"


def create_plan(store: RecruitmentStore, job_keyword: str, mode: str = PLAN_MODE_PASSIVE) -> dict:
    plan_id = store.execute(
        "INSERT INTO plan (job_keyword, mode, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (job_keyword, mode, PLAN_STATE_DRAFT, _now(), _now()),
    )
    return store.query_one("SELECT * FROM plan WHERE id = ?", (plan_id,))


def list_plans(store: RecruitmentStore) -> list[dict]:
    return store.query("SELECT * FROM plan ORDER BY id DESC")


def get_plan(store: RecruitmentStore, plan_id: int) -> dict | None:
    return store.query_one("SELECT * FROM plan WHERE id = ?", (plan_id,))


def set_plan_state(store: RecruitmentStore, plan_id: int, state: str) -> dict | None:
    if state not in PLAN_STATES:
        raise ValueError(f"无效的作业状态：{state}")
    store.execute(
        "UPDATE plan SET state = ?, updated_at = ? WHERE id = ?",
        (state, _now(), plan_id),
    )
    return get_plan(store, plan_id)


def check_plan(boss, settings_ready: bool, plan: dict) -> list[dict]:
    """执行前检查：返回检查项列表，每项含 key/label/ok/detail。"""
    checks: list[dict] = []
    cli_ok = True
    cli_detail = ""
    positions: list[dict] = []
    try:
        positions = boss.list_positions()
    except Exception as exc:  # noqa: BLE001
        cli_ok = False
        cli_detail = str(exc)
    checks.append({"key": "cli", "label": "BOSS 直聘接入", "ok": cli_ok, "detail": cli_detail})
    names = {p.get("name", "") for p in positions}
    job_ok = bool(plan["job_keyword"]) and plan["job_keyword"] in names
    checks.append({
        "key": "position",
        "label": "岗位",
        "ok": job_ok,
        "detail": "" if job_ok else "岗位不在 BOSS 职位列表中",
    })
    checks.append({
        "key": "model",
        "label": "模型配置",
        "ok": settings_ready,
        "detail": "" if settings_ready else "模型配置不完整",
    })
    return checks


def all_checks_pass(checks: list[dict]) -> bool:
    return all(c.get("ok") for c in checks)
