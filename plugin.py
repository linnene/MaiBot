"""local.virtual-travel：虚拟旅行生活的 SDK 入口。"""

from __future__ import annotations

import asyncio
import json
import random
import time

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from maibot_sdk import API, Field, MaiBotPlugin, Tool
from maibot_sdk.types import ToolParameterInfo

from config import TravelConfig
from domain.clock import local_date
from domain.ledger import Ledger
from domain.opportunities import OpportunityService
from domain.trips import TripNotFound, TripService
from planner import DecisionBudget, Planner, ResearchClient


class VirtualTravelPlugin(MaiBotPlugin):
    config_model = TravelConfig

    def _now(self) -> float:
        return time.time()

    # ---------------------------------------------------------------- 生命周期

    async def on_load(self) -> None:
        ZoneInfo(self.config.timezone)
        data_dir = Path(self.ctx.paths.data_dir)
        self.ledger = await asyncio.to_thread(Ledger, data_dir / "travel.sqlite3")
        self.trips = await asyncio.to_thread(TripService, data_dir / "travel.sqlite3", self.ledger)
        self.opportunities = await asyncio.to_thread(
            OpportunityService, data_dir / "travel.sqlite3", self.config.timezone
        )
        self.decision_budget = DecisionBudget(self.ledger.db)
        self.research = ResearchClient(self.ctx)
        self.planner = Planner(
            self.ctx.llm,
            self.trips,
            self.ledger,
            self.decision_budget,
            model_task=self.config.model_task,
            daily_limit=self.config.decision_daily_limit,
        )
        self._last_decision: dict[str, Any] = {}
        self._tick_lock = asyncio.Lock()
        if self.config.enabled:
            await asyncio.to_thread(self._monthly_maintenance, self._now())
        self._loop_task = asyncio.create_task(self._loop(), name="virtual-travel")

    async def on_unload(self) -> None:
        task = getattr(self, "_loop_task", None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def on_config_update(self, scope: str, config_data: Any, version: Any) -> None:
        if scope == "self":
            await self.on_unload()
            await self.on_load()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.poll_seconds)
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 心跳不能被单次失败打断
                self.ctx.logger.warning("虚拟旅行检查失败: %s", exc)

    # ---------------------------------------------------------------- 心跳

    async def tick(self) -> None:
        if not self.config.enabled:
            return
        if self._tick_lock.locked():
            return
        async with self._tick_lock:
            now = self._now()
            await asyncio.to_thread(self._monthly_maintenance, now)
            recorded = await asyncio.to_thread(self.trips.advance, now)
            if recorded:
                for event in recorded:
                    self.ctx.logger.info(
                        "虚拟旅行推进: %s %s", event["segment_id"], event["summary"]
                    )
            due = await asyncio.to_thread(self.opportunities.due, now)
            if not due:
                return
            opportunity = due[0]
            if self._in_quiet_window(now):
                return
            result = await self._consider(now)
            self._last_decision = result
            outcome = str(result.get("action") or "considered")
            await asyncio.to_thread(
                self.opportunities.mark_handled, opportunity["month"], outcome, now
            )

    async def _consider(self, now: float) -> dict[str, Any]:
        trip = await asyncio.to_thread(self.trips.active_trip)
        balance = await asyncio.to_thread(self.ledger.balance, now)
        personality = ""
        try:
            personality = await asyncio.wait_for(
                self.ctx.config.get("personality.personality", ""), timeout=3.0
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            personality = ""
        facts = await self.research.search(
            f"{trip['destination'] if trip else ''} 旅行 交通 住宿", now
        )
        snapshot = {
            "now": now,
            "trip": trip,
            "budget": balance,
            "intent": {"kind": "monthly_opportunity", "month": self._current_month(now)},
            "facts": facts,
            "personality": personality,
        }
        return await self.planner.consider(snapshot, now, self._local_day(now))

    def _monthly_maintenance(self, now: float) -> None:
        month = self._current_month(now)
        enabled_month = self._get_meta("enabled_month")
        if not enabled_month:
            enabled_month = month
            self._set_meta("enabled_month", month)
        for candidate in self._months_between(enabled_month, month):
            self.ledger.ensure_month(candidate, now, self._draw_monthly_yuan)
        self.opportunities.ensure_month(month, now)

    def _draw_monthly_yuan(self) -> int:
        return random.randint(self.config.monthly_min_yuan, self.config.monthly_max_yuan)

    def _get_meta(self, key: str) -> str:
        def operation(connection):
            row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row is not None else ""

        return self.ledger.db.read(operation)

    def _set_meta(self, key: str, value: str) -> None:
        self.ledger.db.transaction(
            lambda connection: connection.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        )

    def _current_month(self, now: float) -> str:
        return local_date(now, self.config.timezone).strftime("%Y-%m")

    def _local_day(self, now: float) -> str:
        return local_date(now, self.config.timezone).isoformat()

    @staticmethod
    def _months_between(start: str, end: str) -> list[str]:
        start_year, start_month = (int(part) for part in start.split("-"))
        end_year, end_month = (int(part) for part in end.split("-"))
        months: list[str] = []
        year, month = start_year, start_month
        while (year, month) <= (end_year, end_month):
            months.append(f"{year:04d}-{month:02d}")
            month += 1
            if month > 12:
                year, month = year + 1, 1
        return months

    def _in_quiet_window(self, now: float) -> bool:
        if self.config.quiet_start == self.config.quiet_end:
            return False
        local = datetime.fromtimestamp(now, ZoneInfo(self.config.timezone)).strftime("%H:%M")
        start, end = self.config.quiet_start, self.config.quiet_end
        if start < end:
            return start <= local < end
        return local >= start or local < end

    # ---------------------------------------------------------------- 投影与事件

    async def build_projection(self, as_of: float, end: float) -> dict[str, Any]:
        projection = await asyncio.to_thread(self.trips.projection, as_of, end)
        projection["generated_at"] = self._now()
        return projection

    def _completed_events(self, cursor: str, limit: int) -> list[dict[str, Any]]:
        def operation(connection):
            rows = connection.execute(
                "SELECT e.* FROM trip_events e "
                "LEFT JOIN travel_event_acks a ON a.event_id = e.event_id "
                "WHERE a.event_id IS NULL AND e.at <= ? "
                "ORDER BY e.at, e.event_id LIMIT ?",
                (self._now(), max(1, min(int(limit), 100))),
            ).fetchall()
            return [dict(row) for row in rows]

        return self.ledger.db.read(operation)

    # ---------------------------------------------------------------- API

    @API(
        name="get_travel_projection",
        description="只读返回虚拟旅行在指定时间窗口的投影，供麦生活安排日程",
        version="1",
        public=True,
    )
    async def api_get_travel_projection(
        self, as_of: float | None = None, end: float | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        now = self._now()
        start = float(as_of) if as_of else now
        finish = float(end) if end else start + 7 * 86_400.0
        return await self.build_projection(start, finish)

    @API(
        name="get_completed_travel_events",
        description="只读返回已经发生且尚未确认的虚拟自身经历",
        version="1",
        public=True,
    )
    async def api_get_completed_travel_events(
        self, cursor: str = "", limit: int = 20, **kwargs: Any
    ) -> dict[str, Any]:
        events = await asyncio.to_thread(self._completed_events, cursor, limit)
        for event in events:
            event["subject"] = "bot"
            event["source_kind"] = "virtual_self_event"
        return {"items": events, "cursor": cursor}

    @API(
        name="ack_travel_events",
        description="确认虚拟自身经历已被接收，避免重复导出",
        version="1",
        public=True,
    )
    async def api_ack_travel_events(
        self, event_ids: list[str] | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        identifiers = [str(item) for item in (event_ids or []) if str(item).strip()]
        if not identifiers:
            return {"acked": 0}

        def operation(connection):
            count = 0
            for event_id in identifiers:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO travel_event_acks (event_id, acked_at) VALUES (?, ?)",
                    (event_id, self._now()),
                )
                count += cursor.rowcount
            return count

        return {"acked": await asyncio.to_thread(self.ledger.db.transaction, operation)}

    @API(
        name="status",
        description="只读查看虚拟旅行的余额、行程与最近决策",
        version="1",
        public=False,
    )
    async def api_status(self, **kwargs: Any) -> dict[str, Any]:
        now = self._now()
        balance = await asyncio.to_thread(self.ledger.balance, now)
        trip = await asyncio.to_thread(self.trips.active_trip)
        opportunity = await asyncio.to_thread(self.opportunities.get, self._current_month(now))
        projection = await self.build_projection(now, now + 7 * 86_400.0)
        return {
            "enabled": self.config.enabled,
            "balance": balance,
            "trip": trip,
            "opportunity": opportunity,
            "projection": projection,
            "last_decision": self._last_decision,
            "auto_share": self.config.auto_share,
            "publish_whitelist": list(self.config.publish_whitelist),
            "memory_export": self.config.memory_export,
        }

    # ---------------------------------------------------------------- 工具

    @Tool(
        "travel_plan",
        brief_description="管理自己的虚拟旅行计划",
        description=(
            "查看、提出、修改、确认、延期或取消自己的虚拟旅行。"
            "查资料不等于买票；没有足够经费时不能出发。"
        ),
        visibility="visible",
        parameters=[
            ToolParameterInfo(
                name="action",
                description="status、propose、revise、confirm、postpone、cancel",
                required=True,
            ),
            ToolParameterInfo(name="trip_id", description="行程 ID", required=False),
            ToolParameterInfo(name="title", description="行程标题", required=False),
            ToolParameterInfo(name="destination", description="目的地", required=False),
            ToolParameterInfo(name="segments_json", description="行程段 JSON 列表", required=False),
            ToolParameterInfo(name="shift_seconds", description="延期秒数", required=False),
            ToolParameterInfo(name="expected_revision", description="期望版本", required=False),
        ],
    )
    async def travel_plan(self, action: str = "status", **kwargs: Any) -> str:
        now = self._now()
        action = str(action or "status").strip()
        if action == "status":
            return json.dumps(await self.api_status(), ensure_ascii=False)
        if action == "propose":
            segments = json.loads(kwargs.get("segments_json") or "[]")
            trip = await asyncio.to_thread(
                self.trips.propose,
                str(kwargs.get("title") or ""),
                now,
                str(kwargs.get("destination") or ""),
                self.config.timezone,
            )
            trip = await asyncio.to_thread(
                self.trips.revise, trip["trip_id"], trip["revision"], {"segments": segments}, now
            )
            return json.dumps({"success": True, "trip": trip}, ensure_ascii=False)
        trip_id = str(kwargs.get("trip_id") or "")
        revision = int(kwargs.get("expected_revision") or 0)
        if action == "confirm":
            trip = await asyncio.to_thread(self.trips.confirm, trip_id, revision, now)
        elif action == "cancel":
            trip = await asyncio.to_thread(self.trips.cancel, trip_id, revision, now)
        elif action == "revise":
            segments = json.loads(kwargs.get("segments_json") or "[]")
            trip = await asyncio.to_thread(
                self.trips.revise, trip_id, revision, {"segments": segments}, now
            )
        elif action == "postpone":
            shift = float(kwargs.get("shift_seconds") or 0)
            current = await asyncio.to_thread(self.trips.get, trip_id)
            segments = [dict(item) for item in current["segments"]]
            for item in segments:
                item["start_at"] = float(item["start_at"]) + shift
                item["end_at"] = float(item["end_at"]) + shift
            trip = await asyncio.to_thread(
                self.trips.revise, trip_id, revision, {"segments": segments}, now
            )
        else:
            return json.dumps({"success": False, "error": f"未知操作: {action}"}, ensure_ascii=False)
        return json.dumps({"success": True, "trip": trip}, ensure_ascii=False)


def create_plugin() -> VirtualTravelPlugin:
    return VirtualTravelPlugin()
