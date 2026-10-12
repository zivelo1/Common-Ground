"""The Backups view in a real browser against the real local server, on disposable synthetic actors only."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Bind the app under test before the reused fixture module imports `app` by name.
from qa_support import evidence_root, temporary_directory
from test_conversation_real_backend import RealConversationTests

import app  # noqa: F401
import backups
import cli

DESKTOP = {"width": 1440, "height": 1000}
PHONE = {"width": 390, "height": 844}


class BackupBrowserTests(RealConversationTests):
    """Reuses the real-backend browser fixture (server, confined browser, guarded network); adds only backup cases."""

    def open_backups(self, actor: str = "human") -> Any:
        page = self.page(actor)
        page.locator("#backups-nav").click()
        page.locator("#backup-summary").wait_for()
        return page

    def status(self) -> dict[str, Any]:
        return self.request("human", "GET", "/api/backups")

    def screenshot(self, page: Any, name: str) -> None:
        page.screenshot(path=str(evidence_root() / f"backups_{name}.png"), full_page=True)

    def test_human_sets_the_interval_backs_up_and_the_list_survives_reload(self) -> None:
        page = self.open_backups()
        self.assertEqual(page.locator(".backup-last-success").inner_text(), "No successful backup yet.")
        self.assertIn("due now", page.locator(".backup-next").inner_text())
        page.locator("#backup-interval").fill("5")
        page.locator("#backup-save").click()
        page.wait_for_function("() => app.backups.status?.settings.version === 2")
        self.assertEqual(self.status()["settings"]["interval_days"], 5)
        page.locator("#backup-run").click()
        page.locator(".backup-item").wait_for()
        self.assertIn("manual", page.locator(".backup-last-success").inner_text())
        self.assertIn("Next scheduled backup", page.locator(".backup-next").inner_text())
        name = self.status()["backups"][0]["name"]
        self.assertIn(name, page.locator(".backup-item").inner_text())
        self.screenshot(page, "desktop")
        page.reload()
        page.locator("#app-shell").wait_for(state="visible")
        page.locator("#backups-nav").click()
        page.locator(".backup-item").wait_for()
        self.assertEqual(page.locator(".backup-item").count(), 1)
        self.assertEqual(page.locator("#backup-interval").input_value(), "5")
        self.assertEqual(self.errors, [])
        self.assertEqual(self.external, [])

    def test_agents_have_no_backups_view_or_access(self) -> None:
        page = self.page("peer")
        self.assertTrue(page.locator("#backups-nav").is_hidden())
        with self.assertRaises(cli.RequestError) as caught:
            self.request("peer", "GET", "/api/backups")
        self.assertEqual(caught.exception.status, 403)

    def test_invalid_interval_is_refused_before_any_request(self) -> None:
        page = self.open_backups()
        before = len(self.requests)
        page.locator("#backup-interval").fill("0")
        page.locator("#backup-save").click()
        page.locator("#backup-error").wait_for()
        self.assertIn("Enter whole days", page.locator("#backup-error").inner_text())
        self.assertFalse(any(row["path"] == "/api/backups/settings" for row in self.requests[before:]))
        self.assertEqual(self.status()["settings"]["version"], 1)

    def test_a_failed_backup_is_shown_recorded_and_then_recovered(self) -> None:
        state = self.folder / "instance_data"
        folder = backups.directory(state)
        elsewhere = Path(temporary_directory(self, prefix="backup-browser-elsewhere-").name)
        folder.rmdir()
        folder.symlink_to(elsewhere, target_is_directory=True)  # synthetic fixture: an unusable backup folder
        page = self.open_backups()
        page.locator(".backup-folder-error").wait_for()
        page.locator("#backup-run").click()
        page.locator("#backup-error").wait_for()
        self.assertIn("Backup failed: The backup folder must not be a symbolic link", page.locator("#backup-error").inner_text())
        page.locator(".backup-last-error").wait_for()
        self.assertIn("Last attempt failed", page.locator(".backup-last-error").inner_text())
        self.assertEqual(page.locator(".backup-last-error").get_attribute("role"), "alert")
        self.screenshot(page, "failure")
        folder.unlink()
        page.locator("#backup-run").click()
        page.locator(".backup-item").wait_for()
        self.assertIn("Earlier failed attempt", page.locator(".backup-last-error").inner_text())
        self.assertEqual(page.locator(".backup-folder-error").count(), 0)

    def test_phone_width_keeps_every_control_visible_without_sideways_scrolling(self) -> None:
        page = self.page()
        page.set_viewport_size(PHONE)
        # The real phone flow: the navigation drawer opens from the menu button and closes after choosing a view.
        page.wait_for_function("() => document.querySelector('#sidebar').getBoundingClientRect().right <= 0")
        page.locator("#open-sidebar").click()
        page.locator("#backups-nav").click()
        page.locator("#backup-summary").wait_for()
        page.wait_for_function("() => document.querySelector('#sidebar').getBoundingClientRect().right <= 0")
        self.assertTrue(page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth"), "the page scrolls sideways")
        board = page.locator(".backup-board").bounding_box()
        for selector in ("#backup-interval", "#backup-save", "#backup-run"):
            page.locator(selector).scroll_into_view_if_needed()
            box = page.locator(selector).bounding_box()
            self.assertIsNotNone(box, selector)
            self.assertGreaterEqual(box["x"], board["x"], selector)
            self.assertLessEqual(box["x"] + box["width"], min(board["x"] + board["width"], PHONE["width"]) + 0.5, selector)
            # Nothing covers the control: the element at its centre is the control itself.
            self.assertTrue(page.evaluate(
                "([selector, x, y]) => document.elementFromPoint(x, y)?.closest(selector) !== null",
                [selector, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2]), selector)
        self.screenshot(page, "phone")
        page.set_viewport_size(DESKTOP)

def load_tests(loader: unittest.TestLoader, standard_tests: unittest.TestSuite, pattern: str | None) -> unittest.TestSuite:
    # Only the backup cases: the imported fixture's own conversation tests run in their own module.
    names = sorted(name for name in vars(BackupBrowserTests) if name.startswith("test_"))
    return unittest.TestSuite(BackupBrowserTests(name) for name in names)


if __name__ == "__main__":
    unittest.main()
