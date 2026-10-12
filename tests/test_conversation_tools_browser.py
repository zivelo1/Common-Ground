"""Offline Chromium checks; all requests are intercepted synthetic fixtures."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from qa_support import STATE_QA, confined_playwright

STATIC = Path(__file__).resolve().parents[1] / "static"
OUTPUT = STATE_QA / "test_receipts/conversation_tools"
ORIGIN = "http://common-ground.test"


class ConversationToolsBrowserTests(unittest.TestCase):
    def test_conversation_controls_preserve_records(self) -> None:
        OUTPUT.mkdir(parents=True, exist_ok=True)
        report: dict[str, Any] = {"checks": {}, "failures": [], "server_started": False,
                                  "real_provider_calls": 0, "external_requests": []}
        messages: list[dict[str, Any]] = [{"id": index, "channel_id": "workspace", "author_id": "agent",
                     "author_name": "Synthetic agent", "body": f"Synthetic message {index}\n" + "Evidence line\n" * 8,
                     "created_at": "2026-10-06T18:00:00+00:00"} for index in range(1, 161)]
        original_messages = json.dumps(messages)
        personal: dict[str, Any] = {"drafts": [], "outbox": [], "preferences": {"values": {}, "version": 0}}
        requests: list[tuple[str, dict[str, Any]]] = []
        errors: list[str] = []
        draft_failure = [False]
        state = {"workspace": {"title": "Synthetic QA", "project_label": "Offline"},
                 "me": {"id": "human", "name": "Synthetic human", "kind": "human"},
                 "participants": [{"id": "human", "name": "Synthetic human", "kind": "human"},
                                  {"id": "agent", "name": "Synthetic agent", "kind": "agent"}],
                 "channels": [{"id": "workspace", "title": "Synthetic workspace", "kind": "shared",
                               "members": ["human", "agent"], "unread": 0}],
                 "decisions": [], "tasks": [], "knowledge": [], "sources": []}

        def check(name: str, value: bool) -> None:
            report["checks"][name] = bool(value)
            self.assertTrue(value, name)

        def route(request: Any) -> None:
            url = urlsplit(request.request.url)
            if url.hostname != "common-ground.test":
                report["external_requests"].append(request.request.url)
                request.abort()
                return
            path = url.path
            if path == "/":
                request.fulfill(path=str(STATIC / "index.html"), content_type="text/html")
                return
            if path.startswith("/static/") and Path(path).name in {"app.js", "style.css", "common-ground.svg"}:
                request.fulfill(path=str(STATIC / Path(path).name))
                return
            body = request.request.post_data_json or {}
            requests.append((path, body))
            result: Any
            if path == "/api/state":
                result = state
            elif path == "/api/personal":
                result = personal
            elif path == "/api/bookmarks":
                result = {"messages": []}  # This synthetic conversation fixture has no saved messages.
            elif path == "/api/messages":
                after = int(parse_qs(url.query).get("after", ["0"])[0])
                page_messages = [message for message in messages if message["id"] > after][:40]
                result = {"messages": page_messages, "cursor": page_messages[-1]["id"] if page_messages else after}
            elif path == "/api/personal/preferences":
                personal["preferences"] = {"values": body["values"], "version": body["version"] + 1}
                result = personal["preferences"]
            elif path == "/api/personal/draft":
                if draft_failure[0]:
                    request.fulfill(status=503, json={"error": "Synthetic draft save unavailable"})
                    return
                draft = {"channel_id": body["channel_id"], "body": body["body"], "version": body["version"] + 1}
                personal["drafts"] = [draft]
                result = draft
            elif path == "/api/read":
                result = {"ok": True}
            elif path == "/api/search":
                result = {"messages": [messages[1]], "decisions": [], "tasks": [], "knowledge": [], "sources": []}
            else:
                report["failures"].append("Unexpected request: " + path)
                request.fulfill(status=400, json={"error": "Unexpected synthetic request"})
                return
            request.fulfill(json=result)

        try:
            with confined_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.route("**/*", route)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(ORIGIN)
                page.locator("#app-shell").wait_for(state="visible")
                page.locator('[data-message-id="40"]').wait_for()
                page.wait_for_function("() => document.querySelector('#personal-save-status')?.dataset.saved === 'true'")
                check("stored_message_numbers_visible", page.get_by_label("Message number 40", exact=True).inner_text() == "#40")
                check("default_text_size_applies", page.locator(".message-bubble").first.evaluate("e => getComputedStyle(e).fontSize") == "14px")
                for _ in range(4):
                    page.get_by_role("button", name="Increase text size", exact=True).click()
                check("maximum_size_stops_at_22", page.locator("#conversation-text-size").inner_text() == "22 px" and page.locator("#larger-text").is_disabled())
                page.reload()
                page.locator('[data-message-id="40"]').wait_for()
                check("text_size_survives_reload", page.locator(".message-bubble").first.evaluate("e => getComputedStyle(e).fontSize") == "22px")
                for _ in range(5):
                    page.get_by_role("button", name="Decrease text size", exact=True).click()
                check("minimum_size_stops_at_12", page.locator("#smaller-text").is_disabled() and page.locator("#conversation-text-size").inner_text() == "12 px")
                page.locator("#message-list").evaluate("e => e.scrollTop = 0")
                page.evaluate("() => Promise.all([jumpToLatestMessage('workspace'), jumpToLatestMessage('workspace')])")
                page.locator('[data-message-id="160"]').wait_for()
                check("concurrent_latest_requests_finish_without_reading_collapsed_history", not page.evaluate("() => cacheFor('workspace').historyLoading"))
                check("latest_fetches_remaining_history_pages", page.get_by_label("Message number 160", exact=True).inner_text() == "#160")
                check("initial_long_history_renders_only_last_50", page.locator("#message-list article.message").count() == 50)
                check("latest_acknowledges_newest_record_after_history_load", max(body["message_id"] for path, body in requests if path == "/api/read") == 160)
                check("latest_scroll_reaches_bottom", page.locator("#message-list").evaluate("e => e.scrollHeight - e.scrollTop - e.clientHeight < 2"))
                page.get_by_role("button", name="Show older messages", exact=False).click()
                check("older_button_loads_next_50_without_deletion", page.locator("#message-list article.message").count() == 100)
                page.get_by_role("button", name="Jump to latest message", exact=True).click()
                page.wait_for_function("() => document.querySelectorAll('#message-list article.message').length === 50")
                page.evaluate("() => { app.outbox.push({channel_id: 'workspace', author_id: 'human', body: 'Synthetic queued retry', client_id: 'queued-qa', created_at: '2026-10-06T18:00:00+00:00', status: 'failed', durable: true}); renderMessages(); }")
                page.locator("#message-input").fill("before AFTER")
                page.locator("#message-input").evaluate("e => e.setSelectionRange(7, 12)")
                page.get_by_role("button", name="Emoji", exact=True).click()
                page.get_by_role("button", name="Thumbs up", exact=True).click()
                check("emoji_replaces_selected_text", page.locator("#message-input").input_value() == "before 👍")
                page.wait_for_timeout(100)
                check("emoji_returns_focus_to_composer", page.evaluate("() => document.activeElement.id") == "message-input")
                page.wait_for_function("() => document.querySelector('#personal-save-status')?.dataset.saved === 'true'")
                check("emoji_uses_existing_draft_save", personal["drafts"][0]["body"] == "before 👍")
                page.get_by_role("button", name="Clear draft", exact=True).click()
                page.get_by_role("button", name="Keep draft", exact=True).click()
                check("cancel_keeps_draft", page.locator("#message-input").input_value() == "before 👍")
                page.get_by_role("button", name="Clear draft", exact=True).click()
                page.locator("dialog").get_by_role("button", name="Clear draft", exact=True).click()
                page.wait_for_function("() => document.querySelector('#personal-save-status')?.dataset.saved === 'true'")
                check("clear_saves_empty_draft", personal["drafts"][0]["body"] == "" and page.locator("#message-input").input_value() == "")
                check("clear_retains_retry_outbox", page.evaluate("() => app.outbox.some(row => row.client_id === 'queued-qa' && row.body === 'Synthetic queued retry')"))
                check("empty_draft_cannot_clear_again", page.locator("#clear-draft").is_disabled())
                check("clear_does_not_delete_delivered_messages", json.dumps(messages) == original_messages and not any("delete" in path for path, _ in requests))
                page.locator("#message-input").fill("KEEP IF SAVE FAILS")
                page.wait_for_function("() => document.querySelector('#personal-save-status')?.dataset.saved === 'true'")
                draft_failure[0] = True
                page.get_by_role("button", name="Clear draft", exact=True).click()
                page.locator("dialog").get_by_role("button", name="Clear draft", exact=True).click()
                page.wait_for_function("() => document.querySelector('#personal-save-status')?.dataset.saved === 'false' && document.querySelector('#notice-text').textContent.includes('not saved yet')")
                check("failed_clear_preserves_last_saved_draft_and_warns", personal["drafts"][0]["body"] == "KEEP IF SAVE FAILS")
                draft_failure[0] = False
                page.locator("#message-input").fill("Recovered draft")
                page.wait_for_function("() => document.querySelector('#personal-save-status')?.dataset.saved === 'true'")
                check("saving_recovers_after_failed_clear", personal["drafts"][0]["body"] == "Recovered draft" and page.locator("#notice").is_hidden())
                page.locator("#message-input").fill("x" * 16000)
                page.locator("#message-input").evaluate("e => e.setSelectionRange(16000, 16000)")
                page.get_by_role("button", name="Emoji", exact=True).click()
                page.get_by_role("button", name="Thumbs up", exact=True).click()
                check("emoji_cannot_exceed_message_limit", len(page.locator("#message-input").input_value()) == 16000)
                page.get_by_role("button", name="Close dialog", exact=True).click()
                page.locator("#message-input").fill("Recovered draft")
                page.locator("#search-input").fill("Synthetic message 2")
                page.locator(".search-message").wait_for()
                page.locator(".search-message").click()
                page.locator('[data-message-id="2"].search-highlight').wait_for()
                check("search_opens_exact_message", page.evaluate("() => document.activeElement.dataset.messageId") == "2")
                page.locator("#dismiss-notice").click()
                page.screenshot(path=str(OUTPUT / "desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                page.wait_for_timeout(400)
                page.get_by_role("button", name="Increase text size", exact=True).click()
                check("mobile_has_no_horizontal_overflow", page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"))
                page.get_by_role("button", name="Emoji", exact=True).click()
                check("mobile_emoji_dialog_fits", page.locator("dialog").evaluate("e => e.getBoundingClientRect().right <= innerWidth"))
                page.get_by_role("button", name="Close dialog", exact=True).click()
                page.screenshot(path=str(OUTPUT / "mobile.png"), full_page=True)
                check("no_browser_errors", errors == [])
                check("no_external_or_provider_requests", report["external_requests"] == [] and not any("adapter" in path for path, _ in requests))
                context.close()
                browser.close()
        except Exception as error:
            report["failures"].append(str(error))
            raise
        finally:
            report["browser_errors"] = errors
            report["request_paths"] = sorted({path for path, _ in requests})
            report["status"] = "PASS" if not report["failures"] and report["checks"] and all(report["checks"].values()) else "FAIL"
            (OUTPUT / "browser_result.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    unittest.main()
