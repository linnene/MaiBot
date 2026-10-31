"""行程领域：版本化状态机、时间推进与只读投影。"""

from __future__ import annotations

import uuid

from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import sqlite3

from .clock import active_segment, overlapping
from ..storage import Database

TERMINAL_STATUSES = {"completed", "cancelled"}
REVISABLE_STATUSES = {"idea", "research", "draft", "confirmed", "preparing"}
ALLOWED_PHASES = {"preparing", "in_transit", "staying", "returning", "free"}
ALLOWED_KINDS = {"flight", "train", "hotel", "activity", "transit", "return", "free"}


class TripNotFound(ValueError):
    """行程不存在。"""


class ActiveTripExists(ValueError):
    """已有一个未结束的行程。"""


class StaleRevision(ValueError):
    """调用方基于旧版本修改行程。"""


class MissingReservation(ValueError):
    """确定硬行程前缺少资金预留。"""


class TripService:
    """行程状态机。同步执行，由插件层放入线程池。"""

    def __init__(self, path: Path, ledger: Any | None = None) -> None:
        self.db = Database(Path(path))
        self.ledger = ledger

    # ---------------------------------------------------------------- 读取

    def get(self, trip_id: str) -> dict[str, Any]:
        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            row = connection.execute("SELECT * FROM trips WHERE trip_id = ?", (trip_id,)).fetchone()
            if row is None:
                raise TripNotFound(f"行程不存在: {trip_id}")
            return self._serialize(connection, row)

        return self.db.read(operation)

    def active_trip(self) -> dict[str, Any] | None:
        def operation(connection: sqlite3.Connection) -> dict[str, Any] | None:
            row = connection.execute("SELECT * FROM trips WHERE active = 1").fetchone()
            return self._serialize(connection, row) if row is not None else None

        return self.db.read(operation)

    # ---------------------------------------------------------------- 写入

    def propose(
        self,
        title: str,
        now: float,
        destination: str = "",
        timezone: str = "Asia/Shanghai",
        trip_id: str | None = None,
    ) -> dict[str, Any]:
        """创建一个想法阶段的新行程；同一时间只允许一个未结束行程。"""

        title = str(title or "").strip()
        if not title:
            raise ValueError("行程标题不能为空")
        ZoneInfo(timezone)
        identifier = trip_id or uuid.uuid4().hex

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            active = connection.execute("SELECT trip_id FROM trips WHERE active = 1").fetchone()
            if active is not None:
                raise ActiveTripExists(f"已有未结束行程: {active['trip_id']}")
            connection.execute(
                "INSERT INTO trips "
                "(trip_id, revision, status, title, destination, timezone, "
                "created_at, updated_at, confirmed_at, cancelled_at, active) "
                "VALUES (?, 0, 'idea', ?, ?, ?, ?, ?, NULL, NULL, 1)",
                (identifier, title, str(destination or ""), timezone, now, now),
            )
            row = connection.execute(
                "SELECT * FROM trips WHERE trip_id = ?", (identifier,)
            ).fetchone()
            return self._serialize(connection, row)

        return self.db.transaction(operation)

    def revise(
        self,
        trip_id: str,
        expected_revision: int,
        patch: dict[str, Any],
        now: float,
    ) -> dict[str, Any]:
        """按版本号修改行程；旧版本结果被拒绝。"""

        allowed = {"title", "destination", "timezone", "segments"}
        unknown = set(patch) - allowed
        if unknown:
            raise ValueError(f"不支持修改的字段: {sorted(unknown)}")

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            row = connection.execute("SELECT * FROM trips WHERE trip_id = ?", (trip_id,)).fetchone()
            if row is None:
                raise TripNotFound(f"行程不存在: {trip_id}")
            if int(row["revision"]) != int(expected_revision):
                raise StaleRevision(
                    f"行程版本已变化：期望 {expected_revision}，实际 {row['revision']}"
                )
            if row["status"] not in REVISABLE_STATUSES:
                raise ValueError(f"当前状态不可修改: {row['status']}")
            segments = self._load_segments(connection, trip_id)
            if segments and min(item["start_at"] for item in segments) <= now:
                raise ValueError("行程已经开始，不能整体改期")

            fields: list[str] = ["revision = revision + 1", "updated_at = ?"]
            values: list[Any] = [now]
            if "title" in patch:
                title = str(patch["title"] or "").strip()
                if not title:
                    raise ValueError("行程标题不能为空")
                fields.append("title = ?")
                values.append(title)
            if "destination" in patch:
                fields.append("destination = ?")
                values.append(str(patch["destination"] or ""))
            if "timezone" in patch:
                ZoneInfo(str(patch["timezone"]))
                fields.append("timezone = ?")
                values.append(str(patch["timezone"]))
            if "segments" in patch:
                parsed = self._parse_segments(trip_id, patch["segments"])
                self._validate_segments(parsed)
                if row["status"] in {"confirmed", "preparing"}:
                    # 已确认行程新增硬段必须先有有效预留，改期不能绕过预算检查。
                    for item in parsed:
                        if not item["requires_funds"]:
                            continue
                        key = item["reservation_key"]
                        reservation = (
                            connection.execute(
                                "SELECT state, expires_at FROM reservations WHERE key = ?",
                                (key,),
                            ).fetchone()
                            if key
                            else None
                        )
                        if (
                            reservation is None
                            or reservation["state"] != "reserved"
                            or float(reservation["expires_at"]) <= now
                        ):
                            raise MissingReservation(
                                f"行程段缺少有效资金预留: {item['segment_id']}"
                            )
                connection.execute("DELETE FROM trip_segments WHERE trip_id = ?", (trip_id,))
                for ordinal, item in enumerate(parsed):
                    connection.execute(
                        "INSERT INTO trip_segments "
                        "(trip_id, segment_id, ordinal, kind, title, start_at, end_at, "
                        "timezone, from_place, to_place, phase, reservation_key, "
                        "requires_funds, settled) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                        (
                            trip_id,
                            item["segment_id"],
                            ordinal,
                            item["kind"],
                            item["title"],
                            item["start_at"],
                            item["end_at"],
                            item["timezone"],
                            item["from_place"],
                            item["to_place"],
                            item["phase"],
                            item["reservation_key"],
                            1 if item["requires_funds"] else 0,
                        ),
                    )
            values.append(trip_id)
            connection.execute(
                f"UPDATE trips SET {', '.join(fields)} WHERE trip_id = ?", values
            )
            updated = connection.execute(
                "SELECT * FROM trips WHERE trip_id = ?", (trip_id,)
            ).fetchone()
            return self._serialize(connection, updated)

        return self.db.transaction(operation)

    def confirm(self, trip_id: str, expected_revision: int, now: float) -> dict[str, Any]:
        """确认行程；硬行程段必须已有资金预留。"""

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            row = connection.execute("SELECT * FROM trips WHERE trip_id = ?", (trip_id,)).fetchone()
            if row is None:
                raise TripNotFound(f"行程不存在: {trip_id}")
            if int(row["revision"]) != int(expected_revision):
                raise StaleRevision(
                    f"行程版本已变化：期望 {expected_revision}，实际 {row['revision']}"
                )
            if row["status"] not in {"idea", "research", "draft"}:
                raise ValueError(f"当前状态不能确认: {row['status']}")
            segments = self._load_segments(connection, trip_id)
            if not segments:
                raise ValueError("确认行程前至少需要一个行程段")
            for item in segments:
                if not item["requires_funds"]:
                    continue
                key = item["reservation_key"]
                if not key:
                    raise MissingReservation(f"行程段缺少资金预留: {item['segment_id']}")
                if not self._reservation_valid(key, now):
                    raise MissingReservation(f"资金预留不存在或已失效: {key}")
            connection.execute(
                "UPDATE trips SET status = 'confirmed', confirmed_at = ?, updated_at = ?, "
                "revision = revision + 1 WHERE trip_id = ?",
                (now, now, trip_id),
            )
            updated = connection.execute(
                "SELECT * FROM trips WHERE trip_id = ?", (trip_id,)
            ).fetchone()
            return self._serialize(connection, updated)

        return self.db.transaction(operation)

    def cancel(self, trip_id: str, expected_revision: int, now: float) -> dict[str, Any]:
        """取消行程；已结束的行程不能取消。"""

        releases: list[str] = []

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            row = connection.execute("SELECT * FROM trips WHERE trip_id = ?", (trip_id,)).fetchone()
            if row is None:
                raise TripNotFound(f"行程不存在: {trip_id}")
            if int(row["revision"]) != int(expected_revision):
                raise StaleRevision(
                    f"行程版本已变化：期望 {expected_revision}，实际 {row['revision']}"
                )
            if row["status"] in TERMINAL_STATUSES:
                raise ValueError(f"行程已结束，不能取消: {row['status']}")
            connection.execute(
                "UPDATE trips SET status = 'cancelled', cancelled_at = ?, updated_at = ?, "
                "revision = revision + 1, active = 0 WHERE trip_id = ?",
                (now, now, trip_id),
            )
            updated = connection.execute(
                "SELECT * FROM trips WHERE trip_id = ?", (trip_id,)
            ).fetchone()
            cancelled = self._serialize(connection, updated)
            releases.extend(
                item["reservation_key"]
                for item in cancelled["segments"]
                if item["reservation_key"] and not item["settled"]
            )
            return cancelled

        result = self.db.transaction(operation)
        # 取消后释放未结算预留，避免资金被占住到原出发日。
        for key in releases:
            try:
                self.ledger.release(key, now)
            except ValueError:
                continue
        return result

    # ---------------------------------------------------------------- 推进

    def advance(self, now: float) -> list[dict[str, Any]]:
        """推进所有已到时间边界；只记录已确定的行程段，不虚构细节。"""

        def operation(connection: sqlite3.Connection) -> list[dict[str, Any]]:
            rows = connection.execute(
                "SELECT * FROM trips WHERE active = 1 AND status IN "
                "('confirmed', 'preparing', 'in_transit', 'staying', 'returning')"
            ).fetchall()
            recorded: list[dict[str, Any]] = []
            for row in rows:
                trip_id = row["trip_id"]
                segments = self._load_segments(connection, trip_id)
                if not segments:
                    continue
                for item in segments:
                    if item["start_at"] > now:
                        continue
                    if item["reservation_key"] and not item["settled"]:
                        if self.ledger is None:
                            raise MissingReservation("Ledger required for funded travel")
                        self.ledger.settle_in(connection, item["reservation_key"], max(item["start_at"], 0.0))
                        connection.execute("UPDATE trip_segments SET settled=1 WHERE trip_id=? AND segment_id=?", (trip_id,item["segment_id"]))
                    event_id = f"{trip_id}:{item['segment_id']}:start"
                    cursor = connection.execute(
                        "INSERT OR IGNORE INTO trip_events "
                        "(event_id, trip_id, segment_id, kind, at, timezone, summary, "
                        "source_kind, occurred_at) "
                        "VALUES (?, ?, ?, 'segment_start', ?, ?, ?, "
                        "'virtual_self_event', ?)",
                        (
                            event_id,
                            trip_id,
                            item["segment_id"],
                            item["start_at"],
                            item["timezone"],
                            item["title"],
                            now,
                        ),
                    )
                    if cursor.rowcount:
                        recorded.append(
                            {
                                "event_id": event_id,
                                "trip_id": trip_id,
                                "segment_id": item["segment_id"],
                                "kind": "segment_start",
                                "at": item["start_at"],
                                "timezone": item["timezone"],
                                "summary": item["title"],
                                "source_kind": "virtual_self_event",
                            }
                        )
                last_end = max(item["end_at"] for item in segments)
                if now >= last_end:
                    connection.execute(
                        "UPDATE trips SET status = 'completed', updated_at = ?, active = 0 "
                        "WHERE trip_id = ?",
                        (now, trip_id),
                    )
                else:
                    current = active_segment(segments, now)
                    status = current["phase"] if current is not None else row["status"]
                    if status == "free":
                        status = "staying"
                    if status not in {"idea", "research", "draft"}:
                        connection.execute(
                            "UPDATE trips SET status = ?, updated_at = ? WHERE trip_id = ?",
                            (status, now, trip_id),
                        )
            return recorded

        return self.db.transaction(operation)

    # ---------------------------------------------------------------- 投影

    def projection(self, as_of: float, end: float) -> dict[str, Any]:
        """返回指定时间窗口的只读投影；计划不会被说成已经发生。"""

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            row = self._select_trip(connection, as_of)
            if row is None:
                return {
                    "schema_version": 1,
                    "trip_id": "",
                    "revision": 0,
                    "phase": "none",
                    "valid_from": None,
                    "valid_until": None,
                    "timezone": "",
                    "location": None,
                    "segments": [],
                    "occurred": False,
                    "source_kind": "none",
                    "health": {"state": "ok", "reason": ""},
                    "next_event": None,
                }
            trip_id = row["trip_id"]
            segments = self._load_segments(connection, trip_id)
            window = overlapping(segments, as_of, end)
            phase = self._phase_at(connection, row, segments, as_of)
            occurred_segments = {
                event["segment_id"]
                for event in connection.execute(
                    "SELECT segment_id FROM trip_events WHERE trip_id = ? AND at <= ?",
                    (trip_id, as_of),
                ).fetchall()
            }
            occurred = bool(occurred_segments)
            next_event = None
            for item in segments:
                if item["start_at"] > as_of:
                    next_event = {
                        "segment_id": item["segment_id"],
                        "at": item["start_at"],
                        "timezone": item["timezone"],
                        "summary": item["title"],
                    }
                    break
            location = self._location_at(segments, as_of, phase, row["timezone"])
            return {
                "schema_version": 1,
                "trip_id": trip_id,
                "revision": int(row["revision"]),
                "phase": phase,
                "valid_from": min((item["start_at"] for item in segments), default=None),
                "valid_until": max((item["end_at"] for item in segments), default=None),
                "timezone": row["timezone"],
                "location": location,
                "segments": [
                    {
                        "segment_id": item["segment_id"],
                        "kind": item["kind"],
                        "title": item["title"],
                        "start_at": item["start_at"],
                        "end_at": item["end_at"],
                        "timezone": item["timezone"],
                        "from_place": item["from_place"],
                        "to_place": item["to_place"],
                        "phase": item["phase"],
                        "source_kind": "virtual_self_event"
                        if item["segment_id"] in occurred_segments
                        else "plan",
                    }
                    for item in window
                ],
                "occurred": occurred,
                "source_kind": "virtual_self_event" if occurred else "plan",
                "health": {"state": "ok", "reason": ""},
                "next_event": next_event,
            }

        return self.db.read(operation)

    # ---------------------------------------------------------------- 内部

    @staticmethod
    def _select_trip(connection: sqlite3.Connection, as_of: float) -> sqlite3.Row | None:
        rows = connection.execute("SELECT * FROM trips ORDER BY created_at DESC").fetchall()
        if not rows:
            return None
        for row in rows:
            containing = connection.execute(
                "SELECT 1 FROM trip_segments WHERE trip_id = ? AND start_at <= ? AND end_at > ? "
                "LIMIT 1",
                (row["trip_id"], as_of, as_of),
            ).fetchone()
            if containing is not None:
                return row
        for row in rows:
            if int(row["active"]) == 1:
                return row
        return rows[0]

    @staticmethod
    def _load_segments(connection: sqlite3.Connection, trip_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT * FROM trip_segments WHERE trip_id = ? ORDER BY ordinal", (trip_id,)
        ).fetchall()
        return [
            {
                "segment_id": row["segment_id"],
                "trip_id": row["trip_id"],
                "kind": row["kind"],
                "title": row["title"],
                "start_at": float(row["start_at"]),
                "end_at": float(row["end_at"]),
                "timezone": row["timezone"],
                "from_place": row["from_place"],
                "to_place": row["to_place"],
                "phase": row["phase"],
                "reservation_key": row["reservation_key"],
                "requires_funds": bool(row["requires_funds"]),
                "settled": bool(row["settled"]),
            }
            for row in rows
        ]

    @staticmethod
    def _serialize(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "trip_id": row["trip_id"],
            "revision": int(row["revision"]),
            "status": row["status"],
            "title": row["title"],
            "destination": row["destination"],
            "timezone": row["timezone"],
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
            "confirmed_at": row["confirmed_at"],
            "cancelled_at": row["cancelled_at"],
            "segments": TripService._load_segments(connection, row["trip_id"]),
        }

    @staticmethod
    def _phase_at(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        segments: list[dict[str, Any]],
        as_of: float,
    ) -> str:
        if row["status"] in {"idea", "research", "draft", "cancelled"}:
            return row["status"]
        current = active_segment(segments, as_of)
        if current is not None:
            return current["phase"]
        if not segments:
            return row["status"]
        first_start = min(item["start_at"] for item in segments)
        if as_of < first_start:
            return row["status"]
        if as_of >= max(item["end_at"] for item in segments):
            return "completed"
        return "staying"

    @staticmethod
    def _location_at(
        segments: list[dict[str, Any]],
        as_of: float,
        phase: str,
        trip_timezone: str,
    ) -> dict[str, Any] | None:
        if phase in {"completed", "cancelled"}:
            return None
        current = active_segment(segments, as_of)
        if current is not None:
            place = current["to_place"] if current["phase"] == "staying" else current["from_place"]
            return {
                "place": place,
                "from_place": current["from_place"],
                "to_place": current["to_place"],
                "timezone": current["timezone"],
                "phase": current["phase"],
            }
        previous = [item for item in segments if item["end_at"] <= as_of]
        if previous:
            last = max(previous, key=lambda item: item["end_at"])
            return {"place": last["to_place"] or last["from_place"], "from_place": last["from_place"], "to_place": last["to_place"], "timezone": last["timezone"] or trip_timezone, "phase": phase}
        if segments and as_of < min(item["start_at"] for item in segments):
            first = min(segments, key=lambda item: item["start_at"])
            return {
                "place": first["from_place"],
                "from_place": first["from_place"],
                "to_place": first["to_place"],
                "timezone": first["timezone"] or trip_timezone,
                "phase": phase,
            }
        return None

    @staticmethod
    def _parse_segments(trip_id: str, raw_segments: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_segments, (list, tuple)):
            raise ValueError("segments 必须为列表")
        parsed: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_segments):
            if not isinstance(raw, dict):
                raise ValueError(f"segments[{index}] 必须为字典")
            segment_id = str(raw.get("segment_id") or "").strip()
            if not segment_id:
                raise ValueError(f"segments[{index}] 缺少 segment_id")
            if segment_id in seen:
                raise ValueError(f"segment_id 重复: {segment_id}")
            seen.add(segment_id)
            kind = str(raw.get("kind") or "").strip()
            phase = str(raw.get("phase") or "").strip()
            if kind not in ALLOWED_KINDS:
                raise ValueError(f"不支持的行程段类型: {kind}")
            if phase not in ALLOWED_PHASES:
                raise ValueError(f"不支持的行程阶段: {phase}")
            timezone = str(raw.get("timezone") or "").strip()
            ZoneInfo(timezone)
            try:
                start_at = float(raw["start_at"])
                end_at = float(raw["end_at"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"segments[{index}] 缺少有效的起止时间") from exc
            parsed.append(
                {
                    "trip_id": trip_id,
                    "segment_id": segment_id,
                    "kind": kind,
                    "title": str(raw.get("title") or "").strip(),
                    "start_at": start_at,
                    "end_at": end_at,
                    "timezone": timezone,
                    "from_place": str(raw.get("from_place") or "").strip(),
                    "to_place": str(raw.get("to_place") or "").strip(),
                    "phase": phase,
                    "reservation_key": str(raw.get("reservation_key") or "").strip(),
                    "requires_funds": bool(raw.get("requires_funds")) or kind in {
                        "flight",
                        "train",
                        "hotel",
                    },
                }
            )
        return parsed

    @staticmethod
    def _validate_segments(segments: list[dict[str, Any]]) -> None:
        ordered = sorted(segments, key=lambda item: item["start_at"])
        previous_end: float | None = None
        for item in ordered:
            if item["end_at"] <= item["start_at"]:
                raise ValueError(f"行程段结束时间必须晚于开始时间: {item['segment_id']}")
            if previous_end is not None and item["start_at"] < previous_end:
                raise ValueError(f"行程段时间重叠: {item['segment_id']}")
            previous_end = item["end_at"]

    def _reservation_valid(self, key: str, now: float) -> bool:
        if self.ledger is None:
            return False

        def operation(connection: sqlite3.Connection) -> bool:
            row = connection.execute(
                "SELECT state, expires_at FROM reservations WHERE key = ?", (key,)
            ).fetchone()
            return (
                row is not None
                and row["state"] == "reserved"
                and float(row["expires_at"]) > now
            )

        return self.ledger.db.read(operation)
