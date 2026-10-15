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

    def test_removed_recipient_is_not_pending_and_restore_preserves_history(self):
        sent = self.message("codex", text="original survives", client="inactive-receipt")
        self.ok("GET", "/api/messages?channel_id=workspace", "atlas")
        before = self.receipts(sent["id"])
        self.ok("POST", "/api/participants/remove", "curator",
                {"id": "atlas", "seen_name": "Atlas", "reason": "Disposable receipt test"})
        self.assertEqual(set(self.by_name(self.receipts(sent["id"]))), {"Curator"})
        self.assertIn("atlas", next(c for c in self.ok("GET", "/api/state", "codex")["channels"]
                                   if c["id"] == "workspace")["members"])
        self.ok("POST", "/api/participants/restore", "curator",
                {"id": "atlas", "seen_name": "Atlas", "reason": "Disposable receipt test"})
        self.assertEqual(self.receipts(sent["id"]), before)
        self.assertEqual(self.ok("GET", "/api/messages?channel_id=workspace", "codex")["messages"][0]["body"],
                         "original survives")

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

    # An agent has no screen, so the messages its own tool fetches from the conversation count as read. The rest is
    # not reading: the channel list, the brief, search, the export and saved messages.
    def markers(self, actor="curator", channel="workspace"):
        return next(c["read_markers"] for c in self.ok("GET", "/api/state", actor)["channels"] if c["id"] == channel)

    def test_an_agents_own_fetch_of_the_conversation_is_its_read(self):
        first = self.message("curator", text="please check", client="f1")
        last = self.message("curator", text="and this", client="f2")
        self.ok("GET", "/api/messages?channel_id=workspace", "codex")
        self.assertEqual(self.markers(), {"codex": last["id"]})
        self.assertEqual(self.by_name(self.receipts(first["id"], actor="curator")),
                         {"Codex": (True, True), "Atlas": (False, False)})
        self.assertEqual(next(c["unread"] for c in self.ok("GET", "/api/state", "codex")["channels"]), 0)
        self.message("codex", text="done", client="f3")
        self.ok("GET", "/api/messages?channel_id=workspace", "curator")      # a person's fetch is not their reading
        self.assertNotIn("curator", self.markers(actor="codex"))

    def test_a_fetch_with_nothing_new_changes_nothing_and_the_next_one_moves_on(self):
        sent = self.message("curator", text="one", client="n1")
        self.ok("GET", "/api/messages?channel_id=workspace", "atlas")
        self.ok("GET", f"/api/messages?channel_id=workspace&after={sent['id']}", "atlas")
        self.assertEqual(self.markers()["atlas"], sent["id"])
        later = self.message("codex", text="two", client="n2")
        self.ok("GET", f"/api/messages?channel_id=workspace&after={sent['id']}", "atlas")
        self.assertEqual(self.markers()["atlas"], later["id"])

    def test_the_channel_list_brief_search_export_and_saved_messages_are_not_reading(self):
        self.message("curator", text="findable words", client="x1")
        for path in ("/api/state", "/api/brief?channel_id=workspace", "/api/search?q=findable", "/api/export",
                     "/api/bookmarks"):
            self.ok("GET", path, "codex")
        self.assertEqual(self.markers(), {})

    def test_an_agents_fetch_marks_only_the_conversation_it_fetched(self):
        private = self.ok("POST", "/api/channels", "curator", {"title": "Curator and Codex", "members": ["curator", "codex"]})
        self.message("curator", text="shared", client="p1")
        secret = self.message("curator", channel=private["id"], text="between two", client="p2")
        self.ok("GET", f"/api/messages?channel_id={private['id']}", "codex")
        self.assertEqual(self.markers(), {})
        self.assertEqual(self.markers(channel=private["id"]), {"codex": secret["id"]})

    def test_edited_only_agent_fetch_counts_as_read_without_marking_a_human_read(self):
        human = next(p["id"] for p in json.loads(self.config.read_text())["participants"] if p["kind"] == "human")
        agent = next(p["id"] for p in json.loads(self.config.read_text())["participants"] if p["kind"] == "agent")
        sent = self.message(human, text="Original", client="edited-only")
        self.ok("POST", f"/api/messages/{sent['id']}/edit", human,
                {"body": f"Edited @{{{agent}}}", "version": 1, "edit_id": "edited-only-change"})
        path = f"/api/messages?channel_id=workspace&after={sent['id']}&changes_after=0"
        self.ok("GET", path, human)
        self.assertNotIn(human, self.markers(actor=agent))
        fetched = self.ok("GET", path, agent)
        self.assertEqual(fetched["messages"], [])
        self.assertEqual([m["id"] for m in fetched["message_updates"]], [sent["id"]])
        self.assertEqual(self.markers(actor=human)[agent], sent["id"])
        channel = next(c for c in self.ok("GET", "/api/state", agent)["channels"] if c["id"] == "workspace")
        self.assertEqual(channel["unread_mentions"], 0)

    def test_fetch_marks_latest_returned_id_without_using_the_requested_cursor(self):
        human = next(p["id"] for p in json.loads(self.config.read_text())["participants"] if p["kind"] == "human")
        agent = next(p["id"] for p in json.loads(self.config.read_text())["participants"] if p["kind"] == "agent")
        first = self.message(human, text="Original", client="max-first")
        self.ok("POST", f"/api/messages/{first['id']}/edit", human,
                {"body": "Edited original", "version": 1, "edit_id": "max-edit"})
        last = self.message(human, text="Later", client="max-last")
        self.ok("GET", f"/api/messages?channel_id=workspace&after={first['id']}", agent)
        self.assertEqual(self.markers(actor=human)[agent], last["id"])
        empty = self.ok("GET", f"/api/messages?channel_id=workspace&after={last['id'] + 100}&changes_after=100", agent)
        self.assertEqual(empty["messages"], [])
        self.assertEqual(empty["message_updates"], [])
        self.assertEqual(self.markers(actor=human)[agent], last["id"])


if __name__ == "__main__":
    unittest.main()
