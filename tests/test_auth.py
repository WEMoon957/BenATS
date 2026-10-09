"""全局会话鉴权：非考勤接口也需登录（或向后兼容的 X-App-Token）。"""

from fastapi.testclient import TestClient

from app.attendance.db import AttendanceStore
from app.main import create_app
from app.recruitment.db import RecruitmentStore


def _boot(tmp_path, monkeypatch):
    monkeypatch.setenv("TALENT_HUB_DATA_DIR", str(tmp_path))
    attendance = AttendanceStore(tmp_path / "attendance.db")
    attendance.initialize()
    recruitment = RecruitmentStore(tmp_path / "recruitment.db")
    recruitment.initialize()
    monkeypatch.setattr("app.main.get_attendance_store", lambda: attendance)
    monkeypatch.setattr("app.main.get_recruitment_store", lambda: recruitment)
    app = create_app(data_dir=tmp_path)
    return TestClient(app), app


def test_non_attendance_api_requires_auth(tmp_path, monkeypatch):
    client, app = _boot(tmp_path, monkeypatch)

    assert client.get("/api/hr/dashboard").status_code == 401
    assert client.get("/api/hr/dashboard", headers={"X-Attendance-Token": "bogus"}).status_code == 401
    # 旧 X-App-Token 仍可用
    assert client.get("/api/hr/dashboard", headers={"X-App-Token": app.state.app_token}).status_code == 200


def test_login_then_access_and_logout(tmp_path, monkeypatch):
    client, _ = _boot(tmp_path, monkeypatch)

    login = client.post("/api/attendance/login", json={"username": "admin", "password": "admin"})
    assert login.status_code == 200
    token = login.json()["token"]

    assert client.get("/api/hr/dashboard", headers={"X-Attendance-Token": token}).status_code == 200

    client.post("/api/attendance/logout", headers={"X-Attendance-Token": token})
    assert client.get("/api/hr/dashboard", headers={"X-Attendance-Token": token}).status_code == 401


def test_login_and_health_are_public(tmp_path, monkeypatch):
    client, _ = _boot(tmp_path, monkeypatch)

    assert client.get("/health").status_code == 200
    # 登录端点无需会话即可访问
    assert client.post("/api/attendance/login", json={"username": "admin", "password": "admin"}).status_code == 200
