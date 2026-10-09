"""极简 .env 加载：启动时把 `.env` 的 KEY=VALUE 注入环境变量。

只读当前工作目录与仓库根下的 `.env`，不覆盖已有的环境变量（shell 里显式设置优先）。
这样与 `docker-compose.yml` 共用同一份 `.env`，正常启动即可切换 MySQL，无需每次手设环境变量。
"""

from __future__ import annotations

import os
from pathlib import Path


def _apply(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_dotenv() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    for candidate in (Path(".env"), repo_root / ".env"):
        if candidate.exists():
            _apply(candidate)
