"""Exercise the real stdio adapter and launcher against isolated SQLite instances."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qa_support import temporary_directory

import app
import cli
import mcp

APPLICATION = Path(app.__file__).parent
PARSE_RECURSION_TEST_DEPTH = 20000


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.directory = temporary_directory(self, prefix="collaboration-adapters-")
        self.root = Path(self.directory.name)
        self.config = self.root / "config.json"
        self.source_temp = temporary_directory(self, prefix="adapter-source-", public_sources=True)
        self.evidence_root = Path(self.source_temp.name)
        self.evidence_file = self.evidence_root / "rules.md"
        self.evidence_file.write_text("# Adapter evidence\nA frozen local fixture; not an approval.\n")
        self.config.write_text(json.dumps({"title": "Adapter checks", "project_label": "Test only", "state_dir": "state",
            "participants": [{"id": "owner", "name": "Owner", "kind": "human"},
                             {"id": "a", "name": "Agent A", "kind": "agent"},
                             {"id": "b", "name": "Agent B", "kind": "agent"},
                             {"id": "c", "name": "Agent C", "kind": "agent"}],
            "source_roots": [{"id": "fixture", "path": str(self.evidence_root), "git": False}],
            "source_files": [{"id": "adapter-evidence", "root": "fixture", "path": "rules.md", "title": "Adapter evidence", "mode": "text"}]}))
        self.server = app.make_server(self.config, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.addCleanup(self.close_server)
        self.thread.start()

    def close_server(self):
        try:
            if self.thread.is_alive():
                self.server.shutdown()
                self.thread.join(timeout=3)
        finally:
            self.server.server_close()
        self.assertFalse(self.thread.is_alive())

    def test_stdio_negotiation_tools_real_message_and_privacy(self):
        shared = cli.request(self.config, "a", "GET", "/api/state")["channels"][0]["id"]
        inputs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                  {"jsonrpc": "2.0", "method": "notifications/initialized"},
                  {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                  {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "collab_send",
                   "arguments": {"channel_id": shared, "body": "שלום · agent adapter test", "client_id": "stdio-idempotency"}}}]
        result = subprocess.run([sys.executable, "-B", str(APPLICATION / "mcp.py"), "--config", str(self.config), "--as", "a"],
                                input="".join(json.dumps(row) + "\n" for row in inputs), capture_output=True, text=True, check=True)
        responses = [json.loads(row) for row in result.stdout.splitlines()]
        self.assertEqual(len(responses), 3)
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-06-18")
        self.assertGreaterEqual(len(responses[1]["result"]["tools"]), 8)
        self.assertFalse(responses[2]["result"]["isError"])
        rows = cli.request(self.config, "b", "GET", f"/api/messages?channel_id={shared}")["messages"]
        self.assertEqual(rows[0]["body"], "שלום · agent adapter test")
        private = cli.request(self.config, "owner", "POST", "/api/channels", {"title": "Private A", "members": ["a"]})
        with self.assertRaises(RuntimeError):
            mcp.call(self.config, "b", "collab_messages", {"channel_id": private["id"]})

    def test_mcp_workflow_preserves_identity_approval_versions_and_evidence(self):
        decision = mcp.call(self.config, "a", "collab_propose_decision", {
            "channel_id": "workspace", "title": "Adapter proposal", "body": "Measured change", "source": "test input"})
        with self.assertRaises(RuntimeError):
            mcp.call(self.config, "a", "collab_decision_status", {"id": decision["id"], "status": "approved", "version": 1})
        approved = mcp.call(self.config, "owner", "collab_decision_status", {"id": decision["id"], "status": "approved", "version": 1})
        self.assertEqual(approved["approved_by"], "owner")
        implemented = mcp.call(self.config, "a", "collab_decision_status", {
            "id": decision["id"], "status": "implemented", "version": 2, "evidence": "Implementation proof"})
        with self.assertRaises(RuntimeError):
            mcp.call(self.config, "a", "collab_decision_status", {
                "id": decision["id"], "status": "verified", "version": 3, "evidence": "Self verification"})
        verified = mcp.call(self.config, "b", "collab_decision_status", {
            "id": decision["id"], "status": "verified", "version": implemented["version"], "evidence": "Independent proof"})
        self.assertEqual(verified["verified_by"], "b")
        task = mcp.call(self.config, "a", "collab_task", {
            "channel_id": "workspace", "title": "Adapter task", "body": "Review evidence", "owner_id": "b"})
        changed = mcp.call(self.config, "b", "collab_task_status", {"id": task["id"], "status": "working", "version": 1})
        self.assertEqual(changed["version"], 2)
        with self.assertRaises(RuntimeError):
            mcp.call(self.config, "a", "collab_task_status", {"id": task["id"], "status": "done", "version": 1})
        search = mcp.call(self.config, "c", "collab_search", {"q": "Adapter"})
        self.assertEqual(len(search["decisions"]), 1)
        self.assertEqual(len(search["tasks"]), 1)

    def test_stdio_bad_tool_arguments_are_errors_without_tracebacks(self):
        inputs = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {}},
                  {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "collab_task_status",
                   "arguments": {"id": "record", "status": "fabricated", "version": 1}}}]
        result = subprocess.run([sys.executable, "-B", str(APPLICATION / "mcp.py"), "--config", str(self.config), "--as", "a"],
            input="".join(json.dumps(row) + "\n" for row in inputs), capture_output=True, text=True, check=True)
        responses = [json.loads(row) for row in result.stdout.splitlines()]
        self.assertEqual(len(responses), 2)
        self.assertTrue(all(row["result"]["isError"] for row in responses))
        self.assertEqual(result.stderr, "")

    def test_stdio_deep_input_reports_error_and_processes_the_next_request(self):
        deeply_nested = '{"jsonrpc":"2.0","id":1,"method":"ping","params":{"nested":' + "[" * PARSE_RECURSION_TEST_DEPTH + "0" + "]" * PARSE_RECURSION_TEST_DEPTH + "}}\n"
        self.assertLess(len(deeply_nested.encode()), mcp.MAX_LINE_BYTES)
        following = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping"}) + "\n"
        result = subprocess.run([sys.executable, "-B", str(APPLICATION / "mcp.py"), "--config", str(self.config), "--as", "a"],
            input=deeply_nested + following, capture_output=True, text=True, check=True)
        responses = [json.loads(row) for row in result.stdout.splitlines()]
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1], {"jsonrpc": "2.0", "id": 2, "result": {}})
        self.assertEqual(result.stderr, "")

    def test_cold_start_tools_keep_memory_provenance_privacy_conflicts_and_versions(self):
        with self.assertRaises(RuntimeError):
            mcp.call(self.config, "a", "collab_refresh_sources", {})
        sources = mcp.call(self.config, "owner", "collab_refresh_sources", {})["sources"]
        self.assertEqual(len(sources), 1)
        source_ref = {"source_id": sources[0]["id"], "sha256": sources[0]["sha256"]}
        remembered = mcp.call(self.config, "a", "collab_remember", {
            "channel_id": "workspace", "kind": "lesson", "outcome": "worked", "status": "documented",
            "title": "Adapter lesson", "body": "The real tool route retained the indexed source identity.",
            "evidence": "Local adapter fixture", "source_refs": [source_ref]})
        alternative = mcp.call(self.config, "b", "collab_remember", {
            "channel_id": "workspace", "kind": "context", "title": "Alternative interpretation",
            "body": "This fixture records an open question.", "conflicts_with": [remembered["id"]]})
        self.assertEqual(alternative["conflicts_with"], [remembered["id"]])
        changed = mcp.call(self.config, "b", "collab_update_knowledge", {
            "id": remembered["id"], "version": remembered["version"], "status": "disputed", "evidence": "A second interpretation is under review."})
        self.assertEqual(changed["version"], 2)
        with self.assertRaises(RuntimeError):
            mcp.call(self.config, "a", "collab_update_knowledge", {"id": remembered["id"], "version": 1, "status": "superseded"})
        history = mcp.call(self.config, "a", "collab_knowledge_history", {"id": remembered["id"]})["history"]
        self.assertEqual([row["action"] for row in history], ["created", "updated"])

        private = cli.request(self.config, "owner", "POST", "/api/channels", {"title": "Private memory", "members": ["a"]})
        sentinel = "private-adapter-memory-sentinel"
        private_memory = mcp.call(self.config, "a", "collab_remember", {
            "channel_id": private["id"], "title": sentinel, "body": "Only the intended readers may see this fixture."})
        with self.assertRaises(RuntimeError):
            mcp.call(self.config, "b", "collab_knowledge_history", {"id": private_memory["id"]})
        self.assertNotIn(sentinel, json.dumps(mcp.call(self.config, "b", "collab_knowledge", {})))
        self.assertNotIn(sentinel, json.dumps(mcp.call(self.config, "b", "collab_brief", {})))
        self.assertEqual(mcp.call(self.config, "b", "collab_search", {"q": sentinel})["knowledge"], [])

        self.evidence_file.write_text("# Adapter evidence\nThe local fixture changed; old claims need another check.\n")
        mcp.call(self.config, "owner", "collab_refresh_sources", {})
        brief = mcp.call(self.config, "a", "collab_brief", {})
        stale = next(row for row in brief["stale"] if row["id"] == remembered["id"])
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["source_refs"], [source_ref])
        self.assertEqual(len(mcp.call(self.config, "a", "collab_source_history", {"id": "adapter-evidence"})["history"]), 2)
        self.assertEqual(brief["decisions"], [])
        requests = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "collab_brief", "arguments": {}}}]
        process = subprocess.run([sys.executable, "-B", str(APPLICATION / "mcp.py"), "--config", str(self.config), "--as", "b"],
            input="".join(json.dumps(row) + "\n" for row in requests), capture_output=True, text=True, check=True)
        replies = [json.loads(row) for row in process.stdout.splitlines()]
        self.assertFalse(replies[1]["result"]["isError"])
        self.assertNotIn(sentinel, process.stdout)
        self.assertEqual(process.stderr, "")
        command = subprocess.run([sys.executable, "-B", str(APPLICATION / "cli.py"), "--config", str(self.config), "--as", "b", "brief"],
            capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(command.stdout)["me"]["id"], "b")
        self.assertNotIn(sentinel, command.stdout)

    def test_adapter_rejects_unknown_fields_and_path_injection(self):
        with self.assertRaises(ValueError):
            mcp.call(self.config, "a", "collab_state", {"unexpected": "field"})
        with self.assertRaises(ValueError):
            mcp.call(self.config, "a", "collab_decision_status", {"id": "../../credentials", "status": "implemented", "version": 1})


class LauncherTests(unittest.TestCase):
    def test_start_reuses_instance_and_safe_stop(self):
        with temporary_directory(prefix="collaboration-launch-") as directory:
            root = Path(directory)
            config = root / "config.json"
            config.write_text(json.dumps({"title": "Launcher check", "project_label": "Test only", "state_dir": "state",
                "participants": [{"id": "owner", "name": "Owner", "kind": "human"}]}))
            command = [sys.executable, "-B", str(APPLICATION / "launch.py"), "--config", str(config)]
            try:
                first = subprocess.run([*command, "--no-browser"], capture_output=True, text=True, check=True, timeout=25)
                original = json.loads((root / "state/runtime.json").read_text())
                second = subprocess.run([*command, "--no-browser"], capture_output=True, text=True, check=True, timeout=25)
                reused = json.loads((root / "state/runtime.json").read_text())
                self.assertEqual(original["pid"], reused["pid"])
                self.assertEqual(original["boot_id"], reused["boot_id"])
                credential = json.loads((root / "state/credentials/owner.json").read_text())
                self.assertNotIn(credential["token"], first.stdout + second.stdout)
                self.assertTrue(original["origin"].startswith("http://127.0.0.1:"))
                cli.request(config, "owner", "POST", "/api/messages", {"channel_id": "workspace", "body": "Persists across restart", "client_id": "restart"})
                subprocess.run([*command, "--stop"], capture_output=True, text=True, check=True, timeout=25)
                self.assertFalse((root / "state/runtime.json").exists())
                subprocess.run([*command, "--no-browser"], capture_output=True, text=True, check=True, timeout=25)
                restarted = json.loads((root / "state/runtime.json").read_text())
                self.assertNotEqual(original["boot_id"], restarted["boot_id"])
                self.assertEqual(cli.request(config, "owner", "GET", "/api/messages?channel_id=workspace")["messages"][0]["body"], "Persists across restart")
            finally:
                stopped = subprocess.run([*command, "--stop"], capture_output=True, text=True, check=True, timeout=25)
                self.assertIn("Stopped", stopped.stdout)


if __name__ == "__main__":
    unittest.main()
