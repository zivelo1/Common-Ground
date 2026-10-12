"""Offline Chromium check of message editing (unit 13): the author's still-editable message shows Edit, another
participant's does not; the dialog sends body, version and a fresh edit_id; the edited text and an "Edited" mark appear;
a refused edit keeps the typed text in the dialog. All requests are intercepted fixtures with the live roster's names;
the editing rules themselves are proved against the real server in test_message_edit.py."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from qa_support import confined_playwright, evidence_root

STATIC = Path(__file__).resolve().parents[1] / "static"
ORIGIN = "http://common-ground.test"


class MessageEditBrowserTests(unittest.TestCase):
    def test_the_author_edits_an_unread_message_and_a_refusal_keeps_the_text(self) -> None:
        report: dict[str, Any] = {"checks": {}, "external_requests": [], "unexpected": [], "edits": []}
        errors: list[str] = []
        server: dict[str, Any] = {"refuse": False, "messages": [
            {"id": 7, "channel_id": "workspace", "author_id": "curator", "author_name": "Curator", "body": "Draft: meet at 10",
             "created_at": "2026-10-10T16:00:00+00:00", "version": 1, "edited_at": None, "can_edit": True},
            {"id": 8, "channel_id": "workspace", "author_id": "codex", "author_name": "Codex", "body": "Noted",
             "created_at": "2026-10-10T16:01:00+00:00", "version": 1, "edited_at": None, "can_edit": False}]}

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
            if url.path.startswith("/static/logos/"):
                request.fulfill(path=str(STATIC / "logos" / Path(url.path).name), content_type="image/svg+xml")
                return
            if url.path.startswith("/static/") and Path(url.path).name in {"app.js", "style.css", "common-ground.svg"}:
                request.fulfill(path=str(STATIC / Path(url.path).name))
                return
            if url.path == "/api/messages/7/edit":
                body = request.request.post_data_json
                report["edits"].append(body)
                if server["refuse"]:
                    request.fulfill(status=409, json={"error": "Message is already retrieved, acknowledged, or predates editing support"})
                    return
                edited = {**server["messages"][0], "body": body["body"], "version": 2,
                          "edited_at": "2026-10-10T16:05:00+00:00"}
                server["messages"][0] = edited
                request.fulfill(json={"message": edited, "applied_version": 2, "replayed": False})
                return
            people: list[dict[str, Any]] = [{"id": "curator", "name": "Curator", "kind": "human", "logo": None},
                                            {"id": "codex", "name": "Codex", "kind": "agent", "logo": "codex-color"}]
            state = {"workspace": {"title": "Common Ground · Aurora Museum", "project_label": "Exhibition planning"},
                     "me": {"id": "curator", "name": "Curator", "kind": "human"}, "participants": people,
                     "channels": [{"id": "workspace", "title": "Workspace", "kind": "shared", "members": ["codex", "curator"], "unread": 0}],
                     "decisions": [], "knowledge": [], "sources": [], "tasks": []}
            answers: dict[str, Any] = {"/api/state": state, "/api/bookmarks": {"messages": []}, "/api/read": {"ok": True},
                                       "/api/messages": {"messages": server["messages"], "cursor": 8,
                                                         "message_updates": [], "change_cursor": 0},
                                       "/api/personal": {"drafts": [], "outbox": [], "preferences": {"values": {}, "version": 0}}}
            if url.path == "/api/personal/preferences":
                sent = request.request.post_data_json or {}
                answers[url.path] = {"values": sent.get("values", {}), "version": sent.get("version", 0) + 1}
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
                own = page.locator('.message[data-message-id="7"]')
                own.wait_for(state="visible")
                check("only_the_authors_editable_message_offers_edit",
                      own.locator(".edit-message").count() == 1
                      and page.locator('.message[data-message-id="8"] .edit-message').count() == 0)
                own.locator(".edit-message").click()
                editor = page.get_by_label("Edited message")
                editor.fill("Final: meet at 11")
                page.get_by_role("button", name="Save edit").click()
                own.locator(".message-bubble", has_text="Final: meet at 11").wait_for(state="visible")
                edit = report["edits"][0]
                check("the_edit_sends_body_version_and_a_fresh_edit_id",
                      (edit["body"], edit["version"]) == ("Final: meet at 11", 1) and isinstance(edit["edit_id"], str)
                      and len(edit["edit_id"]) >= 8)
                check("the_edited_message_is_marked", own.locator(".edited-marker").inner_text() == "Edited")
                page.screenshot(path=str(evidence_root() / "message_edit.png"))
                server["refuse"] = True                          # someone retrieved it meanwhile
                own.locator(".edit-message").click()
                page.get_by_label("Edited message").fill("Final: meet at 12")
                page.get_by_role("button", name="Save edit").click()
                page.locator(".form-error", has_text="already retrieved").wait_for(state="visible")
                check("a_refused_edit_keeps_the_typed_text", page.get_by_label("Edited message").input_value() == "Final: meet at 12")
                browser.close()
        finally:   # the report is evidence on a failure too
            report["page_errors"] = errors
            (evidence_root() / "message_edit.json").write_text(json.dumps(report, indent=1) + "\n")
        self.assertEqual(errors, [])
        self.assertEqual(report["external_requests"], [])
        self.assertEqual(report["unexpected"], [])


if __name__ == "__main__":
    unittest.main()
