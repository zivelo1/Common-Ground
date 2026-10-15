"""Validated public instance identity, rendered without loading custom assets."""
from __future__ import annotations

import re
from html import escape

MONOGRAM = re.compile(r"[A-Z0-9]{1,3}\Z")
COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
LOGO_TITLE = re.compile(rb'<title id="title">[^<]*</title>')
DEFAULT_LOGO_COLOR = b"#6254ce"
BRANDING_FIELDS = frozenset({"name", "monogram", "color"})


def settings(value: object, *, max_name: int) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != BRANDING_FIELDS:
        raise ValueError("branding requires exactly name, monogram and color")
    if not all(isinstance(value[key], str) for key in BRANDING_FIELDS):
        raise ValueError("branding values must be text")
    name = " ".join(value["name"].split())
    if not name or len(name) > max_name or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError("branding name must be nonempty bounded display text")
    if not MONOGRAM.fullmatch(value["monogram"]):
        raise ValueError("branding monogram must be one to three uppercase letters or digits")
    if not COLOR.fullmatch(value["color"]):
        raise ValueError("branding color must be a six-digit hexadecimal color")
    return {"name": name, "monogram": value["monogram"], "color": value["color"].lower()}


def index_page(data: bytes, profile: dict[str, str]) -> bytes:
    name = escape(profile["name"])
    replacements = {
        "<title>Common Ground</title>": f"<title>{name}</title>",
        '<strong id="brand-name">Common Ground</strong>': f'<strong id="brand-name">{name}</strong>',
        '<p class="eyebrow">COMMON GROUND · YOUR LOCAL WORKSPACE</p>':
            f'<p class="eyebrow">{name} · YOUR LOCAL WORKSPACE</p>',
    }
    text = data.decode("utf-8")
    for original, replacement in replacements.items():
        if text.count(original) != 1:
            raise ValueError("Instance branding HTML slot is missing or ambiguous")
        text = text.replace(original, replacement)
    return text.encode("utf-8")


def logo(data: bytes, profile: dict[str, str]) -> bytes:
    if len(LOGO_TITLE.findall(data)) != 1 or data.count(b"</svg>") != 1 or DEFAULT_LOGO_COLOR not in data:
        raise ValueError("Instance branding SVG slots are missing or ambiguous")
    title = ('<title id="title">' + escape(profile["name"]) + "</title>").encode()
    result = LOGO_TITLE.sub(lambda match: title, data).replace(DEFAULT_LOGO_COLOR, profile["color"].encode())
    # Reuse the bundled connection mark. An outlined monogram remains readable
    # over its white nodes, and no user-supplied SVG, path or URL is loaded.
    monogram = (f'<text x="32" y="39" text-anchor="middle" font-family="system-ui,sans-serif" '
                f'font-size="20" font-weight="750" fill="#fff" stroke="{profile["color"]}" '
                f'stroke-width="5" paint-order="stroke">{profile["monogram"]}</text>')
    return result.replace(b"</svg>", monogram.encode() + b"</svg>")
