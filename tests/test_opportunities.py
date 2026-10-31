"""每月随机契机测试：同月稳定、重启不重抽、到点才出现。"""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domain.opportunities import OpportunityService  # noqa: E402

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()


class OpportunityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "travel.sqlite3"
        self.service = OpportunityService(self.path, "Asia/Shanghai")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_ensure_month_draws_once_and_stays_stable(self) -> None:
        draws: list[int] = []

        def draw() -> float:
            draws.append(1)
            return NOW + 3600

        first = self.service.ensure_month("2026-10", NOW, draw)
        second = self.service.ensure_month("2026-10", NOW + 60, draw)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["at"], second["at"])
        self.assertEqual(len(draws), 1)

    def test_draw_is_inside_requested_month(self) -> None:
        result = self.service.ensure_month("2026-10", NOW)
        from domain.clock import month_bounds

        start, end = month_bounds("2026-10", "Asia/Shanghai")
        self.assertGreaterEqual(result["at"], start)
        self.assertLess(result["at"], end)

    def test_restart_does_not_redraw(self) -> None:
        first = self.service.ensure_month("2026-10", NOW)
        reopened = OpportunityService(self.path, "Asia/Shanghai")
        second = reopened.ensure_month("2026-10", NOW + 10)
        self.assertEqual(first["at"], second["at"])

    def test_concurrent_ensure_records_one_opportunity(self) -> None:
        results: list[dict] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(6)

        def worker() -> None:
            try:
                barrier.wait()
                results.append(OpportunityService(self.path, "Asia/Shanghai").ensure_month("2026-10", NOW))
            except BaseException as exc:  # noqa: BLE001 - 测试需要收集线程异常
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len({item["at"] for item in results}), 1)
        self.assertEqual(sum(1 for item in results if item["created"]), 1)

    def test_due_only_lists_unhandled_past_opportunities(self) -> None:
        now_draw = self.service.ensure_month("2026-10", NOW, lambda: NOW - 10)
        self.assertEqual(len(self.service.due(NOW)), 1)
        self.service.mark_handled("2026-10", "considered", NOW)
        self.assertEqual(self.service.due(NOW), [])
        self.assertEqual(self.service.get("2026-10")["at"], now_draw["at"])

    def test_future_opportunity_is_not_due(self) -> None:
        self.service.ensure_month("2026-10", NOW, lambda: NOW + 3600)
        self.assertEqual(self.service.due(NOW), [])


if __name__ == "__main__":
    unittest.main()
