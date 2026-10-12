"""Edit a message only until another participant has it (Collaboration; Codex's 7 October design, ported
as unit 13): the author may edit a message nobody else has retrieved or acknowledged. Any delivery of the body to
another participant ends editing: the conversation, search, the brief, the export, saved messages, or a read marker.
The original text and every edit are kept; a retried edit is never applied twice."""
import json
import sqlite3
import unittest
from pathlib import Path

import test_backend
from qa_support import temporary_directory


class MessageEditTests(unittest.TestCase):
    # Reuse the backend server fixture's methods. Never bind its TestCase class to a module name here: the loader
    # collects every TestCase class in this module's namespace, so its tests would run a second time.
    start = test_backend.BackendTests.start
    stop = test_backend.BackendTests.stop
    tearDown = test_backend.BackendTests.tearDown
    call = test_backend.BackendTests.call
    ok = test_backend.BackendTests.ok
    message = test_backend.BackendTests.message

    def setUp(self):
        self.temp = temporary_directory(self, prefix="message-edit-test-")
        self.root = Path(self.temp.name)
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"title": "Edit Board", "project_label": "Generic", "state_dir": "state",
                                           "participants": [{"id": "curator", "name": "Curator", "kind": "human"},
                                                            {"id": "codex", "name": "Codex", "kind": "agent"}]}))
        self.server = None
        self.thread = None
        self.addCleanup(self.stop)
        self.start()
        self.tokens = {ident: json.loads((self.root / "state" / "credentials" / f"{ident}.json").read_text())["token"]
                       for ident in ("curator", "codex")}

    def edit(self, ident, body, version, edit_id, actor="codex"):
        return self.call("POST", f"/api/messages/{ident}/edit", actor, {"body": body, "version": version, "edit_id": edit_id})

    def test_the_author_edits_before_anyone_else_has_it_and_the_original_is_kept(self):
        sent = self.message("codex", text="draft wording", client="m1")
        self.assertTrue(sent["can_edit"])
        status, result, _ = self.edit(sent["id"], "final wording", 1, "e1")
        self.assertEqual(status, 200, result)
        self.assertEqual((result["message"]["body"], result["message"]["version"], result["replayed"]), ("final wording", 2, False))
        self.assertIsNotNone(result["message"]["edited_at"])
        status, again, _ = self.edit(sent["id"], "final wording", 1, "e1")           # a retry after a lost answer
        self.assertEqual((status, again["replayed"], again["applied_version"]), (200, True, 2))
        history = self.ok("GET", "/api/export", "codex")["message_edit_history"]
        self.assertEqual([(row["body_before"], row["body_after"], row["version_after"]) for row in history],
                         [("draft wording", "final wording", 2)])
        shown = self.ok("GET", "/api/messages?channel_id=workspace", "curator")["messages"]
        self.assertEqual([(row["body"], row["version"], row["can_edit"]) for row in shown], [("final wording", 2, False)])

    def test_any_delivery_to_another_participant_ends_editing(self):
        routes = {"conversation": lambda m: self.ok("GET", "/api/messages?channel_id=workspace", "curator"),
                  "search": lambda m: self.ok("GET", "/api/search?q=wording", "curator"),
                  "brief": lambda m: self.ok("GET", "/api/brief", "curator"),
                  "export": lambda m: self.ok("GET", "/api/export", "curator"),
                  "saved messages": lambda m: (self.ok("POST", "/api/bookmarks", "curator", {"message_id": m, "saved": True}),
                                               self.ok("GET", "/api/bookmarks", "curator")),
                  "read marker": lambda m: self.ok("POST", "/api/read", "curator", {"channel_id": "workspace", "message_id": m})}
        for number, (name, deliver) in enumerate(routes.items()):
            with self.subTest(route=name):
                self.stop()
                self.temp = temporary_directory(self, prefix=f"message-edit-{number}-")
                self.root = Path(self.temp.name)
                self.config = self.root / "config.json"
                self.config.write_text(json.dumps({"title": "Edit Board", "project_label": "Generic", "state_dir": "state",
                                                   "participants": [{"id": "curator", "name": "Curator", "kind": "human"},
                                                                    {"id": "codex", "name": "Codex", "kind": "agent"}]}))
                self.start()
                self.tokens = {ident: json.loads((self.root / "state" / "credentials" / f"{ident}.json").read_text())["token"]
                               for ident in ("curator", "codex")}
                sent = self.message("codex", text="first wording", client=f"m-{number}")
                deliver(sent["id"])
                status, result, _ = self.edit(sent["id"], "second wording", 1, f"e-{number}")
                self.assertEqual(status, 409, result)
                own = self.ok("GET", "/api/messages?channel_id=workspace", "codex")["messages"]
                self.assertEqual([(row["body"], row["can_edit"]) for row in own], [("first wording", False)])

    def test_only_the_author_edits_and_malformed_or_stale_edits_change_nothing(self):
        sent = self.message("codex", text="original", client="m1")
        cases = [(("changed", 1, "x1", "curator"), 403), (("original", 1, "x2", "codex"), 400), (("changed", 2, "x3", "codex"), 409),
                 (("changed", 0, "x4", "codex"), 400), (("", 1, "x5", "codex"), 400)]
        for (body, version, edit_id, actor), expected in cases:
            with self.subTest(edit_id=edit_id):
                status, result, _ = self.edit(sent["id"], body, version, edit_id, actor=actor)
                self.assertEqual(status, expected, result)
        status, result, _ = self.call("POST", f"/api/messages/{sent['id']}/edit", "codex", {"body": "changed", "version": 1,
                                                                                         "edit_id": "x6", "author_id": "curator"})
        self.assertEqual(status, 400, result)
        own = self.ok("GET", "/api/messages?channel_id=workspace", "codex")["messages"]
        self.assertEqual([(row["body"], row["version"]) for row in own], [("original", 1)])

    def test_a_message_from_before_editing_existed_is_never_editable(self):
        sent = self.message("codex", text="old message", client="m1")
        self.stop()
        with sqlite3.connect(self.root / "state" / "collaboration.sqlite3") as db:   # the test's own disposable database
            db.execute("DELETE FROM message_edit_state WHERE message_id=?", (sent["id"],))
        self.start()
        status, result, _ = self.edit(sent["id"], "rewritten", 1, "e1")
        self.assertEqual(status, 409, result)


if __name__ == "__main__":
    unittest.main()
