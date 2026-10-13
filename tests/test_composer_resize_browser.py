"""Offline Chromium check of the resizable message box: a grip on the box's top edge makes it taller when
dragged up and smaller when dragged down; the arrow keys do the same and Home (or a double-click) returns to the
automatic size; the whole message area (box, tools, Send row) never takes more than 60% of the window (50% on a
phone), so Send stays on screen and part of the conversation stays visible; the chosen size survives a reload; a
reader at the newest message stays there. All requests are intercepted synthetic fixtures; no server starts."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from qa_support import confined_playwright, evidence_root

STATIC = Path(__file__).resolve().parents[1] / "static"
ORIGIN = "http://common-ground.test"
STEP = 24                      # px per arrow key press, as the page documents it
MESSAGES: list[dict[str, Any]] = [{"id": key, "channel_id": "workspace", "author_id": "scribe", "author_name": "Scribe",
             "body": f"Message {key}: " + "a line of text " * 12, "created_at": "2026-10-12T11:00:00+00:00",
             "mentions": []} for key in range(1, 41)]


class ComposerResizeBrowserTests(unittest.TestCase):
    def test_the_message_box_resizes_remembers_and_keeps_its_limits(self) -> None:
        report: dict[str, Any] = {"checks": {}, "heights": {}, "external_requests": [], "unexpected": []}
        errors: list[str] = []
        me = {"id": "curator", "name": "Curator", "kind": "human"}

        def check(name: str, value: bool) -> None:
            report["checks"][name] = bool(value)
            self.assertTrue(value, f"{name}: {report['heights']}")

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
            body = request.request.post_data_json or {}
            after = int(parse_qs(url.query).get("after", ["0"])[0])
            state = {"workspace": {"title": "Common Ground", "project_label": "Generic"}, "me": me,
                     "participants": [me, {"id": "scribe", "name": "Scribe", "kind": "agent"}],
                     "channels": [{"id": "workspace", "title": "Workspace", "kind": "shared", "members": ["scribe", "curator"],
                                   "unread": 0, "unread_mentions": 0}],
                     "decisions": [], "knowledge": [], "sources": [], "tasks": []}
            answers: dict[str, Any] = {"/api/state": state, "/api/bookmarks": {"messages": []},
                                       "/api/messages": {"messages": [m for m in MESSAGES if m["id"] > after],
                                                         "cursor": len(MESSAGES)},
                                       "/api/read": {"ok": True},
                                       "/api/personal": {"drafts": [], "outbox": [],
                                                         "preferences": {"values": {}, "version": 0}}}
            if url.path == "/api/personal/preferences":
                answers[url.path] = {"values": body.get("values", {}), "version": body.get("version", 0) + 1}
            if url.path == "/api/personal/draft":
                answers[url.path] = {"channel_id": body["channel_id"], "body": body["body"],
                                     "version": body["version"] + 1}
            if url.path not in answers:
                report["unexpected"].append(url.path)
                request.fulfill(status=400, json={"error": "Unexpected synthetic request"})
                return
            request.fulfill(json=answers[url.path])

        try:
            with confined_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                context.route("**/*", route)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(ORIGIN)
                page.locator('[data-message-id="40"]').wait_for()
                box = page.locator("#message-input")
                grip = page.locator("#composer-resize")
                listing = page.locator("#message-list")

                def height() -> float:
                    return float(box.evaluate("element => element.getBoundingClientRect().height"))

                def at_newest() -> bool:
                    return bool(listing.evaluate(
                        "list => list.scrollHeight - list.scrollTop - list.clientHeight < 4"))

                def drag(dy: float) -> None:
                    area = grip.bounding_box()
                    assert area is not None
                    x, y = area["x"] + area["width"] / 2, area["y"] + area["height"] / 2
                    page.mouse.move(x, y)
                    page.mouse.down()
                    page.mouse.move(x, y + dy / 2, steps=4)
                    page.mouse.move(x, y + dy, steps=4)
                    page.mouse.up()

                check("the_grip_is_a_labelled_focusable_separator",
                      grip.get_attribute("role") == "separator"
                      and grip.get_attribute("aria-orientation") == "horizontal"
                      and bool(grip.get_attribute("aria-label")) and grip.get_attribute("tabindex") == "0")
                listing.evaluate("list => { list.scrollTop = list.scrollHeight; }")
                automatic = report["heights"]["automatic"] = height()
                drag(-200)
                taller = report["heights"]["dragged_up_200"] = height()
                check("dragging_up_makes_the_box_taller", automatic + 180 <= taller <= automatic + 220)
                check("a_reader_at_the_newest_message_stays_there", at_newest())
                check("the_grip_reports_the_height", grip.get_attribute("aria-valuenow") == str(round(taller)))
                drag(100)
                smaller = report["heights"]["dragged_down_100"] = height()
                check("dragging_down_makes_it_smaller", taller - 120 <= smaller <= taller - 80)
                box.fill("\n".join(f"line {n}" for n in range(1, 60)))
                check("typing_keeps_the_chosen_size_and_scrolls_inside",
                      abs(height() - smaller) < 2 and box.evaluate("e => e.scrollHeight > e.clientHeight"))
                box.fill("")
                grip.focus()
                page.keyboard.press("ArrowUp")
                check("arrow_up_adds_one_step", abs(height() - (smaller + STEP)) < 2)
                page.keyboard.press("ArrowDown")
                page.keyboard.press("ArrowDown")
                check("arrow_down_removes_one_step", abs(height() - (smaller - STEP)) < 2)
                kept = report["heights"]["before_reload"] = height()
                page.reload()
                page.locator('[data-message-id="40"]').wait_for()
                report["heights"]["after_reload"] = height()
                check("the_chosen_size_survives_a_reload", abs(height() - kept) < 2)
                drag(-2000)
                report["heights"]["dragged_up_2000"] = height()
                area_height = report["heights"]["message_area_at_max"] = page.locator("#composer-area").evaluate(
                    "area => area.getBoundingClientRect().height")
                check("the_message_area_takes_at_most_60_percent", area_height <= 0.6 * 900 + 1)
                check("send_stays_on_screen", page.locator("#send-message").evaluate(
                    "button => button.getBoundingClientRect().bottom <= innerHeight"))
                report["heights"]["conversation_visible_at_max"] = listing.evaluate("list => list.clientHeight")
                check("part_of_the_conversation_stays_visible", report["heights"]["conversation_visible_at_max"] >= 100)
                page.screenshot(path=str(evidence_root() / "composer_resize_desktop.png"))
                grip.focus()
                page.keyboard.press("Home")
                check("home_returns_to_the_automatic_size", abs(height() - automatic) < 2)
                check("the_automatic_size_is_not_remembered",
                      page.evaluate("() => localStorage.getItem('common-ground.composer-height')") is None)
                drag(-150)
                grip.dblclick()
                check("a_double_click_also_resets", abs(height() - automatic) < 2)
                page.set_viewport_size({"width": 390, "height": 844})
                page.wait_for_timeout(300)
                drag(-2000)
                report["heights"]["phone_dragged_up_2000"] = height()
                check("on_a_phone_the_message_area_takes_at_most_half", page.locator("#composer-area").evaluate(
                    "area => area.getBoundingClientRect().height") <= 0.5 * 844 + 1)
                check("on_a_phone_send_stays_on_screen", page.locator("#send-message").evaluate(
                    "button => button.getBoundingClientRect().bottom <= innerHeight"))
                check("the_phone_page_has_no_horizontal_overflow",
                      page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"))
                page.screenshot(path=str(evidence_root() / "composer_resize_phone.png"))
                browser.close()
        finally:   # the report is evidence on a failure too
            report["page_errors"] = errors
            (evidence_root() / "composer_resize.json").write_text(json.dumps(report, indent=1) + "\n")
        self.assertEqual(errors, [])
        self.assertEqual(report["external_requests"], [])
        self.assertEqual(report["unexpected"], [])


if __name__ == "__main__":
    unittest.main()
