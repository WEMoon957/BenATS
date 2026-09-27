"""通过子进程调用 boss-cli，把 BOSS 直聘数据接入 Talent Hub。

boss-cli 是独立部署的 Node/TS 命令行工具（基于 CDP 驱动本机 Chrome 操作 BOSS
直聘 B 端，见 BenATS 仓库 `boss-cli/` 子目录）。本模块只负责：

1. 调用 `boss` 命令；
2. 把它的纯文本输出解析为结构化数据；
3. 供上层把结果写入 Talent Hub 任务。

前置条件：同一台机器上已安装 boss-cli、已执行 `boss login`，并安装了 Chrome。
可用环境变量 `BOSSCLI_BIN` 覆盖 boss 可执行文件路径（默认 `boss`）。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


class BossCliError(RuntimeError):
    """调用或解析 boss-cli 失败。"""


def boss_bin() -> str:
    return os.getenv("BOSSCLI_BIN", "boss")


class BossCliConnector:
    """boss-cli 子进程封装。"""

    def __init__(self, bin_path: str | None = None) -> None:
        self.bin = bin_path or boss_bin()

    def run(self, *args: str, timeout: int = 180) -> str:
        """执行一条 `boss` 命令并返回标准输出；非零退出时抛 BossCliError。"""
        cmd = [self.bin, *args]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise BossCliError(
                f"未找到 boss-cli 可执行文件（{self.bin}）。请安装 boss-cli 或设置 BOSSCLI_BIN 环境变量。"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise BossCliError(f"boss-cli 命令超时（{timeout}s）：{' '.join(cmd)}") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise BossCliError(
                f"boss-cli 执行失败：{' '.join(cmd)}\n{detail or '（无错误输出）'}"
            )
        return proc.stdout or ""

    def list_positions(self) -> list[dict]:
        """读取职位列表，返回 `[{"name": ..., "status": ...}, ...]`。"""
        out = self.run("positions")
        positions: list[dict] = []
        for line in out.splitlines():
            match = re.match(r"^\s*\d+\.\s+(.+)$", line)
            if not match:
                continue
            fields = [part.strip() for part in match.group(1).split("｜")]
            name = fields[0]
            if not name:
                continue
            status = ""
            for part in fields[1:]:
                if part.startswith("状态:"):
                    status = part[len("状态:"):].strip()
                    break
            positions.append({"name": name, "status": status})
        return positions

    def fetch_jd(self, position_name: str) -> str:
        """抓取指定职位 JD，返回 Markdown 文本（`boss jd` 自带缓存）。"""
        text = self.run("jd", position_name).strip()
        if not text:
            raise BossCliError(f"岗位「{position_name}」JD 为空。")
        return text

    def list_recommend(self, job_keyword: str | None = None) -> list[dict]:
        """读取推荐候选人列表（`boss recommend`），返回 `[{"name": ...}, ...]`。"""
        args = ["recommend"]
        if job_keyword:
            args.append(job_keyword)
        return self._parse_candidate_lines(self.run(*args))

    def list_contacts(self) -> list[dict]:
        """读取已沟通候选人列表（`boss list`）。"""
        return self._parse_candidate_lines(self.run("list"))

    def download_resume(self, candidate_name: str) -> Path:
        """下载候选人附件简历，返回本地文件路径（`boss download-resume`）。"""
        out = self.run("download-resume", candidate_name)
        match = re.search(r"附件简历已下载[:：]\s*(\S+)", out)
        if not match:
            raise BossCliError(
                f"未能从 boss-cli 输出解析附件简历路径：{out.strip()[:200]}"
            )
        path = Path(match.group(1))
        if not path.is_file():
            raise BossCliError(f"附件简历文件不存在：{path}")
        return path

    @staticmethod
    def _parse_candidate_lines(out: str) -> list[dict]:
        """从 `boss list` / `boss recommend` 纯文本输出中提取候选人姓名（去重）。"""
        candidates: list[dict] = []
        seen: set[str] = set()
        for line in out.splitlines():
            match = re.match(r"^(?:-\s*)?\d+\.\s+([^｜]+)", line.strip())
            if not match:
                continue
            name = match.group(1).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            candidates.append({"name": name})
        return candidates