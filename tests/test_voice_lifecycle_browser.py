"""Synthetic device controls for the real voice composer; no microphone is accessed."""
from __future__ import annotations

import base64
import json
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from qa_support import confined_playwright, evidence_root

STATIC = Path(__file__).resolve().parents[1] / "static"
ORIGIN = "http://localhost:47811"
VOICE_DEVICE = """
window.NativeMediaRecorder = window.MediaRecorder;
window.voiceFixture = {pending: [], tracks: [], recorders: [], failure: null};
navigator.mediaDevices.getUserMedia = () => new Promise(resolve => {
  voiceFixture.pending.push(() => {
    const track = {stops: 0, stop() { this.stops++; }};
    voiceFixture.tracks.push(track);
    resolve({getTracks: () => [track]});
  });
});
class FixtureRecorder extends EventTarget {
  static isTypeSupported() { return true; }
  constructor(stream) {
    super();
    if (voiceFixture.failure === 'constructor') throw new Error('Synthetic constructor refusal');
    this.stream = stream; this.state = 'inactive'; this.mimeType = 'audio/webm';
    voiceFixture.recorders.push(this);
  }
  start() {
    if (voiceFixture.failure === 'start') throw new Error('Synthetic start refusal');
    this.state = 'recording';
  }
  stop() {
    if (this.state === 'inactive') throw new Error('Synthetic repeated stop');
    this.state = 'inactive';
    setTimeout(() => {
      const event = new Event('dataavailable');
      event.data = new Blob([new Uint8Array([26, 69, 223, 163, 1])], {type: this.mimeType});
      this.dispatchEvent(event); this.dispatchEvent(new Event('stop'));
    }, 0);
  }
}
window.MediaRecorder = FixtureRecorder;
"""


class VoiceLifecycleBrowserTests(unittest.TestCase):
    @contextmanager
    def page_fixture(self) -> Any:
        report: dict[str, Any] = {"uploads": [], "errors": [], "external": []}
        current = {"id": "human", "name": "Human", "kind": "human"}

        def route(request: Any) -> None:
            url = urlsplit(request.request.url)
            if url.hostname != "localhost":
                report["external"].append(request.request.url)
                request.abort()
                return
            if url.path == "/":
                request.fulfill(path=str(STATIC / "index.html"), content_type="text/html")
                return
            if url.path.startswith("/static/") and Path(url.path).name in {"app.js", "style.css", "common-ground.svg"}:
                request.fulfill(path=str(STATIC / Path(url.path).name))
                return
            if url.path == "/api/audio/" + "a" * 32 and "audio" in report:
                request.fulfill(body=base64.b64decode(report["audio"]), content_type="audio/webm")
                return
            data = request.request.post_data_json or {}
            answers = {
                "/api/state": {"workspace": {"title": "Synthetic", "project_label": "Synthetic"},
                    "me": current, "participants": [current],
                    "channels": [{"id": channel, "title": channel, "kind": "shared", "members": ["human"],
                                  "unread": 0, "read_markers": {}} for channel in ["workspace", "other"]],
                    "decisions": [], "knowledge": [], "sources": [], "tasks": []},
                "/api/bookmarks": {"messages": []},
                "/api/search": {"messages": [], "decisions": [], "tasks": [], "knowledge": [], "sources": []},
                "/api/messages": {"messages": [], "cursor": 0},
                "/api/personal": {"drafts": [], "outbox": [], "preferences": {"values": {}, "version": 0}},
                "/api/personal/preferences": {"values": data.get("values", {}), "version": data.get("version", 0) + 1},
                "/api/personal/draft": {"channel_id": data.get("channel_id"), "body": data.get("body"),
                                        "version": data.get("version", 0) + 1},
            }
            if url.path == "/api/audio":
                report["uploads"].append(data)
                answers[url.path] = {"id": "a" * 32, "url": "/api/audio/" + "a" * 32}
            if url.path not in answers:
                request.fulfill(status=400, json={"error": "Unexpected synthetic request"})
                return
            request.fulfill(json=answers[url.path])

        with confined_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            context.add_init_script(VOICE_DEVICE)
            context.route("**/*", route)
            page = context.new_page()
            page.on("pageerror", lambda error: report["errors"].append(str(error)))
            page.goto(ORIGIN)
            page.wait_for_function("() => document.querySelector('#app-shell').hidden === false")
            try:
                yield page, report
            finally:
                (evidence_root() / (self._testMethodName + ".json")).write_text(json.dumps(report, indent=2) + "\n")
                browser.close()
        self.assertEqual(report["external"], [])

    def begin_pending(self, page: Any) -> None:
        page.locator("#record-voice").click()
        page.wait_for_function("() => voiceFixture.pending.length === 1")

    def grant(self, page: Any) -> None:
        page.evaluate("voiceFixture.pending.shift()()")
        page.wait_for_timeout(60)

    def test_double_click_starts_only_one_permission_request(self) -> None:
        with self.page_fixture() as (page, report):
            self.begin_pending(page)
            page.evaluate("document.querySelector('#record-voice').click()")
            self.assertEqual(page.evaluate("voiceFixture.pending.length"), 1)
            self.assertEqual(report["errors"], [])

    def test_pending_permission_after_logout_never_starts_and_releases_stream(self) -> None:
        with self.page_fixture() as (page, report):
            self.begin_pending(page)
            page.evaluate("showLogin()")
            self.grant(page)
            self.assertEqual(page.evaluate("voiceFixture.recorders.length"), 0)
            self.assertEqual(page.evaluate("voiceFixture.tracks[0].stops"), 1)
            self.assertEqual(report["uploads"], [])
            self.assertEqual(report["errors"], [])

    def test_account_change_stops_active_recording_without_upload(self) -> None:
        with self.page_fixture() as (page, report):
            self.begin_pending(page)
            self.grant(page)
            self.assertEqual(page.evaluate("voiceFixture.recorders[0].state"), "recording")
            page.evaluate("showLogin()")
            page.wait_for_timeout(60)
            self.assertEqual(page.evaluate("voiceFixture.tracks[0].stops"), 1)
            self.assertEqual(page.evaluate("voiceFixture.recorders[0].state"), "inactive")
            self.assertEqual(report["uploads"], [])
            self.assertEqual(report["errors"], [])

    def test_constructor_and_start_failure_release_stream_and_restore_button(self) -> None:
        with self.page_fixture() as (page, report):
            for failure in ["constructor", "start"]:
                with self.subTest(failure=failure):
                    page.evaluate("(failure) => { voiceFixture.failure = failure; }", failure)
                    self.begin_pending(page)
                    self.grant(page)
                    self.assertEqual(page.evaluate("voiceFixture.tracks.at(-1).stops"), 1)
                    self.assertEqual(page.locator("#record-voice").get_attribute("aria-label"), "Record voice message")
                    self.assertFalse(page.locator("#record-voice").is_disabled())
            self.assertEqual(report["uploads"], [])
            self.assertEqual(report["errors"], [])

    def test_recorder_error_releases_stream_without_upload(self) -> None:
        with self.page_fixture() as (page, report):
            self.begin_pending(page)
            self.grant(page)
            page.evaluate("voiceFixture.recorders[0].dispatchEvent(new Event('error'))")
            page.wait_for_timeout(60)
            self.assertEqual(page.evaluate("voiceFixture.tracks[0].stops"), 1)
            self.assertEqual(page.locator("#record-voice").get_attribute("aria-label"), "Record voice message")
            self.assertEqual(report["uploads"], [])
            self.assertEqual(report["errors"], [])

    def test_normal_stop_uploads_once_to_original_channel(self) -> None:
        with self.page_fixture() as (page, report):
            self.begin_pending(page)
            self.grant(page)
            page.evaluate("selectChannel('other')")
            page.locator("#record-voice").click()
            page.wait_for_function("() => !document.querySelector('#record-voice').classList.contains('recording')")
            page.wait_for_timeout(100)
            self.assertEqual(len(report["uploads"]), 1)
            self.assertEqual(report["uploads"][0]["channel_id"], "workspace")
            self.assertEqual(page.evaluate("voiceFixture.tracks[0].stops"), 1)
            self.assertEqual(report["errors"], [])

    def test_message_refresh_preserves_the_audio_player(self) -> None:
        with self.page_fixture() as (page, report):
            page.evaluate("""() => {
                cacheFor('workspace').loaded = true;
                cacheFor('workspace').messages = [{id: 1, channel_id: 'workspace', author_id: 'human',
                    author_name: 'Human', created_at: '2026-10-12T04:00:00+00:00', version: 1,
                    can_edit: false, body: '[Voice message 0:03](/api/audio/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa)'}];
                renderMessages();
                window.originalVoicePlayer = document.querySelector('audio.message-audio');
                originalVoicePlayer.currentTime = 2;
                renderMessages(true);
            }""")
            self.assertTrue(page.evaluate("originalVoicePlayer === document.querySelector('audio.message-audio')"))
            self.assertEqual(page.evaluate("document.querySelector('audio.message-audio').currentTime"), 2)
            self.assertEqual(report["errors"], [])

    def test_old_recorder_error_cannot_cancel_the_new_account_recording(self) -> None:
        with self.page_fixture() as (page, report):
            self.begin_pending(page)
            self.grant(page)
            page.evaluate("showLogin()")
            page.evaluate("start()")
            self.begin_pending(page)
            self.grant(page)
            page.evaluate("voiceFixture.recorders[0].dispatchEvent(new Event('error'))")
            page.wait_for_timeout(60)
            self.assertEqual(page.evaluate("voiceFixture.recorders[1].state"), "recording")
            self.assertEqual(page.evaluate("voiceFixture.tracks[1].stops"), 0)
            self.assertEqual(report["uploads"], [])
            self.assertEqual(report["errors"], [])

    def test_account_change_while_encoding_prevents_upload(self) -> None:
        with self.page_fixture() as (page, report):
            page.evaluate("""() => {
                fileData = () => new Promise(resolve => { window.finishEncoding = resolve; });
                window.pendingAttachment = attachVoice(new Blob(['synthetic']), 1, 'workspace', app.personalGeneration);
            }""")
            page.evaluate("showLogin()")
            page.evaluate("finishEncoding('GkXfowE=')")
            page.wait_for_timeout(60)
            self.assertEqual(report["uploads"], [])
            self.assertEqual(report["errors"], [])

    def test_search_pauses_and_releases_conversation_players(self) -> None:
        with self.page_fixture() as (page, report):
            page.evaluate("""() => {
                cacheFor('workspace').loaded = true;
                cacheFor('workspace').messages = [{id: 1, channel_id: 'workspace', author_id: 'human',
                    author_name: 'Human', created_at: '2026-10-12T04:00:00+00:00', version: 1,
                    can_edit: false, body: '[Voice message 0:03](/api/audio/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa)'}];
                renderMessages();
                window.originalVoicePlayer = document.querySelector('audio.message-audio');
                window.pauseCalls = 0;
                originalVoicePlayer.pause = () => { pauseCalls++; };
                document.querySelector('#search-input').value = 'voice';
                runSearch('voice');
            }""")
            page.wait_for_timeout(60)
            self.assertEqual(page.evaluate("pauseCalls"), 1)
            self.assertEqual(page.evaluate("app.voicePlayers.size"), 0)
            self.assertEqual(report["errors"], [])

    def test_queued_messages_have_distinct_players_for_the_same_attachment(self) -> None:
        with self.page_fixture() as (page, report):
            page.evaluate("""() => {
                cacheFor('workspace').loaded = true;
                app.outbox = ['first', 'second'].map(client_id => ({client_id, channel_id: 'workspace',
                    author_id: 'human', author_name: 'Human', created_at: '2026-10-12T04:00:00+00:00',
                    version: 1, status: 'queued', body: '[Voice message 0:03](/api/audio/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa)'}));
                renderMessages();
                renderMessages(true);
            }""")
            self.assertEqual(page.locator('audio.message-audio').count(), 2)
            self.assertEqual(page.evaluate("app.voicePlayers.size"), 2)
            self.assertEqual(report["errors"], [])

    def test_playable_tone_keeps_playing_across_refresh_and_stops_on_search(self) -> None:
        with self.page_fixture() as (page, report):
            report["audio"] = page.evaluate("""async () => {
                const context = new AudioContext();
                const destination = context.createMediaStreamDestination();
                const oscillator = context.createOscillator();
                oscillator.connect(destination);
                const recorder = new NativeMediaRecorder(destination.stream, {mimeType: 'audio/webm'});
                const chunks = [];
                recorder.addEventListener('dataavailable', event => chunks.push(event.data));
                const stopped = new Promise(resolve => recorder.addEventListener('stop', resolve, {once: true}));
                recorder.start(); oscillator.start(); await context.resume();
                await new Promise(resolve => setTimeout(resolve, 1500));
                recorder.stop(); await stopped; oscillator.stop(); await context.close();
                const bytes = new Uint8Array(await new Blob(chunks).arrayBuffer());
                return btoa(Array.from(bytes, byte => String.fromCharCode(byte)).join(''));
            }""")
            page.evaluate("""() => {
                cacheFor('workspace').loaded = true;
                cacheFor('workspace').messages = [{id: 1, channel_id: 'workspace', author_id: 'human',
                    author_name: 'Human', created_at: '2026-10-12T04:00:00+00:00', version: 1,
                    can_edit: false, body: '[Voice message 0:02](/api/audio/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa)'}];
                renderMessages();
                window.originalVoicePlayer = document.querySelector('audio.message-audio');
            }""")
            # The real message player does not preload audio. Playing requests the media data.
            page.locator("audio.message-audio").click()
            page.evaluate("originalVoicePlayer.play()")
            page.wait_for_function("() => originalVoicePlayer.currentTime > 0.1")
            page.evaluate("window.voiceTimeBeforeRefresh = originalVoicePlayer.currentTime; renderView()")
            self.assertTrue(page.evaluate("originalVoicePlayer === document.querySelector('audio.message-audio')"))
            self.assertFalse(page.evaluate("originalVoicePlayer.paused"))
            self.assertGreaterEqual(page.evaluate("originalVoicePlayer.currentTime"), page.evaluate("voiceTimeBeforeRefresh"))
            page.evaluate("""() => {
                cacheFor('workspace').messages.push({id: 2, channel_id: 'workspace', author_id: 'human',
                    author_name: 'Human', created_at: '2026-10-12T04:01:00+00:00', version: 1, body: 'New text'});
                renderMessages();
                document.querySelector('#search-input').value = 'voice';
                runSearch('voice');
            }""")
            self.assertTrue(page.evaluate("originalVoicePlayer.paused"))
            self.assertEqual(page.evaluate("app.voicePlayers.size"), 0)
            self.assertEqual(page.evaluate("voiceFixture.pending.length"), 0)
            self.assertEqual(report["errors"], [])
