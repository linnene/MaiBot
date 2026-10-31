"""主动游记发布边界：默认关闭、仅白名单群、发送前逐项复核。

本模块只维护队列与校验，不直接调用适配器发送；实际发送由 Host 主动任务完成。
"""

from __future__ import annotations

import hashlib
import json

from collections.abc import Callable
from typing import Any

import sqlite3

from .domain.trips import TripService
from .storage import Database


class Publishing:
    """同步实现；插件层用 ``asyncio.to_thread`` 调用。"""

    def __init__(
        self,
        db: Database,
        trips: TripService,
        auto_share: Callable[[], bool],
        whitelist: Callable[[], list[str]],
        *,
        ttl_seconds: float = 3600.0,
    ) -> None:
        self.db = db
        self.trips = trips
        self.auto_share = auto_share
        self.whitelist = whitelist
        self.ttl_seconds = ttl_seconds

    def propose(self, event_id: str, group_session: str, now: float) -> dict[str, Any]:
        """为已发生的自身经历创建待发送项；不满足许可时不排队。"""

        if not self.auto_share():
            return {"queued": False, "reason": "auto_share_disabled"}
        if str(group_session) not in {str(item) for item in self.whitelist()}:
            return {"queued": False, "reason": "not_whitelisted"}

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            event = connection.execute(
                "SELECT * FROM trip_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if event is None or float(event["at"]) > now:
                return {"queued": False, "reason": "event_not_occurred"}
            trip = connection.execute(
                "SELECT * FROM trips WHERE trip_id = ?", (event["trip_id"],)
            ).fetchone()
            if trip is None or trip["status"] == "cancelled":
                return {"queued": False, "reason": "trip_cancelled"}
            token = hashlib.sha1(
                f"{event_id}:{group_session}:{trip['revision']}".encode()
            ).hexdigest()[:20]
            existing = connection.execute(
                "SELECT * FROM publish_queue WHERE token = ?", (token,)
            ).fetchone()
            if existing is not None:
                return dict(existing) | {"queued": existing["status"] == "queued"}
            expires_at = now + self.ttl_seconds
            connection.execute(
                "INSERT INTO publish_queue "
                "(token, event_id, group_session, revision, expires_at, status, "
                "created_at, updated_at, receipt) "
                "VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, NULL)",
                (token, event_id, group_session, int(trip["revision"]), expires_at, now, now),
            )
            row = connection.execute(
                "SELECT * FROM publish_queue WHERE token = ?", (token,)
            ).fetchone()
            return dict(row) | {"queued": True}

        return self.db.transaction(operation)

    def before_send(self, token: str, now: float) -> dict[str, Any]:
        """发送前复核白名单、行程版本与有效期；不通过即作废，不重试。"""

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            row = connection.execute(
                "SELECT * FROM publish_queue WHERE token = ?", (token,)
            ).fetchone()
            if row is None:
                return {"allowed": False, "reason": "unknown_token"}
            if row["status"] != "queued":
                return {"allowed": False, "reason": f"status_{row['status']}"}
            reason = self._invalidate_reason(connection, row, now)
            if reason:
                self._cancel(connection, token, now)
                return {"allowed": False, "reason": reason}
            return {"allowed": True, "token": token, "event_id": row["event_id"]}

        return self.db.transaction(operation)

    def after_send(self, token: str, receipt: Any, now: float) -> dict[str, Any]:
        """只有明确回执才记为已发送；结果未知保持 unknown，不自动重试。"""

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            row = connection.execute(
                "SELECT * FROM publish_queue WHERE token = ?", (token,)
            ).fetchone()
            if row is None:
                return {"updated": False, "reason": "unknown_token"}
            if row["status"] == "sent":
                return dict(row)
            if row["status"] != "queued":
                # 已作废或未知的发送项不能靠事后回执重新变成已发送。
                return dict(row)
            delivered = _has_receipt(receipt)
            status = "sent" if delivered else "unknown"
            connection.execute(
                "UPDATE publish_queue SET status = ?, updated_at = ?, receipt = ? WHERE token = ?",
                (status, now, json.dumps(receipt, ensure_ascii=False, default=str), token),
            )
            updated = connection.execute(
                "SELECT * FROM publish_queue WHERE token = ?", (token,)
            ).fetchone()
            return dict(updated)

        return self.db.transaction(operation)

    def _invalidate_reason(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        now: float,
    ) -> str:
        if now >= float(row["expires_at"]):
            return "expired"
        if not self.auto_share():
            return "auto_share_disabled"
        if str(row["group_session"]) not in {str(item) for item in self.whitelist()}:
            return "not_whitelisted"
        trip = connection.execute(
            "SELECT * FROM trips WHERE trip_id = "
            "(SELECT trip_id FROM trip_events WHERE event_id = ?)",
            (row["event_id"],),
        ).fetchone()
        if trip is None or trip["status"] == "cancelled":
            return "trip_cancelled"
        if int(trip["revision"]) != int(row["revision"]):
            return "stale_revision"
        return ""

    @staticmethod
    def _cancel(connection: sqlite3.Connection, token: str, now: float) -> None:
        connection.execute(
            "UPDATE publish_queue SET status = 'cancelled', updated_at = ? WHERE token = ?",
            (now, token),
        )


def _has_receipt(receipt: Any) -> bool:
    if isinstance(receipt, dict):
        return bool(receipt.get("success") or receipt.get("message_id") or receipt.get("receipt_id"))
    return bool(receipt)
