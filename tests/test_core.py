import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from core import Store, Reader, clean_url, normalize_feed
from mcp_client import MCPClient, decode_rpc


class StoreFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'readings.db'
        self.store = Store(self.path)
        await self.store.initialize()

    async def asyncTearDown(self):
        self.tmp.cleanup()


class StoreTests(StoreFixture):
    async def test_remembered_only_persistent_global_receipt_dedup(self):
        item = normalize_feed({'id': 'abc123', 'noteCard': {'displayTitle': '光线', 'user': {'nickname': '作者'}}})
        await self.store.save(item, '忽略', False)
        self.assertEqual(await self.store.materials(3), [])
        await self.store.save(item, '想试试窗边的光线', True)
        self.assertEqual(len(await self.store.materials(3)), 1)
        self.assertFalse((await self.store.record_share('missing', 'qzone', '', 'r'))['success'])
        self.assertFalse((await self.store.record_share(item['id'], 'qzone', '', ''))['success'])
        self.assertTrue((await self.store.record_share(item['id'], 'qzone', '', 'receipt'))['success'])
        restarted = Store(self.path)
        await restarted.initialize()
        self.assertEqual(await restarted.materials(3), [])
        self.assertTrue((await restarted.record_share(item['id'], 'private', 'x', 'other'))['already_shared'])
        recalled = await restarted.recall('窗边', 3)
        self.assertEqual(recalled[0]['source_kind'], 'external_reading')
        self.assertNotIn('target_id', json.dumps(recalled))

    async def test_url_privacy_and_images(self):
        self.assertEqual(clean_url('https://www.xiaohongshu.com/explore/abc?xsec_token=secret&user_id=123'), 'https://www.xiaohongshu.com/explore/abc')
        for url in ['http://example.com/a', 'https://127.0.0.1/a', 'https://u:p@example.com/a', 'https://localhost/a', 'https://[::1]/', 'https://10.0.0.1/a']:
            self.assertEqual(clean_url(url), '')
        item = normalize_feed({'id': 'abc123', 'noteCard': {'displayTitle': '书', 'imageList': [{'urlDefault': 'http://x.test/a'}, {'urlDefault': 'https://sns-webpic-qc.xhscdn.com/a?token=bad'}]}})
        self.assertEqual(item['images'], ['https://sns-webpic-qc.xhscdn.com/a'])


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_whitelist_session_init_sse_and_read(self):
        requests = []
        async def exchange(payload, headers):
            requests.append((payload, dict(headers)))
            if payload['method'] == 'initialize':
                return ({'Mcp-Session-Id': 'session'}, json.dumps({'jsonrpc': '2.0', 'id': payload['id'], 'result': {}}).encode(), 'application/json')
            if payload['method'].startswith('notifications/'):
                return ({}, b'', 'application/json')
            response = {'jsonrpc':'2.0','id':payload['id'],'result':{'content':[{'type':'text','text':'{"feeds": []}'}]}}
            return ({}, ('event: message\ndata: '+json.dumps(response)+'\n\n').encode(), 'text/event-stream')
        client = MCPClient(exchange=exchange)
        result = await client.call('list_feeds')
        self.assertEqual(result, {'feeds': []})
        self.assertEqual(requests[-1][1]['Mcp-Session-Id'], 'session')
        with self.assertRaises(ValueError):
            await client.call('publish_content')
        self.assertEqual(len(requests), 3)

    async def test_malformed_oversize_and_rpc_error_fail_closed(self):
        for body in [b'bad', b'x' * 1048577, b'{"jsonrpc":"2.0","id":1,"error":{"code":-1}}']:
            with self.assertRaises(ValueError):
                decode_rpc(body, 'application/json', 1)
        with self.assertRaises(ValueError):
            MCPClient('http://remote.example/mcp')

    async def test_total_deadline(self):
        async def stalled(*args):
            await asyncio.sleep(.2)
        with self.assertRaises(asyncio.TimeoutError):
            await MCPClient(exchange=stalled, timeout=.01).call('list_feeds')


class ReaderTests(StoreFixture):
    async def test_idle_optin_and_daily_budget_persistent(self):
        class Client:
            calls = 0
            async def call(self, tool, arguments=None):
                self.calls += 1
                if tool == 'check_login_status': return {'is_logged_in': True}
                if tool in {'list_feeds','search_feeds'}: return {'feeds':[{'id':'abc123','xsecToken':'secret'}]}
                return {'data':{'note':{'id':'abc123','title':'窗边摄影','desc':'柔和光线','user':{'nickname':'作者'}}}}
        async def judge(item): return {'remember': True, 'impression':'试试柔和光线'}
        client = Client()
        reader = Reader(self.store, client, judge)
        self.assertEqual((await reader.browse(False, '空闲', ['空闲'], ['摄影'], 1, 1))['reason'], 'disabled')
        self.assertEqual((await reader.browse(True, '工作', ['空闲'], ['摄影'], 1, 1))['reason'], 'not_idle')
        result = await reader.browse(True, '空闲', ['空闲'], ['摄影'], 1, 1)
        self.assertEqual(result['remembered'], 1)
        self.assertEqual(client.calls, 3)
        self.assertEqual((await Reader(self.store, client, judge).browse(True, '空闲', ['空闲'], ['摄影'], 1, 1))['reason'], 'budget')
        self.assertNotIn('secret', self.path.read_bytes().decode(errors='ignore'))

    async def test_unavailable_login_and_llm_failures_isolated(self):
        class Client:
            async def call(self, *args): raise RuntimeError('private-token-in-error')
        async def judge(item): raise RuntimeError('model')
        result = await Reader(self.store, Client(), judge).browse(True, '空闲', ['空闲'], ['书'], 2, 1)
        self.assertEqual(result['reason'], 'browse_failed')
        self.assertNotIn('private-token', json.dumps(await self.store.status()))

if __name__ == '__main__': unittest.main()

class LeaseTests(StoreFixture):
    async def test_read_only_preview_then_atomic_reservation_and_release(self):
        item = normalize_feed({'id':'abc123','title':'摄影'})
        await self.store.save(item,'喜欢光线',True)
        self.assertEqual(len(await self.store.materials(3)),1)
        self.assertEqual(len(await self.store.materials(3)),1)
        left,right = await asyncio.gather(self.store.reserve(item['id'],'qzone'),self.store.reserve(item['id'],'private'))
        self.assertEqual(sum([left['success'],right['success']]),1)
        lease = left if left['success'] else right
        self.assertEqual(await self.store.materials(3),[])
        self.assertFalse((await self.store.release(item['id'],'bad'))['success'])
        self.assertFalse((await self.store.record_share(item['id'],'qzone','','r','bad'))['success'])
        self.assertTrue((await self.store.release(item['id'],lease['lease_id']))['success'])
        self.assertEqual(len(await self.store.materials(3)),1)

class RegressionTests(StoreFixture):
    async def test_zero_budget_never_takes_a_session(self):
        self.assertFalse(await self.store.take_budget(0))

    async def test_ignored_reread_preserves_approved_impression(self):
        item=normalize_feed({'id':'abc123','title':'光线'})
        await self.store.save(item,'温柔的窗边光',True)
        await self.store.save(item,'',False)
        self.assertEqual((await self.store.materials(1))[0]['impression'],'温柔的窗边光')

class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_bearer_token_only_in_http_header(self):
        seen=[]
        async def exchange(payload,headers):
            seen.append((payload,headers))
            if payload['method'].startswith('notifications/'):
                return {},b'','application/json'
            return {},json.dumps({'id':payload['id'],'result':{}}).encode(),'application/json'
        client=MCPClient(exchange=exchange,token='test-secret')
        await client.call('check_login_status')
        self.assertTrue(all(headers['Authorization']=='Bearer test-secret' for _,headers in seen))
        self.assertNotIn('test-secret',json.dumps([payload for payload,_ in seen]))

class BrowserDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_operation_can_finish_after_twenty_five_seconds(self):
        # Accelerate only the outer deadline: 27ms stands for a 27s browser startup.
        from unittest.mock import patch
        wait_for = asyncio.wait_for
        async def fast_deadline(awaitable, timeout):
            return await wait_for(awaitable, timeout=timeout / 1000)
        async def exchange(payload, headers):
            if payload['method'].startswith('notifications/'):
                return {}, b'', 'application/json'
            if payload['method'] == 'tools/call':
                await asyncio.sleep(.027)
            result = {'content':[{'type':'text','text':'{"is_logged_in":true}'}]} if payload['method']=='tools/call' else {}
            return {}, json.dumps({'id':payload['id'],'result':result}).encode(), 'application/json'
        client = MCPClient(timeout=35, exchange=exchange)
        with patch('mcp_client.asyncio.wait_for', fast_deadline):
            self.assertTrue((await client.call('check_login_status'))['is_logged_in'])
