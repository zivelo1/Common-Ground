"""The shared Rules in every brief (unit 12, on unit 11's Rules panel): an agent reads the brief when it joins, so the
current Rules travel with it, whole, for the workspace view and for each conversation; before any save they are null."""
import json
import unittest
from pathlib import Path

import test_backend
from qa_support import temporary_directory

from store import MAX_BRIEF_TEXT


class RulesInBriefTests(unittest.TestCase):
    # Reuse the backend server fixture's methods. Never bind its TestCase class to a module name here: the loader
    # collects every TestCase class in this module's namespace, so its tests would run a second time.
    start = test_backend.BackendTests.start
    stop = test_backend.BackendTests.stop
    tearDown = test_backend.BackendTests.tearDown
    call = test_backend.BackendTests.call
    ok = test_backend.BackendTests.ok

    def setUp(self):
        self.temp = temporary_directory(self, prefix="rules-brief-test-")
        self.root = Path(self.temp.name)
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"title": "Rules Board", "project_label": "Generic", "state_dir": "state",
                                           "participants": [{"id": "curator", "name": "Curator", "kind": "human"},
                                                            {"id": "codex", "name": "Codex", "kind": "agent"}]}))
        self.server = None
        self.thread = None
        self.addCleanup(self.stop)
        self.start()
        self.tokens = {ident: json.loads((self.root / "state" / "credentials" / f"{ident}.json").read_text())["token"]
                       for ident in ("curator", "codex")}

    def test_every_brief_carries_the_current_rules_whole(self):
        for path in ("/api/brief", "/api/brief?channel_id=workspace"):
            self.assertIsNone(self.ok("GET", path, "codex")["rules"])
        long_rules = "# Rule #1\n" + "Maximum efficiency with high quality. " * (MAX_BRIEF_TEXT // 20)
        self.assertGreater(len(long_rules), MAX_BRIEF_TEXT)          # longer than any other brief text field may be
        self.ok("POST", "/api/rules", "curator", {"body": "first", "base_version": 0})
        saved = self.ok("POST", "/api/rules", "curator", {"body": long_rules, "base_version": 1})["current"]
        for viewer in ("codex", "curator"):
            for path in ("/api/brief", "/api/brief?channel_id=workspace"):
                with self.subTest(viewer=viewer, path=path):
                    self.assertEqual(self.ok("GET", path, viewer)["rules"], saved)
        self.assertEqual(saved["body"], long_rules)
        self.assertEqual((saved["version"], saved["author_name"]), (2, "Curator"))


if __name__ == "__main__":
    unittest.main()
