"""Browser checks for task file claims, release actions, and permission presentation."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_file_claims
from qa_support import confined_playwright, evidence_root

import app


class FileClaimBrowserTests(unittest.TestCase):
    # Borrow the backend case's fixture methods (never subclass: its 21 tests would run again here).
    setUp_claims = test_file_claims.FileClaimTests.setUp
    setUp_backend = test_file_claims.FileClaimTests.setUp_backend
    configure = test_file_claims.FileClaimTests.configure
    start = test_file_claims.FileClaimTests.start
    stop = test_file_claims.FileClaimTests.stop
    tearDown = test_file_claims.FileClaimTests.tearDown
    call = test_file_claims.FileClaimTests.call
    ok = test_file_claims.FileClaimTests.ok
    task = test_file_claims.FileClaimTests.task

    def setUp(self):
        self.setUp_claims()
        self.errors = []
        self.external = []
        self.paths = []
        self.playwright = confined_playwright()
        driver = self.playwright.__enter__()
        self.addCleanup(self.playwright.__exit__, None, None, None)
        self.browser = driver.chromium.launch(headless=True)
        self.addCleanup(self.browser.close)
        self.addCleanup(self.save_report)

    def save_report(self):
        (evidence_root() / f"file_claims_{self._testMethodName}.json").write_text(json.dumps(
            {"page_errors": self.errors, "external_requests": self.external, "requests": self.paths}, indent=1) + "\n")
        self.assertEqual(self.errors, [])
        self.assertEqual(self.external, [])

    def page(self, actor="a"):
        context = self.browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on("pageerror", lambda error: self.errors.append(str(error)))

        def guard(route):
            request = route.request
            if request.url.startswith(self.server.origin + "/"):
                if "/api/" in request.url:
                    self.paths.append({"method": request.method, "path": request.url.removeprefix(self.server.origin)})
                route.continue_()
            else:
                self.external.append(request.url)
                route.abort()

        context.route("**/*", guard)
        page.goto(self.server.origin)
        page.locator("#access-token").fill(self.tokens[actor])
        page.locator("#login-submit").click()
        page.locator("#app-shell").wait_for()
        page.locator('[data-view="tasks"]').click()
        page.get_by_role("button", name="File ownership", exact=True).first.click()
        if actor == "a":
            page.locator("dialog[open] #dialog-claim-path").wait_for()
        else:
            page.get_by_text("Only this task’s owner can claim its files.", exact=True).wait_for()
        return page

    def test_browser_owner_claim_release_and_history(self):
        task = self.task()
        page = self.page()
        page.locator("#dialog-claim-path").fill("work/browser.txt")
        page.locator("#file-claim-submit").click()
        page.locator("[data-claim-id]").wait_for()
        page.locator("[data-claim-id] input").fill("Synthetic review complete")
        page.get_by_role("button", name="Release ownership", exact=True).click()
        page.get_by_role("button", name="Ownership history", exact=True).click()
        page.get_by_text("Synthetic review complete", exact=False).wait_for()
        rows = self.ok("GET", "/api/file-claims?task_id=" + task["id"], "a")["claims"]
        self.assertEqual([row["status"] for row in rows], ["released"])

    def test_browser_actual_postcommit_loss_locks_same_claim_retry(self):
        task = self.task()
        page = self.page()
        original = app.Handler._send
        dropped = []

        def send(handler, status, payload, **kwargs):
            # The claim is committed, then its answer is lost once: the browser must not create a second claim.
            if handler.path == "/api/file-claims" and handler.command == "POST" and status == 200 and not dropped:
                dropped.append(True)
                handler.close_connection = True
                return None
            return original(handler, status, payload, **kwargs)

        page.locator("#dialog-claim-path").fill("work/lost.txt")
        with patch.object(app.Handler, "_send", send):
            page.locator("#file-claim-submit").click()
            page.locator("#file-claim-error").wait_for()
        self.assertEqual(len(dropped), 1)
        self.assertTrue(page.locator("#dialog-claim-path").is_disabled())
        self.assertEqual(page.locator("#dialog-claim-path").input_value(), "work/lost.txt")
        page.get_by_role("button", name="Confirm same claim", exact=True).click()
        page.locator("[data-claim-id]").wait_for()
        self.assertEqual(len(self.ok("GET", "/api/file-claims?task_id=" + task["id"], "a")["claims"]), 1)

    def test_browser_cancel_mobile_keyboard_and_other_owner(self):
        task = self.task()
        page = self.page()
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#dialog-claim-path").fill("work/unsent.txt")
        page.locator("#dialog-claim-kind").focus()
        self.assertEqual(page.evaluate("() => document.activeElement.id"), "dialog-claim-kind")
        self.assertTrue(page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"))
        page.screenshot(path=str(evidence_root() / "file_claims_phone.png"))
        page.keyboard.press("Escape")
        page.locator("dialog[open]").wait_for(state="detached")
        self.assertEqual(self.ok("GET", "/api/file-claims?task_id=" + task["id"], "a")["claims"], [])
        other = self.page("b")
        self.assertEqual(other.locator("#file-claim-submit").count(), 0)


if __name__ == "__main__":
    unittest.main()
