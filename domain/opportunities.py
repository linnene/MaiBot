"""每月一次随机契机：同月稳定、重启不重抽、不强制出发。"""

from __future__ import annotations

import random

from collections.abc import Callable
from pathlib import Path
from typing import Any

import sqlite3

from domain.clock import month_bounds
from storage import Database


class OpportunityService:
    """持久化每月契机。同步执行，由插件层放入线程池。"""

    def __init__(self, path: Path, timezone: str = "Asia/Shanghai") -> None:
        self.db = Database(Path(path))
        self.timezone = timezone

    def ensure_month(
        self,
        month: str,
        now: float,
        draw: Callable[[], float] | None = None,
    ) -> dict[str, Any]:
        """确保该月存在唯一契机时刻；已有记录时不重新抽取。"""

        start, end = month_bounds(month, self.timezone)

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            existing = connection.execute(
                "SELECT * FROM opportunities WHERE month = ?", (month,)
            ).fetchone()
            if existing is not None:
                return dict(existing) | {"created": False}
            draw_at = draw() if draw is not None else random.uniform(start, end)
            at = float(draw_at)
            if not start <= at < end:
                raise ValueError(f"契机时刻必须落在 {month} 内")
            connection.execute(
                "INSERT INTO opportunities (month, at, created_at) VALUES (?, ?, ?)",
                (month, at, now),
            )
            row = connection.execute(
                "SELECT * FROM opportunities WHERE month = ?", (month,)
            ).fetchone()
            return dict(row) | {"created": True}

        return self.db.transaction(operation)

    def get(self, month: str) -> dict[str, Any] | None:
        def operation(connection: sqlite3.Connection) -> dict[str, Any] | None:
            row = connection.execute(
                "SELECT * FROM opportunities WHERE month = ?", (month,)
            ).fetchone()
            return dict(row) if row is not None else None

        return self.db.read(operation)

    def due(self, now: float) -> list[dict[str, Any]]:
        """返回已到点且尚未处理的契机。"""

        def operation(connection: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = connection.execute(
                "SELECT * FROM opportunities WHERE at <= ? AND handled_at IS NULL "
                "ORDER BY at",
                (now,),
            ).fetchall()
            return [dict(row) for row in rows]

        return self.db.read(operation)

    def mark_handled(self, month: str, outcome: str, now: float) -> dict[str, Any]:
        """标记契机已考虑；考虑不等于出发，也不等于发送消息。"""

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            row = connection.execute(
                "SELECT * FROM opportunities WHERE month = ?", (month,)
            ).fetchone()
            if row is None:
                raise ValueError(f"契机不存在: {month}")
            if row["handled_at"] is None:
                connection.execute(
                    "UPDATE opportunities SET handled_at = ?, outcome = ? WHERE month = ?",
                    (now, str(outcome or ""), month),
                )
            updated = connection.execute(
                "SELECT * FROM opportunities WHERE month = ?", (month,)
            ).fetchone()
            return dict(updated)

        return self.db.transaction(operation)
