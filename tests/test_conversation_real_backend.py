"""Real Common Ground HTTP/browser regression with disposable synthetic actors."""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from playwright.sync_api import sync_playwright
from qa_support import evidence_root, temporary_directory

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
# Direct test-file execution also needs the canonical application on the path.
import app as backend  # noqa: E402 — project path is set above for standalone execution.
import cli  # noqa: E402 — project path is set above for standalone execution.


class RealConversationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = temporary_directory(self, prefix="conversation-real-")
        self.folder = Path(self.temp.name)
        self.errors: list[str] = []
        self.external: list[str] = []
        self.requests: list[dict[str, Any]] = []
        self.pages: list[Any] = []
        self.config = self.folder / "config.json"
        self.config.write_text(json.dumps({"title": "Synthetic real-backend QA", "project_label": "Disposable test actors", "state_dir": "instance_data", "participants": [{"id": ident, "name": "Synthetic " + ident, "kind": "human" if ident == "human" else "agent"} for ident in ("human", "codex", "peer", "outsider")], "source_roots": [], "source_files": []}))
        self.server = backend.make_server(self.config, 0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.addCleanup(self.cleanup_server)
        self.worker.start()
        # The driver inherits a confined temp root without changing the importing
        # process environment; cleanup callbacks also run after setUp failures.
        with patch.dict(os.environ, {"TMPDIR": str(self.folder), "PYTHONDONTWRITEBYTECODE": "1"}):
            self.playwright = sync_playwright().start()
        self.addCleanup(self.playwright.stop)
        self.browser = self.playwright.chromium.launch(headless=True, env={**os.environ, "TMPDIR": str(self.folder)})
        self.addCleanup(self.browser.close)
        self.addCleanup(self.save_receipt)

    def save_receipt(self) -> None:
        report = {"test": self.id(), "scope": "synthetic actors and real local HTTP/backend only", "origin": self.server.origin, "disposable_fixture": str(self.folder), "browser_errors": self.errors, "external_requests": self.external, "requests": self.requests}
        (evidence_root() / (uuid4().hex + ".json")).write_text(json.dumps(report, indent=2) + "\n")

    def cleanup_server(self) -> None:
        try:
            if self.worker.is_alive():
                self.server.shutdown()
                self.worker.join(timeout=backend.REQUEST_TIMEOUT_SECONDS)
        finally:
            self.server.server_close()
        self.assertFalse(self.worker.is_alive())
        self.assertFalse((self.folder / "instance_data/runtime.json").exists())

    def request(self, actor: str, method: str, path: str, body=None):
        return cli.request(self.config, actor, method, path, body)

    def seed(self, count: int, channel: str = "workspace", actor: str = "peer") -> list[dict]:
        return [self.request(actor, "POST", "/api/messages", {"channel_id": channel, "body": f"Synthetic {channel} row {index}\n" + "Reading line\n" * 8, "client_id": uuid4().hex}) for index in range(count)]

    def page(self, actor: str = "human", block_read: bool = False):
        self.read_hold = block_read
        context = self.browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        page = context.new_page()
        page.on("pageerror", lambda error: self.errors.append(str(error)))

        def guard(route):
            request = route.request
            if request.url.startswith(self.server.origin + "/"):
                if "/api/" in request.url:
                    self.requests.append({"method": request.method, "path": request.url.removeprefix(self.server.origin)})
                if self.read_hold and request.url.endswith("/api/read"):
                    route.abort("failed")
                    return
                route.continue_()
            elif request.url.startswith("blob:"):
                route.continue_()
            else:
                self.external.append(request.url)
                route.abort()

        context.route("**/*", guard)
        page.goto(self.server.origin)
        token_path = self.folder / "instance_data/credentials" / (actor + ".json")
        token = json.loads(token_path.read_text())["token"]
        page.locator("#access-token").fill(token)
        page.locator("#login-submit").click()
        page.locator("#app-shell").wait_for(state="visible")
        if actor != "human":
            page.locator('[data-view="conversations"]').click()
        page.locator("#message-list").wait_for()
        self.pages.append(page)
        return page

    def saved(self, page) -> None:
        page.wait_for_function("() => document.querySelector('#personal-save-status')?.dataset.saved === 'true'")

    def test_long_history_acknowledges_visible_latest_and_hidden_channel_stays_unread(self) -> None:
        rows = self.seed(410)
        page = self.page(block_read=True)
        page.get_by_role("button", name="Jump to latest message", exact=True).click()
        page.locator(f'[data-message-id="{rows[-1]["id"]}"]').wait_for()
        self.assertEqual(page.evaluate("() => cacheFor('workspace').visibleCount"), 50)
        self.assertGreater(self.request("human", "GET", "/api/state")["channels"][0]["unread"], 0)
        self.read_hold = False
        page.evaluate("() => markRead('workspace')")
        self.assertEqual(self.request("human", "GET", "/api/state")["channels"][0]["unread"], 0)
        self.assertEqual(page.locator("#message-list article.message").count(), 50)
        page.evaluate("() => Object.defineProperty(document, 'visibilityState', {configurable:true, get:()=> 'hidden'})")
        self.seed(1)
        page.evaluate("() => fetchMessages('workspace')")
        self.assertGreater(self.request("human", "GET", "/api/state")["channels"][0]["unread"], 0)
        page.evaluate("() => { Object.defineProperty(document, 'visibilityState', {configurable:true, get:()=> 'visible'}); document.dispatchEvent(new Event('visibilitychange')); }")
        page.wait_for_function("async () => { const s = await (await fetch('/api/state')).json(); return s.channels.find(c => c.id === 'workspace').unread === 0; }")
        self.assertEqual(self.errors, [])
        self.assertEqual(self.external, [])

    def test_bottom_appends_preserve_scrolled_up_anchor(self) -> None:
        self.seed(60)
        page = self.page()
        page.locator('[data-message-id="60"]').wait_for()
        page.locator("#message-list").evaluate("e => e.scrollTop = 250")
        before = page.locator("#message-list").evaluate("e => ({top:e.scrollTop, first:[...e.querySelectorAll('article.message')].find(n=>n.getBoundingClientRect().bottom>e.getBoundingClientRect().top)?.dataset.messageId})")
        self.seed(25)
        page.evaluate("() => fetchMessages('workspace')")
        after = page.locator("#message-list").evaluate("e => ({top:e.scrollTop, first:[...e.querySelectorAll('article.message')].find(n=>n.getBoundingClientRect().bottom>e.getBoundingClientRect().top)?.dataset.messageId})")
        self.assertAlmostEqual(before["top"], after["top"], delta=1)
        self.assertEqual(before["first"], after["first"])
        page.get_by_role("button", name="Show older messages", exact=False).click()
        self.assertGreater(page.locator("#message-list article.message").count(), 50)

    def test_emoji_restores_composer_focus_after_dialog_close_event(self) -> None:
        page = self.page()
        page.locator("#message-input").fill("before AFTER")
        page.locator("#message-input").evaluate("e => e.setSelectionRange(7,12)")
        page.get_by_role("button", name="Emoji", exact=True).click()
        page.get_by_role("button", name="Thumbs up", exact=True).click()
        page.wait_for_timeout(150)
        self.assertEqual(page.locator("#message-input").input_value(), "before 👍")
        self.assertEqual(page.evaluate("() => document.activeElement.id"), "message-input")
        self.saved(page)
        self.assertEqual(self.request("human", "GET", "/api/personal")["drafts"][0]["body"], "before 👍")

    def test_real_failed_nondurable_send_locks_controls_then_retry_preserves_edited_draft(self) -> None:
        page = self.page()
        original = "Synthetic failed unsaved send"
        edited = "Synthetic separate revised draft"
        page.locator("#message-input").fill(original)
        self.saved(page)
        page.route("**/api/personal/enqueue", lambda route: route.abort("failed"))
        page.locator("#send-message").click()
        page.locator(".message.failed").wait_for()
        self.assertFalse(page.evaluate("() => app.outbox[0].durable"))
        self.assertFalse(page.locator("#message-input").evaluate("e => e.readOnly"))
        self.assertTrue(page.locator("#clear-draft").is_disabled())
        self.assertTrue(page.locator("#emoji-picker").is_disabled())
        page.locator("#message-input").fill(edited)
        page.wait_for_function("() => app.pendingPersonal === 0")
        version = self.request("human", "GET", "/api/personal")["drafts"][0]["version"]
        page.unroute("**/api/personal/enqueue")
        page.get_by_role("button", name="Retry", exact=True).click()
        page.wait_for_function("() => app.outbox.length === 0 || app.outbox[0].status === 'failed'")
        self.assertEqual(page.locator(".message.failed").count(), 0)
        rows = self.request("human", "GET", "/api/messages?channel_id=workspace")["messages"]
        self.assertEqual([row["body"] for row in rows], [original])
        personal = self.request("human", "GET", "/api/personal")
        self.assertEqual(personal["drafts"][0]["body"], edited)
        self.assertEqual(personal["drafts"][0]["version"], version)
        self.assertEqual(personal["outbox"], [])
        self.assertEqual(page.locator("#message-input").input_value(), edited)
        self.saved(page)

    def test_failed_clear_notice_disappears_after_successful_save(self) -> None:
        page = self.page()
        page.locator("#message-input").fill("Synthetic saved draft")
        self.saved(page)
        page.route("**/api/personal/draft", lambda route: route.abort("failed"))
        page.get_by_role("button", name="Clear draft", exact=True).click()
        page.locator("dialog").get_by_role("button", name="Clear draft", exact=True).click()
        page.wait_for_function("() => document.querySelector('#notice-text').textContent.includes('not saved yet')")
        page.unroute("**/api/personal/draft")
        page.locator("#message-input").fill("Synthetic recovered draft")
        self.saved(page)
        self.assertTrue(page.locator("#notice").is_hidden())

    def test_private_group_and_cross_channel_search_load_real_multiple_pages(self) -> None:
        private = self.request("human", "POST", "/api/channels", {"title": "Synthetic private history", "members": ["codex"]})["id"]
        group = self.request("human", "POST", "/api/channels", {"title": "Synthetic group history", "members": ["codex", "peer"]})["id"]
        hidden = self.request("human", "POST", "/api/channels", {"title": "Synthetic hidden history", "members": ["outsider"]})["id"]
        self.seed(4)
        self.seed(3, hidden, "outsider")
        rows = self.seed(430, private, "codex")
        self.seed(3, group, "peer")
        page = self.page("codex")
        page.locator("#search-input").fill(f"Synthetic {private} row 425")
        page.locator(".search-message").wait_for()
        page.locator(".search-message").click()
        target = page.locator(f'[data-message-id="{rows[425]["id"]}"].search-highlight')
        target.wait_for()
        self.assertEqual(page.locator("#content-title").inner_text(), "Synthetic private history")
        self.assertEqual(page.evaluate("() => document.activeElement.dataset.messageId"), str(rows[425]["id"]))
        history_requests = [r for r in self.requests if r["path"].startswith("/api/messages?channel_id=" + private)]
        self.assertGreaterEqual(len(history_requests), 3)
        state = self.request("codex", "GET", "/api/state")
        self.assertIn(group, {c["id"] for c in state["channels"]})
        self.assertNotIn(hidden, {c["id"] for c in state["channels"]})
        self.assertEqual(self.request("codex", "GET", f"/api/search?q=Synthetic%20{hidden}%20row")["messages"], [])
        exported = self.request("codex", "GET", "/api/export")
        self.assertNotIn("Synthetic hidden history", json.dumps(exported))
        with self.assertRaises(cli.RequestError):
            self.request("codex", "GET", "/api/messages?channel_id=" + hidden)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.external, [])


    def test_composer_icons_keep_names_keyboard_actions_and_phone_layout(self) -> None:
        page = self.page()
        for ident, name in (("attach-image", "Attach image"), ("emoji-picker", "Emoji"), ("clear-draft", "Clear draft")):
            control = page.locator("#" + ident)
            self.assertEqual(control.get_attribute("aria-label"), name)
            self.assertTrue(control.get_attribute("title"))
            self.assertEqual(control.locator('svg[aria-hidden="true"]').count(), 1)
            self.assertGreaterEqual(control.bounding_box()["width"], 40)
        page.locator("#message-input").fill("icon keyboard draft")
        self.saved(page)
        page.get_by_role("button", name="Emoji", exact=True).focus()
        page.keyboard.press("Enter")
        page.get_by_role("button", name="Thumbs up", exact=True).click()
        page.wait_for_function("() => document.activeElement.id === 'message-input'")
        self.assertIn("👍", page.locator("#message-input").input_value())
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("() => document.querySelector('#app-shell').classList.remove('sidebar-open')")
        self.assertTrue(page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"))
        page.screenshot(path=str(evidence_root() / "composer_task_icons_phone.png"), full_page=True)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.external, [])

    def test_task_filters_combine_reset_preserve_focus_and_private_visibility(self) -> None:
        private = self.request("human", "POST", "/api/channels", {"title": "Visible private tasks", "members": ["codex"]})["id"]
        hidden = self.request("human", "POST", "/api/channels", {"title": "Hidden tasks", "members": ["peer", "outsider"]})["id"]
        def task(title: str, owner: str, status: str, channel: str = "workspace", actor: str = "human") -> dict:
            value = self.request(actor, "POST", "/api/tasks", {"channel_id": channel, "title": title, "body": "Synthetic detail " + title, "owner_id": owner})
            if status != "open":
                value = self.request(actor, "POST", f"/api/tasks/{value['id']}/status", {"status": status, "version": 1})
            return value
        task("Alpha open", "codex", "open")
        task("Alpha working", "human", "working")
        task("Beta blocked", "codex", "blocked")
        task("Completed task", "human", "done")
        task("Private alpha", "codex", "open", private)
        task("Hidden sentinel", "peer", "open", hidden, "peer")
        page = self.page("codex")
        page.locator('[data-view="tasks"]').click()
        self.assertEqual(page.locator(".record-card").count(), 5)
        self.assertEqual(page.locator(".task-result-count").inner_text(), "5 of 5 tasks")
        for status, count in (("open", 2), ("working", 1), ("blocked", 1), ("done", 1)):
            page.get_by_label("Filter by status", exact=True).select_option(status)
            self.assertEqual(page.locator(".record-card").count(), count)
        page.get_by_label("Filter by status", exact=True).select_option("open")
        page.get_by_label("Filter by owner", exact=True).select_option("codex")
        search = page.get_by_role("searchbox", name="Search tasks", exact=True)
        search.press_sequentially("PRIVATE")
        self.assertEqual(page.evaluate("() => document.activeElement.getAttribute('aria-label')"), "Search tasks")
        self.assertEqual(page.locator(".record-card h3").all_text_contents(), ["Private alpha"])
        search.fill("<script>")
        self.assertEqual(page.locator(".record-card").count(), 0)
        self.assertTrue(page.get_by_text("No tasks match these filters.", exact=True).is_visible())
        page.get_by_role("button", name="Reset filters", exact=True).click()
        self.assertEqual(page.locator(".record-card").count(), 5)
        page.get_by_label("Filter by conversation", exact=True).select_option(private)
        self.assertEqual(page.locator(".record-card h3").all_text_contents(), ["Private alpha"])
        page.get_by_role("button", name="Reset filters", exact=True).click()
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("() => document.querySelector('#app-shell').classList.remove('sidebar-open')")
        self.assertTrue(page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"))
        page.screenshot(path=str(evidence_root() / "composer_task_filters_phone.png"), full_page=True)
        self.assertNotIn("Hidden sentinel", page.locator("#view-content").inner_text())
        page.get_by_label("Filter by status", exact=True).select_option("open")
        page.locator(".record-card").filter(has_text="Alpha open").get_by_role("combobox", name="Status of Alpha open", exact=True).select_option("done")
        page.locator(".record-card").filter(has_text="Alpha open").wait_for(state="detached")
        self.assertEqual(page.locator(".record-card h3").all_text_contents(), ["Private alpha"])
        self.assertEqual(self.errors, [])
        self.assertEqual(self.external, [])


    def test_task_planning_dates_versions_private_permissions_and_restart(self) -> None:
        private = self.request("human", "POST", "/api/channels", {"title": "Planning private", "members": ["codex"]})["id"]
        task = self.request("human", "POST", "/api/tasks", {"channel_id": private, "title": "Real task planning", "owner_id": "codex", "priority": "high", "due_date": "2026-10-16"})
        self.assertEqual((task["priority"], task["due_date"]), ("high", "2026-10-16"))
        path = f"/api/tasks/{task['id']}/details"
        details = {"priority": "urgent", "due_date": "2028-02-29", "version": task["version"]}
        with self.assertRaises(cli.RequestError) as denied:
            self.request("outsider", "POST", path, details)
        self.assertEqual(denied.exception.status, 404)
        for change in ({"due_date": "2026-02-29"}, {"due_date": "20261016"}, {"due_date": True}, {"priority": "invalid"}, {"owner_id": "outsider"}):
            with self.assertRaises(cli.RequestError) as invalid:
                self.request("codex", "POST", path, {**details, **change})
            self.assertEqual(invalid.exception.status, 400)
        updated = self.request("codex", "POST", path, details)
        self.assertEqual(updated["version"], 2)
        with self.assertRaises(cli.RequestError) as stale:
            self.request("codex", "POST", path, details)
        self.assertEqual(stale.exception.status, 409)
        rejected = self.request("codex", "POST", f"/api/tasks/{task['id']}/status", {"status": "rejected", "version": 2})
        self.assertEqual((rejected["status"], rejected["priority"], rejected["due_date"]), ("rejected", "urgent", "2028-02-29"))
        self.cleanup_server()
        self.server = backend.make_server(self.config, 0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        rows = self.request("codex", "GET", "/api/state")["tasks"]
        self.assertEqual(rows, [rejected])
        cleared = self.request("codex", "POST", path, {"priority": "normal", "due_date": None, "version": 3})
        self.assertIsNone(cleared["due_date"])

    def test_task_planning_migrates_old_schema_without_changing_original_task(self) -> None:
        import sqlite3

        task = self.request("human", "POST", "/api/tasks", {"channel_id": "workspace", "title": "Legacy unchanged", "owner_id": "codex"})
        database = self.server.store.db_path
        self.cleanup_server()
        with sqlite3.connect(database) as db:
            db.execute("ALTER TABLE tasks DROP COLUMN priority")
            db.execute("ALTER TABLE tasks DROP COLUMN due_date")
        self.server = backend.make_server(self.config, 0)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        migrated = self.request("human", "GET", "/api/state")["tasks"][0]
        self.assertEqual(migrated, task)

    def test_task_title_and_description_edit_validated_versioned_and_old_payload_kept(self) -> None:
        private = self.request("human", "POST", "/api/channels", {"title": "Text edit private", "members": ["codex"]})["id"]
        task = self.request("human", "POST", "/api/tasks", {"channel_id": private, "title": "Synthetic old title", "body": "Synthetic old details", "owner_id": "codex"})
        path = f"/api/tasks/{task['id']}/details"
        base = {"priority": task["priority"], "due_date": None, "version": task["version"]}
        for change in ({"title": ""}, {"title": "   "}, {"title": "x" * 201}, {"title": 7}, {"body": 7}, {"body": "x" * 16001}, {"title": "a\x00b"}, {"owner_id": "codex"}):
            with self.assertRaises(cli.RequestError) as invalid:
                self.request("codex", "POST", path, {**base, "title": "Synthetic new title", **change})
            self.assertEqual(invalid.exception.status, 400, change)
        with self.assertRaises(cli.RequestError) as denied:
            self.request("outsider", "POST", path, {**base, "title": "Synthetic new title"})
        self.assertEqual(denied.exception.status, 404)
        updated = self.request("codex", "POST", path, {**base, "title": "Synthetic new title", "body": "Synthetic new details"})
        self.assertEqual((updated["title"], updated["body"], updated["version"]), ("Synthetic new title", "Synthetic new details", 2))
        with self.assertRaises(cli.RequestError) as stale:
            self.request("codex", "POST", path, {**base, "title": "Synthetic stale title"})
        self.assertEqual(stale.exception.status, 409)
        # an old client sends priority, due date and version only: the text stays as it is
        planned = self.request("codex", "POST", path, {"priority": "high", "due_date": None, "version": 2})
        self.assertEqual((planned["title"], planned["body"], planned["priority"]), ("Synthetic new title", "Synthetic new details", "high"))
        cleared = self.request("codex", "POST", path, {"priority": "high", "due_date": None, "version": 3, "title": "Synthetic new title", "body": ""})
        self.assertEqual(cleared["body"], "")
        self.assertEqual(self.request("codex", "GET", "/api/state")["tasks"], [cleared])

    def test_task_move_needs_both_memberships_an_owner_in_the_target_and_the_current_version(self) -> None:
        source = self.request("human", "POST", "/api/channels", {"title": "Move source", "members": ["codex"]})["id"]
        narrow = self.request("human", "POST", "/api/channels", {"title": "Move narrow", "members": ["peer"]})["id"]
        task = self.request("human", "POST", "/api/tasks", {"channel_id": source, "title": "Synthetic movable", "body": "Synthetic details", "owner_id": "codex", "priority": "high"})
        path = f"/api/tasks/{task['id']}/move"
        move = {"channel_id": "workspace", "owner_id": "codex", "version": task["version"]}
        # outsider is not in the source; codex is in the source but not in the human-only target
        for actor, body in (("outsider", move), ("codex", {**move, "channel_id": narrow})):
            with self.assertRaises(cli.RequestError) as hidden:
                self.request(actor, "POST", path, body)
            self.assertEqual(hidden.exception.status, 404, (actor, body))
        for change in ({"channel_id": narrow}, {"channel_id": source}, {"owner_id": 7}, {"owner_id": "nobody"}, {"channel_id": 7}, {"title": "Synthetic other"}):
            with self.assertRaises(cli.RequestError) as invalid:
                self.request("human", "POST", path, {**move, **change})
            self.assertEqual(invalid.exception.status, 400, change)
        self.assertEqual(self.request("outsider", "GET", "/api/state")["tasks"], [])
        moved = self.request("human", "POST", path, move)
        kept = ("id", "title", "body", "status", "priority", "due_date")
        self.assertEqual((moved["channel_id"], moved["owner_id"], moved["version"]), ("workspace", "codex", 2))
        self.assertEqual({key: moved[key] for key in kept}, {key: task[key] for key in kept})
        with self.assertRaises(cli.RequestError) as stale:
            self.request("human", "POST", path, move)
        self.assertEqual(stale.exception.status, 409)
        self.assertEqual(self.request("outsider", "GET", "/api/state")["tasks"], [moved])
        reassigned = self.request("human", "POST", path, {"channel_id": "workspace", "owner_id": "peer", "version": 2})
        self.assertEqual((reassigned["owner_id"], reassigned["version"]), ("peer", 3))

    def test_task_move_in_the_browser_warns_and_moves_to_the_chosen_conversation(self) -> None:
        source = self.request("human", "POST", "/api/channels", {"title": "Move source", "members": ["codex"]})["id"]
        task = self.request("human", "POST", "/api/tasks", {"channel_id": source, "title": "Synthetic browser move", "owner_id": "codex"})
        page = self.page()
        page.locator('[data-view="tasks"]').click()
        page.locator(".record-card").filter(has_text="Synthetic browser move").get_by_role("button", name="Move task", exact=True).click()
        dialog = page.get_by_role("dialog")
        self.assertEqual(dialog.get_by_label("Conversation", exact=True).input_value(), source)
        self.assertTrue(dialog.get_by_text("Everyone in the chosen conversation will see this task.", exact=True).is_visible())
        dialog.get_by_label("Conversation", exact=True).select_option("workspace")
        self.assertEqual(dialog.get_by_label("Owner", exact=True).input_value(), "codex")
        dialog.get_by_role("button", name="Move task", exact=True).click()
        dialog.wait_for(state="detached")
        moved = [row for row in self.request("codex", "GET", "/api/state")["tasks"] if row["id"] == task["id"]]
        self.assertEqual([(row["channel_id"], row["owner_id"], row["version"]) for row in moved], [("workspace", "codex", 2)])
        self.assertEqual(self.errors, [])
        self.assertEqual(self.external, [])

    def test_task_title_and_description_edit_in_the_browser(self) -> None:
        self.request("human", "POST", "/api/tasks", {"channel_id": "workspace", "title": "Synthetic old title", "body": "Synthetic old details", "owner_id": "codex"})
        page = self.page()
        page.locator('[data-view="tasks"]').click()
        page.get_by_role("button", name="Edit details", exact=True).click()
        self.assertEqual(page.get_by_label("Task title", exact=True).input_value(), "Synthetic old title")
        self.assertEqual(page.get_by_label("Details", exact=True).input_value(), "Synthetic old details")
        page.get_by_label("Task title", exact=True).fill("Synthetic new title")
        page.get_by_label("Details", exact=True).fill("Synthetic new details")
        page.get_by_role("button", name="Save details", exact=True).click()
        page.get_by_role("heading", name="Synthetic new title", exact=True).wait_for()
        self.assertTrue(page.get_by_text("Synthetic new details", exact=True).is_visible())
        self.assertEqual(self.errors, [])
        self.assertEqual(self.external, [])

    def test_task_planning_browser_create_edit_reject_and_clear_due_date(self) -> None:
        page = self.page()
        page.locator('[data-view="tasks"]').click()
        page.get_by_role("button", name="+ New task", exact=True).click()
        page.get_by_label("Task title", exact=True).fill("Synthetic planning browser")
        page.get_by_label("Priority", exact=True).select_option("high")
        page.get_by_label("Due date (optional)", exact=True).fill("2026-10-16")
        page.get_by_role("button", name="Create task", exact=True).click()
        page.get_by_text("High priority", exact=True).wait_for()
        self.assertTrue(page.get_by_text("Due 16.10.2026", exact=True).is_visible())
        page.get_by_role("button", name="Edit details", exact=True).click()
        page.get_by_label("Priority", exact=True).select_option("urgent")
        page.get_by_label("Due date (optional)", exact=True).fill("")
        page.get_by_role("button", name="Save details", exact=True).click()
        page.get_by_text("Urgent priority", exact=True).wait_for()
        self.assertEqual(page.get_by_text("Due 16.10.2026", exact=True).count(), 0)
        page.get_by_role("combobox", name="Status of Synthetic planning browser", exact=True).select_option("rejected")
        page.locator(".status-rejected").wait_for()
        page.get_by_label("Filter by status", exact=True).select_option("rejected")
        self.assertEqual(page.locator(".record-card").count(), 1)
        self.assertEqual(page.locator(".task-status-control").input_value(), "rejected")
        page.screenshot(path=str(evidence_root() / "task_planning_desktop.png"), full_page=True)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.external, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
