"""Offline Chromium check of three cards marked done without a browser test (Atlas, 10 October 2026): system light and
dark themes, the message time DD.MM.YYYY - HH:MM, and readable formatting with failure isolation. All requests are
intercepted synthetic fixtures; no server starts. The isolation check needs a formatting failure, which no real
message can cause (the formatter caps its depth), so the test serves the real app.js with one appended line that
makes formatting fail for the marked message only; every other line of app.js is the real file."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from qa_support import confined_playwright, evidence_root

STATIC = Path(__file__).resolve().parents[1] / "static"
ORIGIN = "http://common-ground.test"
BREAK = "SYNTHETIC-FORMAT-FAULT"
INJECTED_FAULT = ("\n{ const real = formattedMessage; formattedMessage = (body) => { if (String(body).includes("
                  f"'{BREAK}')) throw new Error('synthetic formatting fault'); return real(body); }}; }}\n")
MESSAGES: list[dict[str, Any]] = [
    {"id": 1, "channel_id": "workspace", "author_id": "codex", "author_name": "Codex",
     "body": "# Plan heading\nA plain line", "created_at": "2026-10-10T13:05:00+00:00"},
    {"id": 2, "channel_id": "workspace", "author_id": "codex", "author_name": "Codex",
     "body": f"{BREAK} raw *text*", "created_at": "2026-10-10T13:06:00+00:00"},
    {"id": 3, "channel_id": "workspace", "author_id": "codex", "author_name": "Codex",
     "body": "After the broken one", "created_at": "2026-10-10T13:07:00+00:00"},
]


class DisplayCardsBrowserTests(unittest.TestCase):
    def test_themes_message_time_formatting_and_isolation(self) -> None:
        report: dict[str, Any] = {"checks": {}, "external_requests": [], "unexpected": [], "backgrounds": {}}
        errors: list[str] = []

        def check(name: str, value: bool) -> None:
            report["checks"][name] = bool(value)
            self.assertTrue(value, name)

        def route(request: Any) -> None:
            url = urlsplit(request.request.url)
            if url.hostname != "common-ground.test":
                report["external_requests"].append(request.request.url)
                request.abort()
                return
            if url.path == "/":
                request.fulfill(path=str(STATIC / "index.html"), content_type="text/html")
                return
            if url.path == "/static/app.js":
                request.fulfill(body=(STATIC / "app.js").read_text() + INJECTED_FAULT, content_type="text/javascript")
                return
            if url.path.startswith("/static/") and Path(url.path).name in {"style.css", "common-ground.svg"}:
                request.fulfill(path=str(STATIC / Path(url.path).name))
                return
            # Fictional participant names exercise the display.
            people = [{"id": "curator", "name": "Curator", "kind": "human"}, {"id": "codex", "name": "Codex", "kind": "agent"}]
            state = {"workspace": {"title": "Common Ground · Aurora Museum", "project_label": "Exhibition planning"}, "me": people[0],
                     "participants": people,
                     "channels": [{"id": "workspace", "title": "Workspace", "kind": "shared",
                                   "members": ["codex", "curator"], "unread": 0}],
                     "decisions": [], "knowledge": [], "sources": [], "tasks": []}
            after = int(parse_qs(url.query).get("after", ["0"])[0])
            page_messages = [message for message in MESSAGES if message["id"] > after]
            answers: dict[str, Any] = {"/api/state": state, "/api/bookmarks": {"messages": []},
                                       "/api/messages": {"messages": page_messages,
                                                         "cursor": page_messages[-1]["id"] if page_messages else after},
                                       "/api/read": {"ok": True},
                                       "/api/personal": {"drafts": [], "outbox": [],
                                                         "preferences": {"values": {}, "version": 0}}}
            if url.path == "/api/personal/preferences":
                body = request.request.post_data_json or {}
                answers[url.path] = {"values": body.get("values", {}), "version": body.get("version", 0) + 1}
            if url.path not in answers:
                report["unexpected"].append(url.path)
                request.fulfill(status=400, json={"error": "Unexpected synthetic request"})
                return
            request.fulfill(json=answers[url.path])

        try:
            with confined_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                for scheme, zone, clock in (("light", "UTC", "13:05"), ("dark", "America/New_York", "09:05")):
                    context = browser.new_context(viewport={"width": 1280, "height": 900}, color_scheme=scheme, timezone_id=zone)
                    context.route("**/*", route)
                    page = context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(ORIGIN)
                    page.locator(".message", has_text="After the broken one").wait_for(state="visible")
                    theme = page.evaluate("() => [document.documentElement.dataset.theme, "
                                          "document.documentElement.dataset.themePreference]")
                    report["backgrounds"][scheme] = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
                    check(f"system_preference_{scheme}_resolves_to_{scheme}", theme == [scheme, "system"])
                    first = page.locator('.message[data-message-id="1"]')
                    time = first.locator("time")
                    check(f"message_time_uses_browser_timezone_{zone}",
                          time.inner_text() == f"10.10.2026 - {clock}"
                          and time.get_attribute("title") == f"10.10.2026 - {clock} · {zone}")
                    if scheme == "dark":
                        check("a_heading_line_renders_as_a_heading",
                              first.locator(".formatted-message h1").inner_text() == "Plan heading")
                        broken = page.locator('.message[data-message-id="2"]')
                        check("a_failed_message_shows_its_original_text_and_a_note",
                              broken.locator(".formatting-fallback").inner_text() == f"{BREAK} raw *text*"
                              and broken.locator(".formatting-error").inner_text() == "Formatting error · original text shown.")
                        check("the_next_message_still_renders_and_sending_stays_enabled",
                              page.locator('.message[data-message-id="3"] .formatted-message').is_visible()
                              and page.locator("#message-input").is_enabled())
                        page.screenshot(path=str(evidence_root() / "display_cards.png"))
                    context.close()
                check("dark_and_light_backgrounds_differ", report["backgrounds"]["light"] != report["backgrounds"]["dark"])
                browser.close()
        finally:   # the report is evidence on a failure too
            report["page_errors"] = errors
            (evidence_root() / "display_cards.json").write_text(json.dumps(report, indent=1) + "\n")
        self.assertEqual(errors, [])
        self.assertEqual(report["external_requests"], [])
        self.assertEqual(report["unexpected"], [])


if __name__ == "__main__":
    unittest.main()
