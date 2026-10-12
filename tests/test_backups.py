"""Backups on disposable synthetic data only: the module itself (consistency under concurrent writes, private files,
interruption, corrupt-backup refusal, restore to a new path) and the real HTTP service (human-only access, settings,
truthful status, recorded failures, the server's scheduler, and a full restore proof through a restarted instance)."""
from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import shutil
import sqlite3
import stat
import sys
import threading
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qa_support import temporary_directory

import app as backend
import backups
import cli

MOMENT = datetime(2026, 10, 8, 14, 30, 0, 123456, tzinfo=timezone.utc)
WAIT_SECONDS = 10


def synthetic_database(path: Path, rows: int) -> None:
    """A WAL database like the app's, with `rows` synthetic messages."""
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL)")
        db.executemany("INSERT INTO messages(body) VALUES (?)", [(f"synthetic message {i}",) for i in range(rows)])
    os.chmod(path, 0o600)


def bodies(path: Path) -> list[str]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        return [row[0] for row in db.execute("SELECT body FROM messages ORDER BY id")]


class Interrupted(BaseException):
    """Stands in for the process stopping in the middle of a copy."""


class BackupModuleTests(unittest.TestCase):

    def test_incomplete_manifest_is_reported_and_source_link_is_refused(self) -> None:
        name = "collaboration-20261008T190000000000Z.sqlite3"
        (self.folder / name).write_bytes(b"synthetic incomplete file")
        (self.folder / (name + ".json")).write_text(json.dumps({"name": name, "bytes": 25}))
        listing = backups.inventory(self.folder)
        self.assertEqual(listing["complete"], [])
        self.assertEqual(listing["incomplete"], [{"name": name, "reason": "no readable manifest"}])
        link = self.root / "linked.sqlite3"
        link.symlink_to(self.database)
        before = sorted(p.name for p in self.folder.iterdir())
        with self.assertRaisesRegex(backups.BackupError, "symbolic link"):
            backups.create(link, self.folder, MOMENT)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), before)

    def test_invalid_manifest_timestamp_is_reported(self) -> None:
        record = backups.create(self.database, self.folder, MOMENT)
        path = self.folder / (record["name"] + ".json")
        for value in ["not a date", "2026-10-08T19:00:00", 1, None]:
            with self.subTest(value=value):
                changed = {**record, "created_at": value}
                path.write_text(json.dumps(changed))
                self.assertEqual(backups.inventory(self.folder)["complete"], [])
    def setUp(self) -> None:
        self.root = Path(temporary_directory(self, prefix="backup-module-").name)
        self.database = self.root / "collaboration.sqlite3"
        synthetic_database(self.database, 50)
        self.folder = backups.directory(self.root)

    def test_backup_is_private_complete_and_verifiable(self) -> None:
        manifest = backups.create(self.database, self.folder, MOMENT)
        path = self.folder / manifest["name"]
        self.assertEqual(stat.S_IMODE(self.folder.stat().st_mode), 0o700)
        for file in (path, path.with_name(path.name + backups.MANIFEST_SUFFIX)):
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600, file.name)
        self.assertEqual(backups.verify(path), manifest)
        self.assertEqual(manifest["integrity_check"], "ok")
        self.assertEqual(bodies(path), bodies(self.database))
        listing = backups.inventory(self.folder)
        self.assertEqual([item["name"] for item in listing["complete"]], [manifest["name"]])
        self.assertEqual(listing["incomplete"], [])
        # One self-contained file: verifying and reading it leaves no SQLite side files.
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), sorted([path.name, path.name + ".json"]))

    def test_same_time_stamp_is_refused_and_nothing_is_overwritten(self) -> None:
        first = backups.create(self.database, self.folder, MOMENT)
        before = (self.folder / first["name"]).read_bytes()
        with self.assertRaisesRegex(backups.BackupError, "already exists"):
            backups.create(self.database, self.folder, MOMENT)
        self.assertEqual((self.folder / first["name"]).read_bytes(), before)

    def test_snapshot_is_consistent_while_another_connection_keeps_writing(self) -> None:
        # Enough data that each copy takes long enough for the writer to commit during it.
        with sqlite3.connect(self.database) as db:
            db.executemany("INSERT INTO messages(body) VALUES (?)", [("x" * 200,)] * 100_000)
        originals = bodies(self.database)
        stop = threading.Event()
        written: list[int] = []

        def writer() -> None:
            with sqlite3.connect(self.database, timeout=10) as db:
                while not stop.is_set():
                    db.execute("INSERT INTO messages(body) VALUES ('written during backup')")
                    db.commit()
                    written.append(1)

        thread = threading.Thread(target=writer)
        thread.start()
        during = []
        try:
            manifests = []
            for i in range(5):
                before = len(written)
                manifests.append(backups.create(self.database, self.folder, MOMENT + timedelta(seconds=i)))
                during.append(len(written) - before)
        finally:
            stop.set()
            thread.join()
        self.assertGreater(max(during), 0, "the writer never committed while a copy was being made")
        for manifest in manifests:
            path = self.folder / manifest["name"]
            backups.verify(path)
            rows = bodies(path)
            # Each snapshot holds every original row and a whole prefix of the concurrent inserts, nothing torn.
            self.assertEqual(rows[: len(originals)], originals)
            self.assertTrue(all(body == "written during backup" for body in rows[len(originals):]))

    def test_interrupted_backup_stays_partial_and_is_not_a_backup(self) -> None:
        with patch.object(backups.os, "rename", side_effect=OSError("simulated power loss")), self.assertRaises(OSError):
            backups.create(self.database, self.folder, MOMENT)
        listing = backups.inventory(self.folder)
        self.assertEqual(listing["complete"], [])
        self.assertEqual([item["reason"] for item in listing["incomplete"]], ["interrupted before completion"])
        manifest = backups.create(self.database, self.folder, MOMENT + timedelta(minutes=1))
        listing = backups.inventory(self.folder)
        self.assertEqual([item["name"] for item in listing["complete"]], [manifest["name"]])
        self.assertEqual(len(listing["incomplete"]), 1)

    def test_database_without_manifest_is_incomplete(self) -> None:
        real_rename = os.rename

        def fail_manifest(source: str | Path, target: str | Path) -> None:
            if str(target).endswith(backups.MANIFEST_SUFFIX):
                raise OSError("simulated stop before the manifest")
            real_rename(source, target)

        with patch.object(backups.os, "rename", side_effect=fail_manifest), self.assertRaises(OSError):
            backups.create(self.database, self.folder, MOMENT)
        reasons = sorted(item["reason"] for item in backups.inventory(self.folder)["incomplete"])
        self.assertEqual(reasons, ["interrupted before completion", "no readable manifest"])
        name = next(p for p in self.folder.iterdir() if backups.NAME.fullmatch(p.name))
        with self.assertRaisesRegex(backups.BackupError, "no readable manifest"):
            backups.verify(name)

    def test_corrupt_or_changed_backups_are_refused(self) -> None:
        manifest = backups.create(self.database, self.folder, MOMENT)
        path = self.folder / manifest["name"]
        target = self.root / "restored.sqlite3"
        raw = bytearray(path.read_bytes())
        raw[len(raw) // 2] ^= 0xFF
        path.write_bytes(bytes(raw))
        with self.assertRaisesRegex(backups.BackupError, "changed after it was made"):
            backups.verify(path)
        with self.assertRaisesRegex(backups.BackupError, "changed after it was made"):
            backups.restore(path, target)
        self.assertFalse(target.exists())
        # A file whose manifest was rewritten to match is still refused by SQLite's own check.
        garbage = self.folder / "collaboration-20261008T143001000000Z.sqlite3"
        garbage.write_bytes(b"not a database" * 100)
        record = {"schema": backups.MANIFEST_SCHEMA, "name": garbage.name, "bytes": garbage.stat().st_size,
                  "sha256": backups._sha256(garbage), "created_at": MOMENT.isoformat(),
                  "integrity_check": "ok", "sqlite_version": sqlite3.sqlite_version}
        garbage.with_name(garbage.name + ".json").write_text(json.dumps(record))
        with self.assertRaisesRegex(backups.BackupError, "not a readable database"):
            backups.verify(garbage)
        with self.assertRaisesRegex(backups.BackupError, "Not a backup file"):
            backups.verify(self.database)

    def test_restore_writes_only_a_new_private_path_with_every_record(self) -> None:
        manifest = backups.create(self.database, self.folder, MOMENT)
        path = self.folder / manifest["name"]
        target = self.root / "restored" / "collaboration.sqlite3"
        target.parent.mkdir()
        result = backups.restore(path, target)
        self.assertEqual(result["integrity_check"], "ok")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertEqual(bodies(target), bodies(self.database))
        before = target.read_bytes()
        for existing in (target, self.database):
            with self.assertRaisesRegex(backups.BackupError, "only to a new path"):
                backups.restore(path, existing)
        self.assertEqual(target.read_bytes(), before)
        (self.root / "side.sqlite3-wal").write_bytes(b"")
        with self.assertRaisesRegex(backups.BackupError, "side file"):
            backups.restore(path, self.root / "side.sqlite3")

    def test_symbolic_links_are_refused(self) -> None:
        other = self.root / "elsewhere"
        other.mkdir()
        linked_state = self.root / "linked_state"
        linked_state.mkdir()
        (linked_state / backups.DIRECTORY_NAME).symlink_to(other)
        with self.assertRaisesRegex(backups.BackupError, "symbolic link"):
            backups.directory(linked_state)
        manifest = backups.create(self.database, self.folder, MOMENT)
        link = self.folder / "collaboration-20261008T150000000000Z.sqlite3"
        link.symlink_to(self.folder / manifest["name"])
        with self.assertRaisesRegex(backups.BackupError, "Not a backup file"):
            backups.verify(link)
        self.assertIn({"name": link.name, "reason": "symbolic link, ignored"}, backups.inventory(self.folder)["incomplete"])

    def test_missing_database_is_refused(self) -> None:
        with self.assertRaisesRegex(backups.BackupError, "does not exist"):
            backups.create(self.root / "absent.sqlite3", self.folder, MOMENT)

    def test_due_time_and_retry_after_error(self) -> None:
        self.assertIsNone(backups.due_at(None, backups.DEFAULT_INTERVAL_DAYS))
        self.assertEqual(backups.due_at(MOMENT, 3), MOMENT + timedelta(days=3))
        self.assertEqual(backups.retry_at(MOMENT), MOMENT + timedelta(seconds=backups.ERROR_RETRY_SECONDS))

    def test_scheduler_ticks_at_start_survives_a_failing_tick_and_stops(self) -> None:
        ticks: list[int] = []
        ticked_twice = threading.Event()

        def tick() -> None:
            ticks.append(1)
            if len(ticks) == 1:
                raise RuntimeError("synthetic failure")
            ticked_twice.set()

        scheduler = backups.Scheduler(tick, poll_seconds=0.01)
        with patch("sys.stderr") as stderr:
            scheduler.start()
            self.assertTrue(ticked_twice.wait(WAIT_SECONDS))
            scheduler.stop()
        self.assertIn("RuntimeError", "".join(call.args[0] for call in stderr.write.call_args_list))
        count = len(ticks)
        threading.Event().wait(0.05)
        self.assertEqual(len(ticks), count)

    def test_command_line_verify_and_restore(self) -> None:
        manifest = backups.create(self.database, self.folder, MOMENT)
        path = self.folder / manifest["name"]

        def run(*argv: str) -> tuple[int, str, str]:
            # The command-line entry point itself, in this process (the reviewed module may exist only in memory).
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = backups.main(list(argv))
            return code, out.getvalue(), err.getvalue()

        code, out, err = run("verify", str(path))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["sha256"], manifest["sha256"])
        target = self.root / "cli_restored.sqlite3"
        code, out, err = run("restore", str(path), str(target))
        self.assertEqual(code, 0, err)
        self.assertEqual(bodies(target), bodies(self.database))
        code, out, err = run("restore", str(path), str(target))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("Refused: Restore writes only to a new path", err)
        with self.assertRaises(SystemExit):
            run("delete", str(path))

class BackupServiceTests(unittest.TestCase):
    """The real HTTP server and Store on a disposable synthetic instance."""

    def setUp(self) -> None:
        self.folder = Path(temporary_directory(self, prefix="backup-service-").name)
        self.config = self.instance(self.folder)
        self.server, self.worker = self.serve(self.config)

    @staticmethod
    def instance(folder: Path) -> Path:
        config = folder / "config.json"
        config.write_text(json.dumps({"title": "Synthetic backup QA", "project_label": "Disposable", "state_dir": "state",
            "participants": [{"id": "human", "name": "Synthetic human", "kind": "human"},
                             {"id": "agent", "name": "Synthetic agent", "kind": "agent"}], "source_roots": [], "source_files": []}))
        return config

    def serve(self, config: Path, *, schedule: bool = False) -> tuple[Any, threading.Thread]:
        server = backend.make_server(config, 0, backup_schedule=schedule)
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        worker.start()

        def stop() -> None:
            try:
                if worker.is_alive():
                    server.shutdown()
                    worker.join(timeout=WAIT_SECONDS)
            finally:
                server.server_close()
            self.assertFalse(worker.is_alive())

        self.addCleanup(stop)
        return server, worker

    def call(self, actor: str, method: str, path: str, body: dict[str, Any] | None = None, config: Path | None = None) -> Any:
        return cli.request(config or self.config, actor, method, path, body)

    def refused(self, status: int, actor: str, method: str, path: str, body: dict[str, Any] | None = None) -> str:
        with self.assertRaises(cli.RequestError) as caught:
            self.call(actor, method, path, body)
        self.assertEqual(caught.exception.status, status, str(caught.exception))
        return str(caught.exception)

    @property
    def state(self) -> Path:
        return self.folder / "state"

    def test_backup_controls_are_for_the_human_only(self) -> None:
        for method, path, body in (("GET", "/api/backups", None), ("POST", "/api/backups/run", {}),
                                   ("POST", "/api/backups/settings", {"interval_days": 5, "version": 1})):
            self.refused(403, "agent", method, path, body)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=WAIT_SECONDS)
        connection.request("GET", "/api/backups", headers={"Host": self.server.host})
        self.assertEqual(connection.getresponse().status, 401)
        connection.close()
        self.assertFalse(any((self.state / backups.DIRECTORY_NAME).glob("*.sqlite3")) if (self.state / backups.DIRECTORY_NAME).exists() else False)

    def test_settings_default_validation_and_version(self) -> None:
        status = self.call("human", "GET", "/api/backups")
        self.assertEqual(status["settings"]["interval_days"], backups.DEFAULT_INTERVAL_DAYS)
        self.assertEqual(status["settings"]["version"], 1)
        self.assertIsNone(status["next_due_at"])
        for body in ({"interval_days": 0, "version": 1}, {"interval_days": backups.MAX_INTERVAL_DAYS + 1, "version": 1},
                     {"interval_days": "3", "version": 1}, {"interval_days": 2.5, "version": 1}, {"interval_days": True, "version": 1},
                     {"interval_days": 3}, {"interval_days": 3, "version": 1, "extra": 1}, {"interval_days": 3, "version": 0}):
            self.refused(400, "human", "POST", "/api/backups/settings", body)
        self.refused(409, "human", "POST", "/api/backups/settings", {"interval_days": 5, "version": 2})
        saved = self.call("human", "POST", "/api/backups/settings", {"interval_days": 5, "version": 1})
        self.assertEqual((saved["settings"]["interval_days"], saved["settings"]["version"], saved["settings"]["updated_by"]), (5, 2, "human"))
        made = self.call("human", "POST", "/api/backups/run", {})
        created = datetime.fromisoformat(made["backups"][0]["created_at"])
        self.assertEqual(datetime.fromisoformat(made["next_due_at"]), created + timedelta(days=5))
        self.refused(400, "human", "POST", "/api/backups/run", {"now": True})

    def test_manual_backup_status_is_read_from_disk(self) -> None:
        self.call("agent", "POST", "/api/messages", {"channel_id": "workspace", "body": "Synthetic record", "client_id": uuid4().hex})
        status = self.call("human", "POST", "/api/backups/run", {})
        self.assertEqual(len(status["backups"]), 1)
        self.assertEqual((status["last_success"]["trigger"], status["last_success"]["actor_id"]), ("manual", "human"))
        self.assertIsNone(status["last_error"])
        name = status["backups"][0]["name"]
        self.assertEqual(status["last_success"]["file_name"], name)
        backups.verify(self.state / backups.DIRECTORY_NAME / name)
        # The human removes the file by hand (synthetic fixture): the status says so instead of trusting the record.
        (self.state / backups.DIRECTORY_NAME / name).unlink()
        status = self.call("human", "GET", "/api/backups")
        self.assertEqual(status["backups"], [])
        self.assertEqual([run["file_name"] for run in status["missing_from_disk"]], [name])
        self.assertIsNone(status["next_due_at"])
        self.assertEqual(self.server.store.scheduled_backup()["status"], "success")
        self.assertEqual(len(self.call("human", "GET", "/api/backups")["backups"]), 1)

    def test_failures_are_recorded_visible_and_retried_later(self) -> None:
        folder = backups.directory(self.state)
        elsewhere = Path(temporary_directory(self, prefix="backup-elsewhere-").name)
        # A genuine refusal: the backup folder is replaced by a symbolic link (synthetic fixture).
        folder.rmdir()
        folder.symlink_to(elsewhere, target_is_directory=True)
        message = self.refused(503, "human", "POST", "/api/backups/run", {})
        self.assertIn("Backup failed: The backup folder must not be a symbolic link", message)
        status = self.call("human", "GET", "/api/backups")
        self.assertEqual(status["folder_error"], "The backup folder must not be a symbolic link")
        self.assertEqual(status["last_error"]["trigger"], "manual")
        self.assertIsNone(status["last_success"])
        self.assertEqual(list(elsewhere.iterdir()), [])
        failed = self.server.store.scheduled_backup()
        self.assertEqual((failed["status"], failed["trigger"]), ("error", "schedule"))
        # A failing folder or disk does not get a new attempt every poll.
        self.assertIsNone(self.server.store.scheduled_backup())
        status = self.call("human", "GET", "/api/backups")
        self.assertIsNotNone(status["retry_not_before"])
        self.assertEqual(sum(run["status"] == "error" for run in status["recent_runs"]), 2)
        folder.unlink()
        status = self.call("human", "POST", "/api/backups/run", {})
        self.assertIsNone(status["folder_error"])
        self.assertGreater(status["last_success"]["id"], status["last_error"]["id"])
        # A SIMULATED full disk (the write is patched to fail): the reason is shown without any file path.
        full = OSError(28, "No space left on device", str(folder / "secret-path-must-not-leak"))
        with patch.object(backups, "_create_private", side_effect=full):
            message = self.refused(503, "human", "POST", "/api/backups/run", {})
        self.assertIn("Backup failed: OSError: No space left on device", message)
        self.assertNotIn("secret-path-must-not-leak", json.dumps(self.call("human", "GET", "/api/backups")))
        # After the retry wait the schedule tries again by itself (failure time moved back; backup file removed).
        long_ago = datetime.now(timezone.utc) - timedelta(seconds=backups.ERROR_RETRY_SECONDS + 1)
        with self.server.store.connection(write=True) as db:
            db.execute("UPDATE backup_runs SET finished_at=? WHERE status='error' AND trigger='schedule'", (long_ago.isoformat(),))
        (folder / status["backups"][0]["name"]).unlink()
        self.assertEqual(self.server.store.scheduled_backup()["status"], "success")

    def test_interrupted_run_is_reported_and_a_second_run_waits(self) -> None:
        with patch.object(backups, "create", side_effect=Interrupted()), self.assertRaises(Interrupted):
            self.server.store._run_backup("manual", "human")
        status = self.call("human", "GET", "/api/backups")
        self.assertEqual([run["status"] for run in status["interrupted_runs"]], ["running"])
        self.assertFalse(status["running"])
        release = threading.Event()
        entered = threading.Event()
        real_create = backups.create

        def slow_create(*args: Any) -> dict[str, Any]:
            entered.set()
            release.wait(WAIT_SECONDS)
            return real_create(*args)

        with patch.object(backups, "create", side_effect=slow_create):
            first = threading.Thread(target=self.call, args=("human", "POST", "/api/backups/run", {}))
            first.start()
            self.assertTrue(entered.wait(WAIT_SECONDS))
            self.assertTrue(self.call("human", "GET", "/api/backups")["running"])
            self.refused(409, "human", "POST", "/api/backups/run", {})
            self.assertIsNone(self.server.store.scheduled_backup())
            release.set()
            first.join(WAIT_SECONDS)
        status = self.call("human", "GET", "/api/backups")
        self.assertEqual(len(status["backups"]), 1)
        self.assertEqual(len(status["interrupted_runs"]), 1)

    def test_scheduler_in_a_running_server_makes_the_first_backup_and_stops_cleanly(self) -> None:
        folder = Path(temporary_directory(self, prefix="backup-scheduled-").name)
        config = self.instance(folder)
        server, worker = self.serve(config, schedule=True)
        deadline = time.monotonic() + WAIT_SECONDS
        status = self.call("human", "GET", "/api/backups", config=config)
        while not status["backups"] and time.monotonic() < deadline:
            threading.Event().wait(0.05)
            status = self.call("human", "GET", "/api/backups", config=config)
        self.assertEqual(len(status["backups"]), 1)
        self.assertEqual(status["last_success"]["trigger"], "schedule")
        self.assertIsNone(status["last_success"]["actor_id"])
        server.shutdown()
        worker.join(WAIT_SECONDS)
        server.server_close()
        self.assertFalse(server.backup_scheduler._thread.is_alive())

    def test_a_server_that_stays_open_after_a_failed_drain_keeps_its_schedule(self) -> None:
        folder = Path(temporary_directory(self, prefix="backup-drain-").name)
        server, worker = self.serve(self.instance(folder), schedule=True)
        server.shutdown()
        worker.join(WAIT_SECONDS)
        held = server.adapters
        server.adapters = types.SimpleNamespace(close=lambda seconds: False)  # provider work that does not drain
        with self.assertRaisesRegex(backend.APIError, "did not drain"):
            server.server_close()
        self.assertTrue(server.backup_scheduler._thread.is_alive())
        server.adapters = held
        server.server_close()
        self.assertFalse(server.backup_scheduler._thread.is_alive())

    def test_backup_during_concurrent_writes_restores_into_a_working_instance(self) -> None:
        decision = self.call("agent", "POST", "/api/decisions", {"channel_id": "workspace", "title": "Synthetic decision", "body": "Plan", "source": "synthetic", "evidence": "synthetic"})
        task = self.call("agent", "POST", "/api/tasks", {"channel_id": "workspace", "title": "Synthetic task", "body": "Do it"})
        self.call("human", "POST", "/api/personal/draft", {"channel_id": "workspace", "body": "Synthetic draft", "version": 0})
        stop = threading.Event()
        errors: list[str] = []

        def writer() -> None:
            while not stop.is_set():
                try:
                    self.call("agent", "POST", "/api/messages", {"channel_id": "workspace", "body": "written during backup", "client_id": uuid4().hex})
                except cli.RequestError as error:
                    errors.append(str(error))

        thread = threading.Thread(target=writer)
        thread.start()
        try:
            for _ in range(3):
                status = self.call("human", "POST", "/api/backups/run", {})
        finally:
            stop.set()
            thread.join(WAIT_SECONDS)
        self.assertEqual(errors, [])
        newest = self.state / backups.DIRECTORY_NAME / status["backups"][0]["name"]
        # Restore into a NEW instance folder, as the human would before swapping it in with the app stopped.
        restored = Path(temporary_directory(self, prefix="backup-restored-").name)
        (restored / "state").mkdir(mode=0o700)
        shutil.copytree(self.state / "credentials", restored / "state" / "credentials")  # synthetic fixture tokens
        result = backups.restore(newest, restored / "state" / "collaboration.sqlite3")
        self.assertEqual(result["integrity_check"], "ok")
        config = self.instance(restored)
        self.serve(config)
        live = self.call("human", "GET", "/api/messages?channel_id=workspace")["messages"]
        again = self.call("human", "GET", "/api/messages?channel_id=workspace", config=config)["messages"]
        self.assertGreater(len(again), 0)
        # The restored history is exactly the live history up to the snapshot: a whole prefix, nothing torn.
        self.assertEqual(again, live[: len(again)])
        export = self.call("human", "GET", "/api/export", config=config)
        self.assertEqual([row["id"] for row in export["decisions"]], [decision["id"]])
        self.assertEqual([row["id"] for row in export["tasks"]], [task["id"]])
        self.assertEqual(export["personal"]["drafts"][0]["body"], "Synthetic draft")
        self.assertEqual(stat.S_IMODE((restored / "state" / "collaboration.sqlite3").stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
