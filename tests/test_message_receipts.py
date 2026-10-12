"""Delivery and read receipts (): for each current member other than the author, when the server first gave
them the message's text and whether their read marker has passed it. Built on records the app already keeps
(message_retrievals, read_markers); the receipts call returns no body and records nothing."""
import json
import sqlite3
import unittest
from pathlib import Path

import test_backend
from qa_support import temporary_directory


class MessageReceiptTests(unittest.TestCase):
    # Reuse the backend server fixture's methods. Never bind its TestCase class to a module name here: the loader
    # collects every TestCase class in this module's namespace, so its tests would run a second time.
    start = test_backend.BackendTests.start
    stop = test_backend.BackendTests.stop
    tearDown = test_backend.BackendTests.tearDown
    call = test_backend.BackendTests.call
    ok = test_backend.BackendTests.ok
    message = test_backend.BackendTests.message

    def setUp(self):
        self.temp = temporary_directory(self, prefix="message-receipts-test-")
        self.root = Path(self.temp.name)
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"title": "Receipts Board", "project_label": "Generic", "state_dir": "state",
                                           "participants": [{"id": "curator", "name": "Curator", "kind": "human"},
                                                            {"id": "codex", "name": "Codex", "kind": "agent"},
                                                            {"id": "atlas", "name": "Atlas", "kind": "agent"}]}))
        self.server = None
        self.thread = None
        self.addCleanup(self.stop)
        self.start()
        self.tokens = {ident: json.loads((self.root / "state" / "credentials" / f"{ident}.json").read_text())["token"]
                       for ident in ("curator", "codex", "atlas")}

    def receipts(self, ident, actor="codex"):
        return self.ok("GET", f"/api/messages/{ident}/receipts", actor)

    def by_name(self, result):
        return {person["name"]: (person["received_at"] is not None, person["read"]) for person in result["recipients"]}

    def test_receipts_follow_retrieval_and_the_read_marker_without_returning_the_body(self):
        sent = self.message("codex", text="private wording 7f3a", client="m1")
        first = self.receipts(sent["id"])
        self.assertTrue(first["received_known"])
        self.assertEqual(self.by_name(first), {"Atlas": (False, False), "Curator": (False, False)})   # the author is not listed
        self.assertNotIn("private wording 7f3a", json.dumps(first))
        self.receipts(sent["id"], actor="atlas")                     # asking for receipts is not receiving the message
        self.assertEqual(self.by_name(self.receipts(sent["id"])), {"Atlas": (False, False), "Curator": (False, False)})
        self.ok("GET", "/api/messages?channel_id=workspace", "curator")
        self.assertEqual(self.by_name(self.receipts(sent["id"])), {"Atlas": (False, False), "Curator": (True, False)})
        self.ok("POST", "/api/read", "curator", {"channel_id": "workspace", "message_id": sent["id"]})
        self.assertEqual(self.by_name(self.receipts(sent["id"])), {"Atlas": (False, False), "Curator": (True, True)})
        later = self.message("codex", text="a later message", client="m2")
        self.ok("POST", "/api/read", "atlas", {"channel_id": "workspace", "message_id": later["id"]})
        self.assertEqual(self.by_name(self.receipts(sent["id"])), {"Atlas": (False, True), "Curator": (True, True)},
                         "a read marker past the message counts as read, even without a recorded retrieval")
        received = {person["name"]: person["received_at"] for person in self.receipts(sent["id"])["recipients"]}
        self.ok("GET", "/api/search?q=wording", "curator")              # a second delivery keeps the first time
        self.assertEqual(received["Curator"], {person["name"]: person["received_at"]
                                           for person in self.receipts(sent["id"])["recipients"]}["Curator"])

    def test_receipts_stay_inside_the_conversation_and_refuse_bad_ids(self):
        private = self.ok("POST", "/api/channels", "curator", {"title": "Curator and Codex", "members": ["curator", "codex"]})
        sent = self.message("codex", channel=private["id"], text="between two", client="p1")
        self.assertEqual(self.by_name(self.receipts(sent["id"])), {"Curator": (False, False)})
        for actor, ident, expected in (("atlas", sent["id"], 404), ("codex", 999_999, 404), ("codex", 0, 400),
                                       ("codex", 9_999_999_999_999_999_999, 400)):
            status, result, _ = self.call("GET", f"/api/messages/{ident}/receipts", actor)
            self.assertEqual(status, expected, (actor, ident, result))
        status, _, _ = self.call("GET", f"/api/messages/{sent['id']}/receipts", None)
        self.assertEqual(status, 401)

    def test_the_state_carries_read_markers_only_for_the_members_own_conversations(self):
        private = self.ok("POST", "/api/channels", "curator", {"title": "Curator and Codex", "members": ["curator", "codex"]})
        shared = self.message("codex", text="shared", client="s1")
        secret = self.message("codex", channel=private["id"], text="between two", client="s2")
        self.ok("POST", "/api/read", "curator", {"channel_id": "workspace", "message_id": shared["id"]})
        self.ok("POST", "/api/read", "curator", {"channel_id": private["id"], "message_id": secret["id"]})
        markers = {channel["id"]: channel["read_markers"] for channel in self.ok("GET", "/api/state", "codex")["channels"]}
        self.assertEqual(markers, {"workspace": {"curator": shared["id"]}, private["id"]: {"curator": secret["id"]}})
        atlas_view = {channel["id"]: channel["read_markers"] for channel in self.ok("GET", "/api/state", "atlas")["channels"]}
        self.assertEqual(atlas_view, {"workspace": {"curator": shared["id"]}})       # the private conversation stays hidden

    def test_an_older_message_says_its_receive_time_is_unknown(self):
        sent = self.message("codex", text="older", client="o1")
        self.stop()
        with sqlite3.connect(self.root / "state" / "collaboration.sqlite3") as db:   # the test's own disposable database
            db.execute("DELETE FROM message_edit_state WHERE message_id=?", (sent["id"],))
        self.start()                                               # the startup step marks it as an older message
        self.ok("GET", "/api/messages?channel_id=workspace", "curator")
        result = self.receipts(sent["id"])
        self.assertFalse(result["received_known"])
        self.assertEqual(self.by_name(result), {"Atlas": (False, False), "Curator": (False, False)})


if __name__ == "__main__":
    unittest.main()
