"""Canonical, lossless encoding of the declared conversation-record payload."""
from __future__ import annotations

import gzip
import hashlib
import io
import json

ARCHIVE_VERSION = 1
ARCHIVE_KIND = "common_ground_conversation_history"


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def encode(payload: dict, maximum_bytes: int) -> bytes:
    raw = canonical(payload)
    if len(raw) > maximum_bytes:
        raise ValueError("Conversation archive exceeds the resource limit")
    envelope = {"kind": ARCHIVE_KIND, "version": ARCHIVE_VERSION,
                "payload_sha256": hashlib.sha256(raw).hexdigest(), "payload": payload}
    encoded = canonical(envelope)
    if len(encoded) > maximum_bytes:
        raise ValueError("Conversation archive exceeds the resource limit")
    return gzip.compress(encoded, mtime=0)


def decode(raw: bytes, maximum_bytes: int) -> dict:
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
        unpacked = stream.read(maximum_bytes + 1)
    if len(unpacked) > maximum_bytes:
        raise ValueError("Conversation archive exceeds the resource limit")
    envelope = json.loads(unpacked)
    if (not isinstance(envelope, dict)
            or set(envelope) != {"kind", "version", "payload_sha256", "payload"}
            or envelope["kind"] != ARCHIVE_KIND or type(envelope["version"]) is not int
            or envelope["version"] != ARCHIVE_VERSION
            or not isinstance(envelope["payload"], dict)
            or hashlib.sha256(canonical(envelope["payload"])).hexdigest() != envelope["payload_sha256"]):
        raise ValueError("Invalid conversation archive identity or payload fingerprint")
    return envelope["payload"]
