"""考勤模块的初始密码门槛与导入文件名消毒。"""

from fastapi.testclient import TestClient

from app.attendance.db import AttendanceStore
from app.main import create_app

NEW_PASSWORD = "new-secret"


def _boot(tmp_path, monkeypatch):
    """起一个隔离的应用实例并完成 admin 登录，返回 (client, headers, account)。"""
    monkeypatch.setenv("TALENT_HUB_DATA_DIR", str(tmp_path))

    def make_store():
        store = AttendanceStore(tmp_path / "attendance.db")
        store.initialize()
        return store

    monkeypatch.setattr("app.main.get_attendance_store", make_store)
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    headers = {"X-App-Token": app.state.app_token}
    login = client.post(
        "/api/attendance/login",
        json={"username": "admin", "password": "admin"},
        headers=headers,
    )
    assert login.status_code == 200
    headers["X-Attendance-Token"] = login.json()["token"]
    return client, headers, login.json()["account"]


def _change_password(client, headers):
    changed = client.post(
        "/api/attendance/change-password",
        json={"current_password": "admin", "new_password": NEW_PASSWORD},
        headers=headers,
    )
    assert changed.status_code == 200


def test_default_admin_must_change_password_before_writing(tmp_path, monkeypatch):
    client, headers, account = _boot(tmp_path, monkeypatch)
    assert account["must_change_password"] is True

    blocked = client.post(
        "/api/attendance/policies", json={"code": "P1", "name": "标准考勤"}, headers=headers
    )
    assert blocked.status_code == 403
    assert "初始密码" in blocked.json()["detail"]

    _change_password(client, headers)
    assert client.get("/api/attendance/me", headers=headers).json()["must_change_password"] is False

    allowed = client.post(
        "/api/attendance/policies", json={"code": "P1", "name": "标准考勤"}, headers=headers
    )
    assert allowed.status_code == 200


def test_import_filename_cannot_escape_directory(tmp_path, monkeypatch):
    client, headers, _ = _boot(tmp_path, monkeypatch)
    _change_password(client, headers)

    response = client.post(
        "/api/attendance/imports",
        params={"year": 2026, "month": 8},
        files={"file": ("../../../evil.xlsx", b"not-a-real-xlsx", "application/octet-stream")},
        headers=headers,
    )
    # 伪造内容不是合法 xlsx，解析阶段返回 400；写入发生在解析之前，落点必须被限制在导入目录内
    assert response.status_code == 400

    import_dir = tmp_path / "attendance_imports" / "2026" / "08"
    assert [path.name for path in sorted(import_dir.glob("*.xlsx"))] == ["evil.xlsx"]
    assert not (tmp_path / "evil.xlsx").exists()
    assert not (tmp_path.parent / "evil.xlsx").exists()

    # 记录的原始文件名同样已消毒，供 HR 在导入列表中辨识
    imports = client.get("/api/attendance/imports", headers=headers)
    assert imports.json()["imports"][0]["original_filename"] == "evil.xlsx"
