from pathlib import Path
from io import BytesIO
import base64
import importlib.util
import unittest
from unittest.mock import AsyncMock
from PIL import Image

spec = importlib.util.spec_from_file_location('image_material', Path(__file__).parents[1] / 'pipelines/image_material.py')
material = importlib.util.module_from_spec(spec)
spec.loader.exec_module(material)


def picture():
    output = BytesIO()
    Image.new('RGB', (128, 128)).save(output, format='PNG')
    return output.getvalue()


class ImageMaterialTests(unittest.IsolatedAsyncioTestCase):
    def test_url_rejects_local_credentials_and_non_https(self):
        for value in ['http://example.com/a.png', 'https://127.0.0.1/a', 'https://[::1]/a',
                      'https://169.254.169.254/a', 'https://a:b@example.com/a', 'file:///tmp/a']:
            self.assertFalse(material.public_https_url(value))
        self.assertTrue(material.public_https_url('https://example.com/a.png'))

    def test_real_pixel_validation_rejects_non_image_or_tiny_data(self):
        self.assertEqual(material.validate_image(picture()), 'image/png')
        for data in [b'<html>not an image</html>', b'', b'x' * (4 * 1024 * 1024 + 1)]:
            with self.assertRaises(ValueError):
                material.validate_image(data)

    async def test_material_returns_source_bytes_and_does_not_send(self):
        search = AsyncMock(return_value=[{'url': 'https://example.com/a.png'}])
        download = AsyncMock(return_value=picture())
        result = await material.search_material('胶片摄影', search, download=download)
        self.assertTrue(result['success'])
        self.assertEqual(base64.b64decode(result['image_base64']), picture())
        self.assertEqual(result['url'], 'https://example.com/a.png')
        self.assertEqual(result['mime_type'], 'image/png')

    async def test_private_candidate_is_never_downloaded(self):
        search = AsyncMock(return_value=[{'url': 'https://127.0.0.1/secret'}])
        download = AsyncMock()
        result = await material.search_material('landscape', search, download=download)
        self.assertFalse(result['success'])
        download.assert_not_awaited()

    async def test_failed_or_nonimage_candidate_produces_no_attachment(self):
        search = AsyncMock(return_value=[{'url': 'https://example.com/a'}])
        download = AsyncMock(return_value=b'not pixels')
        self.assertFalse((await material.search_material('landscape', search, download=download))['success'])
        search.return_value = []
        self.assertFalse((await material.search_material('landscape', search, download=download))['success'])

    async def test_resolver_rejects_private_dns_answer(self):
        resolver = material.PublicResolver()
        resolver.delegate = AsyncMock()
        resolver.delegate.resolve.return_value = [{'host': '127.0.0.1'}]
        with self.assertRaises(OSError):
            await resolver.resolve('example.com')
        await resolver.close()
