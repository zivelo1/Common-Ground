"""Offline Chromium check of removing and restoring people and agents: a human sees Remove
beside everyone but themselves and a Removed list with Restore; both open an in-page form that sends the name the human
saw and a reason; a removed member is offered neither for a new conversation nor for an @mention; an agent sees no
Remove or Restore. All requests are intercepted synthetic fixtures; no server starts and nothing leaves the page."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from qa_support import confined_playwright, evidence_root

STATIC = Path(__file__).resolve().parents[1] / "static"
ORIGIN = "http://common-ground.test"


class ParticipantRemovalBrowserTests(unittest.TestCase):
    def test_a_human_removes_and_restores_and_removed_members_are_not_offered(self) -> None:
        self.check_removal_flow()

    def test_late_dialog_close_does_not_interrupt_composer_mentions(self) -> None:
        self.check_removal_flow(delay_close=True)

    def check_removal_flow(self, delay_close: bool = False) -> None:
        report: dict[str, Any] = {"checks": {}, "external_requests": [], "unexpected": [], "requests": []}
        errors: list[str] = []
        people = {"human": {"id": "human", "name": "Synthetic human", "kind": "human", "active": True},
                  "a": {"id": "a", "name": "Agent A", "kind": "agent", "active": True},
                  "b": {"id": "b", "name": "Agent B", "kind": "agent", "active": False}}
        viewer = {"id": "human"}

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
            if url.path in ("/api/participants/remove", "/api/participants/restore"):
                sent = request.request.post_data_json
                report["requests"].append([url.path, sent])
                people[sent["id"]]["active"] = url.path.endswith("restore")
                request.fulfill(json=dict(people[sent["id"]]))
                return
            me = people[viewer["id"]]
            state = {"workspace": {"title": "Synthetic QA", "project_label": "Offline"}, "me": me,
                     "participants": [dict(person) for person in people.values()],
                     "channels": [{"id": "workspace", "title": "Workspace", "kind": "shared", "members": ["a", "b", "human"],
                                   "unread": 0, "unread_mentions": 0}],
                     "decisions": [], "knowledge": [], "sources": [], "tasks": []}
            answers: dict[str, Any] = {"/api/state": state, "/api/bookmarks": {"messages": []},
                                       "/api/messages": {"messages": [], "cursor": 0}, "/api/read": {"ok": True},
                                       "/api/personal": {"drafts": [], "outbox": [],
                                                         "preferences": {"values": {}, "version": 0}}}
            body = request.request.post_data_json or {}
            if url.path == "/api/personal/preferences":
                answers[url.path] = {"values": body.get("values", {}), "version": body.get("version", 0) + 1}
            if url.path == "/api/personal/draft":
                answers[url.path] = {"channel_id": body["channel_id"], "body": body["body"], "version": body["version"] + 1}
            if url.path == "/api/brief" and viewer["id"] == "a":
                answers[url.path] = {"workspace": state["workspace"], "me": me, "generated_at": "2026-10-12T16:00:00+00:00",
                                     "scope": {"channel_ids": ["workspace"]}, "context": [], "knowledge": [], "lessons": [],
                                     "disputed": [], "stale": [], "decisions": [], "tasks": [], "sources": [],
                                     "source_context": [], "source_scan": {"available_count": 0, "configured_count": 0},
                                     "truncation": {}, "limitations": [], "knowledge_check": None}
            if url.path not in answers:
                report["unexpected"].append(url.path)
                request.fulfill(status=400, json={"error": "Unexpected synthetic request"})
                return
            request.fulfill(json=answers[url.path])

        def buttons() -> list[str | None]:
            return [control.get_attribute("aria-label") for control in page.locator("#participant-list button").all()]

        def submit(label: str, reason: str) -> None:
            page.locator("#participant-list").get_by_role("button", name=label).click()
            dialog = page.locator("dialog[open]")
            dialog.get_by_label("Reason").fill(reason)
            dialog.get_by_role("button", name=label.split()[0], exact=True).click()
            dialog.wait_for(state="hidden")

        try:
            with confined_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                context.route("**/*", route)
                page = context.new_page()
                if delay_close:
                    # Deliver the first real close handler after the next composer input.
                    # This models browser event scheduling without replacing app behavior.
                    page.add_init_script("""
                        const realAdd = HTMLDialogElement.prototype.addEventListener;
                        let delayed = false;
                        window.delayedCloseDelivered = false;
                        HTMLDialogElement.prototype.addEventListener = function(type, handler, options) {
                            if (type !== 'close' || delayed) return realAdd.call(this, type, handler, options);
                            delayed = true;
                            const dialog = this;
                            return realAdd.call(this, type, function(event) {
                                document.addEventListener('input', function watch(input) {
                                    if (input.target.id === 'message-input' && input.target.value.endsWith('@')) {
                                        document.removeEventListener('input', watch);
                                        queueMicrotask(() => {
                                            handler.call(dialog, event);
                                            window.delayedCloseDelivered = true;
                                        });
                                    }
                                });
                            }, options);
                        };
                    """)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(ORIGIN)
                page.locator("#participant-list .participant").first.wait_for(state="visible")
                check("a_human_sees_remove_beside_others_and_restore_for_the_removed",
                      buttons() == ["Rename Synthetic human", "Rename Agent A", "Remove Agent A", "Restore Agent B"])
                remove = page.locator("#participant-list").get_by_role("button", name="Remove Agent A", exact=True)
                check("remove_is_an_accessible_delete_icon_with_a_hover_label",
                      remove.inner_text() == "" and remove.get_attribute("title") == "Remove Agent A"
                      and remove.locator('svg[aria-hidden="true"][focusable="false"] path').count() == 1)
                remove.focus()
                remove.press("Enter")
                confirmation = page.locator("dialog[open]")
                check("opening_the_delete_icon_requires_confirmation_before_any_request",
                      report["requests"] == [] and "Every message and record they made stays" in confirmation.inner_text())
                confirmation.get_by_role("button", name="Cancel", exact=True).click()
                check("cancelling_removal_changes_nothing", report["requests"] == [] and not confirmation.is_visible())
                # Squeezed beside two buttons, a name broke into one letter per line; a readable name takes at most two
                report["name_lines"] = page.locator("#participant-list .participant-name").evaluate_all(
                    "names => names.map(name => Math.round(name.getBoundingClientRect().height"
                    " / parseFloat(getComputedStyle(name).lineHeight)))")
                check("every_name_stays_readable", max(report["name_lines"]) <= 2)
                check("the_removed_list_is_labelled",
                      page.locator("#participant-list .participant-group").inner_text().strip().lower() == "removed"
                      and page.locator("#participant-list .participant.removed .participant-name").all_inner_texts() == ["Agent B"])
                page.locator("#new-channel").click()
                dialog = page.locator("dialog[open]")
                check("a_new_conversation_offers_only_active_agents",
                      dialog.locator("input[name=members]").evaluate_all("boxes => boxes.map(box => box.value)") == ["a"])
                dialog.get_by_role("button", name="Cancel").click()
                box = page.locator("#message-input")
                box.fill("")
                box.type("Hi @")
                page.locator("#mention-picker [role=option]").first.wait_for()   # the picker draws after the keystroke
                if delay_close:
                    check("late_close_delivered_without_stealing_composer_focus",
                          page.evaluate("window.delayedCloseDelivered") and box.evaluate("element => document.activeElement === element"))
                report["mention_options"] = page.locator("#mention-picker [role=option]").all_inner_texts()
                check("an_at_mention_offers_only_active_members", report["mention_options"] == ["Agent A"])
                box.press("Escape")
                box.fill("")
                submit("Remove Agent A", "Left the project")
                page.locator("#participant-list .participant.removed .participant-name", has_text="Agent A").wait_for()
                check("remove_sends_the_seen_name_and_the_reason",
                      report["requests"] == [["/api/participants/remove",
                                              {"id": "a", "seen_name": "Agent A", "reason": "Left the project"}]])
                check("the_removed_agent_moves_to_the_removed_list_and_the_notice_says_messages_stay",
                      "Their messages stay" in page.locator("#notice").inner_text()
                      and buttons() == ["Rename Synthetic human", "Restore Agent A", "Restore Agent B"])
                submit("Restore Agent B", "Back on the project")
                page.locator("#participant-list").get_by_role("button", name="Remove Agent B").wait_for()
                check("restore_sends_the_seen_name_and_the_reason_and_brings_the_member_back",
                      report["requests"][-1] == ["/api/participants/restore",
                                                 {"id": "b", "seen_name": "Agent B", "reason": "Back on the project"}])
                page.screenshot(path=str(evidence_root() / "participant_removal.png"))
                viewer["id"] = "a"
                people["a"]["active"] = True
                page.reload()
                page.locator("#participant-list .participant").first.wait_for(state="visible")
                check("an_agent_sees_no_remove_or_restore",
                      page.locator("#participant-list").get_by_role("button", name="Remove").count() == 0
                      and page.locator("#participant-list").get_by_role("button", name="Restore").count() == 0)
                browser.close()
        finally:   # the report is evidence on a failure too
            report["page_errors"] = errors
            (evidence_root() / "participant_removal.json").write_text(json.dumps(report, indent=1) + "\n")
        self.assertEqual(errors, [])
        self.assertEqual(report["external_requests"], [])
        self.assertEqual(report["unexpected"], [])


if __name__ == "__main__":
    unittest.main()
