"""时间辅助：IANA 时区转换、月份边界与行程段定位。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable
from zoneinfo import ZoneInfo


def _parse_month(month: str) -> tuple[int, int]:
    parts = str(month or "").split("-")
    if len(parts) != 2 or len(parts[0]) != 4 or len(parts[1]) != 2:
        raise ValueError(f"月份必须为 YYYY-MM 格式，收到: {month!r}")
    try:
        year = int(parts[0])
        month_number = int(parts[1])
    except ValueError as exc:
        raise ValueError(f"月份必须为 YYYY-MM 格式，收到: {month!r}") from exc
    if not 1 <= month_number <= 12:
        raise ValueError(f"月份超出范围: {month!r}")
    return year, month_number


def month_bounds(month: str, timezone: str) -> tuple[float, float]:
    """返回管理时区下某月的起止 epoch（左闭右开）。"""

    year, month_number = _parse_month(month)
    zone = ZoneInfo(timezone)
    start = datetime(year, month_number, 1, tzinfo=zone)
    if month_number == 12:
        end = datetime(year + 1, 1, 1, tzinfo=zone)
    else:
        end = datetime(year, month_number + 1, 1, tzinfo=zone)
    return start.timestamp(), end.timestamp()


def local_datetime(at: float, timezone: str) -> datetime:
    """把 epoch 转换成指定 IANA 时区的本地时间。"""

    return datetime.fromtimestamp(at, ZoneInfo(timezone))


def local_date(at: float, timezone: str) -> Any:
    """返回 epoch 在指定时区的本地日期。"""

    return local_datetime(at, timezone).date()


def local_iso(at: float, timezone: str) -> str:
    """返回带偏移量的本地 ISO 时间字符串。"""

    return local_datetime(at, timezone).isoformat(timespec="seconds")


def active_segment(segments: Iterable[dict[str, Any]], at: float) -> dict[str, Any] | None:
    """返回 ``at`` 时刻唯一生效的行程段；边界归属后一段。"""

    for item in sorted(segments, key=lambda value: value["start_at"]):
        if item["start_at"] <= at < item["end_at"]:
            return item
    return None


def overlapping(
    segments: Iterable[dict[str, Any]], start: float, end: float
) -> list[dict[str, Any]]:
    """返回与 ``[start, end)`` 相交的行程段，保持时间顺序。"""

    return [
        item
        for item in sorted(segments, key=lambda value: value["start_at"])
        if item["start_at"] < end and item["end_at"] > start
    ]
