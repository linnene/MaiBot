"""虚拟旅行投影消费：只读、按轮冻结、失败时保留最后有效快照。"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import asyncio
import logging
import json
import os
import tempfile
from pathlib import Path

API_NAME = "local.virtual-travel.get_travel_projection"
TERMINAL_PHASES = {"none", "cancelled", "completed"}
# 只有已确认或已在途的行程才能覆盖日程；想法/草稿不改变她的生活。
ACTIVE_PHASES = {"confirmed", "preparing", "in_transit", "staying", "returning"}
TRAVELLING_PHASES = {"in_transit", "staying", "returning"}
PHASE_SUMMARIES = {
    "in_transit": "在去{destination}的路上",
    "staying": "在{place}游玩",
    "returning": "从{place}返程",
}


class TravelProjectionClient:
    """通过插件 API 获取旅行投影；同一轮内由调用方冻结。"""

    def __init__(
        self,
        api: Any,
        logger: logging.Logger | None = None,
        *,
        timeout: float = 5.0,
        cache_path: Path | None = None,
    ) -> None:
        self.api = api
        self.logger = logger or logging.getLogger(__name__)
        self.timeout = timeout
        self._cached: dict[str, Any] | None = None
        self.cache_path=Path(cache_path) if cache_path is not None else None
        self._restored=False

    async def snapshot(self, now: datetime, end: datetime) -> dict[str, Any]:
        if not self._restored:
            self._restored=True
            await asyncio.to_thread(self._restore)
        try:
            result = await asyncio.wait_for(
                self.api.call(
                    API_NAME,
                    version="1",
                    as_of=now.timestamp(),
                    end=end.timestamp(),
                ),
                timeout=self.timeout,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 旅行插件不可用时降级，不影响麦生活
            self.logger.info("[MaiLife] 旅行投影不可用，沿用最后快照: %s", exc)
            return self.degraded(now, "travel_plugin_unavailable")
        validated = validate_projection(result)
        if validated is None:
            return self.degraded(now, "invalid_projection")
        if (validated.get("health") or {}).get("state")=="disabled" and self._cached and self._cached.get("phase") in TRAVELLING_PHASES:
            return self.degraded(now,"travel_feature_disabled")
        if self._cached and self._cached.get("trip_id") == validated.get("trip_id"):
            if int(validated.get("revision", -1)) < int(self._cached.get("revision", 0)):
                # 旧版本投影不能回退当前日程视图。
                return dict(self._cached)
        self._cached = validated
        await asyncio.to_thread(self._persist)
        return validated

    def _restore(self) -> None:
        if self.cache_path is None:return
        try:self._cached=validate_projection(json.loads(self.cache_path.read_text()))
        except (OSError,ValueError,TypeError):self._cached=None

    def _persist(self) -> None:
        if self.cache_path is None:return
        try:
            self.cache_path.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w",dir=self.cache_path.parent,delete=False) as stream:
                name=stream.name
                json.dump(self._cached,stream,ensure_ascii=False)
            os.replace(name,self.cache_path)
        except OSError as exc:
            self.logger.warning("[MaiLife] 无法保存旅行快照: %s",type(exc).__name__)

    def degraded(self, now: datetime, reason: str) -> dict[str, Any]:
        """返回带降级标记的快照；没有缓存时明确为无行程，而不是回学校模板。"""
        if self._cached is not None:
            cached = dict(self._cached)
            health = dict(cached.get("health") or {})
            health.update({"state": "degraded", "reason": reason})
            cached["health"] = health
            return cached
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
            "health": {"state": "degraded", "reason": reason},
            "next_event": None,
        }


def validate_projection(raw: Any) -> dict[str, Any] | None:
    """只接受结构完整的投影；缺失字段不猜测。"""

    if not isinstance(raw, dict):
        return None
    if int(raw.get("schema_version") or 0) != 1:
        return None
    phase = str(raw.get("phase") or "")
    if not phase:
        return None
    segments = raw.get("segments")
    if not isinstance(segments, list):
        return None
    for item in segments:
        if not isinstance(item, dict):
            return None
        for key in ("segment_id", "start_at", "end_at"):
            if key not in item:
                return None
    return dict(raw)


def _travel_node(
    day: date,
    segment: dict[str, Any],
    start_minute: int,
    end_minute: int,
) -> dict[str, Any]:
    phase = str(segment.get("phase") or "")
    destination = str(segment.get("to_place") or segment.get("title") or "")
    place = str(segment.get("to_place") or "") if phase == "staying" else str(
        segment.get("from_place") or ""
    )
    summary = PHASE_SUMMARIES.get(phase, "{destination}").format(
        destination=destination, place=place or destination
    )
    return {
        "id": f"travel:{segment['segment_id']}:{day.isoformat()}:{start_minute}",
        "day": day.isoformat(),
        "start_minute": max(0, min(1439, start_minute)),
        "end_minute": max(1, min(1440, end_minute)),
        "kind": "travel",
        "summary": summary[:160] or "在路上",
        # 在途时她仍在出发地，不能把落地城市提前当成当前位置。
        "location": (place or destination or "路上")[:80],
        "energy_load": -2.0,
        "shareability": 0.5,
    }


def _carve(node: dict[str, Any], spans: list[tuple[int, int]]) -> list[dict[str, Any]]:
    """从模板节点中切掉旅行硬行程覆盖的分钟段，保留其余生活。"""

    start = int(node["start_minute"])
    end = int(node["end_minute"])
    pieces = [(start, end)]
    for cut_start, cut_end in spans:
        next_pieces: list[tuple[int, int]] = []
        for piece_start, piece_end in pieces:
            if cut_end <= piece_start or cut_start >= piece_end:
                next_pieces.append((piece_start, piece_end))
                continue
            if piece_start < cut_start:
                next_pieces.append((piece_start, cut_start))
            if cut_end < piece_end:
                next_pieces.append((cut_end, piece_end))
        pieces = next_pieces
    result = []
    for index, (piece_start, piece_end) in enumerate(pieces):
        if piece_end <= piece_start:
            continue
        cloned = dict(node)
        cloned["start_minute"] = piece_start
        cloned["end_minute"] = piece_end
        cloned["id"] = f"{node['id']}#{index}"
        result.append(cloned)
    return result


def overlay_day(
    day: date,
    nodes: list[dict[str, Any]],
    projection: dict[str, Any] | None,
    display_timezone: str = "Asia/Shanghai",
) -> list[dict[str, Any]]:
    """用旅行硬行程覆盖当天的学生/模板日程，剩余时间仍由麦生活安排。

    日期边界按麦生活本地的展示时区切分，避免把旅行时区的分钟数套到家乡日期上。
    """

    if not projection:
        return list(nodes)
    phase = str(projection.get("phase") or "")
    if projection.get("source_kind")=="plan" and phase in TRAVELLING_PHASES:
        return list(nodes)
    if phase in TERMINAL_PHASES or phase not in ACTIVE_PHASES:
        return list(nodes)
    segments = projection.get("segments") or []
    zone = ZoneInfo(display_timezone)
    day_start = datetime.combine(day, time.min, tzinfo=zone)
    day_end = day_start + timedelta(days=1)
    spans: list[tuple[int, int]] = []
    travel_nodes: list[dict[str, Any]] = []
    for segment in segments:
        try:
            start_at = float(segment["start_at"])
            end_at = float(segment["end_at"])
        except (KeyError, TypeError, ValueError):
            continue
        overlap_start = max(day_start.timestamp(), start_at)
        overlap_end = min(day_end.timestamp(), end_at)
        if overlap_end <= overlap_start:
            continue
        start_minute = int((overlap_start - day_start.timestamp()) // 60)
        end_minute = int((overlap_end - day_start.timestamp() + 59) // 60)
        if end_minute <= start_minute:
            continue
        spans.append((start_minute, end_minute))
        travel_nodes.append(_travel_node(day, segment, start_minute, end_minute))
    if not spans:
        if phase in TRAVELLING_PHASES:
            # 快照仍说她在旅途中，但没有覆盖当天的行程段：冻结旅行状态，不能瞬间回学校。
            location = projection.get("location")
            place = str(location.get("place") or "") if isinstance(location, dict) else ""
            return [
                {
                    "id": f"travel:degraded:{day.isoformat()}",
                    "day": day.isoformat(),
                    "start_minute": 0,
                    "end_minute": 1440,
                    "kind": "travel",
                    "summary": "在旅行中（日程联动降级）",
                    "location": (place or "外地")[:80],
                    "energy_load": -2.0,
                    "shareability": 0.4,
                }
            ]
        return list(nodes)
    kept: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("kind") == "travel" and str(node.get("id", "")).startswith("travel:"):
            continue
        fragments = _carve(node, spans)
        # A stay away from home also replaces untouched school blocks; a single
        # remaining fragment may still be a truncated block.
        changed = len(fragments)!=1 or (bool(fragments) and (fragments[0]["start_minute"],fragments[0]["end_minute"])!=(node["start_minute"],node["end_minute"]))
        away = phase in {"staying","returning"}
        if (changed or away) and str(node.get("kind")) in {"work", "study"}:
            for fragment in fragments:
                fragment["kind"] = "leisure"
                fragment["summary"] = "旅行中的自由活动" if away else "旅行前的自由活动"
                fragment["energy_load"] = -1.0
                if away:
                    fragment["location"] = str((projection.get("location") or {}).get("place") or "外地")
        kept.extend(fragments)
    return sorted(kept + travel_nodes, key=lambda item: (item["start_minute"], item["end_minute"]))
