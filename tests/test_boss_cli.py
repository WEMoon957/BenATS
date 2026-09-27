"""BossCliConnector 输出解析的单元测试（用 mock 替代真实 boss 子进程）。"""

from pathlib import Path

from app.connectors.boss_cli import BossCliConnector, BossCliError


def make_connector(monkeypatch, output: str):
    connector = BossCliConnector(bin_path="/fake/boss")
    monkeypatch.setattr(connector, "run", lambda *args, **kwargs: output)
    return connector


def test_list_positions_parses_name_and_status(monkeypatch):
    out = (
        "已读取 2 个职位。\n"
        "状态统计：开放中 1｜已关闭 1\n"
        "来源页面：https://www.zhipin.com/web/chat/job/list\n"
        "职位明细：\n"
        "1. 前端工程师｜状态:开放中｜标签:急招｜北京·3年｜看过我:10｜ID:abc\n"
        "2. 后端工程师｜状态:已关闭｜上海·5年｜看过我:1｜ID:def\n"
    )
    connector = make_connector(monkeypatch, out)
    assert connector.list_positions() == [
        {"name": "前端工程师", "status": "开放中"},
        {"name": "后端工程师", "status": "已关闭"},
    ]


def test_fetch_jd_strips_output(monkeypatch):
    out = "\n# 前端工程师\n\n## 职位描述\n负责前端架构。\n"
    connector = make_connector(monkeypatch, out)
    assert connector.fetch_jd("前端工程师") == "# 前端工程师\n\n## 职位描述\n负责前端架构。"


def test_list_recommend_parses_names(monkeypatch):
    out = (
        "当前岗位：前端工程师\n\n"
        "推荐列表（按来源分组）：共 2 人。\n\n"
        "常规推荐（2）\n"
        "  - 1. 张三｜薪资:20-30K｜信息:北京·3年｜可打招呼\n"
        "    优势: 熟悉 React\n"
        "  - 2. 李四｜薪资:15-25K｜信息:上海·2年｜已打招呼\n"
        "    优势: 熟悉 Vue\n\n"
        "打招呼产生的推荐（0）\n"
        "  - 暂无\n"
    )
    connector = make_connector(monkeypatch, out)
    assert connector.list_recommend("前端工程师") == [
        {"name": "张三"},
        {"name": "李四"},
    ]


def test_list_contacts_parses_names_and_dedupes(monkeypatch):
    out = (
        "沟通列表共 3 人，其中 1 人有未读消息。\n"
        "候选人明细：\n"
        "1. 张三｜岗位:前端工程师｜时间:今天\n"
        "2. 王五｜岗位:后端工程师｜时间:昨天\n"
        "3. 张三｜岗位:前端工程师｜时间:更早\n"
    )
    connector = make_connector(monkeypatch, out)
    assert connector.list_contacts() == [{"name": "张三"}, {"name": "王五"}]


def test_download_resume_parses_path(monkeypatch, tmp_path):
    resume = tmp_path / "张三-简历.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    connector = make_connector(monkeypatch, f"附件简历已下载：{resume}\n")
    assert connector.download_resume("张三") == resume


def test_download_resume_rejects_missing_file(monkeypatch):
    connector = make_connector(monkeypatch, "附件简历已下载：/nonexistent/简历.pdf\n")
    try:
        connector.download_resume("张三")
    except BossCliError as exc:
        assert "不存在" in str(exc)
    else:
        raise AssertionError("应当抛出 BossCliError")