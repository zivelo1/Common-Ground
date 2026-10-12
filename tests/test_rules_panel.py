"""Shared Rules panel (Collaboration, card "editable general Rules panel"): every participant reads one Rules text,
only a human saves it, a save from an older version is refused, and every saved version is kept across a restart."""
import json
import unittest
from pathlib import Path

import test_backend
from qa_support import temporary_directory

from store import MAX_RULES, MAX_SQLITE_INTEGER


class RulesPanelTests(unittest.TestCase):
    # Reuse the backend server fixture's methods. Never bind its TestCase class to a module name here: the loader
    # collects every TestCase class in this module's namespace, so its tests would run a second time.
    start = test_backend.BackendTests.start
    stop = test_backend.BackendTests.stop
    tearDown = test_backend.BackendTests.tearDown
    call = test_backend.BackendTests.call
    ok = test_backend.BackendTests.ok

    def setUp(self):
        self.temp = temporary_directory(self, prefix="rules-test-")
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

    def save(self, body, base, actor="curator"):
        return self.call("POST", "/api/rules", actor, {"body": body, "base_version": base})

    def test_everyone_reads_and_only_a_human_saves(self):
        for viewer in ("curator", "codex"):
            self.assertEqual(self.ok("GET", "/api/rules", viewer), {"current": None, "max_chars": MAX_RULES, "history": []})
        status, result, _ = self.save("Rule #1: maximum efficiency.", 0, actor="codex")
        self.assertEqual(status, 403, result)
        self.assertIsNone(self.ok("GET", "/api/rules", "curator")["current"])
        status, saved, _ = self.save("Rule #1: maximum efficiency.", 0)
        self.assertEqual(status, 200, saved)
        seen = self.ok("GET", "/api/rules", "codex")
        self.assertEqual(seen, saved)
        self.assertEqual({key: seen["current"][key] for key in ("version", "body", "author_id", "author_name")},
                         {"version": 1, "body": "Rule #1: maximum efficiency.", "author_id": "curator", "author_name": "Curator"})

    def test_a_save_from_an_older_version_is_refused_and_every_version_survives_a_restart(self):
        self.assertEqual(self.save("first", 0)[0], 200)
        self.assertEqual(self.save("second", 1)[0], 200)
        status, result, _ = self.save("written from version 1 in another tab", 1)
        self.assertEqual((status, result["error"]), (409, "The Rules changed meanwhile; reload before saving"))
        status, result, _ = self.save("second", 2)
        self.assertEqual((status, result["error"]), (409, "The Rules are unchanged"))
        status, result, _ = self.save("ahead of the server", 5)
        self.assertEqual(status, 409, result)
        self.stop()
        self.start()
        rules = self.ok("GET", "/api/rules", "codex")
        self.assertEqual((rules["current"]["version"], rules["current"]["body"]), (2, "second"))
        self.assertEqual([item["version"] for item in rules["history"]], [2, 1])
        self.assertNotIn("body", rules["history"][0])          # the history lists versions; the text shown is current

    def test_malformed_saves_change_nothing(self):
        cases = {"missing base": {"body": "text"}, "extra field": {"body": "text", "base_version": 0, "author_id": "codex"},
                 "blank body": {"body": "  \n ", "base_version": 0}, "too long": {"body": "x" * (MAX_RULES + 1), "base_version": 0},
                 "NUL": {"body": "a\x00b", "base_version": 0}, "body not text": {"body": 5, "base_version": 0},
                 "negative base": {"body": "text", "base_version": -1}, "base as text": {"body": "text", "base_version": "0"},
                 "base as true": {"body": "text", "base_version": True},
                 "base too large": {"body": "text", "base_version": MAX_SQLITE_INTEGER + 1}}
        for name, body in cases.items():
            with self.subTest(name=name):
                status, result, _ = self.call("POST", "/api/rules", "curator", body)
                self.assertEqual(status, 400, result)
        self.assertEqual(self.ok("GET", "/api/rules", "curator")["history"], [])
        status, result, _ = self.save("x" * MAX_RULES, 0)                    # the limit itself is allowed
        self.assertEqual(status, 200, result)


if __name__ == "__main__":
    unittest.main()
