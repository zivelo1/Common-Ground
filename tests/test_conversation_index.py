"""Real-store coverage, changed-history and audience checks for the derived index."""
import base64
import gzip
import io
import json
import subprocess
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qa_support import temporary_directory

import history_archive
from store import MAX_INDEX_BYTES, MAX_PAGE, APIError, Store, load_config


class ConversationIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = temporary_directory(self, prefix="conversation-index-")
        self.root = Path(self.temp.name)
        config = self.root / "config.json"
        config.write_text(json.dumps({"title": "Synthetic board", "project_label": "Offline", "state_dir": "state",
            "participants": [{"id": ident, "name": ident, "kind": kind}
                             for ident, kind in [("human", "human"), ("a", "agent"), ("b", "agent")]]}))
        self.store = Store(load_config(config))
        self.agent = {"id": "a", "name": "a", "kind": "agent"}
        self.outsider = {"id": "b", "name": "b", "kind": "agent"}
        self.human = {"id": "human", "name": "human", "kind": "human"}
        self.private = self.store.add_channel(self.human,
            {"title": "Synthetic private", "members": ["a"]})["id"]

    def post(self, text, channel="workspace"):
        return self.store.add_message(self.agent, {"channel_id": channel, "body": text,
                                                   "client_id": str(self.counter())})

    def counter(self):
        self._counter = getattr(self, "_counter", 0) + 1
        return self._counter

    def test_pages_cover_every_message_with_original_versions_and_no_read_ack(self):
        ids = [self.post("Synthetic café שלום " + str(i))["id"] for i in range(MAX_PAGE + 3)]
        first = self.store.conversation_index(self.human, "workspace")
        self.assertEqual(len(first["messages"]), MAX_PAGE)
        self.assertFalse(first["coverage"]["complete"])
        last = self.store.conversation_index(self.human, "workspace", after=first["next_after"],
                                              snapshot=first["snapshot"])
        self.assertTrue(last["coverage"]["page_reaches_end"])
        self.assertFalse(last["coverage"]["complete"])
        self.assertEqual(last["coverage"]["omitted_before"], MAX_PAGE)
        self.assertEqual([x["id"] for x in first["messages"] + last["messages"]], ids)
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM read_markers").fetchone()[0], 0)
            self.assertGreater(db.execute("SELECT COUNT(*) FROM message_retrievals").fetchone()[0], 0)

    def test_a_tail_request_cannot_claim_complete_history(self):
        ids = [self.post(str(i))["id"] for i in range(3)]
        tail = self.store.conversation_index(self.agent, "workspace", after=ids[1])
        self.assertFalse(tail["coverage"]["complete"])
        self.assertEqual(tail["coverage"]["omitted_before"], 2)
        self.assertTrue(tail["coverage"]["page_reaches_end"])

    def test_private_index_does_not_include_workspace_sources(self):
        with self.store.connection(write=True) as db:
            db.execute("""INSERT INTO sources(id,channel_id,title,path,status,version,checked_at,
                error,selection_json,warnings_json,unindexed_fields) VALUES
                ('public-source','workspace','Synthetic public source','synthetic.txt','missing',1,
                 '2026-10-16T00:00:00Z','','{}','[]',0)""")
        result = self.store.conversation_index(self.agent, self.private)
        self.assertEqual(result["sources"], [])

    def test_former_author_rename_invalidates_old_pages(self):
        self.post("Retained author's message")
        with self.store.connection(write=True) as db:
            db.execute("DELETE FROM members WHERE channel_id='workspace' AND participant_id='a'")
        page = self.store.conversation_index(self.human, "workspace")
        with self.store.connection(write=True) as db:
            db.execute("UPDATE participants SET name='Renamed former author' WHERE id='a'")
        with self.assertRaises(APIError) as error:
            self.store.conversation_index(self.human, "workspace", snapshot=page["snapshot"])
        self.assertEqual(error.exception.status, 409)

    def test_an_edit_behind_the_page_cursor_refuses_the_old_snapshot(self):
        original = self.post("Synthetic original")
        page = self.store.conversation_index(self.agent, "workspace", limit=1)
        self.store.edit_message(self.agent, original["id"],
            {"body": "Synthetic correction", "version": 1, "edit_id": "correction"})
        with self.assertRaises(APIError) as error:
            self.store.conversation_index(self.agent, "workspace", after=page["next_after"],
                                             snapshot=page["snapshot"])
        self.assertEqual(error.exception.status, 409)
        record = self.store.conversation_record(self.agent, "workspace", original["id"])
        self.assertEqual(record["original_body"], "Synthetic original")
        self.assertEqual(record["message"]["body"], "Synthetic correction")
        self.assertEqual(record["edits"][0]["body_before"], "Synthetic original")

    def test_private_content_does_not_change_public_snapshot_or_suggestions(self):
        self.post("Same text")
        before = self.store.conversation_index(self.agent, "workspace")
        secret = self.post("Same text", self.private)
        after = self.store.conversation_index(self.agent, "workspace")
        self.assertEqual(before["snapshot"], after["snapshot"])
        self.assertEqual(after["suggestions"], [])
        with self.assertRaises(APIError) as error:
            self.store.conversation_record(self.agent, "workspace", secret["id"])
        self.assertEqual(error.exception.status, 404)
        with self.assertRaises(APIError) as error:
            self.store.conversation_index(self.outsider, self.private)
        self.assertEqual(error.exception.status, 404)

    def test_duplicate_suggestions_never_change_messages_or_approve_records(self):
        ids = [self.post("Synthetic repeated instruction")["id"] for _ in range(2)]
        decision = self.store.add_decision(self.agent,
            {"channel_id": "workspace", "title": "Synthetic proposal", "body": "Requires human"})
        result = self.store.conversation_index(self.agent, "workspace")
        repeated = [row for row in result["suggestions"] if row["kind"] == "exact_repetition"]
        self.assertEqual(repeated[0]["message_ids"], ids)
        self.assertEqual(result["decisions"][0]["status"], "proposed")
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT status FROM decisions WHERE id=?",
                                        (decision["id"],)).fetchone()[0], "proposed")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)

    def test_task_and_knowledge_changes_invalidate_the_snapshot(self):
        self.post("Synthetic fact reference")
        task = self.store.add_task(self.agent,
            {"channel_id": "workspace", "title": "Synthetic task", "owner_id": "a"})
        before = self.store.conversation_index(self.agent, "workspace")
        self.store.task_status(self.agent, task["id"], {"version": task["version"], "status": "done"})
        with self.assertRaises(APIError):
            self.store.conversation_index(self.agent, "workspace", snapshot=before["snapshot"])
        before = self.store.conversation_index(self.agent, "workspace")
        self.store.add_knowledge(self.agent, {"channel_id": "workspace", "title": "Synthetic conflict",
            "body": "Unresolved", "status": "disputed"})
        with self.assertRaises(APIError):
            self.store.conversation_index(self.agent, "workspace", snapshot=before["snapshot"])
        self.assertEqual(self.store.conversation_index(self.agent, "workspace")["suggestions"][0]["kind"],
                         "knowledge_needs_review")

    def test_removed_members_cannot_read_even_with_a_valid_snapshot(self):
        self.post("Synthetic private", self.private)
        page = self.store.conversation_index(self.agent, self.private)
        with self.store.connection(write=True) as db:
            db.execute("DELETE FROM members WHERE channel_id=? AND participant_id='a'", (self.private,))
        with self.assertRaises(APIError) as error:
            self.store.conversation_index(self.agent, self.private, snapshot=page["snapshot"])
        self.assertEqual(error.exception.status, 404)

    def test_limits_and_snapshot_are_strict(self):
        for kwargs in ({"after": True}, {"after": -1}, {"limit": 0}, {"limit": MAX_PAGE + 1},
                       {"snapshot": "not-a-digest"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(APIError):
                self.store.conversation_index(self.agent, "workspace", **kwargs)

    def test_real_http_cli_and_mcp_have_the_same_scoped_snapshot(self):
        import mcp
        from app import make_server
        from cli import RequestError, request

        self.post("Synthetic Unicode café שלום")
        server = make_server(self.root / "config.json")
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            config = self.root / "config.json"
            raw = request(config, "a", "GET", "/api/conversation-index?channel_id=workspace&limit=1")
            direct = self.store.conversation_index(self.agent, "workspace", limit=1)
            self.assertEqual(raw["snapshot"], direct["snapshot"])
            via_mcp = mcp.call(config, "a", "collab_conversation_index", {"channel_id": "workspace", "limit": 1})
            self.assertEqual(via_mcp["snapshot"], direct["snapshot"])
            result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve().parents[1] / "cli.py"),
                "--config", str(config), "--as", "a", "conversation-index", "--channel", "workspace", "--limit", "1"],
                capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout)["snapshot"], direct["snapshot"])
            message_id = raw["messages"][0]["id"]
            full = mcp.call(config, "a", "collab_conversation_record", {"channel_id": "workspace", "message_id": message_id})
            self.assertEqual(full["original_body"], "Synthetic Unicode café שלום")
            archive = self.root / 'history.json.gz'
            result = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve().parents[1] / 'cli.py'),
                '--config', str(config), '--as', 'a', 'conversation-archive', '--channel', 'workspace',
                '--output', str(archive)], capture_output=True, text=True, check=True)
            self.assertTrue(json.loads(result.stdout)['verified'])
            self.assertEqual(history_archive.decode(archive.read_bytes(), MAX_INDEX_BYTES)['counts']['messages'], 1)
            self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
            for path in ("/api/conversation-index?channel_id=workspace&limit=0",
                         "/api/conversation-index?channel_id=workspace&after=-1",
                         "/api/conversation-index?channel_id=workspace&unknown=1"):
                with self.subTest(path=path), self.assertRaises(RequestError):
                    request(config, "a", "GET", path)
            with self.assertRaises(RequestError) as error:
                request(config, "b", "GET", "/api/conversation-index?channel_id=" + self.private)
            self.assertEqual(error.exception.status, 404)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
            self.assertFalse(thread.is_alive())

    def test_archive_round_trips_original_edits_and_attachment_bytes_only_in_scope(self):
        from PIL import Image

        message = self.post('Synthetic original café שלום')
        self.store.edit_message(self.agent, message['id'],
            {'body': 'Synthetic corrected café שלום', 'version': 1, 'edit_id': 'archive-edit'})
        self.post('PRIVATE CANARY', self.private)
        data = io.BytesIO()
        Image.new('RGB', (2, 2), 'red').save(data, format='PNG')
        image = self.store.add_image(self.agent, {'channel_id': 'workspace', 'client_id': 'archive-image',
            'name': 'synthetic.png', 'data': base64.b64encode(data.getvalue()).decode()})
        compressed = self.store.conversation_archive(self.human, 'workspace')
        payload = history_archive.decode(compressed, MAX_INDEX_BYTES)
        self.assertNotIn('PRIVATE CANARY', json.dumps(payload))
        self.assertNotIn('token_hash', json.dumps(payload))
        self.assertEqual(payload['tables']['messages'][0]['body'], 'Synthetic corrected café שלום')
        self.assertEqual(payload['tables']['message_edit_state'][0]['original_body'], 'Synthetic original café שלום')
        self.assertEqual(payload['tables']['message_edits'][0]['body_before'], 'Synthetic original café שלום')
        retained = payload['tables']['message_images'][0]
        self.assertEqual(retained['id'], image['id'])
        self.assertEqual(base64.b64decode(retained['data_base64']), data.getvalue())
        self.assertEqual(compressed, self.store.conversation_archive(self.human, 'workspace'))
        with self.assertRaises(APIError) as error:
            self.store.conversation_archive(self.outsider, self.private)
        self.assertEqual(error.exception.status, 404)
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM read_markers').fetchone()[0], 0)

    def test_archive_rejects_changed_payload_corruption_and_decompression_overflow(self):
        archive = self.store.conversation_archive(self.agent, 'workspace')
        envelope = json.loads(gzip.decompress(archive))
        envelope['payload']['channel']['title'] = 'Changed bytes'
        with self.assertRaises(ValueError):
            history_archive.decode(gzip.compress(json.dumps(envelope).encode()), MAX_INDEX_BYTES)
        with self.assertRaises((OSError, ValueError, EOFError)):
            history_archive.decode(archive[:-1], MAX_INDEX_BYTES)
        with self.assertRaises(ValueError):
            history_archive.decode(archive, 1)


if __name__ == "__main__":
    unittest.main()
