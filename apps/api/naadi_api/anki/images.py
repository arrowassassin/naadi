"""Pictures of an imported deck: decoded with Pillow (bounded), re-encoded to WebP, stored under the student's folder.

A picture never leaves the package as it came: it is decoded, scaled down and re-encoded, so metadata and any
payload in the original file are gone and the stored size is small (Storage budget). Unsupported formats (SVG,
broken files) are skipped and counted.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from typing import Protocol

from PIL import Image, ImageOps

MAX_PIXELS = 40_000_000
MAX_SIDE = 1600
WEBP_QUALITY = 80
ALLOWED = {"PNG", "JPEG", "GIF", "WEBP", "BMP"}


@dataclass(frozen=True, slots=True)
class WebpImage:
    data: bytes
    width: int
    height: int
    sha256: str


def to_webp(data: bytes) -> WebpImage | None:
    """The picture as a bounded WebP, or None when it cannot be read safely."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ALLOWED:
                return None
            w, h = im.size
            if w < 1 or h < 1 or w * h > MAX_PIXELS:
                return None
            im.seek(0)
            frame = ImageOps.exif_transpose(im) or im
            frame.load()
            mode = "RGBA" if frame.mode in ("RGBA", "LA", "P", "PA") else "RGB"
            flat = frame.convert(mode)
            flat.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            flat.save(out, format="WEBP", quality=WEBP_QUALITY, method=4)
    except (OSError, ValueError, Image.DecompressionBombError, SyntaxError, EOFError):
        return None
    blob = out.getvalue()
    return WebpImage(blob, flat.width, flat.height, hashlib.sha256(blob).hexdigest())


class ImageStore(Protocol):
    """Where pictures live: the `user-uploads` bucket (bucket-relative path `{user_id}/anki/{sha}.webp`)."""

    async def put(self, path: str, data: bytes) -> None: ...

    async def get(self, path: str) -> bytes | None: ...


def media_id(user_id: str, sha256: str) -> str:
    """`media_assets.id` of a private picture: per student, so two students never share a row."""
    return f"u{user_id.replace('-', '')[:12]}-{sha256[:24]}"


def storage_path(user_id: str, sha256: str) -> str:
    return f"{user_id}/anki/{sha256}.webp"
