"""投递箱测试：只导出已发生经历、确认幂等、计划不会被当成记忆。"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory_outbox import MemoryOutbox  # noqa: E402
from storage import Database  # noqa: E402

NOW = 1_900_000_000.0


class MemoryOutboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "travel.sqlite3")
        self.outbox = MemoryOutbox(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def add_event(self, event_id: str, at: float, summary: str = "抵达东京") -> None:
        self.db.transaction(
            lambda connection: connection.execute(
                "INSERT INTO trip_events "
                "(event_id, trip_id, segment_id, kind, at, timezone, summary, "
                "source_kind, occurred_at) "
                "VALUES (?, 'trip-1', 'seg-1', 'segment_start', ?, 'Asia/Tokyo', ?, "
                "'virtual_self_event', ?)",
                (event_id, at, summary, NOW),
            )
        )

    def test_pending_returns_occurred_events_with_provenance(self) -> None:
        self.add_event("event-1", NOW - 60)
        items = self.outbox.pending(NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["event_id"], "event-1")
        self.assertEqual(items[0]["subject"], "bot")
        self.assertEqual(items[0]["source_kind"], "virtual_self_event")
        self.assertEqual(items[0]["timezone"], "Asia/Tokyo")

    def test_future_events_are_not_exported(self) -> None:
        self.add_event("event-later", NOW + 3600)
        self.assertEqual(self.outbox.pending(NOW), [])

    def test_ack_is_idempotent_and_removes_from_pending(self) -> None:
        self.add_event("event-1", NOW - 60)
        self.assertEqual(self.outbox.ack(["event-1"], NOW), 1)
        self.assertEqual(self.outbox.ack(["event-1"], NOW + 1), 0)
        self.assertTrue(self.outbox.acked("event-1"))
        self.assertEqual(self.outbox.pending(NOW + 1), [])

    def test_empty_ack_is_noop(self) -> None:
        self.assertEqual(self.outbox.ack([]), 0)

    def test_pending_is_ordered_and_bounded(self) -> None:
        for index in range(5):
            self.add_event(f"event-{index}", NOW - index * 60)
        items = self.outbox.pending(NOW, limit=2)
        self.assertEqual([item["event_id"] for item in items], ["event-4", "event-3"])

    def test_plan_rows_are_not_events(self) -> None:
        self.db.transaction(
            lambda connection: connection.execute(
                "INSERT INTO trips "
                "(trip_id, revision, status, title, destination, timezone, "
                "created_at, updated_at, active) "
                "VALUES ('trip-1', 0, 'idea', '东京', 'Tokyo', 'Asia/Tokyo', ?, ?, 1)",
                (NOW, NOW),
            )
        )
        self.assertEqual(self.outbox.pending(NOW), [])


if __name__ == "__main__":
    unittest.main()
