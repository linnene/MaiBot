from io import BytesIO
import base64,unittest
from unittest.mock import AsyncMock,patch
from PIL import Image
from source_image import source_image,thumbnail_base64,compact_preview_base64

class OriginalImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_share_preview_is_compact_for_portrait_and_landscape(self):
        for size in [(300,1800),(1800,300)]:
            data=BytesIO();Image.new('RGB',size,'red').save(data,format='JPEG')
            result=base64.b64decode(compact_preview_base64(base64.b64encode(data.getvalue()).decode()))
            with Image.open(BytesIO(result)) as im:
                self.assertEqual(im.size,(360,200))
                self.assertEqual(im.format,'JPEG')
                self.assertGreater(im.getpixel((180,100))[0],200)
            self.assertLess(len(result),100000)

    async def test_invalid_preview_pixels_fail_before_send(self):
        with self.assertRaises(ValueError):compact_preview_base64('not base64')
        with self.assertRaises(ValueError):compact_preview_base64(base64.b64encode(b'not pixels').decode())

    async def test_non_source_hosts_are_not_downloaded(self):
        with patch('source_image.download_public_image',new_callable=AsyncMock) as download:
            self.assertEqual(await source_image(['https://example.com/image.jpg']), '')
            download.assert_not_called()

    async def test_actual_pixels_are_bounded_and_invalid_data_rejected(self):
        data=BytesIO();Image.new('RGB',(1600,1000),'red').save(data,format='PNG')
        decoded=base64.b64decode(thumbnail_base64(data.getvalue()))
        with Image.open(BytesIO(decoded)) as im:
            self.assertEqual(im.format,'JPEG')
            self.assertLessEqual(max(im.size),800)
        with self.assertRaises(ValueError):thumbnail_base64(b'<html>not image</html>')
