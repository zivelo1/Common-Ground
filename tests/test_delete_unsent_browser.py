"""Offline Chromium check: a saved unsent message can be deleted from the retry list (task "delete an unsent message").
Delete sits beside Retry and Keep as draft, asks first, removes only the queued copy and keeps it on a refusal. All
requests are intercepted synthetic fixtures; no server starts and nothing leaves the page."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from qa_support import confined_playwright, evidence_root

STATIC = Path(__file__).resolve().parents[1] / "static"
ORIGIN = "http://common-ground.test"
QUEUED = {"client_id": "synthetic-unsent", "channel_id": "workspace", "body": "Synthetic unsent text",
          "created_at": "2026-10-10T08:00:00+00:00"}


class DeleteUnsentBrowserTests(unittest.TestCase):
    def test_delete_asks_then_removes_only_the_queued_copy_and_keeps_it_on_a_refusal(self) -> None:
        report: dict[str, Any] = {"checks": {}, "external_requests": [], "unexpected": [], "discards": []}
        errors: list[str] = []
        answer = {"status": 409, "json": {"error": "Message already sent or removed; reload"}}

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
            if url.path == "/api/personal/discard":
                report["discards"].append(request.request.post_data_json)
                request.fulfill(status=answer["status"], json=answer["json"])
                return
            state = {"workspace": {"title": "Synthetic QA", "project_label": "Offline"},
                     "me": {"id": "human", "name": "Synthetic human", "kind": "human"},
                     "participants": [{"id": "human", "name": "Synthetic human", "kind": "human"}],
                     "channels": [{"id": "workspace", "title": "Workspace", "kind": "shared", "members": ["human"],
                                   "unread": 0}],
                     "decisions": [], "knowledge": [], "sources": [], "tasks": []}
            answers: dict[str, Any] = {"/api/state": state, "/api/bookmarks": {"messages": []},
                                       "/api/messages": {"messages": [], "cursor": 0}, "/api/read": {"ok": True},
                                       "/api/personal": {"drafts": [], "outbox": [QUEUED],
                                                         "preferences": {"values": {}, "version": 0}}}
            if url.path == "/api/personal/preferences":
                body = request.request.post_data_json or {}
                answers[url.path] = {"values": body.get("values", {}), "version": body.get("version", 0) + 1}
            if url.path not in answers:
                report["unexpected"].append(url.path)
                request.fulfill(status=400, json={"error": "Unexpected synthetic request"})
                return
            request.fulfill(json=answers[url.path])

        with confined_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.route("**/*", route)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(ORIGIN)
            page.locator("#app-shell").wait_for(state="visible")
            message = page.locator(".message.failed", has_text="Synthetic unsent text")
            message.wait_for(state="visible")
            actions = [name.strip() for name in message.locator(".delivery-status button").all_inner_texts()]
            check("delete_beside_retry_and_keep_as_draft", actions == ["Retry", "Keep as draft", "Delete"])
            message.get_by_role("button", name="Delete").click()
            dialog = page.locator("dialog[open]")
            check("delete_asks_first", "Delete unsent message?" in dialog.inner_text())
            dialog.get_by_role("button", name="Keep message").click()
            check("cancel_sends_nothing_and_keeps_it", report["discards"] == [] and message.is_visible())
            message.get_by_role("button", name="Delete").click()
            page.locator("dialog[open]").get_by_role("button", name="Delete message").click()
            page.wait_for_function("() => document.querySelector('#notice') && document.querySelector('#notice').textContent.includes('already sent')")
            check("a_refused_delete_keeps_the_message", report["discards"] == [{"client_id": "synthetic-unsent"}]
                  and message.is_visible())
            answer.update(status=200, json={"client_id": "synthetic-unsent", "discarded": True})
            message.get_by_role("button", name="Delete").click()
            page.locator("dialog[open]").get_by_role("button", name="Delete message").click()
            message.wait_for(state="detached")
            check("a_confirmed_delete_removes_only_the_queued_copy", len(report["discards"]) == 2)
            page.screenshot(path=str(evidence_root() / "delete_unsent.png"))
            browser.close()
        report["page_errors"] = errors
        (evidence_root() / "delete_unsent.json").write_text(json.dumps(report, indent=1) + "\n")
        self.assertEqual(errors, [])
        self.assertEqual(report["external_requests"], [])
        self.assertEqual(report["unexpected"], [])


if __name__ == "__main__":
    unittest.main()
