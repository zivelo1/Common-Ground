"""Daily knowledge check for every project (; unit 3 DESIGN.txt): one record per channel and check day,
the check day running from the configured local time to the same time next day, due and missed computed from records."""
import json
import subprocess
import sys
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qa_support import temporary_directory

import store
from app import make_server
from cli import RequestError, request
from store import APIError, Store, load_config

CHECK = {"time": "18:00", "timezone": "Asia/Jerusalem"}


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


class KnowledgeCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_directory(self, prefix="knowledge-check-")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.store = self.make_store(CHECK)
        self.human = {"id": "human", "kind": "human", "name": "Synthetic human"}
        self.agent = {"id": "agent", "kind": "agent", "name": "Synthetic agent"}
        self.outsider = {"id": "other", "kind": "agent", "name": "Other synthetic agent"}
        with self.store.connection(write=True) as db:   # a private conversation the outsider is not in
            db.execute("INSERT INTO channels(id,title,kind) VALUES('private1','Human and agent','private')")
            db.executemany("INSERT INTO members(channel_id,participant_id) VALUES(?,?)",
                           [("private1", "human"), ("private1", "agent")])

    def make_store(self, check, name="config.json"):
        config = {"title": "Synthetic board", "project_label": "Offline", "state_dir": "state",
                  "participants": [{"id": "human", "name": "Synthetic human", "kind": "human"},
                                   {"id": "agent", "name": "Synthetic agent", "kind": "agent"},
                                   {"id": "other", "name": "Other synthetic agent", "kind": "agent"}]}
        if check is not None:
            config["knowledge_check"] = check
        path = self.root / name
        path.write_text(json.dumps(config))
        return Store(load_config(path))

    def post(self, actor, at, channel="workspace", **fields):
        body = {"channel_id": channel, "outcome": "updated", "note": "Synthetic check", **fields}
        with patch.object(store, "utc_moment", return_value=utc(at)):
            return self.store.record_knowledge_check(actor, body)

    def status(self, actor, at, channel="workspace"):
        with patch.object(store, "utc_moment", return_value=utc(at)):
            return self.store.knowledge_check_status(actor, channel)

    def test_the_setting_is_validated_when_the_config_loads(self):
        for bad in ({"time": "25:00", "timezone": "Asia/Jerusalem"}, {"time": "18:00", "timezone": "Mars/Base"},
                    {"time": "18:00"}, {"time": "6pm", "timezone": "UTC"}, ["18:00", "UTC"],
                    {"time": "18:00", "timezone": "UTC", "extra": 1}):
            with self.subTest(bad=bad), self.assertRaises(APIError) as refused:
                self.make_store(bad, name="bad.json")
            self.assertEqual(refused.exception.status, 400)

    def test_a_project_without_the_setting_records_no_check(self):
        self.store = self.make_store(None, name="plain.json")
        with self.assertRaises(APIError) as refused:
            self.post(self.human, "2026-10-10T16:00:00")
        self.assertEqual(refused.exception.status, 400)
        self.assertEqual(self.status(self.human, "2026-10-10T16:00:00"), {"configured": False})

    def test_the_check_day_starts_at_the_local_time(self):
        # 18:05 Jerusalem (UTC+3 in October before the 25th) belongs to 10 October; 09:00 next morning still does
        record = self.post(self.agent, "2026-10-10T15:05:00")
        self.assertEqual((record["check_date"], record["late"]), ("2026-10-10", False))
        self.assertEqual(self.status(self.human, "2026-10-11T06:00:00")["state"], "done")
        self.assertEqual(self.status(self.human, "2026-10-11T15:00:00")["state"], "due")    # 18:00 on the 11th

    def test_a_check_after_midnight_completes_the_previous_day_late(self):
        record = self.post(self.agent, "2026-10-11T06:00:00")
        self.assertEqual((record["check_date"], record["late"]), ("2026-10-10", True))

    def test_completion_counts_once_per_check_day(self):
        self.post(self.agent, "2026-10-10T15:05:00")
        with self.assertRaises(APIError) as refused:
            self.post(self.human, "2026-10-11T06:00:00", outcome="nothing_changed")
        self.assertEqual(refused.exception.status, 409)

    def test_missed_days_stay_visible(self):
        self.post(self.agent, "2026-10-08T15:30:00")
        self.post(self.agent, "2026-10-10T15:30:00")
        status = self.status(self.human, "2026-10-12T16:00:00")      # 19:00 on the 12th
        self.assertEqual((status["check_date"], status["state"]), ("2026-10-12", "due"))
        self.assertEqual(status["missed"], ["2026-10-09", "2026-10-11"])
        self.assertEqual((status["last"]["check_date"], status["last"]["actor_id"]), ("2026-10-10", "agent"))

    def test_the_daylight_saving_change_uses_the_zones_own_clock(self):
        # Israel leaves summer time at 02:00 on 25 October 2026: 18:00 local is 15:00 UTC on the 24th, 16:00 UTC on the
        # 25th. A fixed +3 offset would put 15:30 UTC on the 25th at 18:30, inside the 25th's check day.
        self.assertEqual(self.post(self.agent, "2026-10-25T15:30:00")["check_date"], "2026-10-24")
        self.assertEqual(self.post(self.agent, "2026-10-25T16:00:00")["check_date"], "2026-10-25")

    def test_invalid_records_are_refused(self):
        cases = [({"outcome": "fixed_everything"}, 400), ({"note": "x" * 4001}, 400), ({"note": 7}, 400),
                 ({"knowledge_ids": ["no-such-record"]}, 400), ({"knowledge_ids": "abc"}, 400),
                 ({"extra": True}, 400)]
        for fields, code in cases:
            with self.subTest(fields=fields), self.assertRaises(APIError) as refused:
                self.post(self.agent, "2026-10-10T15:05:00", **fields)
            self.assertEqual(refused.exception.status, code)
        self.assertEqual(self.status(self.human, "2026-10-10T15:06:00")["state"], "due")

    def test_a_non_member_cannot_record_or_read_a_private_check(self):
        for call in (lambda: self.post(self.outsider, "2026-10-10T15:05:00", channel="private1"),
                     lambda: self.status(self.outsider, "2026-10-10T15:05:00", channel="private1")):
            with self.assertRaises(APIError) as refused:
                call()
            self.assertEqual(refused.exception.status, 404)
        self.assertEqual(self.post(self.agent, "2026-10-10T15:05:00", channel="private1")["channel_id"], "private1")

    def test_the_record_keeps_its_knowledge_links_and_author(self):
        entry = self.store.add_knowledge(self.agent, {"channel_id": "workspace", "title": "Synthetic fact",
                                                      "body": "Synthetic body", "kind": "fact", "outcome": "unknown",
                                                      "status": "proposed", "source": "Synthetic", "evidence": ""})
        record = self.post(self.agent, "2026-10-10T15:05:00", knowledge_ids=[entry["id"]])
        self.assertEqual((record["actor_id"], record["knowledge_ids"]), ("agent", [entry["id"]]))

    def test_the_brief_shows_the_check(self):
        self.post(self.agent, "2026-10-08T15:30:00")
        with patch.object(store, "utc_moment", return_value=utc("2026-10-10T16:00:00")):
            brief = self.store.brief(self.human, "workspace")
        check = brief["knowledge_check"]
        self.assertEqual((check["check_date"], check["state"], check["missed"]), ("2026-10-10", "due", ["2026-10-09"]))


class KnowledgeCheckHttpTests(unittest.TestCase):
    """The same records through the real HTTP server, the CLI's request function and the CLI commands."""

    def setUp(self):
        temp = temporary_directory(self, prefix="knowledge-check-http-")
        self.addCleanup(temp.cleanup)
        self.config = Path(temp.name) / "config.json"
        self.config.write_text(json.dumps({
            "title": "Synthetic board", "project_label": "Offline", "state_dir": "state", "knowledge_check": CHECK,
            "participants": [{"id": "human", "name": "Synthetic human", "kind": "human"},
                             {"id": "agent", "name": "Synthetic agent", "kind": "agent"}]}))
        self.server = make_server(self.config)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()

        def stop():
            self.server.shutdown()
            thread.join(timeout=5)
            self.server.server_close()
        self.addCleanup(stop)

    def cli(self, *arguments, stdin=None):
        process = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve().parents[1] / "cli.py"),
                                  "--config", str(self.config), "--as", "agent", *arguments], input=stdin,
                                 capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout)

    def test_the_route_records_once_and_reports_the_state(self):
        with patch.object(store, "utc_moment", return_value=utc("2026-10-10T15:05:00")):
            record = request(self.config, "agent", "POST", "/api/knowledge-checks",
                             {"channel_id": "workspace", "outcome": "nothing_changed", "note": ""})
            status = request(self.config, "human", "GET", "/api/knowledge-checks?channel_id=workspace")
            with self.assertRaises(RequestError) as refused:
                request(self.config, "human", "POST", "/api/knowledge-checks", {"channel_id": "workspace", "outcome": "updated"})
        self.assertEqual((record["check_date"], status["state"], refused.exception.status), ("2026-10-10", "done", 409))

    def test_container_outcomes_are_bad_requests_without_a_record(self):
        # JSON arrays and objects must get the same client refusal as unknown strings.
        with patch.object(store, "utc_moment", return_value=utc("2026-10-10T15:05:00")):
            for outcome in ([], {}):
                with self.subTest(outcome=outcome):
                    with self.assertRaises(RequestError) as refused:
                        request(self.config, "agent", "POST", "/api/knowledge-checks",
                                {"channel_id": "workspace", "outcome": outcome})
                    self.assertEqual(refused.exception.status, 400)
            status = request(self.config, "human", "GET", "/api/knowledge-checks?channel_id=workspace")
            self.assertEqual(status["state"], "due")
            self.assertIsNone(status["last"])

    def test_the_cli_commands_post_and_read_the_record(self):
        record = self.cli("knowledge-check", "--from-stdin", stdin=json.dumps({"channel_id": "workspace",
                                                                               "outcome": "updated", "note": "CLI"}))
        status = self.cli("knowledge-check-status", "--channel", "workspace")
        self.assertEqual((status["last"]["actor_id"], status["last"]["note"], status["last"]["check_date"]),
                         ("agent", "CLI", record["check_date"]))


if __name__ == "__main__":
    unittest.main()
