"""Validate data-only card images before local display."""

import io
from PIL import Image
from .common import SyncError


def raster(data):
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ("PNG", "JPEG", "WEBP") or image.width * image.height > 20_000_000:
                raise ValueError()
            mime = Image.MIME[image.format]
            image.verify()
        with Image.open(io.BytesIO(data)) as decoded:
            decoded.load()
        return mime
    except Exception as error:
        raise SyncError("INVALID_RESOURCE_IMAGE", "卡图未通过完整图像校验") from error
