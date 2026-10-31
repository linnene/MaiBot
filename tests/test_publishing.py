"""发布边界测试：默认关闭、白名单、版本与回执。"""

from __future__ import annotations

import sys
import tempfile
import unittest

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domain.ledger import Ledger  # noqa: E402
from domain.trips import TripService  # noqa: E402
from publishing import Publishing  # noqa: E402

NOW = 1_900_000_000.0
GROUP = "qq-group:12345"


class PublishingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "travel.sqlite3"
        self.ledger = Ledger(self.path)
        self.ledger.credit_month("2026-10", 20_000, NOW)
        self.trips = TripService(self.path, self.ledger)
        self.auto_share = False
        self.whitelist = [GROUP]
        self.publishing = Publishing(
            self.ledger.db,
            self.trips,
            lambda: self.auto_share,
            lambda: list(self.whitelist),
        )
        self.event_id = self.make_event()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def make_event(self) -> str:
        trip = self.trips.propose("东京", NOW, destination="Tokyo", timezone="Asia/Tokyo")
        segment = {
            "segment_id": "flight",
            "kind": "flight",
            "title": "上海 → 东京",
            "start_at": NOW - 60,
            "end_at": NOW + 3600,
            "timezone": "Asia/Tokyo",
            "from_place": "Shanghai",
            "to_place": "Tokyo",
            "phase": "in_transit",
            "reservation_key": "flight",
            "requires_funds": True,
        }
        self.ledger.reserve(trip["trip_id"], "flight", 500_000, NOW + 86_400.0, NOW)
        trip = self.trips.revise(trip["trip_id"], trip["revision"], {"segments": [segment]}, NOW)
        self.trips.confirm(trip["trip_id"], trip["revision"], NOW)
        events = self.trips.advance(NOW)
        return events[0]["event_id"]

    def queue(self, now: float = NOW) -> dict:
        self.auto_share = True
        return self.publishing.propose(self.event_id, GROUP, now)

    def test_auto_share_disabled_rejects(self) -> None:
        result = self.publishing.propose(self.event_id, GROUP, NOW)
        self.assertFalse(result["queued"])
        self.assertEqual(result["reason"], "auto_share_disabled")

    def test_empty_or_foreign_whitelist_rejects(self) -> None:
        self.auto_share = True
        self.whitelist = []
        self.assertEqual(
            self.publishing.propose(self.event_id, GROUP, NOW)["reason"], "not_whitelisted"
        )
        self.whitelist = ["qq-group:999"]
        self.assertEqual(
            self.publishing.propose(self.event_id, GROUP, NOW)["reason"], "not_whitelisted"
        )

    def test_queued_item_is_allowed_before_send(self) -> None:
        queued = self.queue()
        self.assertTrue(queued["queued"])
        self.assertTrue(self.publishing.before_send(queued["token"], NOW + 1)["allowed"])

    def test_same_event_and_group_queue_one_item(self) -> None:
        first = self.queue()
        second = self.publishing.propose(self.event_id, GROUP, NOW + 10)
        self.assertEqual(first["token"], second["token"])
        count = self.ledger.db.read(
            lambda connection: connection.execute(
                "SELECT COUNT(*) AS total FROM publish_queue"
            ).fetchone()["total"]
        )
        self.assertEqual(count, 1)

    def test_leaving_whitelist_cancels_queued_item(self) -> None:
        queued = self.queue()
        self.whitelist = []
        allowed = self.publishing.before_send(queued["token"], NOW + 1)
        self.assertFalse(allowed["allowed"])
        self.assertEqual(allowed["reason"], "not_whitelisted")

    def test_revision_change_cancels_queued_item(self) -> None:
        queued = self.queue()
        self.ledger.db.transaction(
            lambda connection: connection.execute(
                "UPDATE trips SET revision = revision + 1 WHERE active = 1"
            )
        )
        result = self.publishing.before_send(queued["token"], NOW + 1)
        self.assertEqual(result["reason"], "stale_revision")

    def test_expired_item_is_cancelled(self) -> None:
        queued = self.queue()
        result = self.publishing.before_send(queued["token"], NOW + 7200)
        self.assertEqual(result["reason"], "expired")

    def test_receipt_marks_sent_and_prevents_resend(self) -> None:
        queued = self.queue()
        sent = self.publishing.after_send(queued["token"], {"success": True, "message_id": "m1"}, NOW + 2)
        self.assertEqual(sent["status"], "sent")
        blocked = self.publishing.before_send(queued["token"], NOW + 3)
        self.assertEqual(blocked["reason"], "status_sent")

    def test_unknown_receipt_is_not_retried(self) -> None:
        queued = self.queue()
        unknown = self.publishing.after_send(queued["token"], None, NOW + 2)
        self.assertEqual(unknown["status"], "unknown")
        blocked = self.publishing.before_send(queued["token"], NOW + 3)
        self.assertEqual(blocked["reason"], "status_unknown")

    def test_future_event_is_not_publishable(self) -> None:
        self.auto_share = True
        result = self.publishing.propose(self.event_id, GROUP, NOW - 120)
        self.assertEqual(result["reason"], "event_not_occurred")


if __name__ == "__main__":
    unittest.main()
