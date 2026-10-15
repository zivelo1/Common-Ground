"""Offline Chromium check of read receipts (Collaboration). Under every stored message, yours or another member's:
an orange dot while no member other than its author has read it, green once one has, with the count and exact names
("1/2 read: Codex; Atlas pending"); a red dot while the server has not acknowledged a send. Each message's Receipts dialog lists the other
current members with "Received <time>" or "Not received yet" and "Read" or "Not read yet"; an older message says its
receive time is unknown; a failed load says so. All requests are intercepted synthetic fixtures; no server starts."""
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
RECEIPTS = {
    1: {"message_id": 1, "received_known": True,
        "recipients": [{"id": "codex", "name": "Codex", "kind": "agent", "received_at": "2026-10-11T13:05:00+00:00",
                        "read": False},
                       {"id": "atlas", "name": "Atlas", "kind": "agent", "received_at": None, "read": False}]},
    2: {"message_id": 2, "received_known": False,
        "recipients": [{"id": "codex", "name": "Codex", "kind": "agent", "received_at": None, "read": True}]},
}


class MessageReceiptsBrowserTests(unittest.TestCase):
    def test_the_receipts_dialog_says_who_received_and_who_read(self) -> None:
        report: dict[str, Any] = {"checks": {}, "external_requests": [], "unexpected": [], "dialogs": []}
        errors: list[str] = []
        receipt_paths: list[str] = []
        me = {"id": "curator", "name": "Curator", "kind": "human"}
        messages: list[dict[str, Any]] = [
            {"id": key, "channel_id": "workspace", "author_id": "curator", "author_name": "Curator", "body": body,
             "created_at": "2026-10-11T13:00:00+00:00", "version": 1, "edited_at": None, "can_edit": False}
            for key, body in ((1, "Please check the plan"), (2, "An older message"), (3, "Receipts fail here"))]
        messages.append({"id": 4, "channel_id": "workspace", "author_id": "codex", "author_name": "Codex", "body": "Noted",
                         "created_at": "2026-10-11T13:01:00+00:00", "version": 1, "edited_at": None, "can_edit": False})
        markers = {"codex": 1}

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
            receipt = re.fullmatch(r"/api/messages/([0-9]+)/receipts", url.path)
            if receipt:
                receipt_paths.append(url.path)
                ident = int(receipt.group(1))
                if ident in RECEIPTS:
                    request.fulfill(json=RECEIPTS[ident])
                else:
                    request.fulfill(status=503, json={"error": "Synthetic receipts unavailable"})
                return
            body = request.request.post_data_json or {}
            after = int(parse_qs(url.query).get("after", ["0"])[0])
            state = {"workspace": {"title": "Common Ground", "project_label": "Aurora Museum"}, "me": me,
                     "participants": [me, {"id": "codex", "name": "Codex", "kind": "agent"},
                                      {"id": "atlas", "name": "Atlas", "kind": "agent"},
                                      {"id": "retired", "name": "Retired", "kind": "agent", "active": False}],
                     "channels": [{"id": "workspace", "title": "Workspace", "kind": "shared",
                                   "members": ["codex", "atlas", "curator", "retired"], "unread": 0, "read_markers": dict(markers)}],
                     "decisions": [], "knowledge": [], "sources": [], "tasks": []}
            answers: dict[str, Any] = {"/api/state": state, "/api/bookmarks": {"messages": []},
                                       "/api/messages": {"messages": [m for m in messages if m["id"] > after],
                                                         "cursor": len(messages)},
                                       "/api/read": {"ok": True},
                                       "/api/personal": {"drafts": [], "outbox": [],
                                                         "preferences": {"values": {}, "version": 0}}}
            if url.path == "/api/personal/preferences":
                answers[url.path] = {"values": body.get("values", {}), "version": body.get("version", 0) + 1}
            if url.path not in answers:
                report["unexpected"].append(url.path)
                request.fulfill(status=400, json={"error": "Unexpected synthetic request"})
                return
            request.fulfill(json=answers[url.path])

        def open_receipts(page: Any, ident: int) -> Any:
            page.locator(f'[data-message-id="{ident}"]').get_by_role("button", name="Receipts", exact=True).click()
            dialog = page.locator("dialog[open]")
            dialog.locator(".receipt-list, .field-error, .field-hint:not(:has-text('Loading'))").first.wait_for()
            report["dialogs"].append(dialog.inner_text())
            return dialog

        try:
            with confined_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1280, "height": 900}, timezone_id="UTC")
                context.route("**/*", route)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(ORIGIN)
                page.locator('[data-message-id="4"]').wait_for()

                def summary(ident: int) -> list[str]:
                    line = page.locator(f'[data-message-id="{ident}"] .read-summary')
                    return [line.inner_text(), line.get_attribute("class") or ""] if line.count() else []

                check("one_reader_is_green_with_names", summary(1) == ["1/2 read: Codex; Atlas pending", "read-summary read-some"])
                check("no_reader_is_orange_with_names", summary(2) == ["0/2 read: Codex, Atlas pending", "read-summary read-none"])
                check("another_members_message_lists_everyone_but_its_author",
                      summary(4) == ["0/2 read: Atlas, Curator pending", "read-summary read-none"])
                align = "e => getComputedStyle(e).justifyContent"
                check("each_summary_sits_on_its_message_side",
                      page.locator('[data-message-id="4"] .read-summary').evaluate(align) == "flex-start"
                      and page.locator('[data-message-id="1"] .read-summary').evaluate(align) == "flex-end")
                markers.update(codex=4, atlas=2, curator=4)       # the next poll of the state carries the new markers
                page.wait_for_function("() => document.querySelector('[data-message-id=\"2\"] .read-summary')?.textContent === '2/2 read: Codex, Atlas'")
                check("the_summary_follows_the_polled_state",
                      summary(2) == ["2/2 read: Codex, Atlas", "read-summary read-some"]
                      and summary(3) == ["1/2 read: Codex; Atlas pending", "read-summary read-some"]
                      and summary(4) == ["1/2 read: Curator; Atlas pending", "read-summary read-some"])
                page.evaluate("() => { app.outbox.push({channel_id: 'workspace', author_id: 'curator', author_name: 'Curator', body: 'Unconfirmed', client_id: 'qa-red', created_at: '2026-10-11T13:02:00+00:00', status: 'failed', error: 'Synthetic', durable: true}); renderMessages(); }")
                check("an_unacknowledged_send_has_a_red_dot",
                      page.locator(".delivery-status .receipt-dot.receipt-unacknowledged").count() == 1
                      and page.locator(".delivery-status .receipt-unacknowledged").evaluate("e => getComputedStyle(e).backgroundColor")
                      != page.locator('[data-message-id="1"] .receipt-dot').evaluate("e => getComputedStyle(e).backgroundColor"))
                page.screenshot(path=str(evidence_root() / "receipts_summary_desktop.png"))
                dialog = open_receipts(page, 1)
                rows = dialog.locator(".receipt-item").all_inner_texts()
                check("lists_each_other_member_with_both_states",
                      [row.split("\n") for row in rows]
                      == [["Codex", "Received 11.10.2026 - 13:05", "Not read yet"], ["Atlas", "Not received yet", "Not read yet"]])
                check("explains_that_neither_proves_attention", "Neither proves attention." in dialog.inner_text())
                page.screenshot(path=str(evidence_root() / "receipts_desktop.png"))
                dialog.get_by_role("button", name="Close dialog", exact=True).click()
                dialog = open_receipts(page, 2)
                check("an_older_message_says_received_unknown",
                      dialog.locator(".receipt-item").inner_text().split("\n")
                      == ["Codex", "Received: unknown (sent before receipts were kept)", "Read"])
                dialog.get_by_role("button", name="Close dialog", exact=True).click()
                dialog = open_receipts(page, 3)
                check("a_failed_load_says_so", "Receipts could not be loaded. Synthetic receipts unavailable"
                      in dialog.locator(".field-error").inner_text())
                dialog.get_by_role("button", name="Close dialog", exact=True).click()
                check("one_receipts_request_per_opening",
                      receipt_paths == ["/api/messages/1/receipts", "/api/messages/2/receipts", "/api/messages/3/receipts"])
                page.set_viewport_size({"width": 390, "height": 844})
                page.wait_for_timeout(300)
                dialog = open_receipts(page, 1)
                check("phone_dialog_fits", dialog.evaluate("e => e.getBoundingClientRect().right <= innerWidth")
                      and page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"))
                page.screenshot(path=str(evidence_root() / "receipts_phone.png"))
                browser.close()
        finally:   # the report is evidence on a failure too
            report["page_errors"] = errors
            (evidence_root() / "message_receipts.json").write_text(json.dumps(report, indent=1) + "\n")
        self.assertEqual(errors, [])
        self.assertEqual(report["external_requests"], [])
        self.assertEqual(report["unexpected"], [])


if __name__ == "__main__":
    unittest.main()
