"""Bounded, local-only Streamable HTTP MCP reader; never exposes write calls."""
from typing import Any
from urllib.parse import urlsplit
import asyncio
import json

TOOLS = frozenset({'check_login_status', 'list_feeds', 'search_feeds', 'get_feed_detail'})
MAX_BYTES = 1024 * 1024


def decode_rpc(body: bytes, content_type: str, request_id: int) -> dict:
    if len(body) > MAX_BYTES:
        raise ValueError('MCP payload exceeds budget')
    try:
        raw = body.decode('utf-8')
        if 'text/event-stream' in content_type:
            values = []
            for event in raw.replace('\r\n', '\n').split('\n\n'):
                data = '\n'.join(line[5:].lstrip(' ') for line in event.splitlines() if line.startswith('data:'))
                if data:
                    values.append(json.loads(data))
            value = next((v for v in values if isinstance(v, dict) and v.get('id') == request_id), None)
        else:
            value = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError('Invalid MCP payload') from exc
    if not isinstance(value, dict) or value.get('id') != request_id or 'error' in value:
        raise ValueError('MCP RPC failed or mismatched response')
    result = value.get('result')
    if not isinstance(result, dict) or result.get('isError'):
        raise ValueError('MCP tool failed')
    return result


class MCPClient:
    def __init__(self, url='http://127.0.0.1:18060/mcp', timeout=35., exchange=None, token=''):
        parsed = urlsplit(url)
        if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', '::1', 'localhost'} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('MCP must use a literal loopback HTTP endpoint without credentials')
        if not isinstance(token,str) or len(token)>4096 or any(ord(c)<33 or ord(c)>126 for c in token):
            raise ValueError('Invalid sidecar bearer token')
        self.token = token
        self.url = url
        self.timeout = max(.001, min(60., float(timeout)))
        self.exchange = exchange or self._http
        self.session_id = ''
        self.sequence = 0
        self.lock = asyncio.Lock()
        self.initialized = False

    async def _http(self, payload, headers):
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout), trust_env=False) as session:
            async with session.post(self.url, json=payload, headers=headers, allow_redirects=False) as response:
                if response.status == 404:
                    self.session_id = ''
                    self.initialized = False
                if response.status not in {200, 202, 204}:
                    raise ValueError('MCP HTTP failure')
                chunks = bytearray()
                content_type = response.headers.get('Content-Type', '')
                async for chunk in response.content.iter_chunked(8192):
                    chunks.extend(chunk)
                    if len(chunks) > MAX_BYTES:
                        raise ValueError('MCP payload exceeds budget')
                    # Streamable HTTP servers can keep SSE alive after the result.
                    if 'text/event-stream' in content_type and 'id' in payload:
                        try:
                            decode_rpc(bytes(chunks), content_type, payload['id'])
                        except ValueError:
                            continue
                        break
                return dict(response.headers), bytes(chunks), content_type

    async def _request(self, method, params=None, notify=False):
        self.sequence += 1
        payload = {'jsonrpc':'2.0', 'method':method}
        if not notify:
            payload['id'] = self.sequence
        if params is not None:
            payload['params'] = params
        headers = {'Accept':'application/json, text/event-stream', 'Content-Type':'application/json', 'MCP-Protocol-Version':'2024-11-05'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if self.session_id:
            headers['Mcp-Session-Id'] = self.session_id
        response_headers, body, content_type = await self.exchange(payload, headers)
        for key, value in response_headers.items():
            if key.lower() == 'mcp-session-id':
                if not isinstance(value, str) or len(value) > 256 or any(ord(c) < 33 or ord(c) > 126 for c in value):
                    raise ValueError('Invalid MCP session')
                self.session_id = value
        return {} if notify else decode_rpc(body, content_type, payload['id'])

    async def call(self, tool: str, arguments=None) -> Any:
        if tool not in TOOLS:
            raise ValueError('Read-only MCP tool whitelist denied call')
        async def run():
            async with self.lock:
                if not self.initialized:
                    await self._request('initialize', {'protocolVersion':'2024-11-05', 'capabilities':{}, 'clientInfo':{'name':'local.entertainment', 'version':'0.1.0'}})
                    await self._request('notifications/initialized', notify=True)
                    self.initialized = True
                result = await self._request('tools/call', {'name':tool, 'arguments':arguments or {}})
                if isinstance(result.get('structuredContent'), dict):
                    return result['structuredContent']
                texts = [v.get('text', '') for v in result.get('content', []) if isinstance(v, dict) and v.get('type') == 'text']
                text = '\n'.join(texts)
                try:
                    return json.loads(text)
                except ValueError:
                    return {'text':text[:12000]}
        return await asyncio.wait_for(run(), timeout=self.timeout)
