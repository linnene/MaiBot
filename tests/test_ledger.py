"""账本领域测试：月费幂等、预留/结算/释放/退款、余额不为负。"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import unittest

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from domain.ledger import InsufficientFunds, Ledger, ReservationExpired  # noqa: E402

NOW = 1_900_000_000.0
DAY = 86_400.0


class LedgerCreditTests(unittest.TestCase):
    """月费入账与重启幂等。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "travel.sqlite3"
        self.ledger = Ledger(self.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_credit_month_records_fen_and_reports_balance(self) -> None:
        result = self.ledger.credit_month("2026-10", 18_000, NOW)
        self.assertEqual(result["amount_fen"], 1_800_000)
        self.assertEqual(result["amount_yuan"], 18_000)
        self.assertTrue(result["created"])
        self.assertEqual(self.ledger.balance(NOW)["available_fen"], 1_800_000)

    def test_credit_month_is_idempotent_after_restart(self) -> None:
        self.ledger.credit_month("2026-10", 18_000, NOW)
        reopened = Ledger(self.path)
        again = reopened.credit_month("2026-10", 23_000, NOW + 60)
        self.assertFalse(again["created"])
        self.assertEqual(again["amount_yuan"], 18_000)
        self.assertEqual(reopened.balance(NOW + 60)["available_fen"], 1_800_000)

    def test_credit_month_rejects_out_of_range_amounts(self) -> None:
        for amount in (14_999, 25_001, 0, -1):
            with self.subTest(amount=amount):
                with self.assertRaises(ValueError):
                    self.ledger.credit_month("2026-10", amount, NOW)

    def test_credit_month_accepts_endpoints(self) -> None:
        self.assertEqual(self.ledger.credit_month("2026-10", 15_000, NOW)["amount_fen"], 1_500_000)
        self.assertEqual(self.ledger.credit_month("2026-11", 25_000, NOW)["amount_fen"], 2_500_000)

    def test_ensure_month_draws_once_and_only_when_missing(self) -> None:
        draws: list[int] = []

        def draw() -> int:
            draws.append(1)
            return 15_001

        first = self.ledger.ensure_month("2026-10", NOW, draw)
        second = self.ledger.ensure_month("2026-10", NOW + 5, draw)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(len(draws), 1)
        self.assertEqual(second["amount_yuan"], 15_001)

    def test_concurrent_same_month_credit_records_one_entry(self) -> None:
        amounts = [15_000 + index for index in range(8)]
        results: list[dict] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(len(amounts))

        def worker(amount: int) -> None:
            try:
                barrier.wait()
                results.append(Ledger(self.path).credit_month("2026-10", amount, NOW))
            except BaseException as exc:  # noqa: BLE001 - 测试需要收集线程异常
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(amount,)) for amount in amounts]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(results), len(amounts))
        self.assertEqual(sum(1 for item in results if item["created"]), 1)
        self.assertEqual(self.ledger.balance(NOW)["available_fen"], results[0]["amount_fen"])

    def test_cross_month_credits_accumulate(self) -> None:
        self.ledger.credit_month("2026-10", 15_000, NOW)
        self.ledger.credit_month("2026-11", 25_000, NOW + 31 * DAY)
        self.assertEqual(self.ledger.balance(NOW + 31 * DAY)["available_fen"], 4_000_000)

    def test_credit_rejects_malformed_month(self) -> None:
        for month in ("2026-1", "2026-13", "202610", ""):
            with self.subTest(month=month):
                with self.assertRaises(ValueError):
                    self.ledger.credit_month(month, 18_000, NOW)


class LedgerSpendingTests(unittest.TestCase):
    """预留、结算、释放与退款。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "travel.sqlite3"
        self.ledger = Ledger(self.path)
        self.ledger.credit_month("2026-10", 20_000, NOW)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_reserve_reduces_available_but_not_spent(self) -> None:
        self.ledger.reserve("trip-1", "flight", 500_000, NOW + DAY, NOW)
        snapshot = self.ledger.balance(NOW)
        self.assertEqual(snapshot["available_fen"], 1_500_000)
        self.assertEqual(snapshot["reserved_fen"], 500_000)
        self.assertEqual(snapshot["spent_fen"], 0)

    def test_reserve_rejects_more_than_available(self) -> None:
        with self.assertRaises(InsufficientFunds):
            self.ledger.reserve("trip-1", "hotel", 2_000_001, NOW + DAY, NOW)
        self.assertEqual(self.ledger.balance(NOW)["available_fen"], 2_000_000)

    def test_reserve_is_idempotent_by_key(self) -> None:
        first = self.ledger.reserve("trip-1", "flight", 500_000, NOW + DAY, NOW)
        second = self.ledger.reserve("trip-1", "flight", 900_000, NOW + DAY, NOW)
        self.assertEqual(first["amount_fen"], second["amount_fen"])
        self.assertEqual(self.ledger.balance(NOW)["reserved_fen"], 500_000)

    def test_settle_converts_reservation_to_spend_once(self) -> None:
        self.ledger.reserve("trip-1", "flight", 500_000, NOW + DAY, NOW)
        settled = self.ledger.settle("flight", NOW + 10)
        self.assertEqual(settled["state"], "settled")
        again = self.ledger.settle("flight", NOW + 20)
        self.assertEqual(again["state"], "settled")
        snapshot = self.ledger.balance(NOW + 20)
        self.assertEqual(snapshot["spent_fen"], 500_000)
        self.assertEqual(snapshot["reserved_fen"], 0)
        self.assertEqual(snapshot["available_fen"], 1_500_000)

    def test_settle_rejects_expired_reservation(self) -> None:
        self.ledger.reserve("trip-1", "flight", 500_000, NOW + DAY, NOW)
        with self.assertRaises(ReservationExpired):
            self.ledger.settle("flight", NOW + DAY + 1)

    def test_release_restores_available_and_is_idempotent(self) -> None:
        self.ledger.reserve("trip-1", "hotel", 300_000, NOW + DAY, NOW)
        first = self.ledger.release("hotel", NOW + 5)
        second = self.ledger.release("hotel", NOW + 6)
        self.assertEqual(first["state"], "released")
        self.assertEqual(second["state"], "released")
        self.assertEqual(self.ledger.balance(NOW + 6)["available_fen"], 2_000_000)

    def test_expired_reservation_does_not_block_available(self) -> None:
        self.ledger.reserve("trip-1", "flight", 500_000, NOW + DAY, NOW)
        snapshot = self.ledger.balance(NOW + DAY + 1)
        self.assertEqual(snapshot["reserved_fen"], 0)
        self.assertEqual(snapshot["available_fen"], 2_000_000)

    def test_refund_is_idempotent_and_capped_at_payment(self) -> None:
        self.ledger.reserve("trip-1", "flight", 500_000, NOW + DAY, NOW)
        self.ledger.settle("flight", NOW + 10)
        first = self.ledger.refund("payment:flight", "refund-1", 200_000, NOW + 20)
        second = self.ledger.refund("payment:flight", "refund-1", 200_000, NOW + 21)
        self.assertEqual(first["amount_fen"], 200_000)
        self.assertEqual(second["amount_fen"], 200_000)
        with self.assertRaises(ValueError):
            self.ledger.refund("payment:flight", "refund-2", 400_000, NOW + 22)
        snapshot = self.ledger.balance(NOW + 22)
        self.assertEqual(snapshot["spent_fen"], 300_000)
        self.assertEqual(snapshot["available_fen"], 1_700_000)

    def test_refund_requires_existing_payment(self) -> None:
        with self.assertRaises(ValueError):
            self.ledger.refund("payment:missing", "refund-x", 100, NOW)

    def test_settled_reservation_cannot_be_released(self) -> None:
        self.ledger.reserve("trip-1", "flight", 500_000, NOW + DAY, NOW)
        self.ledger.settle("flight", NOW + 10)
        with self.assertRaises(ValueError):
            self.ledger.release("flight", NOW + 20)


if __name__ == "__main__":
    unittest.main()
