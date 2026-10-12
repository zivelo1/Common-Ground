"""Bounded, decoded raster images and recorded voice messages stored in the private SQLite database."""
from __future__ import annotations

import base64
import binascii
import io
import warnings

from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGE_REQUEST_BYTES = ((MAX_IMAGE_BYTES + 2) // 3) * 4 + 65536
IMAGE_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
MAX_AUDIO_BYTES = MAX_IMAGE_BYTES              # one 8 MB attachment limit, one request limit
# The containers browsers' MediaRecorder writes: WebM (Chromium, Firefox), Ogg (Firefox), MP4 (Safari): (signature,
# offset, content type). Only the container is recognised; the audio is never decoded or transcribed here.
AUDIO_SIGNATURES = ((b"\x1a\x45\xdf\xa3", 0, "audio/webm"), (b"OggS", 0, "audio/ogg"), (b"ftyp", 4, "audio/mp4"))


def validate_image(encoded: object) -> tuple[bytes, str]:
    if not isinstance(encoded, str) or not encoded or len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise ValueError("Choose a PNG, JPEG, or WebP image of at most 8 MB")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("Invalid image encoding") from error
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds the 8 MB limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                content_type = IMAGE_TYPES.get(image.format or "")
                if content_type is None or image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("Choose PNG, JPEG, or WebP with at most 20 million pixels")
                image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError("Invalid or oversized image") from error
    return raw, content_type


def validate_audio(encoded: object) -> tuple[bytes, str]:
    if not isinstance(encoded, str) or not encoded or len(encoded) > ((MAX_AUDIO_BYTES + 2) // 3) * 4:
        raise ValueError("Choose a recording of at most 8 MB")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError("Invalid recording encoding") from error
    if not raw or len(raw) > MAX_AUDIO_BYTES:
        raise ValueError("Recording exceeds the 8 MB limit")
    for signature, offset, content_type in AUDIO_SIGNATURES:
        if raw[offset:offset + len(signature)] == signature:
            return raw, content_type
    raise ValueError("Only WebM, Ogg or MP4 recordings are accepted")
