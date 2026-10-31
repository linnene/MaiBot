"""时间辅助测试：时区、夏令时、跨日期线与月份边界。"""

from __future__ import annotations

import sys
import unittest

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domain.clock import active_segment, local_date, local_iso, month_bounds, overlapping  # noqa: E402


def epoch(year: int, month: int, day: int, hour: int = 0, minute: int = 0, tz: str = "UTC") -> float:
    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz)).timestamp()


class MonthBoundsTests(unittest.TestCase):
    def test_month_bounds_use_management_timezone(self) -> None:
        start, end = month_bounds("2026-10", "Asia/Shanghai")
        self.assertEqual(local_iso(start, "Asia/Shanghai"), "2026-10-01T00:00:00+08:00")
        self.assertEqual(local_iso(end, "Asia/Shanghai"), "2026-11-01T00:00:00+08:00")

    def test_month_bounds_handle_year_rollover(self) -> None:
        start, end = month_bounds("2026-12", "Asia/Shanghai")
        self.assertEqual(local_date(end, "Asia/Shanghai").isoformat(), "2027-01-01")


class DaylightSavingTests(unittest.TestCase):
    def test_new_york_spring_forward_maps_to_single_local_day(self) -> None:
        before = epoch(2026, 3, 8, 6, 59)
        after = epoch(2026, 3, 8, 7, 0)
        self.assertEqual(local_iso(before, "America/New_York"), "2026-03-08T01:59:00-05:00")
        self.assertEqual(local_iso(after, "America/New_York"), "2026-03-08T03:00:00-04:00")
        self.assertEqual(local_date(before, "America/New_York"), local_date(after, "America/New_York"))

    def test_new_york_fall_back_repeats_wall_time_but_keeps_one_date(self) -> None:
        first = epoch(2026, 11, 1, 5, 30)
        second = epoch(2026, 11, 1, 6, 30)
        self.assertEqual(local_iso(first, "America/New_York")[:16], "2026-11-01T01:30")
        self.assertEqual(local_iso(second, "America/New_York")[:16], "2026-11-01T01:30")
        self.assertNotEqual(first, second)
        self.assertEqual(local_date(first, "America/New_York"), local_date(second, "America/New_York"))


class SegmentTests(unittest.TestCase):
    """跨日期线航程仍只有一个当前位置。"""

    def setUp(self) -> None:
        self.flight = {
            "segment_id": "s1",
            "start_at": epoch(2026, 10, 31, 10, 0, "America/Los_Angeles"),
            "end_at": epoch(2026, 11, 1, 14, 0, "Asia/Tokyo"),
            "timezone": "America/Los_Angeles",
            "from_place": "Los Angeles",
            "to_place": "Tokyo",
            "phase": "in_transit",
        }
        self.stay = {
            "segment_id": "s2",
            "start_at": self.flight["end_at"],
            "end_at": self.flight["end_at"] + 3 * 86400,
            "timezone": "Asia/Tokyo",
            "from_place": "Tokyo",
            "to_place": "Tokyo",
            "phase": "staying",
        }
        self.segments = [self.flight, self.stay]

    def test_active_segment_is_unique_across_date_line(self) -> None:
        middle = (self.flight["start_at"] + self.flight["end_at"]) / 2
        found = active_segment(self.segments, middle)
        self.assertIsNotNone(found)
        self.assertEqual(found["segment_id"], "s1")
        self.assertEqual(found["phase"], "in_transit")

    def test_boundary_belongs_to_the_later_segment(self) -> None:
        found = active_segment(self.segments, self.flight["end_at"])
        self.assertEqual(found["segment_id"], "s2")

    def test_no_active_segment_after_return(self) -> None:
        self.assertIsNone(active_segment(self.segments, self.stay["end_at"] + 1))

    def test_overlapping_returns_only_intersecting_segments(self) -> None:
        window = overlapping(self.segments, self.flight["end_at"] - 3600, self.flight["end_at"] + 3600)
        self.assertEqual([item["segment_id"] for item in window], ["s1", "s2"])


if __name__ == "__main__":
    unittest.main()
