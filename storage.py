"""SQLite 连接管理：短连接、WAL、显式事务。

领域层保持同步实现；插件层负责用 ``asyncio.to_thread`` 调用，避免阻塞事件循环。
"""

from __future__ import annotations

import sqlite3

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS monthly_credits (
    month TEXT PRIMARY KEY,
    amount_fen INTEGER NOT NULL CHECK (amount_fen > 0),
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reservations (
    key TEXT PRIMARY KEY,
    trip_id TEXT NOT NULL,
    amount_fen INTEGER NOT NULL CHECK (amount_fen > 0),
    expires_at REAL NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('reserved', 'settled', 'released')),
    created_at REAL NOT NULL,
    settled_at REAL
);
CREATE TABLE IF NOT EXISTS ledger_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('spend', 'refund')),
    trip_id TEXT NOT NULL,
    amount_fen INTEGER NOT NULL CHECK (amount_fen > 0),
    source_payment_key TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_entries_source
    ON ledger_entries (source_payment_key);
"""

SCHEMA_VERSION = "1"


class Database:
    """极简 SQLite 封装，每次操作使用一个短连接。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(_SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('schema_version', ?)",
                (SCHEMA_VERSION,),
            )
        finally:
            connection.close()
        self.path.chmod(0o600)

    def read(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        """在只读连接上执行操作。"""

        connection = self._connect()
        try:
            return operation(connection)
        finally:
            connection.close()

    def transaction(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        """在 ``BEGIN IMMEDIATE`` 事务中执行操作，失败回滚。"""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                result = operation(connection)
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
            return result
        finally:
            connection.close()

    def run(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        """兼容旧调用的别名，等价于 :meth:`read`。"""

        return self.read(operation)


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """把 ``sqlite3.Row`` 转成普通字典。"""

    return dict(row) if row is not None else None
