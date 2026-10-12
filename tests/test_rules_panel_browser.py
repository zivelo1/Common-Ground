"""Offline Chromium check of the shared Rules panel (Collaboration): the Rules entry sits directly above Provider
workspace; a human writes and saves the Rules, sees the formatted text, the version line and the history; a refused
save keeps the typed text; an agent reads the Rules without an edit button. All requests are intercepted fixtures with
the live roster's names; no server starts and nothing leaves the page. The save rules themselves (who may save, stale
versions, limits) are proved against the real server in test_rules_panel.py."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from qa_support import confined_playwright, evidence_root

STATIC = Path(__file__).resolve().parents[1] / "static"
ORIGIN = "http://common-ground.test"
FIRST = "# Rule #1\nMaximum efficiency, high quality."
SECOND = FIRST + "\n\nDisagree before you execute."


class RulesPanelBrowserTests(unittest.TestCase):
    def test_a_human_writes_the_rules_a_refused_save_keeps_the_text_and_an_agent_only_reads(self) -> None:
        report: dict[str, Any] = {"checks": {}, "external_requests": [], "unexpected": [], "saves": []}
        errors: list[str] = []
        server: dict[str, Any] = {"viewer": "curator", "rules": {"current": None, "max_chars": 32000, "history": []},
                                  "refuse_next": False}

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
            if url.path == "/api/rules" and request.request.method == "POST":
                body = request.request.post_data_json
                report["saves"].append(body)
                if server["refuse_next"]:
                    request.fulfill(status=409, json={"error": "The Rules changed meanwhile; reload before saving"})
                    return
                version = body["base_version"] + 1
                entry = {"version": version, "author_id": "curator", "author_name": "Curator", "created_at": "2026-10-10T16:30:00+00:00"}
                server["rules"] = {"current": {**entry, "body": body["body"]}, "max_chars": 32000,
                                   "history": [entry, *server["rules"]["history"]]}
                request.fulfill(json=server["rules"])
                return
            people: list[dict[str, Any]] = [{"id": "curator", "name": "Curator", "kind": "human", "logo": None},
                                            {"id": "codex", "name": "Codex", "kind": "agent", "logo": "codex-color"}]
            me = {key: value for key, value in next(p for p in people if p["id"] == server["viewer"]).items() if key != "logo"}
            state = {"workspace": {"title": "Common Ground · Aurora Museum", "project_label": "Exhibition planning"},
                     "me": me, "participants": people,
                     "channels": [{"id": "workspace", "title": "Workspace", "kind": "shared", "members": ["codex", "curator"], "unread": 0}],
                     "decisions": [], "knowledge": [], "sources": [], "tasks": []}
            answers: dict[str, Any] = {"/api/state": state, "/api/bookmarks": {"messages": []}, "/api/rules": server["rules"],
                                       "/api/messages": {"messages": [], "cursor": 0}, "/api/read": {"ok": True},
                                       "/api/personal": {"drafts": [], "outbox": [], "preferences": {"values": {}, "version": 0}}}
            if url.path == "/api/personal/preferences":
                sent = request.request.post_data_json or {}
                answers[url.path] = {"values": sent.get("values", {}), "version": sent.get("version", 0) + 1}
            if url.path == "/api/brief" and server["viewer"] == "codex":
                # an agent opens on its project brief; an empty brief is enough to reach the Rules
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

        try:
            with confined_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1280, "height": 900}, timezone_id="UTC")
                context.route("**/*", route)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(ORIGIN)
                page.locator("#app-shell").wait_for(state="visible")
                views = page.evaluate("() => [...document.querySelectorAll('.main-nav [data-view]')].map(b => b.dataset.view)")
                report["navigation"] = views
                check("rules_sits_directly_above_provider_workspace", views.index("rules") + 1 == views.index("providers"))
                page.locator('[data-view="rules"]').click()
                page.locator(".empty-state", has_text="No rules saved yet").wait_for(state="visible")
                page.locator("#rules-edit", has_text="Write the Rules").click()
                page.locator("#rules-input").fill(FIRST)
                page.locator("#rules-save").click()
                page.locator(".rules-meta", has_text="Version 1 · saved by Curator").wait_for(state="visible")
                check("the_first_save_starts_from_version_0", report["saves"][0] == {"body": FIRST, "base_version": 0})
                check("the_saved_rules_are_formatted", page.locator(".rules-text h1").inner_text() == "Rule #1")
                check("the_history_lists_the_version", page.locator(".rules-history-item").all_inner_texts()
                      == ["Version 1 · Curator · 10.10.2026 - 16:30"])
                page.locator("#rules-edit", has_text="Edit Rules").click()
                check("editing_starts_from_the_current_text", page.locator("#rules-input").input_value() == FIRST)
                page.locator("#rules-input").fill(SECOND)
                server["refuse_next"] = True                         # someone else saved first
                page.locator("#rules-save").click()
                page.locator("#rules-error", has_text="changed meanwhile").wait_for(state="visible")
                check("a_refused_save_keeps_the_typed_text", page.locator("#rules-input").input_value() == SECOND)
                check("the_refused_save_was_based_on_version_1", report["saves"][1] == {"body": SECOND, "base_version": 1})
                server["refuse_next"] = False
                page.locator("#rules-save").click()
                page.locator(".rules-meta", has_text="Version 2 · saved by Curator").wait_for(state="visible")
                check("the_history_keeps_both_versions", page.locator(".rules-history-item").count() == 2)
                page.screenshot(path=str(evidence_root() / "rules_panel.png"))      # what Curator sees
                server["viewer"] = "codex"
                page.reload()
                page.locator('[data-view="rules"]').click()
                page.locator(".rules-meta", has_text="Version 2 · saved by Curator").wait_for(state="visible")
                check("an_agent_reads_the_rules_without_an_edit_button",
                      page.locator("#rules-edit").count() == 0 and page.locator(".rules-text", has_text="Disagree before").is_visible())
                page.screenshot(path=str(evidence_root() / "rules_panel_agent_view.png"))
                browser.close()
        finally:   # the report is evidence on a failure too
            report["page_errors"] = errors
            (evidence_root() / "rules_panel.json").write_text(json.dumps(report, indent=1) + "\n")
        self.assertEqual(errors, [])
        self.assertEqual(report["external_requests"], [])
        self.assertEqual(report["unexpected"], [])


if __name__ == "__main__":
    unittest.main()
