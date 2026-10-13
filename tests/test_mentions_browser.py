"""Offline Chromium check of @mentions: the conversation draws a server-listed @{id} marker as a
chip with the participant's current name and highlights a mention of the reader; the conversation list shows the
reader's unread mentions; in the composer "@" offers the conversation's other members, and sending turns a chosen or
typed @Name into its @{id} marker. All requests are intercepted synthetic fixtures; no server starts."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from qa_support import confined_playwright, evidence_root

STATIC = Path(__file__).resolve().parents[1] / "static"
ORIGIN = "http://common-ground.test"
MEMBERS = ["atlas", "codex", "curator", "scribe"]
MARKER = re.compile(r"@\{([a-zA-Z0-9][a-zA-Z0-9_-]{0,63})\}")
MESSAGES = [
    (1, "scribe", "@{curator} please check the plan"),
    (2, "scribe", "@{atlas} and @{nobody} and @{codex}"),
    (3, "scribe", "`@{atlas}` in code stays code"),
]


def server_mentions(body: str) -> list[str]:
    return list(dict.fromkeys(ident for ident in MARKER.findall(body) if ident in MEMBERS))


class MentionBrowserTests(unittest.TestCase):
    def test_mentions_draw_notify_and_compose(self) -> None:
        report: dict[str, Any] = {"checks": {}, "external_requests": [], "unexpected": []}
        errors: list[str] = []
        sent: list[str] = []
        me = {"id": "curator", "name": "Curator", "kind": "human"}
        participants = [me, {"id": "scribe", "name": "Scribe", "kind": "agent"},
                        {"id": "atlas", "name": "Atlas", "kind": "agent"},
                        {"id": "codex", "name": "Codex Reviewer", "kind": "agent"}]
        messages: list[dict[str, Any]] = [{"id": key, "channel_id": "workspace", "author_id": author, "author_name": "Scribe", "body": body,
                     "created_at": "2026-10-12T11:00:00+00:00", "mentions": server_mentions(body)}
                    for key, author, body in MESSAGES]

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
            state = {"workspace": {"title": "Common Ground", "project_label": "Generic"}, "me": me,
                     "participants": participants,
                     "channels": [{"id": "workspace", "title": "Workspace", "kind": "shared", "members": MEMBERS,
                                   "unread": 3, "unread_mentions": 1}],
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
                answers[url.path] = {"channel_id": body["channel_id"], "body": body["body"],
                                     "version": body["version"] + 1}
            if url.path == "/api/personal/enqueue":
                sent.append(body["body"])
                saved = {"id": 10 + len(sent), "channel_id": "workspace", "author_id": "curator", "author_name": "Curator",
                         "body": body["body"], "created_at": "2026-10-12T11:05:00+00:00",
                         "mentions": server_mentions(body["body"])}
                answers[url.path] = {"delivered": saved, "draft": {"channel_id": "workspace", "body": "", "version": 9}}
            if url.path not in answers:
                report["unexpected"].append(url.path)
                request.fulfill(status=400, json={"error": "Unexpected synthetic request"})
                return
            request.fulfill(json=answers[url.path])

        def message(key: int) -> Any:
            return page.locator(f'[data-message-id="{key}"]')

        try:
            with confined_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                context.route("**/*", route)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(ORIGIN)
                message(3).wait_for()
                # drawing: a chip with the current name; the reader's own mention is highlighted
                check("own_mention_chip_uses_the_current_name",
                      message(1).locator(".mention.mention-me").inner_text() == "@Curator")
                check("a_message_mentioning_the_reader_is_highlighted",
                      "mentions-me" in (message(1).get_attribute("class") or "")
                      and "mentions-me" not in (message(2).get_attribute("class") or ""))
                check("member_chips_and_a_non_member_stays_text",
                      message(2).locator(".mention").all_inner_texts() == ["@Atlas", "@Codex Reviewer"]
                      and "@{nobody}" in message(2).inner_text())
                check("code_keeps_the_marker", message(3).locator(".mention").count() == 0
                      and message(3).locator("code").inner_text() == "@{atlas}")
                # the conversation list: the unread-mention badge
                badge = page.locator(".channel-button .mention-badge")
                check("unread_mention_badge", badge.inner_text() == "@1"
                      and badge.get_attribute("aria-label") == "1 unread mention of you")
                # composing: "@" offers the other members, filtered as you type; Enter chooses
                box = page.locator("#message-input")
                box.fill("")
                box.type("Hi @")
                options = page.locator("#mention-picker [role=option]")
                check("picker_lists_the_other_members", options.all_inner_texts() == ["Atlas", "Codex Reviewer", "Scribe"])
                box.type("at")
                check("picker_filters_as_you_type", options.all_inner_texts() == ["Atlas"])
                box.press("Enter")
                check("enter_inserts_the_name", box.input_value() == "Hi @Atlas ")
                check("picker_closes_after_choosing", page.locator("#mention-picker").is_hidden())
                box.type("and @co")
                box.press("Escape")
                check("escape_closes_without_inserting",
                      page.locator("#mention-picker").is_hidden() and box.input_value() == "Hi @Atlas and @co")
                box.fill("Hi @Atlas and @Codex Reviewer, mail a@atlas.example, not @Atlased")
                page.screenshot(path=str(evidence_root() / "mentions_desktop.png"))
                box.press("Control+Enter")
                message(11).wait_for()
                check("sending_turns_member_names_into_markers",
                      sent == ["Hi @{atlas} and @{codex}, mail a@atlas.example, not @Atlased"])
                check("the_sent_message_draws_its_chips",
                      message(11).locator(".mention").all_inner_texts() == ["@Atlas", "@Codex Reviewer"])
                check("the_composer_is_cleared", box.input_value() == "")
                page.set_viewport_size({"width": 390, "height": 844})
                page.wait_for_timeout(300)
                box.type("@")
                check("phone_picker_fits_without_horizontal_overflow",
                      page.locator("#mention-picker").is_visible()
                      and page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"))
                page.screenshot(path=str(evidence_root() / "mentions_phone.png"))
                browser.close()
        finally:   # the report is evidence on a failure too
            report["page_errors"] = errors
            report["sent"] = sent
            (evidence_root() / "mentions.json").write_text(json.dumps(report, indent=1) + "\n")
        self.assertEqual(errors, [])
        self.assertEqual(report["external_requests"], [])
        self.assertEqual(report["unexpected"], [])


if __name__ == "__main__":
    unittest.main()
