"""Real SDK/API dispatch tests with fake image retrieval, never a chat send."""
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import AsyncMock, patch
import sys
import unittest

ROOT = Path(__file__).parents[1]
if 'google_search_plugin' not in sys.modules:
    package = ModuleType('google_search_plugin')
    package.__path__ = [str(ROOT)]
    sys.modules['google_search_plugin'] = package
from google_search_plugin.config import GoogleSearchPluginConfig
from google_search_plugin.plugin import GoogleSearchPlugin


class ImageAPI(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin = GoogleSearchPlugin()
        self.plugin._plugin_config_instance = GoogleSearchPluginConfig()
        self.plugin._ensure_pipelines_ready = lambda: True
        self.plugin._image_pipeline = SimpleNamespace(_search_with_fallback=AsyncMock())

    async def test_disabled_rejects_without_search(self):
        self.plugin.config.actions.image_search_enabled = False
        result = await self.plugin.search_image_material('landscape')
        self.assertFalse(result['success'])
        self.plugin._image_pipeline._search_with_fallback.assert_not_awaited()

    async def test_data_only_api_and_canonical_registration(self):
        self.plugin.config.actions.image_search_enabled = True
        expected = {'success': True, 'image_base64': 'pixels', 'url': 'https://example.com/a.png'}
        with patch('google_search_plugin.pipelines.image_material.search_material', new=AsyncMock(return_value=expected)) as retrieve:
            self.assertEqual(await self.plugin.search_image_material('landscape'), expected)
            retrieve.assert_awaited_once()
        self.assertTrue(any(item['name'] == 'search_image_material' for item in self.plugin.get_components()))
