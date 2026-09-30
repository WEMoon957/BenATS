"""通过子进程调用 zhaopin-cli，把智联招聘数据接入 Talent Hub。

zhaopin-cli 是独立部署的 Node/TS 命令行工具（基于 CDP 驱动本机 Chrome 操作
智联招聘 B 端，见本仓库 `zhaopin-cli/` 子目录）。本模块只负责：

1. 调用 `zhaopin` 命令；
2. 把它的纯文本输出解析为结构化数据；
3. 供上层把结果写入 Talent Hub 任务。

前置条件：同一台机器上已安装 zhaopin-cli、已执行 `zhaopin login`，并安装了 Chrome。
可用环境变量 `ZHAOPINCLI_BIN` 覆盖 zhaopin 可执行文件路径（默认 `zhaopin`）。

登录必须由用户本人在浏览器里完成，本模块不代填任何平台凭据。
"""

from __future__ import annotations

import os
import re
import subprocess


class ZhaopinCliError(RuntimeError):
    """调用或解析 zhaopin-cli 失败。"""


def zhaopin_bin() -> str:
    """返回 zhaopin 可执行文件路径。"""
    return os.getenv("ZHAOPINCLI_BIN", "zhaopin")


class ZhaopinCliConnector:
    """zhaopin-cli 子进程封装。"""

    def __init__(self, bin_path: str | None = None) -> None:
        self.bin = bin_path or zhaopin_bin()

    def run(self, *args: str, timeout: int = 180) -> str:
        """执行一条 `zhaopin` 命令并返回标准输出；非零退出时抛 ZhaopinCliError。"""
        cmd = [self.bin, *args]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise ZhaopinCliError(
                f"未找到 zhaopin-cli 可执行文件（{self.bin}）。"
                "请安装 zhaopin-cli 或设置 ZHAOPINCLI_BIN 环境变量。"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ZhaopinCliError(
                f"zhaopin-cli 命令超时（{timeout}s）：{' '.join(cmd)}"
            ) from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise ZhaopinCliError(
                f"zhaopin-cli 执行失败：{' '.join(cmd)}\n{detail or '（无错误输出）'}"
            )
        return proc.stdout or ""

    def login(self) -> str:
        """打开登录页，由用户手动完成登录。"""
        return self.run("login")

    def list_positions(self, keyword: str | None = None) -> list[dict]:
        """读取职位选择弹层里的岗位列表，返回 `[{"name": ...}, ...]`。"""
        args = ["positions"]
        if keyword:
            args.append(keyword)
        out = self.run(*args)
        positions: list[dict] = []
        for line in out.splitlines():
            match = re.match(r"^\s*\d+\.\s+(.+)$", line)
            if not match:
                continue
            name = match.group(1).strip()
            if not name:
                continue
            if all(item["name"] != name for item in positions):
                positions.append({"name": name})
        return positions

    def list_recommend(self, job_keyword: str | None = None) -> list[dict]:
        """读取推荐候选人列表，返回姓名、基本信息与是否可打招呼。"""
        args = ["recommend"]
        if job_keyword:
            args.append(job_keyword)
        return self._parse_candidate_lines(self.run(*args))

    def open_detail(self, candidate_name: str) -> str:
        """打开候选人详情并返回正文文本。"""
        text = self.run("open", candidate_name).strip()
        if not text:
            raise ZhaopinCliError(f"候选人「{candidate_name}」的详情为空。")
        return text

    def greet(self, candidate_name: str) -> str:
        """对候选人打招呼，返回执行结果说明。"""
        return self.run("greet", candidate_name)

    def request_resume(self, candidate_name: str) -> str:
        """打开候选人聊天框并索要附件简历。"""
        return self.run("request", candidate_name, "resume")

    def request_contact(
        self, candidate_name: str, phone: bool = False, wechat: bool = False
    ) -> str:
        """索要候选人的电话或微信；两者至少指定一项。"""
        kinds: list[str] = []
        if phone:
            kinds.append("phone")
        if wechat:
            kinds.append("wechat")
        if not kinds:
            raise ZhaopinCliError("请至少指定 phone 或 wechat 其中一项。")
        return self.run("request", candidate_name, *kinds)

    @staticmethod
    def _parse_candidate_lines(out: str) -> list[dict]:
        """解析 `zhaopin recommend` 的纯文本输出，按姓名去重。"""
        candidates: list[dict] = []
        seen: set[str] = set()
        for line in out.splitlines():
            match = re.match(r"^\s*\d+\.\s+([^｜]+)(?:｜(.*))?$", line)
            if not match:
                continue
            name = match.group(1).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            rest = (match.group(2) or "").strip()
            basic_info = ""
            part_can_greet = "可打招呼" in rest
            for part in rest.split("｜"):
                if part.startswith("信息:"):
                    basic_info = part[len("信息:"):].strip()
                    break
            candidates.append(
                {"name": name, "basic_info": basic_info, "can_greet": part_can_greet}
            )
        return candidates
