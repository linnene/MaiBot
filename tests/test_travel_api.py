"""旅行资料只读 API：返回结构化事实，不进入发送路径。"""

from __future__ import annotations

import logging
import sys
import unittest

from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).parents[1]
if "google_search_plugin" not in sys.modules:
    package = ModuleType("google_search_plugin")
    package.__path__ = [str(ROOT)]
    sys.modules["google_search_plugin"] = package

from google_search_plugin.config import GoogleSearchPluginConfig  # noqa: E402
from google_search_plugin.plugin import GoogleSearchPlugin  # noqa: E402
from google_search_plugin.search_engines.base import SearchResult  # noqa: E402


class TravelFactsApi(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.plugin = GoogleSearchPlugin()
        self.plugin._plugin_config_instance = GoogleSearchPluginConfig()
        self.plugin._ctx = SimpleNamespace(logger=logging.getLogger("test"))
        self.plugin._ensure_pipelines_ready = lambda: True
        self.plugin._engine_chain = SimpleNamespace(search_with_fallback=AsyncMock())

    async def test_returns_sourced_facts_without_sending(self) -> None:
        self.plugin._engine_chain.search_with_fallback.return_value = (
            [
                SearchResult(
                    title="Tokyo flights",
                    url="https://example.test/flights",
                    snippet="直飞约三小时",
                    content="十月上海到东京直飞约三小时。",
                )
            ],
            "fake-engine",
        )
        result = await self.plugin.search_travel_facts("东京 机票", limit=3)
        self.assertTrue(result["success"])
        self.assertEqual(result["engine"], "fake-engine")
        self.assertEqual(len(result["items"]), 1)
        item = result["items"][0]
        self.assertEqual(item["url"], "https://example.test/flights")
        self.assertEqual(item["source_kind"], "web_search")
        self.assertGreater(item["fetched_at"], 0)
        self.assertFalse(hasattr(self.plugin.ctx, "send"))

    async def test_empty_and_sensitive_queries_never_reach_engine(self) -> None:
        empty = await self.plugin.search_travel_facts("")
        self.assertFalse(empty["success"])
        sensitive = await self.plugin.search_travel_facts("我的私聊内容 access_token=abc")
        self.assertEqual(sensitive["error"], "invalid_query")
        self.plugin._engine_chain.search_with_fallback.assert_not_awaited()

    async def test_failure_is_explicit_and_not_fabricated(self) -> None:
        self.plugin._engine_chain.search_with_fallback.side_effect = RuntimeError("network")
        result = await self.plugin.search_travel_facts("东京 酒店")
        self.assertEqual(result["error"], "search_failed")
        self.assertNotIn("items", result)

    async def test_api_is_registered_publicly(self) -> None:
        components = {item["name"]: item for item in self.plugin.get_components()}
        self.assertIn("search_travel_facts", components)
        self.assertEqual(components["search_travel_facts"]["type"], "API")
        self.assertTrue(components["search_travel_facts"]["metadata"]["public"])


if __name__ == "__main__":
    unittest.main()
