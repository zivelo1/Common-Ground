"""Offline Chromium check of the brief's daily knowledge check (unit 3): due state, last check, missed days, recording it
through the dialog and the done state afterwards. All requests are intercepted synthetic fixtures; no server starts."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from qa_support import confined_playwright, evidence_root

STATIC = Path(__file__).resolve().parents[1] / "static"
ORIGIN = "http://common-ground.test"


class KnowledgeCheckBrowserTests(unittest.TestCase):
    def test_the_brief_shows_and_records_the_daily_check(self) -> None:
        report: dict[str, Any] = {"checks": {}, "external_requests": [], "unexpected": [], "posted": []}
        errors: list[str] = []
        check: dict[str, Any] = {"configured": True, "time": "18:00", "timezone": "Asia/Jerusalem",
                                 "check_date": "2026-10-10", "state": "due", "missed": ["2026-10-09"],
                                 "last": {"channel_id": "workspace", "check_date": "2026-10-08", "actor_id": "agent",
                                          "created_at": "2026-10-08T15:30:00+00:00", "late": False,
                                          "outcome": "nothing_changed", "note": "Synthetic earlier check",
                                          "knowledge_ids": []}}
        state = {"workspace": {"title": "Synthetic QA", "project_label": "Offline"},
                 "me": {"id": "human", "name": "Synthetic human", "kind": "human"},
                 "participants": [{"id": "human", "name": "Synthetic human", "kind": "human"},
                                  {"id": "agent", "name": "Synthetic agent", "kind": "agent"}],
                 "channels": [{"id": "workspace", "title": "Workspace", "kind": "shared", "members": ["human", "agent"],
                               "unread": 0}],
                 "decisions": [], "tasks": [], "knowledge": [], "sources": []}
        preferences = {"values": {"view": "brief"}, "version": 1}

        def brief() -> dict[str, Any]:
            return {"workspace": state["workspace"], "me": state["me"], "generated_at": "2026-10-10T16:00:00+00:00",
                    "scope": {"channel_ids": ["workspace"]}, "context": [], "knowledge": [], "lessons": [],
                    "disputed": [], "stale": [], "decisions": [], "tasks": [], "sources": [], "source_context": [],
                    "source_scan": {"available_count": 0, "configured_count": 0}, "truncation": {}, "limitations": [],
                    "knowledge_check": json.loads(json.dumps(check))}

        def check_name(name: str, value: bool) -> None:
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
            if url.path == "/api/knowledge-checks" and request.request.method == "POST":
                report["posted"].append(body)
                check.update(state="done", missed=["2026-10-09"],
                             last={**check["last"], "check_date": "2026-10-10", "actor_id": "human",
                                   "outcome": body["outcome"], "note": body["note"]})
                request.fulfill(json=check["last"])
                return
            if url.path == "/api/personal/preferences":
                preferences.update(values=body.get("values", {}), version=body.get("version", 0) + 1)
                request.fulfill(json=preferences)
                return
            answers: dict[str, Any] = {"/api/state": state, "/api/bookmarks": {"messages": []},
                                       "/api/messages": {"messages": [], "cursor": 0}, "/api/read": {"ok": True},
                                       "/api/personal": {"drafts": [], "outbox": [], "preferences": preferences}}
            if url.path == "/api/brief" and parse_qs(url.query).get("channel_id") == ["workspace"]:
                answers["/api/brief"] = brief()
            if url.path not in answers:
                report["unexpected"].append(url.path + ("?" + url.query if url.query else ""))
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
            section = page.locator(".knowledge-check")
            section.wait_for(state="visible")
            text = section.inner_text()
            check_name("due_state_shown", "2026-10-10: due" in text)
            check_name("last_check_shown", "Last check 2026-10-08 by Synthetic agent · Nothing changed" in text)
            check_name("missed_day_shown", "Missed: 2026-10-09" in text)
            page.get_by_role("button", name="Record the check").click()
            page.get_by_label("Outcome").select_option("updated")
            page.get_by_label("Note").fill("Reconciled the roadmap")
            page.get_by_role("button", name="Record check").click()
            page.locator('.knowledge-check[data-state="done"]').wait_for()
            check_name("posted_once_for_the_selected_conversation", report["posted"] == [
                {"channel_id": "workspace", "outcome": "updated", "note": "Reconciled the roadmap"}])
            done = page.locator(".knowledge-check").inner_text()
            check_name("done_state_shown", "2026-10-10: done" in done and "Knowledge updated" in done)
            check_name("no_record_button_when_done", page.get_by_role("button", name="Record the check").count() == 0)
            page.screenshot(path=str(evidence_root() / "knowledge_check_done.png"))
            browser.close()
        report["page_errors"] = errors
        (evidence_root() / "knowledge_check.json").write_text(json.dumps(report, indent=1) + "\n")
        self.assertEqual(errors, [])
        self.assertEqual(report["external_requests"], [])
        self.assertEqual(report["unexpected"], [])


if __name__ == "__main__":
    unittest.main()
