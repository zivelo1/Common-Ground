"""Offline Chromium check: each private conversation's rhombus has its own stable colour (Collaboration; ).
All requests are intercepted synthetic fixtures; no server starts and nothing leaves the page."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from qa_support import confined_playwright, evidence_root

STATIC = Path(__file__).resolve().parents[1] / "static"
ORIGIN = "http://common-ground.test"
PRIVATE = {"c0ffee01": "Curator and Codex", "5a1e0002": "Curator and Atlas", "9b0b0003": "Curator and Scribe"}
MINIMUM_CONTRAST = 3.0      # WCAG 2 non-text contrast for a graphical object


def luminance(rgb: str) -> float:
    def channel(value: int) -> float:
        value_01 = value / 255
        return value_01 / 12.92 if value_01 <= 0.03928 else ((value_01 + 0.055) / 1.055) ** 2.4
    red, green, blue = (int(part) for part in re.findall(r"\d+", rgb)[:3])
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast(one: str, two: str) -> float:
    light, dark = sorted((luminance(one), luminance(two)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


class RhombusColourBrowserTests(unittest.TestCase):
    def test_each_private_conversation_keeps_its_own_colour(self) -> None:
        report: dict[str, Any] = {"checks": {}, "external_requests": [], "unexpected": []}
        titles = dict(PRIVATE)
        errors: list[str] = []

        def state() -> dict[str, Any]:
            channels = [{"id": "workspace", "title": "Workspace", "kind": "shared", "members": ["human", "agent"],
                         "unread": 0},
                        {"id": "group01", "title": "Review group", "kind": "group", "members": ["human", "agent", "other"],
                         "unread": 0}]
            channels += [{"id": ident, "title": title, "kind": "private", "members": ["human", "agent"], "unread": 0}
                         for ident, title in titles.items()]
            return {"workspace": {"title": "Synthetic QA", "project_label": "Offline"},
                    "me": {"id": "human", "name": "Synthetic human", "kind": "human"},
                    "participants": [{"id": "human", "name": "Synthetic human", "kind": "human"},
                                     {"id": "agent", "name": "Synthetic agent", "kind": "agent"}],
                    "channels": channels, "decisions": [], "knowledge": [], "sources": [],
                    "tasks": [{"id": "task01", "channel_id": "9b0b0003", "title": "Synthetic task", "body": "",
                               "status": "open", "priority": "normal", "owner_id": "agent", "due_date": None,
                               "version": 1, "created_at": "2026-10-10T08:00:00+00:00",
                               "updated_at": "2026-10-10T08:00:00+00:00"}]}

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
            if url.path.startswith("/static/") and Path(url.path).name in {"app.js", "style.css", "common-ground.svg"}:
                request.fulfill(path=str(STATIC / Path(url.path).name))
                return
            answers: dict[str, Any] = {"/api/state": state(), "/api/bookmarks": {"messages": []},
                                       "/api/messages": {"messages": [], "cursor": 0}, "/api/read": {"ok": True},
                                       "/api/personal": {"drafts": [], "outbox": [],
                                                         "preferences": {"values": {}, "version": 0}}}
            if url.path == "/api/personal/preferences":      # the app saves its view preferences; echo them back
                body = request.request.post_data_json or {}
                answers[url.path] = {"values": body.get("values", {}), "version": body.get("version", 0) + 1}
            if url.path not in answers:
                report["unexpected"].append(url.path)
                request.fulfill(status=400, json={"error": "Unexpected synthetic request"})
                return
            request.fulfill(json=answers[url.path])

        def colours(page: Any) -> dict[str, str]:
            return {ident: page.locator(".channel-button", has_text=title).locator(".channel-symbol")
                    .evaluate("e => getComputedStyle(e).color") for ident, title in titles.items()}

        def sidebar(page: Any) -> str:
            return page.locator(".sidebar").evaluate("e => getComputedStyle(e).backgroundColor")

        with confined_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.route("**/*", route)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(ORIGIN)
            page.locator("#app-shell").wait_for(state="visible")
            first = colours(page)
            shared = page.locator(".channel-button", has_text="Workspace").locator(".channel-symbol").evaluate(
                "e => getComputedStyle(e).color")
            group = page.locator(".channel-button", has_text="Review group").locator(".channel-symbol").evaluate(
                "e => getComputedStyle(e).color")
            report["light"] = {"private": first, "shared": shared, "group": group, "sidebar": sidebar(page)}
            check("three_private_conversations_three_colours", len(set(first.values())) == len(PRIVATE))
            check("private_colours_differ_from_shared_and_group", not set(first.values()) & {shared, group})
            check("light_contrast_at_least_3", all(contrast(colour, sidebar(page)) >= MINIMUM_CONTRAST
                                                   for colour in first.values()))
            page.locator('[data-view="tasks"]').click()
            card = page.locator(".record-card", has_text="Synthetic task").locator(".card-channel-symbol")
            check("task_card_rhombus_matches_sidebar", card.evaluate("e => getComputedStyle(e).color") == first["9b0b0003"])
            page.locator('[data-view="conversations"]').click()
            page.locator(".channel-button", has_text="Curator and Atlas").click()
            header = page.locator(".context-icon").evaluate("e => getComputedStyle(e).color")
            check("conversation_header_rhombus_matches_sidebar", header == first["5a1e0002"])
            page.reload()
            page.locator("#app-shell").wait_for(state="visible")
            check("same_colours_after_reload", colours(page) == first)
            titles["c0ffee01"] = "Renamed conversation"
            page.reload()
            page.locator("#app-shell").wait_for(state="visible")
            check("same_colours_after_a_rename", colours(page) == first)
            page.evaluate("() => { document.documentElement.dataset.theme = 'dark'; }")
            dark = colours(page)
            report["dark"] = {"private": dark, "sidebar": sidebar(page)}
            check("dark_three_colours", len(set(dark.values())) == len(PRIVATE))
            check("dark_contrast_at_least_3", all(contrast(colour, sidebar(page)) >= MINIMUM_CONTRAST
                                                  for colour in dark.values()))
            page.screenshot(path=str(evidence_root() / "rhombus_colours_dark.png"))
            browser.close()
        report["page_errors"] = errors
        (evidence_root() / "rhombus_colours.json").write_text(json.dumps(report, indent=1) + "\n")
        self.assertEqual(errors, [])
        self.assertEqual(report["external_requests"], [])
        self.assertEqual(report["unexpected"], [])


if __name__ == "__main__":
    unittest.main()
