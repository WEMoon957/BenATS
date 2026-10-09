"""JSON 元数据后端：把 JsonStore 的记录元数据与具体存储解耦。

- 文件后端：每个任务一个目录、元数据原子写 JSON（单机原行为）。
- MySQL 后端：集中存到 `json_records` 表（kind, record_id, archived, updated_at, data）。

文件（简历 / 录音 / 产物）始终留在数据目录磁盘上，与元数据后端无关。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

from .mysql_backend import MySqlDatabase

_RECORD_ID_RE = re.compile(r"[a-f0-9]{32}")

_MYSQL_SCHEMA = """
CREATE TABLE IF NOT EXISTS json_records (
    kind VARCHAR(32) NOT NULL COMMENT '记录类型：job 筛选任务 / call 电话任务 / outreach 触达草稿',
    record_id VARCHAR(64) NOT NULL COMMENT '记录 id（32 位 hex）',
    archived TINYINT NOT NULL DEFAULT 0 COMMENT '是否归档（1 归档 / 0 未归档）',
    updated_at TEXT NOT NULL COMMENT '更新时间',
    data LONGTEXT NOT NULL COMMENT '完整记录 JSON（任务字段与状态）',
    PRIMARY KEY (kind, record_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='任务/触达元数据（简历、录音等文件仍存磁盘）';
"""


class JsonMetadataBackend(ABC):
    @abstractmethod
    def get(self, kind: str, record_id: str) -> dict | None:
        """读取一条记录，不存在返回 None。"""

    @abstractmethod
    def save(self, kind: str, record: dict) -> None:
        """写入（新增或覆盖）一条记录。"""

    @abstractmethod
    def list(self, kind: str, *, archived: bool, limit: int | None, offset: int) -> list[dict]:
        """按 updated_at 倒序返回匹配 archived 的记录。"""

    @abstractmethod
    def delete(self, kind: str, record_id: str) -> None:
        """删除一条记录的元数据。"""


class FileJsonBackend(JsonMetadataBackend):
    """文件后端：`<root>/<record_id>/<metadata_name>` 原子写 JSON。"""

    def __init__(self, root: Path, metadata_name: str, temp_prefix: str) -> None:
        self._root = root
        self._metadata_name = metadata_name
        self._temp_prefix = temp_prefix

    def _path(self, record_id: str) -> Path:
        if not _RECORD_ID_RE.fullmatch(record_id):
            raise ValueError("无效的任务编号")
        return self._root / record_id / self._metadata_name

    def get(self, kind: str, record_id: str) -> dict | None:
        path = self._path(record_id)
        if not path.exists():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        record.setdefault("archived_at", None)
        return record

    def save(self, kind: str, record: dict) -> None:
        path = self._path(record["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            prefix=self._temp_prefix, suffix=".tmp", dir=path.parent,
        )
        os.close(descriptor)
        temp_path = Path(temp_name)
        try:
            temp_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)

    def list(self, kind: str, *, archived: bool, limit: int | None, offset: int) -> list[dict]:
        records: list[dict] = []
        for path in self._root.glob(f"*/{self._metadata_name}"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            record.setdefault("archived_at", None)
            if bool(record["archived_at"]) == archived:
                records.append(record)
        records.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        start = max(0, offset)
        return records[start:] if limit is None else records[start : start + max(0, limit)]

    def delete(self, kind: str, record_id: str) -> None:
        # 元数据文件随存储层的 rmtree 一并删除，这里无需单独处理
        return None


class MySqlJsonBackend(JsonMetadataBackend):
    """MySQL 后端：所有种类的任务记录共用一张 `json_records` 表，按 kind 区分。"""

    def __init__(self, database: MySqlDatabase) -> None:
        self._db = database
        self._db.execute_script(_MYSQL_SCHEMA)

    def get(self, kind: str, record_id: str) -> dict | None:
        rows = self._db.query(
            "SELECT data FROM json_records WHERE kind = %s AND record_id = %s",
            (kind, record_id),
        )
        if not rows:
            return None
        record = json.loads(rows[0]["data"])
        record.setdefault("archived_at", None)
        return record

    def save(self, kind: str, record: dict) -> None:
        self._db.execute(
            "INSERT INTO json_records (kind, record_id, archived, updated_at, data) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON DUPLICATE KEY UPDATE archived = VALUES(archived), "
            "updated_at = VALUES(updated_at), data = VALUES(data)",
            (
                kind,
                record["id"],
                1 if record.get("archived_at") else 0,
                record.get("updated_at", ""),
                json.dumps(record, ensure_ascii=False),
            ),
        )

    def list(self, kind: str, *, archived: bool, limit: int | None, offset: int) -> list[dict]:
        rows = self._db.query(
            "SELECT data FROM json_records WHERE kind = %s AND archived = %s "
            "ORDER BY updated_at DESC",
            (kind, 1 if archived else 0),
        )
        records = [json.loads(row["data"]) for row in rows]
        start = max(0, offset)
        return records[start:] if limit is None else records[start : start + max(0, limit)]

    def delete(self, kind: str, record_id: str) -> None:
        self._db.execute(
            "DELETE FROM json_records WHERE kind = %s AND record_id = %s", (kind, record_id)
        )
