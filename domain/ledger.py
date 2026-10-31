"""虚拟旅行账本：月费、预留、消费、释放与退款。

所有金额以整数分保存；真实资金与模型预算不进入本账本。
"""

from __future__ import annotations

import re
import time

from collections.abc import Callable
from pathlib import Path
from typing import Any

import sqlite3

from storage import Database

MONTHLY_MIN_YUAN = 15_000
MONTHLY_MAX_YUAN = 25_000
_MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class InsufficientFunds(ValueError):
    """可用余额不足。"""


class ReservationExpired(ValueError):
    """预留已过期，不能结算。"""


def _validate_month(month: str) -> str:
    if not isinstance(month, str) or not _MONTH_PATTERN.fullmatch(month):
        raise ValueError(f"月份必须为 YYYY-MM 格式，收到: {month!r}")
    return month


def _validate_amount_yuan(amount_yuan: int) -> int:
    if isinstance(amount_yuan, bool) or not isinstance(amount_yuan, int):
        raise ValueError("月费必须为整数元")
    if not MONTHLY_MIN_YUAN <= amount_yuan <= MONTHLY_MAX_YUAN:
        raise ValueError(
            f"月费必须在 {MONTHLY_MIN_YUAN}—{MONTHLY_MAX_YUAN} 元之间，收到: {amount_yuan}"
        )
    return amount_yuan


class Ledger:
    """持久化账本。所有方法同步执行，由插件层放入线程池调用。"""

    def __init__(self, path: Path) -> None:
        self.db = Database(Path(path))

    # ---------------------------------------------------------------- 入账

    def credit_month(self, month: str, amount_yuan: int, now: float | None = None) -> dict[str, Any]:
        """记录某月月费；同月只保留第一笔，重复调用返回既有记录。"""

        month = _validate_month(month)
        amount_yuan = _validate_amount_yuan(amount_yuan)
        stamp = time.time() if now is None else now
        amount_fen = amount_yuan * 100

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            existing = connection.execute(
                "SELECT month, amount_fen, created_at FROM monthly_credits WHERE month = ?",
                (month,),
            ).fetchone()
            if existing is not None:
                return {
                    "month": existing["month"],
                    "amount_fen": existing["amount_fen"],
                    "amount_yuan": existing["amount_fen"] // 100,
                    "created": False,
                    "credited_at": existing["created_at"],
                }
            connection.execute(
                "INSERT INTO monthly_credits (month, amount_fen, created_at) VALUES (?, ?, ?)",
                (month, amount_fen, stamp),
            )
            return {
                "month": month,
                "amount_fen": amount_fen,
                "amount_yuan": amount_yuan,
                "created": True,
                "credited_at": stamp,
            }

        return self.db.transaction(operation)

    def ensure_month(
        self,
        month: str,
        now: float,
        draw: Callable[[], int],
    ) -> dict[str, Any]:
        """仅在缺少该月记录时调用 ``draw`` 抽取月费，避免重启重抽。

        ``draw`` 在事务内执行，并发调用同一个月时只有一个调用者会看到空记录。
        """

        month = _validate_month(month)

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            existing = connection.execute(
                "SELECT month, amount_fen, created_at FROM monthly_credits WHERE month = ?",
                (month,),
            ).fetchone()
            if existing is not None:
                return {
                    "month": existing["month"],
                    "amount_fen": existing["amount_fen"],
                    "amount_yuan": existing["amount_fen"] // 100,
                    "created": False,
                    "credited_at": existing["created_at"],
                }
            amount_yuan = _validate_amount_yuan(draw())
            amount_fen = amount_yuan * 100
            connection.execute(
                "INSERT INTO monthly_credits (month, amount_fen, created_at) VALUES (?, ?, ?)",
                (month, amount_fen, now),
            )
            return {
                "month": month,
                "amount_fen": amount_fen,
                "amount_yuan": amount_yuan,
                "created": True,
                "credited_at": now,
            }

        return self.db.transaction(operation)

    def credited_months(self) -> list[str]:
        """返回已入账月份，按时间排序。"""

        def operation(connection: sqlite3.Connection) -> list[str]:
            rows = connection.execute(
                "SELECT month FROM monthly_credits ORDER BY month"
            ).fetchall()
            return [row["month"] for row in rows]

        return self.db.read(operation)

    # ---------------------------------------------------------------- 余额

    def balance(self, now: float) -> dict[str, int]:
        """返回累计入账、净支出、有效预留与可用余额（单位：分）。"""

        def operation(connection: sqlite3.Connection) -> dict[str, int]:
            credited = connection.execute(
                "SELECT COALESCE(SUM(amount_fen), 0) AS total FROM monthly_credits"
            ).fetchone()["total"]
            spent = connection.execute(
                "SELECT COALESCE(SUM(amount_fen), 0) AS total FROM ledger_entries WHERE kind = 'spend'"
            ).fetchone()["total"]
            refunded = connection.execute(
                "SELECT COALESCE(SUM(amount_fen), 0) AS total FROM ledger_entries WHERE kind = 'refund'"
            ).fetchone()["total"]
            reserved = connection.execute(
                "SELECT COALESCE(SUM(amount_fen), 0) AS total FROM reservations "
                "WHERE state = 'reserved' AND expires_at > ?",
                (now,),
            ).fetchone()["total"]
            net_spent = int(spent) - int(refunded)
            return {
                "credited_fen": int(credited),
                "spent_fen": net_spent,
                "refunded_fen": int(refunded),
                "reserved_fen": int(reserved),
                "available_fen": int(credited) - net_spent - int(reserved),
            }

        return self.db.read(operation)

    # ---------------------------------------------------------------- 预留

    def reserve(
        self,
        trip_id: str,
        key: str,
        amount_fen: int,
        expires_at: float,
        now: float | None = None,
    ) -> dict[str, Any]:
        """预留资金；同一 ``key`` 幂等。"""

        if amount_fen <= 0:
            raise ValueError("预留金额必须为正整数分")
        stamp = time.time() if now is None else now

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            existing = connection.execute(
                "SELECT * FROM reservations WHERE key = ?", (key,)
            ).fetchone()
            if existing is not None:
                return dict(existing)
            if expires_at <= stamp:
                raise ValueError("预留到期时间必须晚于当前时间")
            snapshot = self._balance_in(connection, stamp)
            if snapshot["available_fen"] < amount_fen:
                raise InsufficientFunds(
                    f"可用余额不足：需要 {amount_fen} 分，可用 {snapshot['available_fen']} 分"
                )
            connection.execute(
                "INSERT INTO reservations "
                "(key, trip_id, amount_fen, expires_at, state, created_at, settled_at) "
                "VALUES (?, ?, ?, ?, 'reserved', ?, NULL)",
                (key, trip_id, amount_fen, expires_at, stamp),
            )
            row = connection.execute("SELECT * FROM reservations WHERE key = ?", (key,)).fetchone()
            return dict(row)

        return self.db.transaction(operation)

    def settle(self, key: str, now: float | None = None) -> dict[str, Any]:
        """把有效预留转为支出；重复结算幂等。"""

        stamp = time.time() if now is None else now

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            row = connection.execute("SELECT * FROM reservations WHERE key = ?", (key,)).fetchone()
            if row is None:
                raise ValueError(f"预留不存在: {key}")
            if row["state"] == "settled":
                return dict(row)
            if row["state"] == "released":
                raise ValueError(f"预留已释放，不能结算: {key}")
            if row["expires_at"] <= stamp:
                raise ReservationExpired(f"预留已过期: {key}")
            connection.execute(
                "INSERT OR IGNORE INTO ledger_entries "
                "(key, kind, trip_id, amount_fen, source_payment_key, created_at) "
                "VALUES (?, 'spend', ?, ?, NULL, ?)",
                (f"payment:{key}", row["trip_id"], row["amount_fen"], stamp),
            )
            connection.execute(
                "UPDATE reservations SET state = 'settled', settled_at = ? WHERE key = ?",
                (stamp, key),
            )
            updated = connection.execute(
                "SELECT * FROM reservations WHERE key = ?", (key,)
            ).fetchone()
            return dict(updated)

        return self.db.transaction(operation)

    def release(self, key: str, now: float | None = None) -> dict[str, Any]:
        """释放未结算预留；重复释放幂等。"""

        stamp = time.time() if now is None else now

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            row = connection.execute("SELECT * FROM reservations WHERE key = ?", (key,)).fetchone()
            if row is None:
                raise ValueError(f"预留不存在: {key}")
            if row["state"] == "settled":
                raise ValueError(f"预留已结算，不能释放: {key}")
            if row["state"] == "reserved":
                connection.execute(
                    "UPDATE reservations SET state = 'released', settled_at = ? WHERE key = ?",
                    (stamp, key),
                )
            updated = connection.execute(
                "SELECT * FROM reservations WHERE key = ?", (key,)
            ).fetchone()
            return dict(updated)

        return self.db.transaction(operation)

    def extend(self, key: str, expires_at: float, now: float | None = None) -> dict[str, Any]:
        """改期时延长预留有效期；已结算或已释放的预留保持不变。"""

        stamp = time.time() if now is None else now

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            row = connection.execute("SELECT * FROM reservations WHERE key = ?", (key,)).fetchone()
            if row is None:
                raise ValueError(f"预留不存在: {key}")
            if row["state"] == "reserved" and expires_at > float(row["expires_at"]):
                if expires_at <= stamp:
                    raise ValueError("新的预留到期时间必须晚于当前时间")
                connection.execute(
                    "UPDATE reservations SET expires_at = ? WHERE key = ?",
                    (expires_at, key),
                )
            updated = connection.execute(
                "SELECT * FROM reservations WHERE key = ?", (key,)
            ).fetchone()
            return dict(updated)

        return self.db.transaction(operation)

    # ---------------------------------------------------------------- 退款

    def refund(
        self,
        payment_key: str,
        key: str,
        amount_fen: int,
        now: float | None = None,
    ) -> dict[str, Any]:
        """登记退款；同一 ``key`` 幂等，累计退款不得超过原支付。"""

        if amount_fen <= 0:
            raise ValueError("退款金额必须为正整数分")
        stamp = time.time() if now is None else now

        def operation(connection: sqlite3.Connection) -> dict[str, Any]:
            payment = connection.execute(
                "SELECT * FROM ledger_entries WHERE key = ? AND kind = 'spend'",
                (payment_key,),
            ).fetchone()
            if payment is None:
                raise ValueError(f"原支付不存在: {payment_key}")
            existing = connection.execute(
                "SELECT * FROM ledger_entries WHERE key = ?", (key,)
            ).fetchone()
            if existing is not None:
                return dict(existing)
            refunded = connection.execute(
                "SELECT COALESCE(SUM(amount_fen), 0) AS total FROM ledger_entries "
                "WHERE kind = 'refund' AND source_payment_key = ?",
                (payment_key,),
            ).fetchone()["total"]
            if int(refunded) + amount_fen > int(payment["amount_fen"]):
                raise ValueError("累计退款不能超过原支付金额")
            connection.execute(
                "INSERT INTO ledger_entries "
                "(key, kind, trip_id, amount_fen, source_payment_key, created_at) "
                "VALUES (?, 'refund', ?, ?, ?, ?)",
                (key, payment["trip_id"], amount_fen, payment_key, stamp),
            )
            row = connection.execute(
                "SELECT * FROM ledger_entries WHERE key = ?", (key,)
            ).fetchone()
            return dict(row)

        return self.db.transaction(operation)

    # ---------------------------------------------------------------- 内部

    @staticmethod
    def _balance_in(connection: sqlite3.Connection, now: float) -> dict[str, int]:
        credited = connection.execute(
            "SELECT COALESCE(SUM(amount_fen), 0) AS total FROM monthly_credits"
        ).fetchone()["total"]
        spent = connection.execute(
            "SELECT COALESCE(SUM(amount_fen), 0) AS total FROM ledger_entries WHERE kind = 'spend'"
        ).fetchone()["total"]
        refunded = connection.execute(
            "SELECT COALESCE(SUM(amount_fen), 0) AS total FROM ledger_entries WHERE kind = 'refund'"
        ).fetchone()["total"]
        reserved = connection.execute(
            "SELECT COALESCE(SUM(amount_fen), 0) AS total FROM reservations "
            "WHERE state = 'reserved' AND expires_at > ?",
            (now,),
        ).fetchone()["total"]
        net_spent = int(spent) - int(refunded)
        return {
            "credited_fen": int(credited),
            "spent_fen": net_spent,
            "refunded_fen": int(refunded),
            "reserved_fen": int(reserved),
            "available_fen": int(credited) - net_spent - int(reserved),
        }
