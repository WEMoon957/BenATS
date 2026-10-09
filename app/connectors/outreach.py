"""招聘平台触达（打招呼 / 发消息 / 操作）的审核队列与执行。

触达不会自动发送：先由系统生成「待审核草稿」，HR 逐条审核；审核通过后才调用
对应平台的 CLI 真正触达候选人，并记录执行结果。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .boss_cli import BossCliConnector, BossCliError
from .zhaopin_cli import ZhaopinCliConnector, ZhaopinCliError
from ..db.json_backend import JsonMetadataBackend
from ..repository import JsonStore, utc_now

# 触达动作归属的招聘平台
BOSS_PLATFORM = "boss"
ZHAOPIN_PLATFORM = "zhaopin"


@dataclass(frozen=True)
class OutreachAction:
    """一条待执行触达动作，对应一条或多条平台命令。

    kind 取值：
    - ``greet``：打招呼（BOSS：`boss greet`；智联：`zhaopin recommend` + `zhaopin greet`）
    - ``send``：发消息（先打开会话，再发送；目前仅 BOSS 支持）
    - ``action``：平台操作（BOSS：`not-fit` / `remark` / `request-attachment-resume` / `wechat`；
      智联：``request-attachment-resume`` 由 `zhaopin request` 自行打开聊天框完成）

    platform 决定把动作翻译成哪一套命令，取 ``boss`` 或 ``zhaopin``。
    """

    kind: str
    target: str
    text: str = ""
    request_resume: bool = False
    job_keyword: str = ""
    command: str = ""
    remark: str = ""
    platform: str = BOSS_PLATFORM


class OutreachStore(JsonStore):
    """触达草稿仓储：每条草稿一个目录，状态机 pending → approved/rejected → sent/failed。"""

    def __init__(self, root: Path, *, backend: JsonMetadataBackend | None = None) -> None:
        super().__init__(
            root, subdir="outreaches", metadata_name="outreach.json", temp_prefix="outreach-",
            kind="outreach", backend=backend,
        )

    def _new_record(self, record_id: str, now: str, *, action: OutreachAction) -> dict:
        return {
            "id": record_id,
            "action": asdict(action),
            "status": "pending",
            "result": "",
            "error": "",
            "created_at": now,
            "updated_at": now,
            "archived_at": None,
        }

    def create_unique(self, *, action: OutreachAction) -> dict | None:
        """创建去重草稿：相同候选人 + 动作类型 + 命令的待审核草稿已存在时返回 None，否则创建并返回新记录。"""
        with self._lock:
            for record in self.list_records(archived=False):
                if record.get("status") != "pending":
                    continue
                existing = record.get("action", {})
                if (
                    existing.get("target") == action.target
                    and existing.get("kind") == action.kind
                    and existing.get("command", "") == action.command
                ):
                    return None
            return self.create(action=action)


def execute_outreach(
    connector: BossCliConnector | ZhaopinCliConnector, action: OutreachAction
) -> str:
    """把一条触达动作翻译成对应平台的命令并执行，返回执行结果文本。

    connector 必须与 ``action.platform`` 匹配：
    ``boss`` 传 BossCliConnector，``zhaopin`` 传 ZhaopinCliConnector。
    """
    if action.platform == ZHAOPIN_PLATFORM:
        return _execute_zhaopin(connector, action)
    return _execute_boss(connector, action)


def _execute_boss(connector: BossCliConnector, action: OutreachAction) -> str:
    """把触达动作翻译成 boss 命令并执行。"""
    if action.kind == "greet":
        if action.job_keyword:
            # 打招呼必须在推荐列表页完成，先读取该岗位推荐列表把页面切过去
            connector.run("recommend", action.job_keyword)
            return connector.run("greet", action.target, "--job", action.job_keyword)
        return connector.run("greet", action.target)

    if action.kind == "send":
        connector.run("chat", action.target, "--strict")
        args = ["send", "--text", action.text]
        if action.request_resume:
            args.append("--request-resume")
        return connector.run(*args)

    if action.kind == "action":
        connector.run("chat", action.target, "--strict")
        if action.command == "remark":
            return connector.run("action", "remark", "--remark", action.remark)
        return connector.run("action", action.command)

    raise BossCliError(f"未知触达动作：{action.kind}")


def _execute_zhaopin(connector: ZhaopinCliConnector, action: OutreachAction) -> str:
    """把触达动作翻译成 zhaopin 命令并执行。

    打招呼同样要在推荐列表页完成，因此先按岗位切到推荐页再打招呼；
    索要简历由 `zhaopin request` 自行打开聊天框完成，没有单独的聊天页动作。
    """
    if action.kind == "greet":
        if action.job_keyword:
            connector.run("recommend", action.job_keyword)
        return connector.run("greet", action.target)

    if action.kind == "action" and action.command == "request-attachment-resume":
        return connector.run("request", action.target, "resume")

    raise ZhaopinCliError(
        f"智联暂不支持该触达动作：{action.kind} {action.command}".strip()
    )