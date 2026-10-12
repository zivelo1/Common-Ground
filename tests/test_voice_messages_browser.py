"""Offline Chromium check of voice messages (). The page's real MediaRecorder records a real clip: the
composer's microphone button starts and stops it, the recording is uploaded once and a "Voice message m:ss" link enters
the draft; in the conversation such a link plays in place, and any other link stays a link. All requests are
intercepted synthetic fixtures; no server starts; nothing leaves the page."""
from __future__ import annotations

import base64
import json
import re
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from qa_support import confined_playwright, evidence_root

STATIC = Path(__file__).resolve().parents[1] / "static"
# The microphone exists only in a secure context; the app runs on 127.0.0.1, so the fixture origin is localhost too
# (attempt 1 used http://common-ground.test, where the page has no navigator.mediaDevices). No server listens there:
# every request is intercepted below.
HOST = "localhost"
ORIGIN = "http://localhost:47811"
# The DEVICE only is replaced: getUserMedia returns a WebAudio tone. On this Mac the headless browser's microphone request
# is never answered (probe_voice.py in the unit folder: Chromium's fake-device flags, a microphone grant for one origin or
# for all, and the full Chromium all wait more than 5 s); recording, encoding, upload and drawing stay the app's own.
TONE_MICROPHONE = """navigator.mediaDevices.getUserMedia = async () => {
    const audio = new AudioContext(); const tone = audio.createOscillator(); const sink = audio.createMediaStreamDestination();
    tone.connect(sink); tone.start(); return sink.stream; };"""
VOICE_ID = "a" * 32
VOICE_URL = "/api/audio/" + VOICE_ID
WEBM_SIGNATURE = b"\x1a\x45\xdf\xa3"


class VoiceMessagesBrowserTests(unittest.TestCase):
    def test_record_attach_and_play_a_voice_message(self) -> None:
        report: dict[str, Any] = {"checks": {}, "external_requests": [], "unexpected": [], "uploads": []}
        errors: list[str] = []
        drafts: list[str] = []
        me = {"id": "curator", "name": "Curator", "kind": "human"}
        messages: list[dict[str, Any]] = [
            {"id": 1, "channel_id": "workspace", "author_id": "atlas", "author_name": "Atlas", "version": 1, "edited_at": None,
             "can_edit": False, "created_at": "2026-10-11T14:00:00+00:00",
             "body": f"Listen: [Voice message 0:03]({VOICE_URL}) and read [the guide](https://example.org/guide)"}]

        def check(name: str, value: bool) -> None:
            report["checks"][name] = bool(value)
            self.assertTrue(value, name)

        def route(request: Any) -> None:
            url = urlsplit(request.request.url)
            if url.hostname != HOST:
                report["external_requests"].append(request.request.url)
                request.abort()
                return
            if url.path == "/":
                request.fulfill(path=str(STATIC / "index.html"), content_type="text/html")
                return
            if url.path.startswith("/static/") and Path(url.path).name in {"app.js", "style.css", "common-ground.svg"}:
                request.fulfill(path=str(STATIC / Path(url.path).name))
                return
            if url.path == VOICE_URL:
                request.fulfill(body=WEBM_SIGNATURE + b"synthetic", content_type="audio/webm")
                return
            body = request.request.post_data_json or {}
            after = int(parse_qs(url.query).get("after", ["0"])[0])
            state = {"workspace": {"title": "Common Ground", "project_label": "Aurora Museum"}, "me": me,
                     "participants": [me, {"id": "atlas", "name": "Atlas", "kind": "agent"}],
                     "channels": [{"id": "workspace", "title": "Workspace", "kind": "shared",
                                   "members": ["atlas", "curator"], "unread": 0, "read_markers": {}}],
                     "decisions": [], "knowledge": [], "sources": [], "tasks": []}
            answers: dict[str, Any] = {"/api/state": state, "/api/bookmarks": {"messages": []},
                                       "/api/messages": {"messages": [m for m in messages if m["id"] > after], "cursor": 1},
                                       "/api/read": {"ok": True},
                                       "/api/personal": {"drafts": [], "outbox": [],
                                                         "preferences": {"values": {}, "version": 0}}}
            if url.path == "/api/personal/preferences":
                answers[url.path] = {"values": body.get("values", {}), "version": body.get("version", 0) + 1}
            if url.path == "/api/personal/draft":
                drafts.append(body["body"])
                answers[url.path] = {"channel_id": body["channel_id"], "body": body["body"], "version": body["version"] + 1}
            if url.path == "/api/audio" and request.request.method == "POST":
                raw = base64.b64decode(body["data"])
                report["uploads"].append({"name": body["name"], "bytes": len(raw), "webm": raw[:4] == WEBM_SIGNATURE})
                answers[url.path] = {"id": VOICE_ID, "url": VOICE_URL, "name": body["name"], "content_type": "audio/webm",
                                     "bytes": len(raw)}
            if url.path not in answers:
                report["unexpected"].append(url.path)
                request.fulfill(status=400, json={"error": "Unexpected synthetic request"})
                return
            request.fulfill(json=answers[url.path])

        try:
            with confined_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required"])
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                context.add_init_script(TONE_MICROPHONE)
                context.route("**/*", route)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(ORIGIN)
                page.locator('[data-message-id="1"]').wait_for()
                # the conversation: an app voice link plays in place; another link stays a link
                bubble = page.locator('[data-message-id="1"] .message-bubble')
                check("voice_link_becomes_a_player", bubble.evaluate(
                    "e => { const a = e.querySelector('audio.message-audio'); return a && a.controls"
                    " && a.getAttribute('src') === '" + VOICE_URL + "' && a.getAttribute('aria-label') === 'Voice message 0:03'; }"))
                check("other_links_stay_links", bubble.evaluate(
                    "e => e.querySelectorAll('audio').length === 1 && e.querySelector('a').href === 'https://example.org/guide'"))
                # the composer: start, stop, upload once, link in the draft
                record = page.locator("#record-voice")
                record.click()
                page.wait_for_function("() => document.querySelector('#record-voice').getAttribute('aria-label') === 'Stop recording'")
                check("recording_button_shows_stop", "recording" in (record.get_attribute("class") or ""))
                page.wait_for_timeout(1300)
                record.click()
                page.wait_for_function("() => document.querySelector('#message-input').value.includes('/api/audio/')")
                draft = page.locator("#message-input").input_value()
                check("draft_gets_one_voice_link", re.fullmatch(r"\[Voice message 0:0[1-3]\]\(" + re.escape(VOICE_URL) + r"\)", draft) is not None)
                check("one_real_webm_recording_uploaded", len(report["uploads"]) == 1 and report["uploads"][0]["webm"]
                      and report["uploads"][0]["bytes"] > 100)
                check("button_returns_to_record", record.get_attribute("aria-label") == "Record voice message")
                page.wait_for_function("() => document.querySelector('#personal-save-status')?.dataset.saved === 'true'")
                check("the_draft_save_carries_the_link", drafts[-1] == draft)
                page.screenshot(path=str(evidence_root() / "voice_messages_desktop.png"))
                page.set_viewport_size({"width": 390, "height": 844})
                page.wait_for_timeout(300)
                check("phone_has_no_horizontal_overflow", page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"))
                page.screenshot(path=str(evidence_root() / "voice_messages_phone.png"))
                browser.close()
        finally:   # the report is evidence on a failure too
            report["page_errors"] = errors
            (evidence_root() / "voice_messages.json").write_text(json.dumps(report, indent=1) + "\n")
        self.assertEqual(errors, [])
        self.assertEqual(report["external_requests"], [])
        self.assertEqual(report["unexpected"], [])


if __name__ == "__main__":
    unittest.main()
