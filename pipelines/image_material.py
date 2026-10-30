"""Data-only public image retrieval; no chat send, redirect or private-network access."""
from __future__ import annotations

from io import BytesIO
from typing import Awaitable, Callable
from urllib.parse import urlsplit
import asyncio
import base64
import ipaddress
import socket
import warnings

import aiohttp
from aiohttp.abc import AbstractResolver
from PIL import Image

MAX_BYTES = 4 * 1024 * 1024


def public_https_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            return False
        if parsed.port not in (None, 443) or parsed.hostname.lower() in {'localhost', 'localhost.localdomain'}:
            return False
        try:
            return ipaddress.ip_address(parsed.hostname).is_global
        except ValueError:
            return True  # DNS answers are checked by the actual connection resolver.
    except (TypeError, ValueError):
        return False


class PublicResolver(AbstractResolver):
    """Check the addresses actually used by aiohttp, avoiding a separate DNS check race."""
    def __init__(self) -> None:
        self.delegate = aiohttp.DefaultResolver()

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET) -> list[dict]:
        addresses = await self.delegate.resolve(host, port, family)
        if not addresses or any(not ipaddress.ip_address(row['host']).is_global for row in addresses):
            raise OSError('Image host did not resolve exclusively to public addresses')
        return addresses

    async def close(self) -> None:
        await self.delegate.close()


def validate_image(data: bytes) -> str:
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Image outside byte budget')
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        try:
            with Image.open(BytesIO(data)) as image:
                width, height = image.size
                if min(width, height) < 64 or max(width, height) > 8192 or width * height > 20_000_000:
                    raise ValueError('Image outside pixel budget')
                kind = image.format
                if kind not in {'JPEG', 'PNG', 'WEBP', 'GIF'}:
                    raise ValueError('Unsupported image format')
                image.verify()
        except (OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError('Invalid image pixels') from exc
    return {'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp', 'GIF': 'image/gif'}[kind]


async def download_public_image(url: str) -> bytes:
    if not public_https_url(url):
        raise ValueError('Expected a public HTTPS image URL')
    resolver = PublicResolver()
    try:
        connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False)
        async with aiohttp.ClientSession(connector=connector, trust_env=False,
                                         timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url, allow_redirects=False) as response:
                if response.status != 200 or not response.headers.get('Content-Type', '').lower().startswith('image/'):
                    raise ValueError('Image response rejected')
                chunks = []
                size = 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError('Image exceeds byte budget')
                    chunks.append(chunk)
                return b''.join(chunks)
    finally:
        await resolver.close()


async def search_material(
    query: str, search: Callable[[str], Awaitable[list[dict]]], *,
    download: Callable[[str], Awaitable[bytes]] = download_public_image,
) -> dict:
    """Retrieve a bounded candidate; caller must still check semantic relevance."""
    async with asyncio.timeout(30):
        candidates = await search(query)
        for item in candidates[:5]:
            url = item.get('url', '')
            if not public_https_url(url):
                continue
            try:
                data = await download(url)
                mime = await asyncio.to_thread(validate_image, data)
            except (ValueError, OSError, aiohttp.ClientError, TimeoutError):
                continue
            return {'success': True, 'image_base64': base64.b64encode(data).decode('ascii'),
                    'url': url, 'mime_type': mime, 'source_kind': 'web_image'}
        return {'success': False, 'error': 'no_valid_image', 'image_base64': '', 'url': ''}
