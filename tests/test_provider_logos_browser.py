"""Offline Chromium check: a participant with a configured logo shows that local image beside the name, in the people
list and on its messages; the human keeps initials; nothing is fetched from outside the app (Collaboration).
All requests are intercepted synthetic fixtures; no server starts and nothing leaves the page."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from qa_support import confined_playwright, evidence_root

STATIC = Path(__file__).resolve().parents[1] / "static"
ORIGIN = "http://common-ground.test"


class ProviderLogoBrowserTests(unittest.TestCase):
    def test_configured_logos_show_beside_names_and_the_human_keeps_initials(self) -> None:
        report: dict[str, Any] = {"checks": {}, "external_requests": [], "unexpected": [], "logo_requests": []}
        errors: list[str] = []
        viewer = {"id": "curator"}

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
                report["logo_requests"].append(url.path)
                request.fulfill(path=str(STATIC / "logos" / Path(url.path).name), content_type="image/svg+xml")
                return
            if url.path.startswith("/static/") and Path(url.path).name in {"app.js", "style.css", "common-ground.svg"}:
                request.fulfill(path=str(STATIC / Path(url.path).name))
                return
            # Fictional participant roster with all bundled provider marks.
            people: list[dict[str, Any]] = [{"id": "curator", "name": "Curator", "kind": "human", "logo": None},
                                            {"id": "codex", "name": "Codex", "kind": "agent", "logo": "codex-color"},
                                            {"id": "atlas", "name": "Atlas", "kind": "agent", "logo": "claude-color"},
                                            {"id": "scribe", "name": "Scribe", "kind": "agent", "logo": "claude-color"},
                                            {"id": "gemini", "name": "Gemini", "kind": "agent", "logo": "gemini-color"}]
            me = {key: value for key, value in next(p for p in people if p["id"] == viewer["id"]).items() if key != "logo"}
            state = {"workspace": {"title": "Common Ground · Aurora Museum", "project_label": "Exhibition planning"}, "me": me,
                     "participants": people,
                     "channels": [{"id": "workspace", "title": "Workspace", "kind": "shared",
                                   "members": ["codex", "scribe", "gemini", "atlas", "curator"], "unread": 0}],
                     "decisions": [], "knowledge": [], "sources": [], "tasks": []}
            message = {"id": 7, "channel_id": "workspace", "author_id": "codex", "author_name": "Codex",
                       "body": "Test message from an agent with a logo", "created_at": "2026-10-10T16:00:00+00:00"}
            answers: dict[str, Any] = {"/api/state": state, "/api/bookmarks": {"messages": []},
                                       "/api/messages": {"messages": [message], "cursor": 7}, "/api/read": {"ok": True},
                                       "/api/personal": {"drafts": [], "outbox": [],
                                                         "preferences": {"values": {}, "version": 0}}}
            if url.path == "/api/personal/preferences":
                body = request.request.post_data_json or {}
                answers[url.path] = {"values": body.get("values", {}), "version": body.get("version", 0) + 1}
            if url.path == "/api/brief" and viewer["id"] == "codex":
                # an agent opens on its project brief; an empty synthetic brief is enough for the current-user card
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
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                context.route("**/*", route)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(ORIGIN)
                page.locator("#app-shell").wait_for(state="visible")
                people = page.locator("#participant-list .participant")
                people.nth(4).wait_for(state="visible")
                page.locator(".message", has_text="Test message from an agent").wait_for(state="visible")
                # every logo image has finished loading before it is measured
                page.wait_for_function("[...document.querySelectorAll('.avatar.logo img')].every(i => i.complete)")
                listed = page.evaluate("""() => [...document.querySelectorAll('#participant-list .participant')].map(p => {
                    const avatar = p.querySelector('.avatar'), image = avatar.querySelector('img');
                    return {name: p.querySelector('.participant-name').textContent, text: avatar.textContent,
                            src: image ? image.getAttribute('src') : null, alt: image ? image.alt : null,
                            width: image ? image.naturalWidth : 0, title: avatar.title};
                })""")
                report["listed"] = listed
                by_name = {row["name"].replace(" (you)", ""): row for row in listed}
                check("the_human_keeps_initials", by_name["Curator"]["src"] is None and by_name["Curator"]["text"] == "CU")
                check("each_agent_shows_its_own_local_logo",
                      [by_name[name]["src"] for name in ("Codex", "Atlas", "Scribe", "Gemini")]
                      == ["/static/logos/codex-color.svg", "/static/logos/claude-color.svg",
                          "/static/logos/claude-color.svg", "/static/logos/gemini-color.svg"])
                check("the_logos_really_load", all(by_name[name]["width"] > 0 for name in ("Codex", "Atlas", "Gemini")))
                check("the_name_stays_beside_the_logo_and_the_image_is_decorative",
                      by_name["Codex"]["alt"] == "" and by_name["Codex"]["title"] == "Codex"
                      and by_name["Codex"]["text"] == "")
                author = page.locator(".message", has_text="Test message from an agent").locator(".avatar img")
                check("the_message_author_shows_the_logo", author.get_attribute("src") == "/static/logos/codex-color.svg")
                page.screenshot(path=str(evidence_root() / "provider_logos.png"))      # what Curator sees
                viewer["id"] = "codex"                   # the current-user card of an agent with a logo
                page.reload()
                page.locator("#current-user .avatar.logo img").wait_for(state="attached")
                check("the_current_user_card_shows_its_own_logo",
                      page.locator("#current-user .avatar img").get_attribute("src") == "/static/logos/codex-color.svg")
                page.screenshot(path=str(evidence_root() / "provider_logos_agent_view.png"))
                browser.close()
        finally:
            report["page_errors"] = errors
            (evidence_root() / "provider_logos.json").write_text(json.dumps(report, indent=1) + "\n")
        self.assertEqual(errors, [])
        self.assertEqual(report["external_requests"], [])
        self.assertEqual(report["unexpected"], [])


if __name__ == "__main__":
    unittest.main()
