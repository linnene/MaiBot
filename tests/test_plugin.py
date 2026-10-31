"""插件装配测试：真实 SDK 组件注册、只读 API 与重启幂等。"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import tempfile
import unittest

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import bootstrap  # noqa: E402,F401
from _virtual_travel_test_pkg.config import TravelConfig  # noqa: E402
from _virtual_travel_test_pkg.plugin import VirtualTravelPlugin  # noqa: E402

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()


class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.plugin = self.build()

    async def asyncTearDown(self) -> None:
        await self.plugin.on_unload()
        self.tmp.cleanup()

    def build(self, enabled: bool = False) -> VirtualTravelPlugin:
        plugin = VirtualTravelPlugin()
        plugin._plugin_config_instance = TravelConfig(enabled=enabled, poll_seconds=600)
        plugin._ctx = SimpleNamespace(
            paths=SimpleNamespace(data_dir=self.tmp.name),
            logger=logging.getLogger("test"),
            config=SimpleNamespace(get=AsyncMock(return_value="安静、喜欢海边")),
            llm=SimpleNamespace(generate=AsyncMock(return_value={"success": True, "response": "{}"})),
            api=SimpleNamespace(call=AsyncMock(return_value={"items": []})),
        )
        plugin._now = lambda: NOW
        return plugin

    async def test_components_register_through_real_sdk(self) -> None:
        components = {item["name"]: item for item in self.plugin.get_components()}
        self.assertIn("travel_plan", components)
        self.assertEqual(components["travel_plan"]["type"], "TOOL")
        for name in (
            "get_travel_projection",
            "get_completed_travel_events",
            "ack_travel_events",
            "propose_travel_share",
            "before_send_travel_share",
            "after_send_travel_share",
        ):
            self.assertEqual(components[name]["type"], "API")
            self.assertTrue(components[name]["metadata"]["public"])
        self.assertEqual(components["status"]["type"], "API")
        self.assertFalse(components["status"]["metadata"]["public"])

    async def test_disabled_plugin_does_not_credit_or_call_model(self) -> None:
        await self.plugin.on_load()
        status = await self.plugin.api_status()
        self.assertFalse(status["enabled"])
        self.assertEqual(status["balance"]["credited_fen"], 0)
        self.plugin.ctx.llm.generate.assert_not_awaited()

    async def test_enabled_plugin_credits_current_month_once(self) -> None:
        plugin = self.build(enabled=True)
        try:
            await plugin.on_load()
            first = await plugin.api_status()
            self.assertTrue(first["enabled"])
            self.assertGreaterEqual(first["balance"]["credited_fen"], 1_500_000)
            self.assertLessEqual(first["balance"]["credited_fen"], 2_500_000)

            reopened = self.build(enabled=True)
            try:
                await reopened.on_load()
                second = await reopened.api_status()
            finally:
                await reopened.on_unload()
            self.assertEqual(
                first["balance"]["credited_fen"], second["balance"]["credited_fen"]
            )
        finally:
            await plugin.on_unload()

    async def test_projection_api_is_read_only_and_versioned(self) -> None:
        await self.plugin.on_load()
        projection = await self.plugin.api_get_travel_projection()
        self.assertEqual(projection["schema_version"], 1)
        self.assertEqual(projection["phase"], "none")
        self.assertEqual(projection["segments"], [])
        self.assertIn("generated_at", projection)

    async def test_completed_events_can_be_acked_once(self) -> None:
        await self.plugin.on_load()
        plugin = self.plugin
        now = NOW
        trip = await asyncio.to_thread(
            plugin.trips.propose, "东京", now, "Tokyo", "Asia/Tokyo"
        )
        segment = {
            "segment_id": "flight",
            "kind": "flight",
            "title": "上海 → 东京",
            "start_at": now - 3600,
            "end_at": now + 3600,
            "timezone": "Asia/Tokyo",
            "from_place": "Shanghai",
            "to_place": "Tokyo",
            "phase": "in_transit",
            "reservation_key": "flight",
            "requires_funds": True,
        }
        await asyncio.to_thread(
            plugin.ledger.credit_month, "2026-10", 20_000, now
        )
        await asyncio.to_thread(
            plugin.ledger.reserve, trip["trip_id"], "flight", 500_000, now + 86_400.0, now
        )
        trip = await asyncio.to_thread(
            plugin.trips.revise, trip["trip_id"], trip["revision"], {"segments": [segment]}, now
        )
        await asyncio.to_thread(plugin.trips.confirm, trip["trip_id"], trip["revision"], now)
        await asyncio.to_thread(plugin.trips.advance, now + 60)

        events = await plugin.api_get_completed_travel_events()
        self.assertEqual(len(events["items"]), 1)
        self.assertEqual(events["items"][0]["subject"], "bot")
        self.assertEqual(events["items"][0]["source_kind"], "virtual_self_event")
        event_id = events["items"][0]["event_id"]
        self.assertEqual((await plugin.api_ack_travel_events([event_id]))["acked"], 1)
        self.assertEqual((await plugin.api_ack_travel_events([event_id]))["acked"], 0)
        self.assertEqual((await plugin.api_get_completed_travel_events())["items"], [])

    async def test_tool_status_returns_json_without_sending(self) -> None:
        await self.plugin.on_load()
        payload = json.loads(await self.plugin.travel_plan("status"))
        self.assertIn("balance", payload)
        self.assertFalse(hasattr(self.plugin.ctx, "send"))

    async def test_disabled_plugin_does_not_serve_or_mutate_travel(self) -> None:
        await self.plugin.on_load()
        projection = await self.plugin.api_get_travel_projection()
        self.assertEqual(projection["phase"], "none")
        self.assertEqual(projection["health"]["state"], "disabled")
        payload = json.loads(await self.plugin.travel_plan("propose", title="东京"))
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"], "travel_disabled")
        self.assertIsNone(await asyncio.to_thread(self.plugin.trips.active_trip))

    async def test_manifest_declares_cross_plugin_api_capability(self) -> None:
        manifest = json.loads((ROOT / "_manifest.json").read_text(encoding="utf-8"))
        self.assertIn("api.call", manifest["capabilities"])
        self.assertIn("llm.generate", manifest["capabilities"])


if __name__ == "__main__":
    unittest.main()
