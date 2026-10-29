"""Behavior tests against real SDK; no network, LLM or QQ messages."""
import asyncio
import base64
import importlib.util
import json
import logging
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('drawing_plugin_under_test', ROOT/'plugin.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
SVG = '<svg width="100" height="50"><circle cx="40" cy="25" r="20" fill="red"/></svg>'
PNG = b'\x89PNG\r\n\x1a\n' + b'test'

class DrawingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sent = []
        self.delivery = {'sent': True, 'message_id': 'test'}
        async def send(image, stream_id, **kwargs):
            self.sent.append((image,stream_id,kwargs))
            return self.delivery
        self.p = m.create_plugin()
        self.p._ctx = SimpleNamespace(paths=SimpleNamespace(data_dir=self.tmp.name),logger=logging.getLogger('test'),send=SimpleNamespace(image=send))
        self.p._plugin_config_instance = m.EasySVGCreatorConfig()
        await self.p.on_load()
        self.renders = 0
        async def render(svg):
            self.renders += 1
            return PNG
        self.p._render_png = render

    async def asyncTearDown(self):
        await self.p.on_unload()
        self.tmp.cleanup()

    async def test_send_confirmed_and_duplicate(self):
        first = await self.p.generate_svg(SVG, stream_id='session-a')
        second = await self.p.generate_svg(SVG, stream_id='session-a')
        self.assertTrue(first['image_sent']); self.assertEqual(second['reason'],'duplicate')
        self.assertEqual(self.renders,1); self.assertEqual(len(self.sent),1)
        self.assertTrue(self.sent[0][2]['return_details'])

    async def test_cooldown_and_different_sessions(self):
        await self.p.generate_svg(SVG,stream_id='session-a')
        new = SVG.replace('red','blue')
        self.assertEqual((await self.p.generate_svg(new,stream_id='session-a'))['reason'],'cooldown')
        self.assertTrue((await self.p.generate_svg(new,stream_id='session-b'))['image_sent'])

    async def test_failed_send_does_not_consume_quota(self):
        self.delivery={'sent':False,'message_id':None}
        self.assertFalse((await self.p.generate_svg(SVG,stream_id='session-a'))['image_sent'])
        self.delivery={'sent':True,'message_id':'ok'}
        self.assertTrue((await self.p.generate_svg(SVG,stream_id='session-a'))['image_sent'])

    async def test_disabled_or_missing_stream_no_send(self):
        self.assertFalse((await self.p.generate_svg(SVG))['image_sent'])
        self.p.config.plugin.enabled=False
        self.assertEqual((await self.p.generate_svg(SVG,stream_id='session'))['status'],'disabled')
        self.assertFalse(self.sent)

    async def test_persistent_duplicate_after_reload(self):
        await self.p.generate_svg(SVG,stream_id='session-a')
        await self.p.on_load()
        self.assertEqual((await self.p.generate_svg(SVG,stream_id='session-a'))['reason'],'duplicate')
        self.assertNotIn('session-a',(Path(self.tmp.name)/'drawing-state.json').read_text())

    async def test_busy_rejected(self):
        async with self.p._guard:
            self.assertEqual((await self.p.generate_svg(SVG,stream_id='session'))['status'],'busy')
        self.assertFalse(self.sent)

    async def test_render_failure_no_send(self):
        async def broken(svg): raise asyncio.TimeoutError()
        self.p._render_png=broken
        self.assertEqual((await self.p.generate_svg(SVG,stream_id='session'))['status'],'error')
        self.assertFalse(self.sent);self.assertFalse(self.p._state['last_sent'])

    async def test_reject_external_and_oversize(self):
        invalid = [SVG.replace('<circle','<image href="file:///etc/passwd"'),SVG.replace('<circle','<g style="fill:url(https://example.com)"'), '<!DOCTYPE svg>'+SVG, '<svg width="1" height="1"><script/></svg>', '<svg width="10" height="10">'+' '*50000+'</svg>','<svg width="10" height="10">'+'<g/>'*2001+'</svg>','<html/>',SVG.replace('width="100"','width="100%"')]
        for value in invalid:
            with self.subTest(value=value[:40]):
                result=await self.p.generate_svg(value,stream_id='session')
                self.assertEqual(result['status'],'error')
        self.assertFalse(self.sent)

    async def test_bounded_dimensions_and_viewbox(self):
        import xml.etree.ElementTree as ET
        root=ET.fromstring(self.p._prepare_svg(SVG,100000))
        self.assertEqual((root.get('width'),root.get('height')),('1024','512'))
        root=ET.fromstring(self.p._prepare_svg('<svg viewBox="0 0 50 100"/>',800))
        self.assertEqual((root.get('width'),root.get('height')),('400','800'))

    async def test_timeout_kills_real_process(self):
        from unittest.mock import patch
        import sys
        original = asyncio.create_subprocess_exec
        created = []
        async def delayed(*args, **kwargs):
            process = await original(sys.executable, '-c', 'import time; time.sleep(30)', **kwargs)
            created.append(process)
            return process
        self.p.config.behavior.render_timeout_seconds=2
        with patch.object(m.asyncio, 'create_subprocess_exec', delayed):
            with self.assertRaises(asyncio.TimeoutError):
                await m.EasySVGCreatorPlugin._render_png(self.p,SVG)
        self.assertIsNotNone(created[0].returncode)
        self.assertFalse(self.p._processes)

    @unittest.skipUnless(os.environ.get('REAL_RENDER')=='1','real native rendering enabled in server staging')
    async def test_real_render_in_subprocess(self):
        svg=self.p._prepare_svg(SVG,800)
        png=await m.EasySVGCreatorPlugin._render_png(self.p,svg)
        self.assertEqual(struct.unpack('>II',png[16:24]),(800,400))
        Path(self.tmp.name,'render.png').write_bytes(png)
        self.assertFalse(self.p._processes)

if __name__ == '__main__':unittest.main()
