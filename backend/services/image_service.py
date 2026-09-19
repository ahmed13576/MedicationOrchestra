"""
image_service.py — Medication Orchestra

Upload hardening. The previous implementation passed whatever bytes arrived to
Vertex AI while declaring `mime_type="image/jpeg"` unconditionally — including
for the AVIF fixtures that ship in this repository.

What this module guarantees:
  * The declared MIME type matches the actual bytes (sniffed from magic numbers),
    instead of being hard-coded.
  * Only real, decodable images are forwarded (with a pixel-count cap to blunt
    decompression bombs).
  * EXIF metadata is stripped by re-encoding, so location and device identifiers
    in a prescription photo are never stored or sent to a model.
  * Oversized images are downscaled rather than rejected, because a caregiver's
    photo should not fail for being 12 MP.

Pillow is a hard dependency here; if it is unavailable the service degrades to
magic-byte validation only and says so in `prepared.notes`.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 40_000_000          # ~40 MP: guards against decompression bombs
MAX_EDGE_PIXELS = 2400           # downscale target; OCR does not need more
JPEG_QUALITY = 82

#: magic bytes -> declared mime
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)

#: ISO-BMFF brands used by HEIC/HEIF and AVIF (the repo's own fixtures are AVIF).
_ISOBMFF_BRANDS = {
    b"heic": "image/heic",
    b"heix": "image/heic",
    b"hevc": "image/heic",
    b"mif1": "image/heif",
    b"msf1": "image/heif",
    b"avif": "image/avif",
    b"avis": "image/avif",
}

ALLOWED_MIME = {
    "image/jpeg", "image/png", "image/heic", "image/heif", "image/avif",
    "image/webp", "image/bmp", "image/tiff", "image/gif",
}


@dataclass
class PreparedImage:
    data: bytes
    mime_type: str
    width: int = 0
    height: int = 0
    re_encoded: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def size_kb(self) -> float:
        return round(len(self.data) / 1024.0, 1)


class UnsupportedImage(ValueError):
    """Raised with a message safe to show the user."""


def sniff_mime(data: bytes) -> str | None:
    """Return the MIME type implied by the bytes, or None if unrecognised."""
    for signature, mime in _SIGNATURES:
        if data.startswith(signature):
            return mime
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        return _ISOBMFF_BRANDS.get(brand)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _pil():
    try:
        from PIL import Image

        return Image
    except Exception:
        return None


def prepare_image(raw: bytes, max_bytes: int = MAX_UPLOAD_BYTES) -> PreparedImage:
    """Validate, normalise and strip metadata from an uploaded image.

    Raises UnsupportedImage with a user-facing message when the bytes are not a
    usable image. Never forwards unvalidated bytes to a model provider.
    """
    if not raw:
        raise UnsupportedImage("The uploaded file was empty. Please take the photo again.")
    if len(raw) > max_bytes:
        raise UnsupportedImage(
            f"Image is too large ({len(raw) / 1024 / 1024:.1f} MB). "
            f"Please keep photos under {max_bytes // 1024 // 1024} MB."
        )

    mime = sniff_mime(raw)
    if mime is None:
        raise UnsupportedImage(
            "That file does not look like a photo. Please upload a JPEG or PNG image of "
            "the prescription or medicine strip."
        )
    if mime not in ALLOWED_MIME:
        raise UnsupportedImage(f"Images of type {mime} are not supported.")

    Image = _pil()
    if Image is None:
        logger.warning("Pillow unavailable: forwarding image without re-encoding")
        out = PreparedImage(data=raw, mime_type=mime)
        out.notes.append("metadata_not_stripped")
        return out

    try:
        with Image.open(io.BytesIO(raw)) as img:
            img.verify()  # cheap structural check before decoding pixels
        with Image.open(io.BytesIO(raw)) as img:
            width, height = img.size
            if width * height > MAX_PIXELS:
                raise UnsupportedImage(
                    "That image has too many pixels to process safely. Please take a "
                    "smaller photo."
                )
            prepared = img.convert("RGB")
            if max(prepared.size) > MAX_EDGE_PIXELS:
                prepared.thumbnail((MAX_EDGE_PIXELS, MAX_EDGE_PIXELS))
            buffer = io.BytesIO()
            # Re-encoding drops EXIF (including GPS) entirely.
            prepared.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            data = buffer.getvalue()
            result = PreparedImage(
                data=data,
                mime_type="image/jpeg",
                width=prepared.size[0],
                height=prepared.size[1],
                re_encoded=True,
            )
            if mime != "image/jpeg":
                result.notes.append(f"converted_from_{mime.split('/')[-1]}")
            return result
    except UnsupportedImage:
        raise
    except Exception as exc:
        logger.warning("Image validation failed: %s", exc)
        raise UnsupportedImage(
            "We could not read that image. Please retake the photo in good light."
        ) from exc
