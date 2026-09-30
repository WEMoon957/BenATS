"""ZhaopinCliConnector 输出解析的单元测试（用 mock 替代真实 zhaopin 子进程）。"""

import pytest

from app.connectors.zhaopin_cli import ZhaopinCliConnector, ZhaopinCliError


def make_connector(monkeypatch, output: str) -> ZhaopinCliConnector:
    connector = ZhaopinCliConnector(bin_path="/fake/zhaopin")
    monkeypatch.setattr(connector, "run", lambda *args, **kwargs: output)
    return connector


def test_list_positions_parses_names(monkeypatch):
    out = (
        "职位搜索结果（关键词：后端）：共 2 个。\n"
        "\n"
        "  1. 后端开发工程师\n"
        "  2. 后端架构师\n"
    )
    connector = make_connector(monkeypatch, out)
    assert connector.list_positions("后端") == [
        {"name": "后端开发工程师"},
        {"name": "后端架构师"},
    ]


def test_list_recommend_parses_basic_info_and_greet_state(monkeypatch):
    out = (
        "当前岗位：后端开发工程师\n"
        "\n"
        "推荐候选人：共 2 人。\n"
        "\n"
        "  1. 张三｜信息:28岁 / 5年经验 / 本科｜可打招呼\n"
        "     经历: 某互联网公司 后端开发\n"
        "  2. 李四｜信息:31岁 / 8年经验 / 硕士｜已打过招呼\n"
    )
    connector = make_connector(monkeypatch, out)
    assert connector.list_recommend("后端开发工程师") == [
        {"name": "张三", "basic_info": "28岁 / 5年经验 / 本科", "can_greet": True},
        {"name": "李四", "basic_info": "31岁 / 8年经验 / 硕士", "can_greet": False},
    ]


def test_list_recommend_dedupes_same_name(monkeypatch):
    out = "  1. 张三｜信息:28岁｜可打招呼\n  2. 张三｜信息:28岁｜可打招呼\n"
    connector = make_connector(monkeypatch, out)
    assert connector.list_recommend() == [
        {"name": "张三", "basic_info": "28岁", "can_greet": True},
    ]


def test_open_detail_strips_whitespace(monkeypatch):
    connector = make_connector(monkeypatch, "\n  张三的在线简历正文  \n")
    assert connector.open_detail("张三") == "张三的在线简历正文"


def test_open_detail_rejects_empty(monkeypatch):
    connector = make_connector(monkeypatch, "   \n")
    with pytest.raises(ZhaopinCliError):
        connector.open_detail("张三")


def test_request_resume_passes_resume_kind(monkeypatch):
    captured: dict = {}
    connector = ZhaopinCliConnector(bin_path="/fake/zhaopin")

    def fake_run(*args, **kwargs):
        captured["args"] = args
        return "ok"

    monkeypatch.setattr(connector, "run", fake_run)
    connector.request_resume("张三")
    assert captured["args"] == ("request", "张三", "resume")


def test_request_contact_requires_a_kind(monkeypatch):
    connector = ZhaopinCliConnector(bin_path="/fake/zhaopin")
    monkeypatch.setattr(connector, "run", lambda *args, **kwargs: "ok")

    with pytest.raises(ZhaopinCliError):
        connector.request_contact("张三")


def test_request_contact_passes_both_kinds(monkeypatch):
    captured: dict = {}
    connector = ZhaopinCliConnector(bin_path="/fake/zhaopin")

    def fake_run(*args, **kwargs):
        captured["args"] = args
        return "ok"

    monkeypatch.setattr(connector, "run", fake_run)
    connector.request_contact("张三", phone=True, wechat=True)
    assert captured["args"] == ("request", "张三", "phone", "wechat")


def test_run_reports_missing_binary(monkeypatch):
    connector = ZhaopinCliConnector(bin_path="/definitely/not/exists/zhaopin")
    with pytest.raises(ZhaopinCliError) as excinfo:
        connector.run("positions")
    assert "未找到" in str(excinfo.value)


def test_run_raises_on_nonzero_exit(monkeypatch):
    def fake_run(*args, **kwargs):
        raise ZhaopinCliError("zhaopin-cli 执行失败：boom")

    connector = ZhaopinCliConnector(bin_path="/fake/zhaopin")
    monkeypatch.setattr(connector, "run", fake_run)
    with pytest.raises(ZhaopinCliError):
        connector.list_positions()
