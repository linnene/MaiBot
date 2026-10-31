"""旅行投影联动测试：日程覆盖、上下文、时间线与降级。"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
# 仓库目录名是 mai-life，但插件包名必须是 Mai_life；用临时链接让相对导入成立。
_LINK_ROOT = Path(tempfile.mkdtemp())
_LINK = _LINK_ROOT / "Mai_life"
if not _LINK.exists():
    _LINK.symlink_to(ROOT, target_is_directory=True)
if str(_LINK_ROOT) not in sys.path:
    sys.path.insert(0, str(_LINK_ROOT))

from Mai_life.core.environment import EnvironmentService  # noqa: E402
from Mai_life.life.schedule_service import ScheduleService  # noqa: E402
from Mai_life.life.travel_projection import (  # noqa: E402
    TravelProjectionClient,
    overlay_day,
    validate_projection,
)

TZ = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 10, 20, 9, 0, tzinfo=TZ)
DAY = NOW.date().isoformat()


def node(node_id: str, start: int, end: int, kind: str, summary: str, location: str) -> dict:
    return {
        "id": node_id,
        "day": DAY,
        "start_minute": start,
        "end_minute": end,
        "kind": kind,
        "summary": summary,
        "location": location,
        "energy_load": -3.0,
        "shareability": 0.3,
    }


def template_nodes() -> list[dict]:
    return [
        node("sleep", 0, 480, "sleep", "安稳睡觉", "卧室"),
        node("class", 480, 720, "work", "上午去学校上课", "学校"),
        node("lunch", 720, 780, "meal", "吃午饭", "食堂"),
        node("study", 780, 1080, "study", "下午在图书馆自习", "图书馆"),
        node("free", 1080, 1380, "leisure", "自由活动", "家里"),
        node("sleep2", 1380, 1440, "sleep", "准备睡觉", "卧室"),
    ]


def projection(phase: str = "in_transit", *, start: datetime | None = None, end: datetime | None = None) -> dict:
    start = start or datetime(2026, 10, 20, 9, 30, tzinfo=TZ)
    finish = end or datetime(2026, 10, 20, 11, 30, tzinfo=TZ)
    return {
        "schema_version": 1,
        "trip_id": "trip-1",
        "revision": 3,
        "phase": phase,
        "valid_from": start.timestamp(),
        "valid_until": finish.timestamp(),
        "timezone": "Asia/Tokyo",
        "location": {"place": "Tokyo", "from_place": "Shanghai", "to_place": "Tokyo", "phase": phase},
        "segments": [
            {
                "segment_id": "flight",
                "kind": "flight",
                "title": "上海 → 东京",
                "start_at": start.timestamp(),
                "end_at": finish.timestamp(),
                "timezone": "Asia/Tokyo",
                "from_place": "Shanghai",
                "to_place": "Tokyo",
                "phase": phase,
                "source_kind": "plan",
            }
        ],
    }


class OverlayTests(unittest.TestCase):
    def test_travel_hard_segment_replaces_student_template(self) -> None:
        merged = overlay_day(NOW.date(), template_nodes(), projection(), "Asia/Tokyo")
        kinds = [(item["start_minute"], item["kind"], item["summary"]) for item in merged]
        self.assertIn((570, "travel", "在去Tokyo的路上"), kinds)
        self.assertFalse(any(item["summary"] == "上午去学校上课" for item in merged))
        self.assertTrue(any(item["summary"] == "吃午饭" for item in merged))

    def test_flight_keeps_one_location_and_does_not_land_early(self) -> None:
        merged = overlay_day(NOW.date(), template_nodes(), projection(), "Asia/Tokyo")
        travel = [item for item in merged if item["kind"] == "travel"]
        self.assertEqual(len(travel), 1)
        # 在途时仍在出发地，落地城市不能提前成为当前位置。
        self.assertEqual(travel[0]["location"], "Shanghai")
        self.assertEqual(travel[0]["start_minute"], 570)

    def test_home_day_minutes_use_home_timezone_not_trip_zone(self) -> None:
        home = ZoneInfo("Asia/Shanghai")
        start = datetime(2026, 11, 2, 20, 0, tzinfo=home)
        proj = projection("in_transit", start=start, end=start + timedelta(hours=14))
        proj["timezone"] = "America/New_York"
        merged = overlay_day(datetime(2026, 11, 2).date(), template_nodes(), proj, "Asia/Shanghai")
        travel = [item for item in merged if item["kind"] == "travel"]
        self.assertEqual(len(travel), 1)
        self.assertEqual(travel[0]["start_minute"], 1200)

    def test_stale_active_projection_freezes_travel_instead_of_school(self) -> None:
        proj = projection("staying", start=NOW, end=NOW + timedelta(hours=2))
        proj["segments"][0]["start_at"] = NOW.timestamp() - 10 * 86400
        proj["segments"][0]["end_at"] = NOW.timestamp() - 9 * 86400
        merged = overlay_day(NOW.date(), template_nodes(), proj, "Asia/Tokyo")
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["kind"], "travel")
        self.assertIn("降级", merged[0]["summary"])

    def test_completed_trip_restores_normal_schedule(self) -> None:
        merged = overlay_day(NOW.date(), template_nodes(), projection("completed"), "Asia/Tokyo")
        self.assertEqual([item["id"] for item in merged], [item["id"] for item in template_nodes()])
        self.assertFalse(any(item["kind"] == "travel" for item in merged))

    def test_invalid_projection_is_rejected(self) -> None:
        self.assertIsNone(validate_projection({"schema_version": 2}))
        self.assertIsNone(validate_projection({"schema_version": 1, "phase": "x", "segments": [{"bad": 1}]}))


class FakeApi:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    async def call(self, name, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_returns_validated_projection(self) -> None:
        client = TravelProjectionClient(FakeApi(projection()))
        result = await client.snapshot(NOW, NOW + timedelta(days=1))
        self.assertEqual(result["trip_id"], "trip-1")

    async def test_failure_without_cache_is_degraded_not_school_template(self) -> None:
        client = TravelProjectionClient(FakeApi(error=RuntimeError("offline")))
        result = await client.snapshot(NOW, NOW + timedelta(days=1))
        self.assertEqual(result["phase"], "none")
        self.assertEqual(result["health"]["state"], "degraded")

    async def test_failure_with_cache_keeps_last_confirmed_trip(self) -> None:
        api = FakeApi(projection())
        client = TravelProjectionClient(api)
        first = await client.snapshot(NOW, NOW + timedelta(days=1))
        api.error = RuntimeError("offline")
        second = await client.snapshot(NOW + timedelta(minutes=5), NOW + timedelta(days=1))
        self.assertEqual(second["trip_id"], first["trip_id"])
        self.assertEqual(second["health"]["state"], "degraded")
        self.assertEqual(second["health"]["reason"], "travel_plugin_unavailable")

    async def test_older_revision_does_not_roll_back_cached_view(self) -> None:
        api = FakeApi(projection())
        client = TravelProjectionClient(api)
        await client.snapshot(NOW, NOW + timedelta(days=1))
        rolled_back = projection()
        rolled_back["revision"] = 1
        api.result = rolled_back
        result = await client.snapshot(NOW, NOW + timedelta(days=1))
        self.assertEqual(result["revision"], 3)


class FakeStore:
    def __init__(self, nodes: list[dict]) -> None:
        self.nodes = nodes
        self.scene_calls: list[str] = []

    async def get_framework(self, day: str) -> list[dict]:
        return [dict(item) for item in self.nodes]

    async def get_scene(self, node_id: str) -> dict:
        self.scene_calls.append(node_id)
        return {}

    async def get_scenes_by_framework_ids(self, ids) -> dict:
        return {}


def make_schedule(nodes: list[dict]) -> tuple[ScheduleService, FakeStore]:
    store = FakeStore(nodes)
    config = SimpleNamespace(
        schedule=SimpleNamespace(detail_lead_minutes=30),
        environment=SimpleNamespace(timezone="Asia/Tokyo"),
    )
    service = ScheduleService(
        store, config, SimpleNamespace(task_available=lambda name: False), str(ROOT), None
    )
    return service, store


class ScheduleIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_context_uses_shared_travel_projection(self) -> None:
        service, _store = make_schedule(template_nodes())
        service.set_travel_projection(projection())
        context = await service.context(NOW + timedelta(hours=1))
        self.assertIsNotNone(context["current"])
        self.assertEqual(context["current"]["kind"], "travel")
        self.assertEqual(context["current"]["location"], "Shanghai")

    async def test_state_timeline_uses_travel_segment_for_offline_advance(self) -> None:
        service, _store = make_schedule(template_nodes())
        service.set_travel_projection(projection())
        spans = await service.state_timeline(
            datetime(2026, 10, 20, 9, 0, tzinfo=TZ), datetime(2026, 10, 20, 12, 0, tzinfo=TZ)
        )
        travel_spans = [span for span in spans if span["segment"].get("kind") == "travel"]
        self.assertEqual(len(travel_spans), 1)
        self.assertEqual(travel_spans[0]["segment"]["location"], "Shanghai")

    async def test_expand_due_does_not_refine_travel_hard_segments(self) -> None:
        service, store = make_schedule([node("t", 540, 600, "travel", "在去Tokyo的路上", "Tokyo")])
        service.set_travel_projection(projection(start=NOW, end=NOW + timedelta(hours=2)))
        await service.expand_due(NOW, store.nodes, {"energy": 50, "hunger": 30}, "晴")
        self.assertNotIn("t", store.scene_calls)

    async def test_return_restores_normal_schedule(self) -> None:
        service, _store = make_schedule(template_nodes())
        service.set_travel_projection(projection("completed"))
        context = await service.context(NOW)
        self.assertEqual(context["current"]["kind"], "work")


class EnvironmentTests(unittest.TestCase):
    def test_travel_location_marks_destination_weather_unknown(self) -> None:
        env = EnvironmentService.__new__(EnvironmentService)
        env.config = SimpleNamespace(environment=SimpleNamespace(timezone="Asia/Shanghai"))
        env._festival_cache = ("", "")
        snapshot = env.snapshot(NOW, travel=projection())
        self.assertEqual(snapshot["travel_location"], "Tokyo")
        self.assertEqual(snapshot["travel_timezone"], "Asia/Tokyo")
        self.assertEqual(snapshot["weather_scope"], "destination_unknown")

    def test_home_snapshot_stays_labeled_home(self) -> None:
        env = EnvironmentService.__new__(EnvironmentService)
        env.config = SimpleNamespace(environment=SimpleNamespace(timezone="Asia/Shanghai"))
        env._festival_cache = ("", "")
        snapshot = env.snapshot(NOW)
        self.assertEqual(snapshot["weather_scope"], "home")
        self.assertNotIn("travel_location", snapshot)

    def test_travel_weather_text_does_not_reuse_home_weather(self) -> None:
        from Mai_life.plugin import MaiLifePlugin

        plugin = MaiLifePlugin.__new__(MaiLifePlugin)
        plugin._env = SimpleNamespace(weather_text=lambda weather: "上海小雨")
        text = plugin._weather_for_travel({}, {"location": {"place": "Tokyo"}})
        self.assertIn("Tokyo", text)
        self.assertIn("天气未知", text)
        self.assertNotIn("上海", text)
        self.assertEqual(plugin._weather_for_travel({}, None), "上海小雨")


if __name__ == "__main__":
    unittest.main()


class DeliveryRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_response_preserves_last_active_trip(self):
        from unittest.mock import AsyncMock
        api=SimpleNamespace(call=AsyncMock(return_value=projection('staying')))
        client=TravelProjectionClient(api)
        await client.snapshot(NOW,NOW+timedelta(days=1))
        api.call.return_value=dict(projection('none'),trip_id='',segments=[],health={'state':'disabled'})
        result=await client.snapshot(NOW,NOW+timedelta(days=1))
        self.assertEqual(result['trip_id'],'trip-1');self.assertEqual(result['phase'],'staying')

    async def test_snapshot_survives_consumer_restart_and_provider_failure(self):
        from unittest.mock import AsyncMock
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'snapshot.json';api=SimpleNamespace(call=AsyncMock(return_value=projection('staying')))
            first=TravelProjectionClient(api,cache_path=path);await first.snapshot(NOW,NOW+timedelta(days=1))
            api.call.side_effect=RuntimeError('offline')
            recovered=await TravelProjectionClient(api,cache_path=path).snapshot(NOW,NOW+timedelta(days=1))
            self.assertEqual(recovered['trip_id'],'trip-1');self.assertEqual(recovered['health']['state'],'degraded')

    async def test_unconfirmed_projection_cannot_override_life(self):
        value=projection('staying');value['source_kind']='plan';value['occurred']=False
        self.assertEqual(overlay_day(NOW.date(),template_nodes(),value,'Asia/Tokyo'),template_nodes())


class TravelSchoolConflictTests(unittest.TestCase):
    def test_destination_stay_does_not_retain_school_fragments(self):
        value=projection('staying')
        merged=overlay_day(NOW.date(),template_nodes(),value,'Asia/Tokyo')
        self.assertFalse(any(n['kind'] in {'work','study'} for n in merged))
        for n in merged:
            if '旅行' in n['summary'] and n['kind']=='leisure':self.assertEqual(n['location'],'Tokyo')
