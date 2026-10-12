"""Offline Chromium check: the source-scan age reads "37 seconds old", never "37s old" (task "spaces between numbers
and words in messages": UI-generated labels). All requests are intercepted synthetic fixtures; no server starts."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from qa_support import confined_playwright, evidence_root

STATIC = Path(__file__).resolve().parents[1] / "static"
ORIGIN = "http://common-ground.test"
GLUED = re.compile(r"\d[^\W\d_]")      # a digit touching a letter, e.g. "37s"


class NumberSpacingBrowserTests(unittest.TestCase):
    def test_the_source_scan_age_keeps_a_space_between_number_and_word(self) -> None:
        report: dict[str, Any] = {"checks": {}, "external_requests": [], "unexpected": [], "summaries": []}
        errors: list[str] = []
        scan = {"age_seconds": 37.9}

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
            me = {"id": "agent", "name": "Synthetic agent", "kind": "agent"}
            state = {"workspace": {"title": "Synthetic QA", "project_label": "Offline"}, "me": me, "participants": [me],
                     "channels": [{"id": "workspace", "title": "Workspace", "kind": "shared", "members": ["agent"],
                                   "unread": 0}],
                     "decisions": [], "knowledge": [], "sources": [], "tasks": []}
            source_scan = {"checked_at": "2026-10-10T16:00:00+00:00", "age_seconds": scan["age_seconds"],
                           "available_count": 3, "configured_count": 4, "issues": []}
            answers: dict[str, Any] = {"/api/state": state, "/api/bookmarks": {"messages": []},
                                       "/api/messages": {"messages": [], "cursor": 0}, "/api/read": {"ok": True},
                                       "/api/personal": {"drafts": [], "outbox": [],
                                                         "preferences": {"values": {}, "version": 0}},
                                       "/api/brief": {"workspace": state["workspace"], "me": me,
                                                      "generated_at": "2026-10-10T16:00:00+00:00",
                                                      "scope": {"channel_ids": ["workspace"]}, "context": [],
                                                      "knowledge": [], "lessons": [], "disputed": [], "stale": [],
                                                      "decisions": [], "tasks": [], "sources": [], "source_context": [],
                                                      "source_scan": source_scan, "truncation": {}, "limitations": [],
                                                      "knowledge_check": None}}
            if url.path == "/api/personal/preferences":
                body = request.request.post_data_json or {}
                answers[url.path] = {"values": body.get("values", {}), "version": body.get("version", 0) + 1}
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
                for age, expected in ((37.9, "37 seconds old"), (1.2, "1 second old")):
                    scan["age_seconds"] = age
                    page.goto(ORIGIN)
                    # the summary first says "not checked yet"; wait for the brief's scan to reach it
                    summary = page.locator("#source-scan-summary", has_text="Last source scan")
                    summary.wait_for(state="visible")
                    text = summary.inner_text()
                    report["summaries"].append(text)
                    check(f"reads_{expected.replace(' ', '_')}", expected in text and not GLUED.search(text))
                page.screenshot(path=str(evidence_root() / "number_spacing.png"))
                browser.close()
        finally:   # the report is evidence on a failure too
            report["page_errors"] = errors
            (evidence_root() / "number_spacing.json").write_text(json.dumps(report, indent=1) + "\n")
        self.assertEqual(errors, [])
        self.assertEqual(report["external_requests"], [])
        self.assertEqual(report["unexpected"], [])


if __name__ == "__main__":
    unittest.main()
