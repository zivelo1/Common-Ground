"""Render the real UI with fictional data, fully offline and without a server.

Run from any directory with the project's Python environment:
    python tests/generate_demo_screenshots.py

Requires Playwright and its installed Chromium browser. No browser, package,
model, microphone, configuration or live database is downloaded or accessed.
Published PNGs go to docs/screenshots; the validation report and disposable
browser profile stay under ignored state/demo-qa.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
FIXTURE = ROOT / "examples/museum-demo.json"
OUTPUT = ROOT / "docs/screenshots"
QA = ROOT / "state/demo-qa"
ORIGIN = "http://common-ground.demo.test"
HOST = urlsplit(ORIGIN).hostname
DESKTOP = {"width": 1440, "height": 1060}
MOBILE = {"width": 430, "height": 932}


def generate() -> dict[str, Any]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    QA.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"fictional": True, "server_started": False, "microphone_used": False,
              "external_requests": [], "unexpected_requests": [], "page_errors": [],
              "screenshots": {}, "checks": {}, "source_sha256": {}}
    for name in ("index.html", "app.js", "style.css", "common-ground.svg"):
        report["source_sha256"][name] = hashlib.sha256((STATIC / name).read_bytes()).hexdigest()
    report["fixture_sha256"] = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    preferences: dict[str, Any] = {"values": {"view": "conversations", "channel_id": "workspace"}, "version": 1}

    def intercept(route: Any) -> None:
        request = route.request
        url = urlsplit(request.url)
        if url.hostname != HOST:
            report["external_requests"].append(request.url)
            route.abort()
            return
        if url.path == "/":
            route.fulfill(path=str(STATIC / "index.html"), content_type="text/html")
            return
        if url.path.startswith("/static/"):
            path = (STATIC / url.path.removeprefix("/static/")).resolve()
            if path.is_relative_to(STATIC.resolve()) and path.is_file():
                route.fulfill(path=str(path))
                return
        payload = request.post_data_json or {} if request.method == "POST" else {}
        if url.path == "/api/personal/preferences":
            preferences.update(values=payload.get("values", {}), version=preferences["version"] + 1)
            route.fulfill(json=preferences)
            return
        query = parse_qs(url.query)
        after = int(query.get("after", ["0"])[0])
        channel = query.get("channel_id", ["workspace"])[0]
        messages = [item for item in data["messages"] if item["channel_id"] == channel and item["id"] > after]
        responses = {
            "/api/state": data["state"],
            "/api/messages": {"messages": messages, "cursor": messages[-1]["id"] if messages else after,
                              "message_updates": [], "change_cursor": 0},
            "/api/read": {"ok": True},
            "/api/bookmarks": {"messages": []},
            "/api/personal": {"drafts": [], "outbox": [], "preferences": preferences},
            "/api/brief": data["brief"],
            "/api/knowledge": {"knowledge": data["state"]["knowledge"], "source_scan": data["source_scan"]},
            "/api/rules": data["rules"],
        }
        if re.fullmatch(r"/api/messages/\d+/receipts", url.path):
            responses[url.path] = {"received_known": True, "recipients": [
                {"id": "atlas", "name": "Atlas", "received_at": "2030-05-14T09:16:00+00:00", "read": True},
                {"id": "scribe", "name": "Scribe", "received_at": "2030-05-14T09:17:00+00:00", "read": True},
                {"id": "prism", "name": "Prism", "received_at": "2030-05-14T09:18:00+00:00", "read": False},
            ]}
        if url.path in responses:
            route.fulfill(json=responses[url.path])
            return
        report["unexpected_requests"].append(request.method + " " + url.path)
        route.fulfill(status=400, json={"error": "Unsupported offline demo request"})

    def capture(page: Any, filename: str, expected: str) -> None:
        page.get_by_text(expected, exact=False).first.wait_for(state="visible")
        page.evaluate("() => document.fonts.ready")
        text = page.locator("body").inner_text()
        if re.search(r"/(?:Users|home|private)/|[A-Z]:\\", text):
            raise AssertionError("Unexpected local filesystem path in screenshot text")
        if "Fictional museum demo" not in text:
            raise AssertionError("The fictional demonstration label is missing")
        if page.locator(".field-error:visible, .notice.error:visible").count():
            raise AssertionError("The UI contains a visible error")
        path = OUTPUT / filename
        page.screenshot(path=str(path), animations="disabled")
        report["screenshots"][filename] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                            "width": page.viewport_size["width"], "height": page.viewport_size["height"],
                                            "visible_text": text}

    try:
        with tempfile.TemporaryDirectory(prefix="browser-profile-", dir=QA) as folder:
            with patch.dict(os.environ, {"TMPDIR": folder, "PYTHONDONTWRITEBYTECODE": "1"}), sync_playwright() as driver:
                browser = driver.chromium.launch(headless=True)
                context = browser.new_context(viewport=DESKTOP, color_scheme="light", locale="en-GB",
                                              timezone_id="UTC", service_workers="block")
                context.route("**/*", intercept)
                page = context.new_page()
                page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
                page.goto(ORIGIN, wait_until="networkidle")
                page.locator('.message[data-message-id="106"]').wait_for(state="visible")
                page.evaluate("() => { const list = document.getElementById('message-list'); const first = list.querySelector('[data-message-id=\"104\"]'); list.scrollTop = first.offsetTop - list.offsetTop; }")
                capture(page, "conversation.png", "installation timetable is ready")
                page.locator('.message[data-message-id="106"]').get_by_role("button", name="Receipts", exact=True).click()
                page.get_by_text("Not read yet", exact=True).wait_for(state="visible")
                capture(page, "receipts.png", "Neither proves attention")
                page.get_by_role("button", name="Close dialog").click()
                for view, filename, expected in [
                    ("tasks", "tasks.png", "Review the 42 object labels"),
                    ("decisions", "decisions.png", "Offer a quiet preview before opening night"),
                    ("brief", "joining-brief.png", "A useful starting point for every agent"),
                    ("rules", "rules.png", "Exhibition collaboration rules"),
                ]:
                    page.locator('[data-view="' + view + '"]').click()
                    capture(page, filename, expected)
                page.locator('[data-view="brief"]').click()
                page.get_by_role("button", name="Knowledge", exact=True).click()
                capture(page, "knowledge.png", "A sourced record is not approval")
                page.locator(".project-board").evaluate("element => { element.scrollTop = 560; }")
                capture(page, "knowledge-stale.png", "Gallery B capacity needs a fresh check")
                report["checks"]["desktop_has_no_horizontal_overflow"] = page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
                context.close()
                preferences["values"] = {"view": "conversations", "channel_id": "workspace"}
                mobile = browser.new_context(viewport=MOBILE, is_mobile=True, device_scale_factor=1,
                                             color_scheme="light", locale="en-GB", timezone_id="UTC", service_workers="block")
                mobile.route("**/*", intercept)
                page = mobile.new_page()
                page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
                page.goto(ORIGIN, wait_until="networkidle")
                page.locator('.message[data-message-id="106"]').wait_for(state="visible")
                capture(page, "mobile.png", "installation timetable is ready")
                report["checks"]["mobile_has_no_horizontal_overflow"] = page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
                mobile.close()
                browser.close()
        report["checks"]["source_unchanged_during_render"] = all(
            hashlib.sha256((STATIC / name).read_bytes()).hexdigest() == fingerprint
            for name, fingerprint in report["source_sha256"].items())
        report["checks"]["fixture_unchanged_during_render"] = hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == report["fixture_sha256"]
        report["checks"]["all_requests_intercepted"] = not report["external_requests"] and not report["unexpected_requests"]
        report["checks"]["no_page_errors"] = not report["page_errors"]
        if not all(report["checks"].values()):
            raise AssertionError("Offline demo validation failed; see the local report")
        return report
    finally:
        (QA / "screenshot-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    result = generate()
    print(json.dumps({"screenshots": list(result["screenshots"]), "checks": result["checks"],
                      "external_requests": len(result["external_requests"]),
                      "unexpected_requests": len(result["unexpected_requests"]),
                      "page_errors": len(result["page_errors"])}, indent=2))
