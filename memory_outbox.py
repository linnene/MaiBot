"""已发生旅行经历的投递箱：只导出已推进事件，确认后才不再重复。"""

from __future__ import annotations

import time

from typing import Any

import sqlite3

from storage import Database


class MemoryOutbox:
    """同步实现；插件层用 ``asyncio.to_thread`` 调用。"""

    def __init__(self, db: Database) -> None:
        self.db = db

    def pending(self, now: float, limit: int = 20) -> list[dict[str, Any]]:
        """返回已到时间且未被确认的自身经历，附带明确归属字段。"""

        bounded = max(1, min(int(limit), 100))

        def operation(connection: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = connection.execute(
                "SELECT e.* FROM trip_events e "
                "LEFT JOIN travel_event_acks a ON a.event_id = e.event_id "
                "WHERE a.event_id IS NULL AND e.at <= ? "
                "ORDER BY e.at, e.event_id LIMIT ?",
                (now, bounded),
            ).fetchall()
            return [
                {
                    **dict(row),
                    "subject": "bot",
                    "source_kind": "virtual_self_event",
                }
                for row in rows
            ]

        return self.db.read(operation)

    def ack(self, event_ids: list[str], now: float | None = None) -> int:
        """记录接收确认；重复确认不产生新记录。"""

        stamp = time.time() if now is None else now
        identifiers = [str(item) for item in event_ids if str(item).strip()]
        if not identifiers:
            return 0

        def operation(connection: sqlite3.Connection) -> int:
            count = 0
            for event_id in identifiers:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO travel_event_acks (event_id, acked_at) VALUES (?, ?)",
                    (event_id, stamp),
                )
                count += cursor.rowcount
            return count

        return self.db.transaction(operation)

    def acked(self, event_id: str) -> bool:
        def operation(connection: sqlite3.Connection) -> bool:
            row = connection.execute(
                "SELECT 1 FROM travel_event_acks WHERE event_id = ?", (event_id,)
            ).fetchone()
            return row is not None

        return self.db.read(operation)
