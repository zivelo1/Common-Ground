"""Remove people and agents: a removed participant becomes inactive, never deleted.
Its token and every browser session stop working at the next request, all its messages and records stay, a restart
never brings it back, and a human restores it explicitly. Only a human removes or restores; nobody removes
themselves; every change has a history row."""
import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor

import test_backend


class ParticipantRemovalTests(unittest.TestCase):
    # Reuse the backend server fixture's methods. Never bind its TestCase class to a module name here: the loader
    # collects every TestCase class in this module's namespace, so its tests would run a second time.
    setUp = test_backend.BackendTests.setUp
    start = test_backend.BackendTests.start
    stop = test_backend.BackendTests.stop
    tearDown = test_backend.BackendTests.tearDown
    call = test_backend.BackendTests.call
    ok = test_backend.BackendTests.ok

    def remove(self, ident, seen, actor="human", reason="Left the project", **extra):
        return self.call("POST", "/api/participants/remove", actor,
                         {"id": ident, "seen_name": seen, "reason": reason, **extra})

    def restore(self, ident, seen, actor="human", reason="Back on the project"):
        return self.call("POST", "/api/participants/restore", actor, {"id": ident, "seen_name": seen, "reason": reason})

    def people(self):
        return {person["id"]: person["active"] for person in self.ok("GET", "/api/state")["participants"]}

    def history(self):
        with sqlite3.connect(self.root / "state" / "collaboration.sqlite3") as db:
            return db.execute("SELECT participant_id,action,actor_id,reason FROM participant_status_history ORDER BY id").fetchall()

    def add_second_human(self):
        config = json.loads(self.config.read_text())
        config["participants"].append({"id": "h2", "name": "Second Human", "kind": "human"})
        self.config.write_text(json.dumps(config))
        self.stop()
        self.start()
        self.tokens["h2"] = json.loads((self.root / "state" / "credentials" / "h2.json").read_text())["token"]

    def login(self, actor):
        _, _, headers = self.call("POST", "/api/login", actor=None, body={"token": self.tokens[actor]})
        return {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}

    def test_a_removed_agent_is_locked_out_and_its_history_stays(self):
        sent = self.ok("POST", "/api/messages", "a", {"channel_id": "workspace", "body": "before", "client_id": "m1"})
        session = self.login("a")
        status, result, _ = self.remove("a", "Agent A")
        self.assertEqual((status, result), (200, {"id": "a", "name": "Agent A", "kind": "agent", "active": False}))
        self.assertEqual(self.call("GET", "/api/state", "a")[0], 401)                        # token: CLI, MCP, adapters
        self.assertEqual(self.call("GET", "/api/state", None, headers=session)[0], 401)      # open browser session
        self.assertEqual(self.call("POST", "/api/login", None, body={"token": self.tokens["a"]})[0], 401)
        message = self.ok("GET", "/api/messages?channel_id=workspace")["messages"][0]
        self.assertEqual((message["id"], message["author_id"], message["author_name"], message["body"]),
                         (sent["id"], "a", "Agent A", "before"))
        self.assertEqual(self.people(), {"human": True, "a": False, "b": True})
        self.assertEqual(self.history(), [("a", "remove", "human", "Left the project")])

    def test_restore_lets_the_same_token_back_in(self):
        self.assertEqual(self.remove("a", "Agent A")[0], 200)
        status, result, _ = self.restore("a", "Agent A")
        self.assertEqual((status, result["active"]), (200, True))
        self.assertEqual(self.call("GET", "/api/state", "a")[0], 200)
        self.assertIn("a", next(c for c in self.ok("GET", "/api/state")["channels"] if c["id"] == "workspace")["members"])
        self.assertEqual([row[1] for row in self.history()], ["remove", "restore"])

    def test_a_restart_never_brings_a_removed_participant_back(self):
        self.assertEqual(self.remove("a", "Agent A")[0], 200)
        self.stop()
        self.start()
        self.assertEqual(self.people(), {"human": True, "a": False, "b": True})
        self.assertEqual(self.call("GET", "/api/state", "a")[0], 401)

    def test_refused_removals_change_nothing(self):
        refusals = {
            "agent caller": (self.remove("b", "Agent B", actor="a")[0], 403),
            "yourself": (self.remove("human", "Human")[0], 409),
            "unknown id": (self.remove("nobody", "Nobody")[0], 404),
            "invalid id": (self.remove("../x", "X")[0], 400),
            "stale view": (self.remove("a", "Old name")[0], 409),
            "blank reason": (self.remove("a", "Agent A", reason="   ")[0], 400),
            "unknown field": (self.remove("a", "Agent A", kind="human")[0], 400),
            "restore an active one": (self.restore("a", "Agent A")[0], 409),
            "agent restores": (self.restore("a", "Agent A", actor="b")[0], 403),
        }
        self.assertEqual({case: got for case, (got, _) in refusals.items()},
                         {case: want for case, (_, want) in refusals.items()})
        self.assertEqual(self.people(), {"human": True, "a": True, "b": True})
        self.assertEqual(self.history(), [])

    def test_removing_twice_is_refused(self):
        self.assertEqual(self.remove("a", "Agent A")[0], 200)
        self.assertEqual(self.remove("a", "Agent A")[0], 409)
        self.assertEqual(len(self.history()), 1)

    def test_two_humans_removing_each_other_at_once_leave_one_active(self):
        self.add_second_human()
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(lambda pair: self.remove(pair[0], pair[1], actor=pair[2])[0],
                                       (("h2", "Second Human", "human"), ("human", "Human", "h2"))))
        self.assertEqual(statuses[0], 200)
        self.assertIn(statuses[1], (401, 403))      # the loser was removed first: refused at login or in the write
        states = {ident: active for ident, active in self.people_as_raw().items() if ident in ("human", "h2")}
        self.assertEqual(sorted(states.values()), [False, True])
        self.assertEqual(len(self.history()), 1)

    def people_as_raw(self):
        with sqlite3.connect(self.root / "state" / "collaboration.sqlite3") as db:
            return {ident: bool(active) for ident, active in db.execute("SELECT id,active FROM participants")}

    def test_a_removed_participant_cannot_join_a_new_conversation(self):
        self.assertEqual(self.remove("a", "Agent A")[0], 200)
        self.assertEqual(self.call("POST", "/api/channels", "human", {"title": "Plan", "members": ["a"]})[0], 409)
        self.assertEqual(self.ok("POST", "/api/channels", "human", {"title": "Plan", "members": ["b"]})["members"],
                         ["b", "human"])

    def test_adding_an_agent_with_a_removed_id_is_refused(self):
        self.assertEqual(self.remove("a", "Agent A")[0], 200)
        self.assertEqual(self.call("POST", "/api/participants", "human", {"id": "a", "name": "New A", "kind": "agent"})[0], 409)


if __name__ == "__main__":
    unittest.main()
