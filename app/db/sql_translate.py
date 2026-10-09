"""SQLite → MySQL 的 SQL 方言翻译，仅覆盖本项目实际用到的差异。

- 占位符 `?` → `%s`（跳过字符串字面量内的 `?`）
- `INSERT OR IGNORE` → `INSERT IGNORE`
- `ON CONFLICT(x) DO UPDATE SET a = excluded.a` → `ON DUPLICATE KEY UPDATE a = VALUES(a)`

其余（CASE WHEN、聚合、LIMIT/OFFSET、TEXT 存 JSON）两方言一致，不处理。
"""

from __future__ import annotations

import re

_INSERT_OR_IGNORE_RE = re.compile(r"INSERT\s+OR\s+IGNORE", re.IGNORECASE)
_ON_CONFLICT_RE = re.compile(r"ON\s+CONFLICT\s*\([^)]*\)\s+DO\s+UPDATE\s+SET\s+", re.IGNORECASE)
_EXCLUDED_RE = re.compile(r"excluded\.(\w+)", re.IGNORECASE)


def translate(sql: str) -> str:
    """把 SQLite 方言的 SQL 翻译成 MySQL 方言。"""
    return _on_conflict(_insert_or_ignore(_placeholders(sql)))


def _placeholders(sql: str) -> str:
    """`?` 换成 `%s`，但跳过单引号字符串字面量内的 `?`。"""
    out: list[str] = []
    in_single = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            # SQLite 与 MySQL 都用两个单引号转义字面量内的单引号
            if in_single and i + 1 < len(sql) and sql[i + 1] == "'":
                out.append("''")
                i += 2
                continue
            in_single = not in_single
            out.append(ch)
        elif ch == "?" and not in_single:
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _insert_or_ignore(sql: str) -> str:
    return _INSERT_OR_IGNORE_RE.sub("INSERT IGNORE", sql)


def _on_conflict(sql: str) -> str:
    match = _ON_CONFLICT_RE.search(sql)
    if not match:
        return sql
    tail = _EXCLUDED_RE.sub(r"VALUES(\1)", sql[match.end() :])
    return sql[: match.start()] + "ON DUPLICATE KEY UPDATE " + tail
