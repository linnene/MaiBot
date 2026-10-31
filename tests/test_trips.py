"""行程状态机测试：版本、取消、时间推进、离线恢复与资金预留。"""

from __future__ import annotations

import sys
import tempfile
import unittest

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domain.ledger import Ledger  # noqa: E402
from domain.trips import (  # noqa: E402
    ActiveTripExists,
    MissingReservation,
    StaleRevision,
    TripNotFound,
    TripService,
)

NOW = 1_900_000_000.0
DAY = 86_400.0


def segment(
    segment_id: str,
    start_at: float,
    end_at: float,
    *,
    phase: str = "in_transit",
    kind: str = "flight",
    timezone: str = "Asia/Tokyo",
    from_place: str = "Los Angeles",
    to_place: str = "Tokyo",
    reservation_key: str = "",
    requires_funds: bool = False,
) -> dict:
    return {
        "segment_id": segment_id,
        "trip_id": "",
        "kind": kind,
        "title": f"{from_place} → {to_place}" if phase == "in_transit" else to_place,
        "start_at": start_at,
        "end_at": end_at,
        "timezone": timezone,
        "from_place": from_place,
        "to_place": to_place,
        "phase": phase,
        "reservation_key": reservation_key,
        "requires_funds": requires_funds,
    }


class TripTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "travel.sqlite3"
        self.ledger = Ledger(self.path)
        self.ledger.credit_month("2026-10", 20_000, NOW)
        self.trips = TripService(self.path, self.ledger)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def make_confirmed(self, start_at: float, segments: list[dict], reservation_fen: int = 500_000) -> dict:
        trip = self.trips.propose("东京秋日", NOW, destination="Tokyo", timezone="Asia/Tokyo")
        for item in segments:
            # 机票/火车/酒店属于硬行程，确认前必须有有效预留。
            if item["kind"] in {"flight", "train", "hotel"}:
                item["reservation_key"] = item["segment_id"]
                item["requires_funds"] = True
                self.ledger.reserve(trip["trip_id"], item["segment_id"], reservation_fen, item["start_at"] + DAY, NOW)
        trip = self.trips.revise(trip["trip_id"], trip["revision"], {"segments": segments}, NOW)
        return self.trips.confirm(trip["trip_id"], trip["revision"], NOW)


class LifecycleTests(TripTestCase):
    def test_propose_creates_idea_trip_at_revision_zero(self) -> None:
        trip = self.trips.propose("东京秋日", NOW, destination="Tokyo", timezone="Asia/Tokyo")
        self.assertEqual(trip["status"], "idea")
        self.assertEqual(trip["revision"], 0)
        self.assertEqual(trip["trip_id"], self.trips.active_trip()["trip_id"])

    def test_only_one_active_trip_allowed(self) -> None:
        self.trips.propose("东京", NOW)
        with self.assertRaises(ActiveTripExists):
            self.trips.propose("大阪", NOW)

    def test_revise_rejects_stale_revision(self) -> None:
        trip = self.trips.propose("东京", NOW)
        revised = self.trips.revise(trip["trip_id"], 0, {"title": "东京秋日"}, NOW + 1)
        self.assertEqual(revised["revision"], 1)
        with self.assertRaises(StaleRevision):
            self.trips.revise(trip["trip_id"], 0, {"title": "旧版本"}, NOW + 2)

    def test_revise_rejects_overlapping_or_backwards_segments(self) -> None:
        trip = self.trips.propose("东京", NOW)
        overlap = [
            segment("a", NOW + DAY, NOW + 2 * DAY),
            segment("b", NOW + DAY + 3600, NOW + 3 * DAY),
        ]
        with self.assertRaises(ValueError):
            self.trips.revise(trip["trip_id"], 0, {"segments": overlap}, NOW)
        backwards = [segment("c", NOW + 2 * DAY, NOW + DAY)]
        with self.assertRaises(ValueError):
            self.trips.revise(trip["trip_id"], 0, {"segments": backwards}, NOW)

    def test_revise_rejects_unknown_trip(self) -> None:
        with self.assertRaises(TripNotFound):
            self.trips.revise("missing", 0, {"title": "x"}, NOW)

    def test_confirm_requires_reservation_for_funded_segments(self) -> None:
        trip = self.trips.propose("东京", NOW)
        trip = self.trips.revise(
            trip["trip_id"],
            0,
            {"segments": [segment("a", NOW + DAY, NOW + 2 * DAY, requires_funds=True)]},
            NOW,
        )
        with self.assertRaises(MissingReservation):
            self.trips.confirm(trip["trip_id"], trip["revision"], NOW)

    def test_confirm_accepts_reserved_segments(self) -> None:
        trip = self.make_confirmed(
            NOW + DAY,
            [segment("a", NOW + DAY, NOW + 2 * DAY, requires_funds=True)],
        )
        self.assertEqual(trip["status"], "confirmed")
        self.assertEqual(self.ledger.balance(NOW)["reserved_fen"], 500_000)

    def test_cancel_requires_current_revision_and_frees_active_slot(self) -> None:
        trip = self.trips.propose("东京", NOW)
        with self.assertRaises(StaleRevision):
            self.trips.cancel(trip["trip_id"], 5, NOW)
        cancelled = self.trips.cancel(trip["trip_id"], 0, NOW + 1)
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertIsNone(self.trips.active_trip())
        self.trips.propose("大阪", NOW + 2)

    def test_unconfirmed_trip_never_becomes_experience(self) -> None:
        trip = self.trips.propose("北海道", NOW, destination="Sapporo", timezone="Asia/Tokyo")
        item = segment("flight", NOW + 3600, NOW + 7200)
        self.trips.revise(trip["trip_id"], trip["revision"], {"segments": [item]}, NOW)
        self.assertEqual(self.trips.advance(NOW + 7200), [])
        projection = self.trips.projection(NOW + 7200, NOW + 9000)
        self.assertFalse(projection["occurred"])
        self.assertEqual(projection["source_kind"], "plan")

    def test_cancel_releases_unsettled_reservation(self) -> None:
        trip = self.make_confirmed(
            NOW + DAY, [segment("flight", NOW + DAY, NOW + 2 * DAY, requires_funds=True)]
        )
        self.assertEqual(self.ledger.balance(NOW)["reserved_fen"], 500_000)
        self.trips.cancel(trip["trip_id"], trip["revision"], NOW + 1)
        self.assertEqual(self.ledger.balance(NOW + 1)["reserved_fen"], 0)

    def test_confirm_rejects_expired_reservation(self) -> None:
        trip = self.trips.propose("东京", NOW, destination="Tokyo", timezone="Asia/Tokyo")
        self.ledger.reserve(trip["trip_id"], "flight", 500_000, NOW + 10, NOW)
        item = segment("flight", NOW + DAY, NOW + 2 * DAY, requires_funds=True)
        item["reservation_key"] = "flight"
        trip = self.trips.revise(trip["trip_id"], trip["revision"], {"segments": [item]}, NOW)
        with self.assertRaises(MissingReservation):
            self.trips.confirm(trip["trip_id"], trip["revision"], NOW + 100)


class AdvanceTests(TripTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.start = NOW + DAY
        self.end = self.start + 3 * DAY
        self.trip = self.make_confirmed(
            self.start,
            [
                segment("flight", self.start, self.start + 12 * 3600, requires_funds=True),
                segment(
                    "stay",
                    self.start + 12 * 3600,
                    self.end,
                    phase="staying",
                    kind="hotel",
                    from_place="Tokyo",
                    to_place="Tokyo",
                ),
            ],
        )
        self.trip_id = self.trip["trip_id"]

    def test_advance_before_start_creates_no_events(self) -> None:
        events = self.trips.advance(NOW)
        self.assertEqual(events, [])
        self.assertEqual(self.trips.projection(NOW, NOW + DAY)["phase"], "confirmed")

    def test_advance_creates_events_once_and_settles_reservation(self) -> None:
        events = self.trips.advance(self.start + 60)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "segment_start")
        self.assertEqual(events[0]["segment_id"], "flight")
        snapshot = self.ledger.balance(self.start + 60)
        self.assertEqual(snapshot["spent_fen"], 500_000)
        # 酒店预留仍未到期，继续占用余额。
        self.assertEqual(snapshot["reserved_fen"], 500_000)

        again = self.trips.advance(self.start + 120)
        self.assertEqual(again, [])
        reopened = TripService(self.path, self.ledger)
        self.assertEqual(reopened.advance(self.start + 180), [])

    def test_projection_reports_single_location_and_phase(self) -> None:
        projection = self.trips.projection(self.start + 3600, self.start + 7200)
        self.assertEqual(projection["phase"], "in_transit")
        self.assertEqual(projection["location"]["from_place"], "Los Angeles")
        self.assertEqual(projection["location"]["to_place"], "Tokyo")
        self.assertEqual(projection["timezone"], "Asia/Tokyo")
        self.assertEqual([item["segment_id"] for item in projection["segments"]], ["flight"])

    def test_offline_recovery_advances_only_known_segments(self) -> None:
        events = self.trips.advance(self.end + DAY)
        self.assertEqual([event["segment_id"] for event in events], ["flight", "stay"])
        projection = self.trips.projection(self.end + DAY, self.end + 2 * DAY)
        self.assertEqual(projection["phase"], "completed")
        self.assertIsNone(projection["location"])

    def test_cancelled_trip_never_advances(self) -> None:
        self.trips.cancel(self.trip_id, self.trip["revision"], NOW + 1)
        self.assertEqual(self.trips.advance(self.end + DAY), [])
        self.assertEqual(self.trips.projection(self.start + 60, self.start + 120)["phase"], "cancelled")

    def test_projection_before_confirm_exposes_plan_not_experience(self) -> None:
        fresh_path = Path(self.tmp.name) / "fresh.sqlite3"
        fresh = TripService(fresh_path, Ledger(fresh_path))
        trip = fresh.propose("北海道", NOW, destination="Sapporo", timezone="Asia/Tokyo")
        projection = fresh.projection(NOW, NOW + DAY)
        self.assertEqual(projection["phase"], "idea")
        self.assertFalse(projection["occurred"])
        self.assertEqual(projection["segments"], [])
        self.assertEqual(trip["revision"], projection["revision"])

    def test_revision_survives_restart(self) -> None:
        reopened = TripService(self.path, self.ledger)
        self.assertEqual(reopened.active_trip()["revision"], self.trip["revision"])


if __name__ == "__main__":
    unittest.main()
