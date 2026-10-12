"""Offline Chromium check of text formatting in the human chat (Collaboration: bold, italic, underline and
similar). The composer's buttons and shortcuts wrap the selection in plain-text markers; the conversation draws them.
All requests are intercepted synthetic fixtures; no server starts."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from qa_support import confined_playwright, evidence_root

STATIC = Path(__file__).resolve().parents[1] / "static"
ORIGIN = "http://common-ground.test"
COMPOSED = "Please **_++check++_** the ~~old~~ new plan"   # what the composer steps below must produce
BODIES = {
    1: "**bold** _italic_ ++under++ ~~strike~~",
    2: COMPOSED,
    3: "C++ and C++ stay plain; so do i++ + j++ and 1 ~ 2 ~~ 3",
    4: "`++code++ ~~code~~` stays code",
    5: "++unclosed and ~~unclosed",
    6: r"escaped: \~~kept\~~ and \++kept\++",
    7: r"~~an escaped pair never closes\~~",
    8: r"*not italic\* and **not bold\*\* and _not italic\_",   # literal bold needs both stars escaped
    9: r"*italic ends after an escaped backslash\\*",
}


class TextFormattingBrowserTests(unittest.TestCase):
    def test_composer_formats_and_the_conversation_draws_it(self) -> None:
        report: dict[str, Any] = {"checks": {}, "external_requests": [], "unexpected": []}
        errors: list[str] = []
        drafts: list[str] = []
        me = {"id": "curator", "name": "Curator", "kind": "human"}
        messages: list[dict[str, Any]] = [{"id": key, "channel_id": "workspace", "author_id": "atlas", "author_name": "Atlas", "body": body,
                     "created_at": "2026-10-11T16:00:00+00:00"} for key, body in BODIES.items()]

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
            body = request.request.post_data_json or {}
            after = int(parse_qs(url.query).get("after", ["0"])[0])
            state = {"workspace": {"title": "Common Ground", "project_label": "Aurora Museum"}, "me": me,
                     "participants": [me, {"id": "atlas", "name": "Atlas", "kind": "agent"}],
                     "channels": [{"id": "workspace", "title": "Workspace", "kind": "shared",
                                   "members": ["curator", "atlas"], "unread": 0}],
                     "decisions": [], "knowledge": [], "sources": [], "tasks": []}
            answers: dict[str, Any] = {"/api/state": state, "/api/bookmarks": {"messages": []},
                                       "/api/messages": {"messages": [m for m in messages if m["id"] > after],
                                                         "cursor": len(messages)},
                                       "/api/read": {"ok": True},
                                       "/api/personal": {"drafts": [], "outbox": [],
                                                         "preferences": {"values": {}, "version": 0}}}
            if url.path == "/api/personal/preferences":
                answers[url.path] = {"values": body.get("values", {}), "version": body.get("version", 0) + 1}
            if url.path == "/api/personal/draft":
                drafts.append(body["body"])
                answers[url.path] = {"channel_id": body["channel_id"], "body": body["body"],
                                     "version": body["version"] + 1}
            if url.path not in answers:
                report["unexpected"].append(url.path)
                request.fulfill(status=400, json={"error": "Unexpected synthetic request"})
                return
            request.fulfill(json=answers[url.path])

        def bubble(key: int) -> Any:
            return page.locator(f'[data-message-id="{key}"] .message-bubble')

        try:
            with confined_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                context.route("**/*", route)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(ORIGIN)
                page.locator('[data-message-id="9"]').wait_for()
                # the conversation draws every style, and leaves ordinary text and code alone
                first = bubble(1)
                styles = "e => ['strong', 'em', 'u', 's'].map(tag => e.querySelector(tag)?.textContent ?? null)"
                check("draws_bold_italic_underline_strikethrough",
                      first.evaluate(styles) == ["bold", "italic", "under", "strike"])
                check("draws_nested_styles", bubble(2).evaluate(
                    "e => [e.querySelector('strong em u')?.textContent, e.querySelector('s')?.textContent]")
                      == ["check", "old"])
                check("plain_plus_and_tilde_text_stays_plain",
                      bubble(3).locator("u, s").count() == 0
                      and "C++ and C++ stay plain; so do i++ + j++ and 1 ~ 2 ~~ 3" in bubble(3).inner_text())
                check("code_keeps_its_markers", bubble(4).locator("code u, code s").count() == 0
                      and bubble(4).locator("code").inner_text() == "++code++ ~~code~~")
                check("unclosed_markers_stay_text", bubble(5).locator("u, s").count() == 0
                      and "++unclosed and ~~unclosed" in bubble(5).inner_text())
                check("escaped_markers_stay_text", bubble(6).locator("u, s").count() == 0
                      and "escaped: ~~kept~~ and ++kept++" in bubble(6).inner_text())
                check("an_escaped_pair_never_closes", bubble(7).locator("s").count() == 0
                      and "~~an escaped pair never closes~~" in bubble(7).inner_text())
                check("escaped_star_and_underscore_never_close", bubble(8).locator("em, strong").count() == 0
                      and "*not italic* and **not bold** and _not italic_" in bubble(8).inner_text())
                check("an_escaped_backslash_lets_the_marker_close",
                      bubble(9).evaluate("e => e.querySelector('em')?.textContent ?? null")
                      == "italic ends after an escaped backslash\\")
                # the composer: buttons and shortcuts wrap the selection; the cursor keeps the same words selected
                box = page.locator("#message-input")
                box.fill("Please check the old new plan")
                box.evaluate("e => e.setSelectionRange(7, 12)")              # "check"
                page.get_by_role("button", name="Bold", exact=True).click()
                check("bold_button_wraps_selection", box.input_value() == "Please **check** the old new plan")
                check("selection_stays_on_the_words",
                      box.evaluate("e => e.value.slice(e.selectionStart, e.selectionEnd)") == "check")
                page.get_by_role("button", name="Italic", exact=True).click()
                box.press("Control+u")
                check("italic_button_and_underline_shortcut_nest",
                      box.input_value() == "Please **_++check++_** the old new plan")
                start = box.input_value().index("old")
                box.evaluate(f"e => e.setSelectionRange({start - 1}, {start + 4})")   # " old " with its spaces
                page.get_by_role("button", name="Strikethrough", exact=True).click()
                check("spaces_stay_outside_the_markers", box.input_value() == COMPOSED)
                check("focus_returns_to_composer", page.evaluate("() => document.activeElement.id") == "message-input")
                page.wait_for_function("() => document.querySelector('#personal-save-status')?.dataset.saved === 'true'")
                check("formatting_uses_the_existing_draft_save", drafts[-1] == COMPOSED)
                box.fill("")
                page.get_by_role("button", name="Bold", exact=True).click()
                box.type("x")
                check("empty_selection_puts_the_cursor_inside", box.input_value() == "**x**")
                box.fill("x" * 15997)
                box.evaluate("e => e.setSelectionRange(0, 1)")
                page.get_by_role("button", name="Bold", exact=True).click()
                check("formatting_cannot_exceed_message_limit", box.input_value() == "x" * 15997)
                page.locator("#dismiss-notice").click()
                box.fill(COMPOSED)
                page.screenshot(path=str(evidence_root() / "text_formatting_desktop.png"))
                page.set_viewport_size({"width": 390, "height": 844})
                page.wait_for_timeout(300)
                check("phone_has_no_horizontal_overflow",
                      page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"))
                page.screenshot(path=str(evidence_root() / "text_formatting_phone.png"))
                browser.close()
        finally:   # the report is evidence on a failure too
            report["page_errors"] = errors
            report["drafts_saved"] = [draft[:120] for draft in drafts[-3:]]   # the long-limit draft is 15,997 characters
            (evidence_root() / "text_formatting.json").write_text(json.dumps(report, indent=1) + "\n")
        self.assertEqual(errors, [])
        self.assertEqual(report["external_requests"], [])
        self.assertEqual(report["unexpected"], [])


if __name__ == "__main__":
    unittest.main()
