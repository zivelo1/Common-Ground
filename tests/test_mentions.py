"""@mentions: a mention is the plain-text marker @{participant-id} in a message body. The server
derives who is mentioned (ids that are current members of that conversation), returns it on every message record as
`mentions`, and counts each reader's unread mentions per conversation in /api/state. A non-member id notifies nobody.
Nothing wakes an agent or calls a model."""
import json
import unittest
from pathlib import Path

import test_backend
from qa_support import temporary_directory


class MentionTests(unittest.TestCase):
    # Reuse the backend server fixture's methods; never bind its TestCase class to a module name here (it would run
    # its tests a second time).
    start = test_backend.BackendTests.start
    stop = test_backend.BackendTests.stop
    tearDown = test_backend.BackendTests.tearDown
    call = test_backend.BackendTests.call
    ok = test_backend.BackendTests.ok
    message = test_backend.BackendTests.message

    def setUp(self):
        self.temp = temporary_directory(self, prefix="mentions-test-")
        self.root = Path(self.temp.name)
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps({"title": "Mentions Board", "project_label": "Generic", "state_dir": "state",
                                           "participants": [{"id": "curator", "name": "Curator", "kind": "human"},
                                                            {"id": "atlas", "name": "Atlas", "kind": "agent"},
                                                            {"id": "scribe", "name": "Scribe", "kind": "agent"}]}))
        self.server = None
        self.thread = None
        self.addCleanup(self.stop)
        self.start()
        self.tokens = {ident: json.loads((self.root / "state" / "credentials" / f"{ident}.json").read_text())["token"]
                       for ident in ("curator", "atlas", "scribe")}

    def unread_mentions(self, actor, channel="workspace"):
        state = self.ok("GET", "/api/state", actor)
        return next(c["unread_mentions"] for c in state["channels"] if c["id"] == channel)

    def test_the_record_lists_each_mentioned_member_once_in_order(self):
        sent = self.message("curator", text="@{scribe} and @{atlas}, then @{scribe} again", client="m1")
        self.assertEqual(sent["mentions"], ["scribe", "atlas"])
        [listed] = self.ok("GET", "/api/messages?channel_id=workspace", "atlas")["messages"]
        self.assertEqual(listed["mentions"], ["scribe", "atlas"])

    def test_unknown_ids_plain_at_words_and_emails_mention_nobody(self):
        for client, text in (("u1", "@{nobody} is not here"), ("u2", "write to a@atlas.example or @Atlas by name"),
                             ("u3", "a broken @{atlas marker and @{} and @{at las}")):
            with self.subTest(text=text):
                self.assertEqual(self.message("curator", text=text, client=client)["mentions"], [])

    def test_a_non_member_is_not_mentioned_in_a_private_conversation(self):
        private = self.ok("POST", "/api/channels", "curator", {"title": "Curator and Scribe", "members": ["curator", "scribe"]})
        sent = self.message("curator", channel=private["id"], text="ask @{atlas} later, @{scribe} now", client="p1")
        self.assertEqual(sent["mentions"], ["scribe"])
        self.assertNotIn(private["id"], [c["id"] for c in self.ok("GET", "/api/state", "atlas")["channels"]])
        self.assertEqual(self.unread_mentions("scribe", private["id"]), 1)

    def test_unread_mentions_count_until_the_reader_marks_them_read(self):
        self.assertEqual(self.unread_mentions("atlas"), 0)
        first = self.message("curator", text="@{atlas} please look", client="r1")
        self.message("scribe", text="no mention here", client="r2")
        self.message("scribe", text="@{atlas} and @{curator}", client="r3")
        self.assertEqual(self.unread_mentions("atlas"), 2)
        self.assertEqual(self.unread_mentions("curator"), 1)
        self.ok("POST", "/api/read", "atlas", {"channel_id": "workspace", "message_id": first["id"]})
        self.assertEqual(self.unread_mentions("atlas"), 1)

    def test_an_agents_fetch_of_the_conversation_clears_its_unread_mentions(self):
        # Read status: the messages an agent's own tool fetches count as read, so that agent's
        # mention badge clears; reading /api/state is not reading, and another agent's count stays.
        self.message("curator", text="@{atlas} and @{scribe}", client="f1")
        self.assertEqual((self.unread_mentions("atlas"), self.unread_mentions("scribe")), (1, 1))
        self.ok("GET", "/api/messages?channel_id=workspace", "atlas")
        self.assertEqual((self.unread_mentions("atlas"), self.unread_mentions("scribe")), (0, 1))

    def test_mentioning_yourself_is_not_an_unread_mention(self):
        self.message("atlas", text="note to @{atlas}", client="s1")
        self.assertEqual(self.unread_mentions("atlas"), 0)

    def test_a_rename_keeps_the_mention_on_the_same_participant(self):
        sent = self.message("scribe", text="@{atlas} review", client="n1")
        self.ok("POST", "/api/participants/rename", "curator", {"id": "atlas", "name": "Atlas Judge", "seen_name": "Atlas"})
        [listed] = self.ok("GET", "/api/messages?channel_id=workspace", "curator")["messages"]
        self.assertEqual((listed["id"], listed["mentions"]), (sent["id"], ["atlas"]))
        self.assertEqual(self.unread_mentions("atlas"), 1)

    def test_an_edit_that_adds_a_mention_updates_the_record_and_the_count(self):
        sent = self.message("scribe", text="review please", client="e1")
        edited = self.ok("POST", f"/api/messages/{sent['id']}/edit", "scribe",
                         {"body": "review please @{atlas}", "version": sent["version"], "edit_id": "e1-1"})
        self.assertEqual(edited["message"]["mentions"], ["atlas"])
        self.assertEqual(self.unread_mentions("atlas"), 1)


if __name__ == "__main__":
    unittest.main()
