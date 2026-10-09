"""员工生命周期：入离职事件流、到期离职与人员流动看板。"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from app.attendance import lifecycle as L
from app.attendance.db import AttendanceStore
from app.attendance.feishu_events import _member_from_event, apply_member_change

CHINA_TZ = timezone(timedelta(hours=8))


def _store(tmp_path) -> AttendanceStore:
    store = AttendanceStore(tmp_path / "attendance.db")
    store.initialize()
    return store


def _insert(store, **overrides) -> int:
    values = {
        "employee_no": "E001",
        "name": "张三",
        "department": "技术部",
        "position": "",
        "join_date": None,
        "employment_status": "regular",
        "active": 1,
    }
    values.update(overrides)
    return store.execute(
        "INSERT INTO employee (employee_no, name, aliases, department, position, join_date, "
        "employment_status, active, created_at, updated_at) VALUES (?, ?, '[]', ?, ?, ?, ?, ?, 't', 't')",
        (
            values["employee_no"],
            values["name"],
            values["department"],
            values["position"],
            values["join_date"],
            values["employment_status"],
            values["active"],
        ),
    )


def _employee(store, employee_id):
    return store.query_one("SELECT * FROM employee WHERE id = ?", (employee_id,), table="employee")


# ---- 事件流 ----


def test_record_event_is_idempotent(tmp_path):
    store = _store(tmp_path)
    employee_id = _insert(store)
    employee = _employee(store, employee_id)

    first = L.record_event(
        store, employee, event_type=L.EVENT_ONBOARD, effective_date="2026-08-01", source=L.SOURCE_FEISHU
    )
    second = L.record_event(
        store, employee, event_type=L.EVENT_ONBOARD, effective_date="2026-08-01", source=L.SOURCE_FEISHU
    )

    assert first is True
    assert second is False
    assert len(store.query("SELECT id FROM employee_lifecycle_event")) == 1


def test_offboard_employee_sets_state_and_records_event(tmp_path):
    store = _store(tmp_path)
    employee_id = _insert(store)

    assert L.offboard_employee(
        store, employee_id, effective_date="2026-10-20", source=L.SOURCE_RESIGNATION, reason="个人发展"
    )

    row = _employee(store, employee_id)
    assert row["active"] == 0
    assert row["employment_status"] == "left"
    assert row["leave_date"] == "2026-10-20"
    event = store.query("SELECT * FROM employee_lifecycle_event")[0]
    assert event["event_type"] == L.EVENT_OFFBOARD
    assert event["source"] == "resignation"
    assert event["reason"] == "个人发展"


def test_derive_events_from_sync_only_records_real_changes(tmp_path):
    store = _store(tmp_path)
    employee_id = _insert(store, employee_no="E100", name="新同事", active=1)
    employee = {"id": employee_id, "employee_no": "E100", "name": "新同事", "department": "技术部"}

    written = L.derive_events_from_sync(
        store,
        [
            # 新入职
            {"employee": employee, "was_active": False, "is_active": True, "join_date": "2026-09-01"},
            # 状态没变，不该产生事件
            {"employee": employee, "was_active": True, "is_active": True, "join_date": None},
            # 离职
            {"employee": employee, "was_active": True, "is_active": False, "join_date": None},
        ],
    )

    assert written == 2
    events = store.query("SELECT event_type, effective_date FROM employee_lifecycle_event ORDER BY id")
    assert [event["event_type"] for event in events] == ["onboard", "offboard"]
    assert events[0]["effective_date"] == "2026-09-01"


# ---- 到期自动离职 ----


def test_process_due_offboards_waits_until_last_working_day(tmp_path):
    store = _store(tmp_path)
    employee_id = _insert(store)
    store.execute(
        "INSERT INTO resignation_request (token, employee_id, employee_no, employee_name, department, "
        "position, status, last_working_day, reason_category, created_at, updated_at) "
        "VALUES ('tk', ?, 'E001', '张三', '技术部', '', ?, '2026-10-20', '个人发展', 't', 't')",
        (employee_id, L.STATUS_CONFIRMED),
    )

    before = L.process_due_offboards(store, on_date=date(2026, 10, 19))
    assert before["processed"] == 0
    assert _employee(store, employee_id)["active"] == 1

    after = L.process_due_offboards(store, on_date=date(2026, 10, 20))
    assert after["processed"] == 1
    row = _employee(store, employee_id)
    assert row["active"] == 0
    assert row["leave_date"] == "2026-10-20"
    request = store.query_one("SELECT status FROM resignation_request")
    assert request["status"] == L.STATUS_COMPLETED


def test_process_due_offboards_skips_unconfirmed_requests(tmp_path):
    store = _store(tmp_path)
    employee_id = _insert(store)
    store.execute(
        "INSERT INTO resignation_request (token, employee_id, employee_no, employee_name, department, "
        "position, status, last_working_day, created_at, updated_at) "
        "VALUES ('tk', ?, 'E001', '张三', '技术部', '', ?, '2026-10-01', 't', 't')",
        (employee_id, L.STATUS_SUBMITTED),
    )

    assert L.process_due_offboards(store, on_date=date(2026, 10, 9))["processed"] == 0
    assert _employee(store, employee_id)["active"] == 1


# ---- 人员流动看板 ----


def test_employee_flow_dashboard_summarises_months_and_reasons(tmp_path):
    store = _store(tmp_path)
    employee_id = _insert(store, join_date="2026-08-01")
    employee = _employee(store, employee_id)
    L.record_event(
        store, employee, event_type=L.EVENT_ONBOARD, effective_date="2026-08-01", source=L.SOURCE_FEISHU
    )
    store.execute(
        "INSERT INTO resignation_request (token, employee_id, employee_no, employee_name, department, "
        "position, status, last_working_day, reason_category, created_at, updated_at) "
        "VALUES ('tk', ?, 'E001', '张三', '技术部', '', ?, '2026-09-15', '个人发展', 't', 't')",
        (employee_id, L.STATUS_CONFIRMED),
    )
    L.process_due_offboards(store, on_date=date(2026, 9, 15))

    board = L.employee_flow_dashboard(store, months=3, on_date=date(2026, 10, 9))

    assert board["total"] == 1
    assert board["active"] == 0
    assert [month["month"] for month in board["months"]] == ["2026-08", "2026-09", "2026-10"]
    by_month = {month["month"]: month for month in board["months"]}
    assert by_month["2026-08"]["onboard"] == 1
    assert by_month["2026-08"]["headcount"] == 1
    assert by_month["2026-09"]["offboard"] == 1
    assert by_month["2026-09"]["headcount"] == 0
    assert by_month["2026-09"]["turnover_rate"] == 100.0
    assert board["reasons"] == [{"category": "个人发展", "count": 1}]
    assert board["pending"]["completed"] == 1


def test_employee_flow_dashboard_back_projects_headcount_for_pre_existing_staff(tmp_path):
    """看板按当前在职数倒推历史人数：没有事件的老员工也要算进去。"""
    store = _store(tmp_path)
    _insert(store, employee_no="E001", name="老员工")

    board = L.employee_flow_dashboard(store, months=2, on_date=date(2026, 10, 9))

    assert board["active"] == 1
    assert all(month["headcount"] == 1 for month in board["months"])


# ---- 飞书事件映射 ----


def _member(**overrides):
    flags = {key: overrides.pop(key, False) for key in
             ("is_frozen", "is_resigned", "is_activated", "is_exited", "is_unjoin")}
    return SimpleNamespace(status=SimpleNamespace(**flags), **overrides)


def test_apply_member_change_creates_then_offboards(tmp_path):
    store = _store(tmp_path)
    created = _member_from_event(
        _member(open_id="ou-1", user_id="u-1", name="张三", employee_no="E001", join_time=1735689600)
    )
    created["department"] = "技术部"

    assert apply_member_change(store, created)["action"] == "onboard"

    resigned = _member_from_event(
        _member(open_id="ou-1", user_id="u-1", name="张三", employee_no="E001", is_resigned=True)
    )
    resigned["department"] = "技术部"
    assert apply_member_change(store, resigned)["action"] == "offboard"

    row = store.query_one("SELECT * FROM employee WHERE employee_no = 'E001'", table="employee")
    assert row["active"] == 0
    assert row["feishu_open_id"] == "ou-1"
    assert row["feishu_user_id"] == "u-1"
    assert [event["event_type"] for event in
            store.query("SELECT event_type FROM employee_lifecycle_event ORDER BY id")] == ["onboard", "offboard"]


def test_apply_member_change_deleted_marks_left(tmp_path):
    store = _store(tmp_path)
    info = _member_from_event(_member(open_id="ou-9", name="李四", employee_no="E009"))
    info["department"] = ""
    apply_member_change(store, info)

    assert apply_member_change(store, info, deleted=True)["action"] == "offboard"
    assert store.query_one("SELECT active FROM employee WHERE employee_no='E009'")["active"] == 0


def test_apply_member_change_skips_members_without_name(tmp_path):
    store = _store(tmp_path)
    info = _member_from_event(_member(open_id="ou-x", name="", employee_no=""))

    assert apply_member_change(store, info)["action"] == "skipped"
    assert store.query("SELECT id FROM employee") == []


# ---- 事件长连接引擎 ----


def test_event_engine_enabled_defaults_on_with_credentials(tmp_path):
    """有飞书凭证即视为开启；显式写 "0" 可关闭。无凭证时不启动、也不报「已开启」。"""
    from app.attendance.feishu_events import FeishuEventEngine

    store = _store(tmp_path)
    engine = FeishuEventEngine(store)

    assert engine.configured is False
    assert engine.enabled is False
    engine.start()
    assert engine.running is False

    store.set_config("feishu_app_id", "cli_x")
    store.set_config("feishu_app_secret", "secret")
    assert engine.configured is True
    assert engine.enabled is True

    store.set_config("feishu_events_enabled", "0")
    assert engine.enabled is False


def test_event_engine_reports_disconnected_without_socket(tmp_path):
    from app.attendance.feishu_events import FeishuEventEngine

    engine = FeishuEventEngine(_store(tmp_path))

    assert engine.connected is False
    assert engine.last_event() is None
