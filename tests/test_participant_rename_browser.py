"""Offline Chromium check: a human renames a participant from the people list (task "rename people and agents").
Rename opens a dialog with the current name, sends the name the human saw, keeps the dialog open on a refusal and
shows the new name after success; an agent sees no Rename control. All requests are intercepted synthetic fixtures;
no server starts and nothing leaves the page."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from qa_support import confined_playwright, evidence_root

STATIC = Path(__file__).resolve().parents[1] / "static"
ORIGIN = "http://common-ground.test"


class ParticipantRenameBrowserTests(unittest.TestCase):
    def test_a_human_renames_from_the_people_list_and_an_agent_cannot(self) -> None:
        report: dict[str, Any] = {"checks": {}, "external_requests": [], "unexpected": [], "renames": []}
        errors: list[str] = []
        names = {"human": "Synthetic human", "a": "Agent A"}
        viewer = {"id": "human"}
        answer = {"status": 409, "json": {"error": "The name changed meanwhile; reload before renaming"}}

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
            if url.path == "/api/participants/rename":
                sent = request.request.post_data_json
                report["renames"].append(sent)
                if answer["status"] == 200:
                    names[sent["id"]] = " ".join(sent["name"].split())
                    answer["json"] = {"id": sent["id"], "name": names[sent["id"]], "kind": "agent"}
                request.fulfill(status=answer["status"], json=answer["json"])
                return
            people = [{"id": "human", "name": names["human"], "kind": "human"}, {"id": "a", "name": names["a"], "kind": "agent"}]
            me = next(person for person in people if person["id"] == viewer["id"])
            state = {"workspace": {"title": "Synthetic QA", "project_label": "Offline"}, "me": me, "participants": people,
                     "channels": [{"id": "workspace", "title": "Workspace", "kind": "shared", "members": ["human", "a"],
                                   "unread": 0}],
                     "decisions": [], "knowledge": [], "sources": [], "tasks": []}
            answers: dict[str, Any] = {"/api/state": state, "/api/bookmarks": {"messages": []},
                                       "/api/messages": {"messages": [], "cursor": 0}, "/api/read": {"ok": True},
                                       "/api/personal": {"drafts": [], "outbox": [],
                                                         "preferences": {"values": {}, "version": 0}}}
            if url.path == "/api/personal/preferences":
                body = request.request.post_data_json or {}
                answers[url.path] = {"values": body.get("values", {}), "version": body.get("version", 0) + 1}
            if url.path == "/api/brief" and viewer["id"] == "a":
                # an agent opens on its project brief; an empty synthetic brief is enough for the people list
                answers[url.path] = {"workspace": state["workspace"], "me": me, "generated_at": "2026-10-10T16:00:00+00:00",
                                     "scope": {"channel_ids": ["workspace"]}, "context": [], "knowledge": [], "lessons": [],
                                     "disputed": [], "stale": [], "decisions": [], "tasks": [], "sources": [],
                                     "source_context": [], "source_scan": {"available_count": 0, "configured_count": 0},
                                     "truncation": {}, "limitations": [], "knowledge_check": None}
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
            people = page.locator("#participant-list .participant")
            people.first.wait_for(state="visible")
            labels = [control.get_attribute("aria-label") for control in people.locator("button").all()]
            check("a_human_sees_rename_for_every_participant", labels == ["Rename Synthetic human", "Rename Agent A"])
            people.nth(1).get_by_role("button", name="Rename Agent A").click()
            dialog = page.locator("dialog[open]")
            check("the_dialog_starts_with_the_current_name", dialog.get_by_label("New display name").input_value() == "Agent A")
            dialog.get_by_label("New display name").fill("Agent  Alpha")
            dialog.get_by_role("button", name="Rename", exact=True).click()
            dialog.get_by_role("alert").wait_for(state="visible")
            check("a_refused_rename_keeps_the_dialog_and_says_why",
                  "reload before renaming" in dialog.get_by_role("alert").inner_text() and dialog.is_visible()
                  and report["renames"] == [{"id": "a", "name": "Agent  Alpha", "seen_name": "Agent A"}])
            dialog.get_by_role("button", name="Cancel").click()
            answer["status"] = 200
            people.nth(1).get_by_role("button", name="Rename Agent A").click()
            dialog = page.locator("dialog[open]")
            dialog.get_by_label("New display name").fill("Agent  Alpha")
            dialog.get_by_role("button", name="Rename", exact=True).click()
            page.locator("#participant-list .participant-name", has_text="Agent Alpha").wait_for(state="visible")
            check("a_confirmed_rename_shows_the_new_name", len(report["renames"]) == 2
                  and "Renamed to Agent Alpha" in page.locator("#notice").inner_text())
            page.screenshot(path=str(evidence_root() / "participant_rename.png"))
            viewer["id"] = "a"
            page.reload()
            page.locator("#app-shell").wait_for(state="visible")
            page.locator("#participant-list .participant").first.wait_for(state="visible")
            check("an_agent_sees_no_rename_control",
                  page.locator("#participant-list").get_by_role("button", name="Rename").count() == 0)
            browser.close()
        report["page_errors"] = errors
        (evidence_root() / "participant_rename.json").write_text(json.dumps(report, indent=1) + "\n")
        self.assertEqual(errors, [])
        self.assertEqual(report["external_requests"], [])
        self.assertEqual(report["unexpected"], [])


if __name__ == "__main__":
    unittest.main()
