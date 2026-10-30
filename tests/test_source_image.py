from io import BytesIO
import base64,unittest
from unittest.mock import AsyncMock,patch
from PIL import Image
from source_image import source_image,thumbnail_base64

class OriginalImageTests(unittest.IsolatedAsyncioTestCase):
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
