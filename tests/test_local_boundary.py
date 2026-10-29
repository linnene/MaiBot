from pathlib import Path
from types import SimpleNamespace
import importlib.util
import sys
import unittest

spec=importlib.util.spec_from_file_location("query_guard",Path(__file__).parents[1]/"pipelines/query_guard.py")
guard=importlib.util.module_from_spec(spec); spec.loader.exec_module(guard)


class QueryTests(unittest.TestCase):
    def test_schema_contains_no_null_keywords(self):
        schema=guard.web_search_parameters()
        self.assertEqual(schema["required"],["question"])
        self.assertEqual(schema["properties"]["question"]["maxLength"],160)
        def check(value):
            self.assertIsNotNone(value)
            if isinstance(value,dict):
                for item in value.values():check(item)
            elif isinstance(value,list):
                for item in value:check(item)
        check(schema)

    def test_public_queries(self):
        for text in ("重庆今天气温", "Open Meteo forecast documentation", "富士相机胶片模拟"):
            self.assertEqual(guard.query_error(text), "")

    def test_no_private_export(self):
        for text in ("聊天记录", "first\nsecond", "x"*161, "me@example.com", "sk-abcdefghijk", "联系13812345678"):
            self.assertTrue(guard.query_error(text))
        self.assertTrue(guard.query_error("查询user123的动态", ["user123"]))

    def test_source_links(self):
        results=[SimpleNamespace(url=u,title="source") for u in ("javascript:alert(1)","https://example.com/a","https://example.com/a","https://user:pass@example.com")]
        self.assertEqual(guard.source_links(results),"- source: https://example.com/a")

    def test_weak_open_match_does_not_accept_unrelated_openai_page(self):
        sys.path.insert(0,str(Path(__file__).parents[2]))
        from google_search_plugin.search_engines.bing import BingEngine
        engine=BingEngine()
        words=engine._build_keywords("Open Meteo weather forecast documentation")
        self.assertFalse(engine._is_relevant("OpenAI","Open a new application","https://openai.smapply.org/",words))
        self.assertTrue(engine._is_relevant("Open-Meteo forecast API documentation","Weather forecast","https://open-meteo.com/en/docs",words))
        chinese=engine._build_keywords("重庆 天气")
        self.assertTrue(engine._is_relevant("重庆天气预报","今天气温","https://weather.example/a",chinese))
        self.assertFalse(engine._is_relevant("重庆大学","校园介绍","https://university.example/a",chinese))

    def test_registered_tool_uses_canonical_schema(self):
        sys.path.insert(0,str(Path(__file__).parents[2]))
        from google_search_plugin.config import GoogleSearchPluginConfig
        from google_search_plugin.plugin import GoogleSearchPlugin
        plugin=GoogleSearchPlugin(); plugin._plugin_config_instance=GoogleSearchPluginConfig()
        component=next(c for c in plugin.get_components() if c["name"]=="web_search")
        self.assertEqual(component["metadata"]["parameters_raw"],guard.web_search_parameters())
