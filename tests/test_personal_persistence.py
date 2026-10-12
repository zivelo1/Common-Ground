"""Synthetic owner-only persistence, lost acknowledgement and shutdown checks."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import sqlite3
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from qa_support import temporary_directory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Canonical app imports follow its explicit path for direct test execution.
from app import Handler, make_server
from store import MAX_MESSAGE, Store, load_config

REQUEST_TIMEOUT_SECONDS = 5

# Frozen pre-personal schema fixture. It must not follow the current Store's
# initializer: publishing a new Store must still exercise a real old database.
LEGACY_SCHEMA_SQL = """
    CREATE TABLE participants (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE);
    CREATE TABLE channels (id TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL);
    CREATE TABLE members (
      channel_id TEXT REFERENCES channels(id), participant_id TEXT REFERENCES participants(id),
      PRIMARY KEY(channel_id,participant_id));
    CREATE TABLE messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id TEXT REFERENCES channels(id),
      author_id TEXT REFERENCES participants(id), body TEXT NOT NULL, client_id TEXT NOT NULL,
      created_at TEXT NOT NULL, UNIQUE(author_id,client_id));
    CREATE INDEX messages_channel ON messages(channel_id,id);
    CREATE TABLE read_markers (
      channel_id TEXT REFERENCES channels(id), participant_id TEXT REFERENCES participants(id),
      message_id INTEGER NOT NULL, PRIMARY KEY(channel_id,participant_id));
    CREATE TABLE decisions (
      id TEXT PRIMARY KEY, channel_id TEXT REFERENCES channels(id), title TEXT NOT NULL,
      body TEXT NOT NULL, status TEXT NOT NULL, source TEXT NOT NULL, evidence TEXT NOT NULL,
      author_id TEXT REFERENCES participants(id), approved_by TEXT, implemented_by TEXT,
      verified_by TEXT, updated_at TEXT NOT NULL, version INTEGER NOT NULL);
    CREATE TABLE decision_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT, decision_id TEXT REFERENCES decisions(id),
      actor_id TEXT REFERENCES participants(id), created_at TEXT NOT NULL,
      before_json TEXT, after_json TEXT NOT NULL);
    CREATE TABLE tasks (
      id TEXT PRIMARY KEY, channel_id TEXT REFERENCES channels(id), title TEXT NOT NULL,
      body TEXT NOT NULL, owner_id TEXT REFERENCES participants(id), status TEXT NOT NULL,
      updated_at TEXT NOT NULL, version INTEGER NOT NULL);
    CREATE TABLE sources (
      id TEXT PRIMARY KEY, channel_id TEXT REFERENCES channels(id), title TEXT NOT NULL,
      path TEXT NOT NULL, status TEXT NOT NULL, sha256 TEXT, version INTEGER NOT NULL,
      checked_at TEXT NOT NULL, mtime_ns INTEGER, git_revision TEXT, git_blob TEXT,
      error TEXT NOT NULL, selection_json TEXT NOT NULL, warnings_json TEXT NOT NULL,
      unindexed_fields INTEGER NOT NULL, headings_json TEXT NOT NULL DEFAULT '[]',
      projection_json TEXT NOT NULL DEFAULT 'null');
    CREATE TABLE source_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT REFERENCES sources(id),
      actor_id TEXT REFERENCES participants(id), created_at TEXT NOT NULL, snapshot_json TEXT NOT NULL);
    CREATE TABLE knowledge (
      id TEXT PRIMARY KEY, channel_id TEXT REFERENCES channels(id), title TEXT NOT NULL,
      body TEXT NOT NULL, kind TEXT NOT NULL, outcome TEXT NOT NULL, status TEXT NOT NULL,
      source TEXT NOT NULL, evidence TEXT NOT NULL, source_refs_json TEXT NOT NULL,
      conflicts_json TEXT NOT NULL, author_id TEXT REFERENCES participants(id),
      updated_by TEXT REFERENCES participants(id), updated_at TEXT NOT NULL, version INTEGER NOT NULL,
      stale INTEGER NOT NULL, stale_reasons_json TEXT NOT NULL);
    CREATE TABLE knowledge_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT, knowledge_id TEXT REFERENCES knowledge(id),
      actor_id TEXT REFERENCES participants(id), created_at TEXT NOT NULL, action TEXT NOT NULL,
      before_json TEXT, after_json TEXT NOT NULL);
"""
PERSONAL_TABLES = {"personal_drafts", "personal_outbox", "personal_preferences"}


class PersonalPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = temporary_directory(self, prefix="personal-persistence-")
        self.root = Path(self.directory.name)
        self.config = self.root / "config.json"
        self.configuration = {"title": "Synthetic persistence", "project_label": "Disposable only", "state_dir": "state",
            "participants": [{"id": "owner", "name": "Synthetic owner", "kind": "human"},
                             {"id": "a", "name": "Synthetic A", "kind": "agent"},
                             {"id": "b", "name": "Synthetic B", "kind": "agent"}]}
        self.config.write_text(json.dumps(self.configuration))
        self.server = None
        self.thread = None
        self.addCleanup(self.stop)
        self.start()
        self.tokens = {actor: json.loads((self.root / "state" / "credentials" / f"{actor}.json").read_text())["token"] for actor in ("owner", "a", "b")}

    def start(self):
        self.server = make_server(self.config)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            if self.thread.is_alive():
                self.server.shutdown()
            self.server.server_close()
            self.thread.join(REQUEST_TIMEOUT_SECONDS)
            self.assertFalse(self.thread.is_alive())
            self.server = None

    def tearDown(self):
        self.stop()
        self.directory.cleanup()

    def call(self, path, body=None, actor="owner"):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=REQUEST_TIMEOUT_SECONDS)
        headers = {"Authorization": "Bearer " + self.tokens[actor]}
        method = "GET" if body is None else "POST"
        if body is not None:
            headers.update({"Content-Type": "application/json", "X-Collab-Request": "1"})
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        try:
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def ok(self, path, body=None, actor="owner"):
        status, result = self.call(path, body, actor)
        self.assertEqual(status, 200, result)
        return result

    def test_retry_old_message_preserves_newer_draft_revision_and_idempotence(self):
        draft = self.ok("/api/personal/draft", {"channel_id": "workspace", "body": "Newer synthetic draft", "version": 0}, "a")
        data = {"channel_id": "workspace", "body": "Original failed synthetic message", "client_id": "preserved-retry", "draft_version": draft["version"], "preserve_draft": True}
        queued = self.ok("/api/personal/enqueue", data, "a")
        self.assertEqual(queued["draft"], draft)
        first = self.ok("/api/personal/send", {"client_id": data["client_id"]}, "a")
        repeated = self.ok("/api/personal/send", {"client_id": data["client_id"]}, "a")
        self.assertEqual(first["id"], repeated["id"])
        self.assertEqual(self.ok("/api/personal/enqueue", data, "a")["delivered"]["id"], first["id"])
        personal = self.ok("/api/personal", actor="a")
        self.assertEqual(personal["drafts"], [draft])
        self.assertEqual(personal["outbox"], [])
        self.assertEqual([row["body"] for row in self.ok("/api/messages?channel_id=workspace", actor="a")["messages"]], [data["body"]])

    def test_preserved_retry_rejects_stale_revision_and_invalid_flag(self):
        first = self.ok("/api/personal/draft", {"channel_id": "workspace", "body": "First synthetic draft", "version": 0}, "a")
        latest = self.ok("/api/personal/draft", {"channel_id": "workspace", "body": "Other-tab synthetic edit", "version": first["version"]}, "a")
        data = {"channel_id": "workspace", "body": "Original synthetic retry", "client_id": "stale-preserved-retry", "draft_version": first["version"], "preserve_draft": True}
        self.assertEqual(self.call("/api/personal/enqueue", data, "a")[0], 409)
        for changed in ({**data, "preserve_draft": "yes"}, {k: v for k, v in data.items() if k != "draft_version"}):
            self.assertEqual(self.call("/api/personal/enqueue", changed, "a")[0], 400)
        personal = self.ok("/api/personal", actor="a")
        self.assertEqual(personal["drafts"], [latest])
        self.assertEqual(personal["outbox"], [])

    def test_owner_only_private_drafts_preferences_and_export(self):
        private = self.ok("/api/channels", {"title": "Synthetic private", "members": ["a"]})["id"]
        draft = self.ok("/api/personal/draft", {"channel_id": private, "body": "OWNER_UNPOSTED_PRIVATE", "version": 0})
        self.assertEqual(draft["version"], 1)
        self.ok("/api/personal/preferences", {"values": {"theme": "dark", "channel_id": private}, "version": 0})
        queued = self.ok("/api/personal/enqueue", {"channel_id": private, "body": "OWNER_QUEUE_PRIVATE", "client_id": "private-retry"})
        self.assertEqual(queued["outbox"]["client_id"], "private-retry")
        for actor in ("a", "b"):
            personal = self.ok("/api/personal", actor=actor)
            self.assertEqual(personal, {"drafts": [], "outbox": [], "preferences": {"values": {}, "version": 0, "updated_at": None}})
            self.assertNotIn("OWNER_UNPOSTED_PRIVATE", json.dumps(self.ok("/api/export", actor=actor)))
            self.assertNotIn("OWNER_QUEUE_PRIVATE", json.dumps(self.ok("/api/export", actor=actor)))
        self.assertEqual(self.call("/api/personal/draft", {"channel_id": private, "body": "intrusion", "version": 0}, "b")[0], 404)
        self.assertEqual(self.call("/api/personal/preferences", {"values": {"channel_id": private}, "version": 0}, "b")[0], 404)
        self.assertEqual(self.call("/api/personal/send", {"client_id": "private-retry"}, "a")[0], 404)
        self.assertEqual(self.call("/api/personal/draft", {"channel_id": private, "body": "intrusion", "version": 0, "owner_id": "owner"}, "a")[0], 400)
        self.ok("/api/personal/draft", {"channel_id": private, "body": "A_INDEPENDENT_DRAFT", "version": 0}, "a")
        self.assertEqual(self.ok("/api/personal")["drafts"][0]["body"], "OWNER_UNPOSTED_PRIVATE")
        with self.server.store.connection(write=True) as db:
            db.execute("DELETE FROM members WHERE channel_id=? AND participant_id=?", (private, "a"))
        self.assertEqual(self.ok("/api/personal", actor="a")["drafts"], [])
        self.assertNotIn("OWNER_UNPOSTED_PRIVATE", json.dumps(self.ok("/api/state")))
        self.assertEqual(self.ok("/api/search?q=OWNER_UNPOSTED_PRIVATE")["messages"], [])

    def test_versions_validate_conflicts_and_lost_save_acknowledgement(self):
        original = {"channel_id": "workspace", "body": "First tab", "version": 0}
        first = self.ok("/api/personal/draft", original)
        self.assertEqual(self.ok("/api/personal/draft", original), first)
        self.assertEqual(self.call("/api/personal/draft", {**original, "body": "Second stale tab"})[0], 409)
        self.ok("/api/personal/draft", {**original, "body": "Changed with current version", "version": 1})
        for version in (True, -1, "1"):
            self.assertEqual(self.call("/api/personal/draft", {**original, "version": version})[0], 400)
        self.assertEqual(self.call("/api/personal/draft", {**original, "body": "x" * (MAX_MESSAGE + 1)})[0], 400)
        self.assertEqual(self.call("/api/personal/preferences", {"version": 0, "values": {"theme": "unsupported"}})[0], 400)
        preference = self.ok("/api/personal/preferences", {"version": 0, "values": {"theme": "dark"}})
        self.assertEqual(self.ok("/api/personal/preferences", {"version": 0, "values": {"theme": "dark"}}), preference)
        self.assertEqual(self.call("/api/personal/preferences", {"version": 0, "values": {"theme": "light"}})[0], 409)

    def test_cleared_drafts_keep_versions_without_consuming_active_quota(self):
        private = self.ok("/api/channels", {"title": "Synthetic cleared draft", "members": ["a"]})["id"]
        with patch("store.MAX_PERSONAL_ITEMS", 1):
            self.ok("/api/personal/draft", {"channel_id": private, "body": "First active draft", "version": 0})
            self.assertEqual(self.call("/api/personal/draft", {"channel_id": "workspace", "body": "Second active draft", "version": 0})[0], 409)
            cleared = self.ok("/api/personal/draft", {"channel_id": private, "body": "", "version": 1})
            self.ok("/api/personal/draft", {"channel_id": "workspace", "body": "Second active draft", "version": 0})
            self.assertEqual(cleared["version"], 2)
            self.assertEqual(self.call("/api/personal/draft", {"channel_id": private, "body": "Stale overwrite", "version": 0})[0], 409)

    def test_enqueue_send_restart_and_concurrent_retry_exactly_once(self):
        body = "One synthetic retry; original identifier retained"
        draft = self.ok("/api/personal/draft", {"channel_id": "workspace", "body": body, "version": 0}, "a")
        request = {"channel_id": "workspace", "body": body, "client_id": "stable-original-id", "draft_version": draft["version"]}
        self.ok("/api/personal/enqueue", request, "a")
        self.ok("/api/personal/enqueue", request, "a")
        self.assertEqual(self.call("/api/personal/enqueue", {**request, "body": "replacement"}, "a")[0], 409)
        self.assertEqual(self.ok("/api/personal", actor="a")["drafts"][0]["body"], "")
        original_origin = self.server.origin
        self.stop()
        self.start()
        self.assertNotEqual(original_origin, self.server.origin)
        self.assertEqual(self.ok("/api/personal", actor="a")["outbox"][0]["client_id"], "stable-original-id")
        with ThreadPoolExecutor(max_workers=8) as pool:
            messages = list(pool.map(lambda _: self.ok("/api/personal/send", {"client_id": "stable-original-id"}, "a"), range(8)))
        self.assertEqual(len({row["id"] for row in messages}), 1)
        self.assertEqual(len(self.ok("/api/messages?channel_id=workspace", actor="a")["messages"]), 1)
        self.assertEqual(self.ok("/api/personal", actor="a")["outbox"], [])
        self.stop()
        self.start()
        self.assertEqual(self.ok("/api/personal/send", {"client_id": "stable-original-id"}, "a")["id"], messages[0]["id"])

    def test_lost_enqueue_and_delivery_responses_leave_durable_exact_state(self):
        body = "Synthetic lost response"
        original_send = Handler._send

        def lose_response(handler, status, payload, **kwargs):
            if status == 200 and handler.path == lost_path:
                raise ConnectionResetError("Synthetic response loss")
            return original_send(handler, status, payload, **kwargs)

        lost_path = "/api/personal/enqueue"
        with patch.object(Handler, "_send", lose_response), self.assertRaises(http.client.RemoteDisconnected):
            self.call(lost_path, {"channel_id": "workspace", "body": body, "client_id": "lost-ack"})
        self.assertEqual(self.ok("/api/personal")["outbox"][0]["client_id"], "lost-ack")
        lost_path = "/api/personal/send"
        with patch.object(Handler, "_send", lose_response), self.assertRaises(http.client.RemoteDisconnected):
            self.call(lost_path, {"client_id": "lost-ack"})
        self.assertEqual(self.ok("/api/personal")["outbox"], [])
        accepted = self.ok("/api/messages?channel_id=workspace")["messages"]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(self.ok(lost_path, {"client_id": "lost-ack"})["id"], accepted[0]["id"])

    def test_restore_versus_send_has_one_atomic_outcome(self):
        self.ok("/api/personal/enqueue", {"channel_id": "workspace", "body": "Synthetic restore race", "client_id": "restore-race"})
        with ThreadPoolExecutor(max_workers=2) as pool:
            send = pool.submit(self.call, "/api/personal/send", {"client_id": "restore-race"})
            restore = pool.submit(self.call, "/api/personal/restore", {"client_id": "restore-race", "version": 0})
            sent, restored = send.result(), restore.result()
        personal = self.ok("/api/personal")
        messages = self.ok("/api/messages?channel_id=workspace")["messages"]
        self.assertEqual(personal["outbox"], [])
        if sent[0] == 200:
            self.assertEqual(restored[0], 409)
            self.assertEqual(len(messages), 1)
        else:
            self.assertEqual(sent[0], 404)
            self.assertEqual(restored[0], 200)
            self.assertEqual(messages, [])
            self.assertEqual(personal["drafts"][0]["body"], "Synthetic restore race")

    def test_discard_removes_only_the_owners_queued_copy(self):
        self.ok("/api/personal/enqueue", {"channel_id": "workspace", "body": "Synthetic unsent text", "client_id": "discard-me"}, "a")
        self.assertEqual(self.call("/api/personal/discard", {"client_id": "discard-me"}, "b")[0], 409)   # not b's copy
        self.assertEqual(self.ok("/api/personal", actor="a")["outbox"][0]["client_id"], "discard-me")
        self.assertEqual(self.ok("/api/personal/discard", {"client_id": "discard-me"}, "a"),
                         {"client_id": "discard-me", "discarded": True})
        self.assertEqual(self.ok("/api/personal", actor="a")["outbox"], [])
        self.assertEqual(self.ok("/api/messages?channel_id=workspace", actor="a")["messages"], [])
        self.assertEqual(self.call("/api/personal/discard", {"client_id": "discard-me"}, "a")[0], 409)   # already gone

    def test_discard_never_removes_a_delivered_message(self):
        self.ok("/api/personal/enqueue", {"channel_id": "workspace", "body": "Synthetic delivered text", "client_id": "kept"})
        sent = self.ok("/api/personal/send", {"client_id": "kept"})
        status, answer = self.call("/api/personal/discard", {"client_id": "kept"})
        self.assertEqual(status, 409, answer)
        self.assertEqual([row["id"] for row in self.ok("/api/messages?channel_id=workspace")["messages"]], [sent["id"]])

    def test_discard_refuses_malformed_input(self):
        for body in ({}, {"client_id": ""}, {"client_id": "x", "extra": 1}, {"client_id": 7}):
            self.assertEqual(self.call("/api/personal/discard", body)[0], 400, body)

    def test_discard_versus_send_has_one_atomic_outcome(self):
        self.ok("/api/personal/enqueue", {"channel_id": "workspace", "body": "Synthetic discard race", "client_id": "discard-race"})
        with ThreadPoolExecutor(max_workers=2) as pool:
            send = pool.submit(self.call, "/api/personal/send", {"client_id": "discard-race"})
            discard = pool.submit(self.call, "/api/personal/discard", {"client_id": "discard-race"})
            sent, discarded = send.result(), discard.result()
        messages = self.ok("/api/messages?channel_id=workspace")["messages"]
        self.assertEqual(self.ok("/api/personal")["outbox"], [])
        if sent[0] == 200:
            self.assertEqual((discarded[0], len(messages)), (409, 1))
        else:
            self.assertEqual((sent[0], discarded[0], messages), (404, 200, []))

    def test_shutdown_drains_open_transaction_and_rejects_later_write(self):
        entered = threading.Event()
        release = threading.Event()
        closed = threading.Event()
        original_save = self.server.store._save_message

        def pending_save(*args):
            result = original_save(*args)
            entered.set()
            if not release.wait(REQUEST_TIMEOUT_SECONDS):
                raise RuntimeError("Synthetic transaction was not released")
            return result

        self.server.store._save_message = pending_save
        # The TCP connection is accepted, but its HTTP headers are not complete.
        late = socket.create_connection(self.server.server_address, timeout=REQUEST_TIMEOUT_SECONDS)
        late.sendall(b"POST /api/messages HTTP/1.1\r\n")
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(self.call, "/api/messages", {"channel_id": "workspace", "body": "Finishes during stop", "client_id": "drained"})
            self.assertTrue(entered.wait(REQUEST_TIMEOUT_SECONDS))
            self.ok("/api/shutdown", {})

            def finish_close():
                self.thread.join(REQUEST_TIMEOUT_SECONDS)
                self.server.server_close()
                closed.set()

            closer = pool.submit(finish_close)
            try:
                headers = f"Host: {self.server.host}\r\nAuthorization: Bearer {self.tokens['owner']}\r\nContent-Type: application/json\r\nX-Collab-Request: 1\r\nContent-Length: 2\r\n\r\n{{}}"
                late.sendall(headers.encode())
                response = http.client.HTTPResponse(late)
                response.begin()
                self.assertEqual(response.status, 503)
                response.read()
                self.assertFalse(closed.is_set(), "Stop completed while a transaction was still active")
                self.assertTrue(self.server.runtime_path.exists())
            finally:
                late.close()
                release.set()
            self.assertEqual(pending.result()[0], 200)
            closer.result()
        self.assertTrue(closed.is_set())
        self.assertFalse(self.server.runtime_path.exists())
        self.assertIsNone(self.server._instance_lock)
        self.server = None
        self.start()
        self.assertEqual([item["body"] for item in self.ok("/api/messages?channel_id=workspace")["messages"]], ["Finishes during stop"])
        with self.server.store.connection() as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_additive_migration_preserves_original_rows(self):
        legacy_config = self.root / "legacy.json"
        legacy_config.write_text(json.dumps({**self.configuration, "state_dir": "legacy-state"}))
        config = load_config(legacy_config)
        state_dir = config["state_dir"]
        state_dir.mkdir(mode=0o700)
        credentials = state_dir / "credentials"
        credentials.mkdir(mode=0o700)
        # Match only the synthetic credentials made by setUp, so initialization
        # cannot replace the historical participant hashes under comparison.
        for person in self.configuration["participants"]:
            name = f"{person['id']}.json"
            data = (self.root / "state" / "credentials" / name).read_bytes()
            fd = os.open(credentials / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
        with self.server.store.connection() as db:
            participants = [dict(row) for row in db.execute("SELECT id,name,kind,token_hash FROM participants ORDER BY id")]
        created_at = "2026-10-05T16:00:00+00:00"
        content = "Synthetic historical evidence only"
        fingerprint = hashlib.sha256(content.encode()).hexdigest()
        decision = {"id": "1" * 32, "channel_id": "workspace", "title": "Synthetic legacy proposal",
            "body": content, "status": "proposed", "source": "Synthetic source", "evidence": content,
            "author_id": "owner", "approved_by": None, "implemented_by": None, "verified_by": None,
            "updated_at": created_at, "version": 1}
        source = {"id": "synthetic-source", "channel_id": "workspace", "title": "Synthetic legacy source",
            "path": "fixture/evidence.md", "status": "available", "sha256": fingerprint, "version": 1,
            "checked_at": created_at, "mtime_ns": None, "git_revision": None, "git_blob": None,
            "error": "", "selection_json": "{}", "warnings_json": "[]", "unindexed_fields": 0,
            "headings_json": "[]", "projection_json": "null"}
        knowledge = {"id": "2" * 32, "channel_id": "workspace", "title": "Synthetic legacy lesson",
            "body": content, "kind": "lesson", "outcome": "worked", "status": "documented",
            "source": "Synthetic source", "evidence": content,
            "source_refs_json": json.dumps([{"source_id": source["id"], "sha256": fingerprint}]),
            "conflicts_json": "[]", "author_id": "owner", "updated_by": "owner", "updated_at": created_at,
            "version": 1, "stale": 0, "stale_reasons_json": "[]"}
        rows = {
            "participants": participants,
            "channels": [{"id": "workspace", "title": "Workspace", "kind": "shared"}],
            "members": [{"channel_id": "workspace", "participant_id": person["id"]} for person in participants],
            "messages": [{"id": 1, "channel_id": "workspace", "author_id": "owner", "body": content,
                          "client_id": "legacy", "created_at": created_at}],
            "read_markers": [{"channel_id": "workspace", "participant_id": "owner", "message_id": 1}],
            "decisions": [decision],
            "decision_history": [{"id": 1, "decision_id": decision["id"], "actor_id": "owner",
                                  "created_at": created_at, "before_json": None, "after_json": json.dumps(decision)}],
            "tasks": [{"id": "3" * 32, "channel_id": "workspace", "title": "Synthetic legacy task",
                       "body": content, "owner_id": "a", "status": "blocked", "updated_at": created_at, "version": 1}],
            "sources": [source],
            "source_history": [{"id": 1, "source_id": source["id"], "actor_id": "owner", "created_at": created_at,
                                "snapshot_json": json.dumps({**source, "content": content})}],
            "knowledge": [knowledge],
            "knowledge_history": [{"id": 1, "knowledge_id": knowledge["id"], "actor_id": "owner",
                                   "created_at": created_at, "action": "created", "before_json": None,
                                   "after_json": json.dumps(knowledge)}],
        }
        database = state_dir / "collaboration.sqlite3"
        fd = os.open(database, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(fd)
        with sqlite3.connect(database) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.executescript(LEGACY_SCHEMA_SQL)
            for table, records in rows.items():
                for record in records:
                    columns = ",".join(record)
                    placeholders = ",".join("?" for _ in record)
                    db.execute(f"INSERT INTO {table}({columns}) VALUES({placeholders})", tuple(record.values()))
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            self.assertEqual(tables, set(rows))
            self.assertFalse(PERSONAL_TABLES & tables, "Migration must begin with no personal tables")
            original_columns = {table: ",".join(row[1] for row in db.execute(f'PRAGMA table_info("{table}")')) for table in tables}
            before = {table: db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall() for table in tables}
            before_counts = {table: len(records) for table, records in before.items()}
            self.assertTrue(all(before_counts.values()), "Every original table must have historical rows to preserve")
        migrated = Store(config)
        with migrated.connection() as db:
            after = {table: [tuple(row) for row in db.execute(f'SELECT {original_columns[table]} FROM "{table}" ORDER BY rowid')] for table in tables}
            self.assertEqual(before, after)
            self.assertEqual([tuple(row) for row in db.execute("SELECT priority,due_date FROM tasks")], [("normal", None)] * before_counts["tasks"])
            self.assertEqual(before_counts, {table: len(records) for table, records in after.items()})
            migrated_tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(PERSONAL_TABLES & migrated_tables, PERSONAL_TABLES)
            for table in PERSONAL_TABLES:
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0], 0)
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_shutdown_does_not_hang_on_incomplete_http_headers(self):
        accepted = threading.Event()
        original_get_request = self.server.get_request

        def record_accept():
            result = original_get_request()
            accepted.set()
            return result

        self.server.get_request = record_accept
        with patch("app.REQUEST_TIMEOUT_SECONDS", 0.1):
            partial = socket.create_connection(self.server.server_address, timeout=REQUEST_TIMEOUT_SECONDS)
            try:
                partial.sendall(b"POST /api/messages HTTP/1.1\r\n")
                self.assertTrue(accepted.wait(REQUEST_TIMEOUT_SECONDS))
                started = time.monotonic()
                self.ok("/api/shutdown", {})
                self.thread.join(REQUEST_TIMEOUT_SECONDS)
                self.server.server_close()
                self.assertLess(time.monotonic() - started, REQUEST_TIMEOUT_SECONDS)
                self.assertFalse(self.server.runtime_path.exists())
            finally:
                partial.close()
        self.server = None


if __name__ == "__main__":
    unittest.main()
