"""Rename people and agents: same id, same messages, a history row, no look-alike names, and a restart keeps it."""
import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor

import test_backend


class ParticipantRenameTests(unittest.TestCase):
    # Reuse the backend server fixture's methods. Never bind its TestCase class to a module name here: the loader
    # collects every TestCase class in this module's namespace, so its 27 tests would run a second time.
    setUp = test_backend.BackendTests.setUp
    start = test_backend.BackendTests.start
    stop = test_backend.BackendTests.stop
    tearDown = test_backend.BackendTests.tearDown
    call = test_backend.BackendTests.call
    ok = test_backend.BackendTests.ok

    def rename(self, ident, name, seen, actor="human", **extra):
        return self.call("POST", "/api/participants/rename", actor, {"id": ident, "name": name, "seen_name": seen, **extra})

    def names(self):
        return {person["id"]: person["name"] for person in self.ok("GET", "/api/state")["participants"]}

    def history(self):
        with sqlite3.connect(self.root / "state" / "collaboration.sqlite3") as db:
            return db.execute("SELECT participant_id,old_name,new_name,actor_id FROM participant_renames ORDER BY id").fetchall()

    def test_a_human_renames_an_agent_and_old_messages_keep_their_author_id(self):
        sent = self.ok("POST", "/api/messages", "a", {"channel_id": "workspace", "body": "before", "client_id": "r1"})
        status, result, _ = self.rename("a", "  Agent Alpha  ", "Agent A")
        self.assertEqual((status, result), (200, {"id": "a", "name": "Agent Alpha", "kind": "agent"}))
        self.assertEqual(self.names(), {"human": "Human", "a": "Agent Alpha", "b": "Agent B"})
        message = self.ok("GET", "/api/messages?channel_id=workspace")["messages"][0]
        self.assertEqual((message["id"], message["author_id"], message["author_name"], message["body"]),
                         (sent["id"], "a", "Agent Alpha", "before"))
        self.assertEqual(self.history(), [("a", "Agent A", "Agent Alpha", "human")])

    def test_a_rename_survives_a_restart_and_config_json_is_never_rewritten(self):
        self.assertEqual(self.rename("human", "Curator", "Human")[0], 200)
        config = json.loads(self.config.read_text())
        config["participants"].append({"id": "c", "name": "Agent C", "kind": "agent"})
        self.config.write_text(json.dumps(config))
        before = self.config.read_bytes()
        self.stop()
        self.start()
        # the database keeps the shown name; config.json only names a participant the first time it is added
        self.assertEqual(self.names(), {"human": "Curator", "a": "Agent A", "b": "Agent B", "c": "Agent C"})
        self.assertEqual(self.config.read_bytes(), before)

    def test_refused_renames_change_nothing(self):
        refusals = {
            "agent caller": (self.rename("a", "Self", "Agent A", actor="a")[0], 403),
            "unknown id": (self.rename("nobody", "Name", "Name")[0], 404),
            "invalid id": (self.rename("../x", "Name", "Name")[0], 400),
            "blank name": (self.rename("a", "   ", "Agent A")[0], 400),
            "too long": (self.rename("a", "x" * 201, "Agent A")[0], 400),
            "unknown field": (self.rename("a", "Name", "Agent A", kind="human")[0], 400),
            "missing seen name": (self.call("POST", "/api/participants/rename", "human", {"id": "a", "name": "Name"})[0], 400),
            "look-alike of another": (self.rename("b", " agent   A ", "Agent B")[0], 409),
            "same as now": (self.rename("a", "Agent A", "Agent A")[0], 409),
            "stale view": (self.rename("a", "New", "Old name")[0], 409),
        }
        self.assertEqual({case: got for case, (got, _) in refusals.items()},
                         {case: want for case, (_, want) in refusals.items()})
        self.assertEqual(self.names(), {"human": "Human", "a": "Agent A", "b": "Agent B"})
        self.assertEqual(self.history(), [])

    def test_two_renames_from_the_same_view_have_one_winner(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(lambda name: self.rename("b", name, "Agent B")[0], ("Bee", "Bea")))
        self.assertEqual(statuses, [200, 409])
        self.assertIn(self.names()["b"], {"Bee", "Bea"})
        self.assertEqual(len(self.history()), 1)


if __name__ == "__main__":
    unittest.main()
