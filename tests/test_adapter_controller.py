"""Synthetic real Store/Runner/controller and loopback API regressions."""

from __future__ import annotations

import copy
import json
import sqlite3
import sys
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qa_support import temporary_directory

from adapter_controller import AdapterController
from app import make_server
from providers.base import AdapterError, ProviderError
from store import APIError, Store, load_config


class SyntheticTransport:
    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = self.unknown = False

    def post(
        self,
        endpoint: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        limits: Any,
        cancel: Any,
    ) -> dict[str, Any]:
        self.calls.append((endpoint, copy.deepcopy(payload)))
        self.entered.set()
        if self.block and not self.release.wait(5):
            raise RuntimeError("Synthetic test release timed out")
        if self.unknown:
            raise ProviderError("unknown", "synthetic_timeout")
        return {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "SYNTHETIC RESULT <script>literal</script>",
                        }
                    ],
                }
            ],
            "usage": {"input_tokens": 4, "output_tokens": 5},
        }


class ControllerFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = temporary_directory(self, 
            prefix="provider-ui-"
        )
        self.root = Path(self.tmp.name)
        self.workspace = self.root / "config.json"
        self.workspace.write_text(
            json.dumps(
                {
                    "title": "Synthetic UI",
                    "project_label": "Disposable",
                    "state_dir": "state",
                    "participants": [
                        {"id": "human", "name": "Synthetic human", "kind": "human"},
                        {
                            "id": "second",
                            "name": "Second synthetic human",
                            "kind": "human",
                        },
                        {"id": "agent", "name": "Synthetic agent", "kind": "agent"},
                        {
                            "id": "other",
                            "name": "Other synthetic agent",
                            "kind": "agent",
                        },
                    ],
                }
            )
        )
        self.store = Store(load_config(self.workspace))
        self.human = {"id": "human", "kind": "human", "name": "Synthetic human"}
        self.second = {
            "id": "second",
            "kind": "human",
            "name": "Second synthetic human",
        }
        self.agent = {"id": "agent", "kind": "agent", "name": "Synthetic agent"}
        self.other = {"id": "other", "kind": "agent", "name": "Other synthetic agent"}
        self.private = self.store.add_channel(
            self.human, {"title": "Synthetic private", "members": ["agent"]}
        )["id"]
        self.hidden = self.store.add_channel(
            self.second, {"title": "Not visible to human", "members": ["agent"]}
        )["id"]
        self.provider_path = self.root / "providers.json"
        self.policy_path = self.root / "policy.json"
        module: dict[str, Any] = {
            "id": "manual",
            "provider": "manual",
            "participant_id": "agent",
            "enabled": True,
            "model": None,
            "endpoint": None,
            "allowed_channel_ids": ["workspace", self.private, self.hidden],
            "credential_env": None,
            "local_only_verified": False,
            "local_only_evidence": None,
            "limits": {
                "max_context_bytes": 50000,
                "max_response_bytes": 12000,
                "max_output_tokens": 64,
                "timeout_seconds": 0.5,
                "max_concurrent": 1,
                "max_run_usd": None,
            },
            "pricing": None,
        }
        remote = copy.deepcopy(module)
        remote.update(
            {
                "id": "remote",
                "provider": "openai_responses",
                "model": "synthetic",
                "endpoint": "https://api.openai.com/v1/responses",
                "credential_env": "SYNTHETIC_KEY",
            }
        )
        remote["limits"]["max_run_usd"] = "1"
        remote["pricing"] = {
            "input_usd_per_million": "1",
            "output_usd_per_million": "2",
            "max_input_tokens": 100,
            "checked_at": datetime.now(timezone.utc).date().isoformat(),
            "evidence": "https://example.invalid/synthetic-prices",
        }
        self.raw: dict[str, Any] = {
            "schema_version": 1,
            "workspace_config": str(self.workspace),
            "instance_limits": {
                "max_concurrent": 1,
                "daily_budget_usd": "2",
                "pricing_max_age_days": 30,
            },
            "modules": [module, remote],
        }
        self.provider_path.write_text(json.dumps(self.raw))
        self.policy_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "human_launchers": {"manual": ["human"], "remote": ["human"]},
                }
            )
        )
        self.transport = SyntheticTransport()
        self.secret_reads: list[str] = []
        self.controller = self.make_controller(self.store)
        self.server: Any = None
        self.server_thread: Any = None
        self.addCleanup(self.cleanup_resources)

    def make_controller(self, store: Store) -> AdapterController:
        def secret(name: str) -> str:
            self.secret_reads.append(name)
            return "SYNTHETIC-NOT-A-REAL-KEY"

        return AdapterController(
            store,
            self.workspace,
            self.provider_path,
            self.policy_path,
            transport=self.transport,
            secret_reader=secret,
        )

    def tearDown(self) -> None:
        # Keep direct QA callers compatible while unittest also runs these
        # callbacks after a setup failure. The cleanup stack executes once.
        self.doCleanups()

    def cleanup_resources(self) -> None:
        self.transport.release.set()
        if self.server is not None:
            if self.server_thread.is_alive():
                self.server.shutdown()
            self.server_thread.join(3)
            self.server.server_close()
        self.assertTrue(self.controller.close(3))

    def prepared(
        self,
        module: str = "manual",
        channel: str = "workspace",
        actor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.controller.prepare(
            actor or self.human,
            {
                "module_id": module,
                "channel_id": channel,
                "prompt": "Synthetic explicit prompt",
            },
        )

    @staticmethod
    def action(row: dict[str, Any]) -> dict[str, Any]:
        return {key: row[key] for key in ("module_id", "run_id", "prepared_sha256")}

    def settled(
        self, row: dict[str, Any], actor: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            current = self.controller.status(
                actor or self.human, row["module_id"], row["run_id"]
            )
            if not current["operation"]["active"]:
                return current
            time.sleep(0.005)
        self.fail("Synthetic controller did not finish")

    def start_http(self) -> None:
        self.assertTrue(self.controller.close(3))
        self.server = make_server(
            self.workspace, controller_factory=self.make_controller
        )
        self.controller = self.server.adapters
        self.server_thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}
        )
        self.server_thread.start()

    def http(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        actor: str = "human",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        token = json.loads(
            (self.root / "state/credentials" / (actor + ".json")).read_text()
        )["token"]
        request_headers = {"Authorization": "Bearer " + token}
        if body is not None:
            request_headers.update(
                {"Content-Type": "application/json", "X-Collab-Request": "1"}
            )
        request_headers.update(headers or {})
        request = Request(
            self.server.origin + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=request_headers,
        )
        try:
            with urlopen(request, timeout=3) as reply:
                return reply.status, json.load(reply)
        except HTTPError as reply:
            return reply.code, json.load(reply)

    def test_manual_prepare_poll_import_publish_are_separate(self) -> None:
        row = self.prepared(channel=self.private)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.secret_reads, [])
        self.assertEqual(self.store.messages(self.agent, self.private)["messages"], [])
        self.controller.schedule(self.human, self.action(row), "run")
        row = self.settled(row)
        self.assertEqual(row["status"], "awaiting_manual")
        for _ in range(3):
            self.controller.status(self.human, "manual", row["run_id"])
        self.assertEqual(self.transport.calls, [])
        self.controller.import_result(
            self.human, {**self.action(row), "text": "PRIVATE SYNTHETIC RESULT"}
        )
        self.assertEqual(self.store.messages(self.agent, self.private)["messages"], [])
        self.controller.schedule(self.human, self.action(row), "publish")
        result = self.settled(row)
        self.assertEqual(result["publication_status"], "published")
        messages = self.store.messages(self.agent, self.private)["messages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["author_id"], "agent")

    def test_human_needs_explicit_grant_and_agent_owns_module(self) -> None:
        denied = AdapterController(
            self.store, self.workspace, self.provider_path, transport=self.transport
        )
        self.assertEqual(denied.descriptors(self.human)["modules"], [])
        with self.assertRaises(APIError):
            denied.prepare(
                self.human,
                {
                    "module_id": "manual",
                    "channel_id": "workspace",
                    "prompt": "SYNTHETIC",
                },
            )
        with self.assertRaises(APIError):
            self.prepared(actor=self.other)
        self.assertEqual(self.prepared(actor=self.agent)["actor"], "agent")

    def test_both_caller_and_agent_audience_required(self) -> None:
        with self.assertRaises(APIError):
            self.prepared(channel=self.hidden)
        self.store.add_channel(
            self.human, {"title": "Human only", "members": ["other"]}
        )
        self.assertNotIn(
            self.hidden,
            self.controller.descriptors(self.human)["modules"][0][
                "allowed_channel_ids"
            ],
        )
        row = self.prepared(channel=self.hidden, actor=self.agent)
        with self.assertRaises(APIError):
            self.controller.status(self.human, "manual", row["run_id"])

    def test_private_recent_runs_are_filtered_before_pagination(self) -> None:
        hidden = self.prepared(channel=self.hidden, actor=self.agent)
        visible = self.prepared()
        listed = self.controller.recent(self.human, "manual", None, 1, None)
        self.assertEqual([r["run_id"] for r in listed["runs"]], [visible["run_id"]])
        self.assertIsNone(listed["next_before"])
        with self.assertRaises(APIError):
            self.controller.recent(self.human, "manual", None, 1, hidden["run_id"])
        self.assertNotIn("prepared", listed["runs"][0])

    def test_recent_cursor_limit_and_restoration(self) -> None:
        rows = [self.prepared() for _ in range(3)]
        first = self.controller.recent(self.human, "manual", "workspace", 2, None)
        self.assertEqual(len(first["runs"]), 2)
        second = self.controller.recent(
            self.human, "manual", "workspace", 2, first["next_before"]
        )
        self.assertEqual(len(second["runs"]), 1)
        self.assertEqual(len({r["run_id"] for r in first["runs"] + second["runs"]}), 3)
        with self.assertRaises(AdapterError):
            self.controller.recent(self.human, "manual", None, 51, None)
        self.assertTrue(self.controller.close(1))
        self.controller = self.make_controller(self.store)
        self.assertEqual(
            self.controller.status(self.human, "manual", rows[0]["run_id"])["status"],
            "prepared",
        )
        self.assertEqual(self.transport.calls, [])

    def test_removed_caller_membership_blocks_status_and_publication(self) -> None:
        row = self.prepared(channel=self.private)
        with sqlite3.connect(self.store.db_path) as db:
            db.execute(
                "DELETE FROM members WHERE channel_id=? AND participant_id='human'",
                (self.private,),
            )
        with self.assertRaises(APIError):
            self.controller.status(self.human, "manual", row["run_id"])
        with self.assertRaises(APIError):
            self.controller.schedule(self.human, self.action(row), "publish")

    def test_status_snapshot_never_reports_idle_with_old_publication_state(
        self,
    ) -> None:
        row = self.prepared()
        run_id = row["run_id"]
        with self.controller.journal.connection() as db:
            db.execute(
                "UPDATE jobs SET publication_status='publishing' WHERE run_id=?",
                (run_id,),
            )
        with self.controller.lock:
            self.controller.active.add(run_id)
        retrieved, attempted, finished = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        runner = self.controller._runner(self.human, "manual")
        original_status = runner.status

        def status(module_id: str, run: str) -> dict[str, Any]:
            snapshot = original_status(module_id, run)
            retrieved.set()
            self.assertTrue(attempted.wait(1))
            finished.wait(0.03)
            return snapshot

        def finish() -> None:
            retrieved.wait(1)
            attempted.set()
            with self.controller.lock:
                with self.controller.journal.connection() as db:
                    db.execute(
                        "UPDATE jobs SET publication_status='unconfirmed' WHERE run_id=?",
                        (run_id,),
                    )
                self.controller.active.discard(run_id)
            finished.set()

        worker = threading.Thread(target=finish)
        worker.start()
        try:
            with (
                patch.object(self.controller, "_runner", return_value=runner),
                patch.object(runner, "status", side_effect=status),
            ):
                snapshot = self.controller.status(self.human, "manual", run_id)
            self.assertTrue(
                snapshot["operation"]["active"]
                or snapshot["publication_status"] == "unconfirmed"
            )
        finally:
            worker.join(2)
        self.assertFalse(worker.is_alive())

    def test_unknown_and_hidden_run_errors_do_not_reveal_existence(self) -> None:
        hidden = self.prepared(channel=self.hidden, actor=self.agent)
        errors = []
        for run in (hidden["run_id"], "0" * 32):
            with self.assertRaises(APIError) as caught:
                self.controller.status(self.human, "manual", run)
            errors.append((caught.exception.status, str(caught.exception)))
        self.assertEqual(errors[0], errors[1])

    def test_stop_drains_admitted_request_before_worker_registration(self) -> None:
        row = self.prepared("remote")
        admitted, release = threading.Event(), threading.Event()
        original_admit = self.controller._admit
        failures: list[Exception] = []

        def pause_admit() -> None:
            original_admit()
            admitted.set()
            release.wait(2)

        def schedule() -> None:
            try:
                self.controller.schedule(self.human, self.action(row), "run")
            except APIError as error:
                failures.append(error)

        with patch.object(self.controller, "_admit", side_effect=pause_admit):
            worker = threading.Thread(target=schedule)
            worker.start()
            self.assertTrue(admitted.wait(1))
            try:
                self.assertFalse(self.controller.close(0.03))
            finally:
                release.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(self.controller.close(1))
        self.assertEqual(len(failures), 1)
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.secret_reads, [])

    def test_unknown_generation_is_durable_and_never_retries(self) -> None:
        self.transport.unknown = True
        row = self.prepared("remote")
        self.controller.schedule(self.human, self.action(row), "run")
        row = self.settled(row)
        self.assertEqual(row["status"], "unknown")
        with self.assertRaises(APIError):
            self.controller.schedule(self.human, self.action(row), "run")
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(len(self.secret_reads), 1)

    def test_changed_context_requires_new_preview_without_transport(self) -> None:
        row = self.prepared("remote")
        self.store.add_message(
            self.agent,
            {
                "channel_id": "workspace",
                "body": "Changed synthetic context",
                "client_id": "context-change",
            },
        )
        self.controller.schedule(self.human, self.action(row), "run")
        row = self.settled(row)
        self.assertIn("context changed", row["operation"]["error"])
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.secret_reads, [])

    def test_worker_capacity_duplicate_and_stop_barrier(self) -> None:
        self.transport.block = True
        row = self.prepared("remote")
        self.controller.schedule(self.human, self.action(row), "run")
        self.assertTrue(self.transport.entered.wait(1))
        with self.assertRaises(APIError):
            self.prepared()
        with self.assertRaises(APIError):
            self.controller.schedule(self.human, self.action(row), "run")
        self.assertFalse(self.controller.close(0.01))
        with self.assertRaises(APIError):
            self.prepared()
        self.transport.release.set()
        self.assertTrue(self.controller.close(2))
        self.assertEqual(self.settled(row)["status"], "unknown")

    def test_unconfirmed_publication_retries_original_result_once(self) -> None:
        row = self.prepared()
        self.controller.schedule(self.human, self.action(row), "run")
        self.settled(row)
        self.controller.import_result(
            self.human, {**self.action(row), "text": "SYNTHETIC LOST ACK"}
        )
        send = self.store.send_queued

        def lose(actor: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
            send(actor, data)
            raise RuntimeError("Synthetic lost acknowledgement")

        with patch.object(self.store, "send_queued", side_effect=lose):
            self.controller.schedule(self.human, self.action(row), "publish")
            self.assertEqual(self.settled(row)["publication_status"], "unconfirmed")
        self.controller.schedule(self.human, self.action(row), "publish")
        self.assertEqual(self.settled(row)["publication_status"], "published")
        self.assertEqual(
            len(self.store.messages(self.agent, "workspace")["messages"]), 1
        )
        self.assertEqual(self.transport.calls, [])

    def test_exact_body_hash_and_spoofed_actor_rejected(self) -> None:
        with self.assertRaises(AdapterError):
            self.controller.prepare(
                self.human,
                {
                    "module_id": "manual",
                    "channel_id": "workspace",
                    "prompt": "Synthetic",
                    "actor": "agent",
                },
            )
        row = self.prepared()
        with self.assertRaises(AdapterError):
            self.controller.schedule(
                self.human, {**self.action(row), "prepared_sha256": "0" * 64}, "run"
            )
        self.assertEqual(self.transport.calls, [])

    def test_http_guards_and_safe_descriptors(self) -> None:
        self.start_http()
        status, descriptors = self.http("/api/adapters")
        self.assertEqual(status, 200)
        self.assertNotIn("credential_env", json.dumps(descriptors))
        self.assertEqual(
            self.http("/api/adapters", headers={"Host": "evil.invalid"})[0], 403
        )
        self.assertEqual(
            self.http("/api/adapters", headers={"Origin": "http://evil.invalid"})[0],
            403,
        )
        body = {"module_id": "manual", "channel_id": "workspace", "prompt": "Synthetic"}
        self.assertEqual(
            self.http("/api/adapters/prepare", body, headers={"X-Collab-Request": ""})[
                0
            ],
            403,
        )
        self.assertEqual(
            self.http("/api/adapters/prepare", body, actor="other")[0], 403
        )
        self.assertEqual(
            self.http("/api/adapters/prepare", {**body, "token": "SYNTHETIC"})[0], 409
        )
        self.assertEqual(
            self.http("/api/adapters/prepare", {**body, "prompt": "x" * 66000})[0], 413
        )
        self.assertEqual(
            self.http("/api/adapters/runs?module_id=manual&limit=51")[0], 409
        )
        self.assertEqual(
            self.http("/api/adapters/runs?module_id=manual&limit=1&limit=2")[0], 400
        )
        self.assertEqual(
            self.http("/api/adapters/runs?module_id=manual&actor=other")[0], 400
        )

    def test_http_async_run_and_stop_drain_hold_lock(self) -> None:
        self.start_http()
        self.transport.block = True
        status, row = self.http(
            "/api/adapters/prepare",
            {"module_id": "remote", "channel_id": "workspace", "prompt": "Synthetic"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.http("/api/adapters/run", self.action(row))[0], 200)
        self.assertTrue(self.transport.entered.wait(1))
        self.assertEqual(self.http("/api/shutdown", {})[0], 200)
        self.assertEqual(
            self.http(
                "/api/messages",
                {
                    "channel_id": "workspace",
                    "body": "Late synthetic write",
                    "client_id": "late",
                },
            )[0],
            503,
        )
        self.assertTrue(self.server.runtime_path.exists())
        with self.assertRaises(APIError):
            make_server(self.workspace)
        self.transport.release.set()
        self.server_thread.join(3)
        self.assertFalse(self.server_thread.is_alive())
        self.server.server_close()
        self.assertFalse(self.server.runtime_path.exists())
        self.assertEqual(
            self.controller.journal.get(row["run_id"], "agent", "remote")["status"],
            "unknown",
        )
        self.server = None

    def test_disabled_configuration_never_infers(self) -> None:
        for module in self.raw["modules"]:
            module["enabled"] = False
        self.provider_path.write_text(json.dumps(self.raw))
        self.assertTrue(self.controller.close(1))
        self.controller = self.make_controller(self.store)
        self.assertTrue(
            all(
                d["blocked_reasons"]
                for d in self.controller.descriptors(self.human)["modules"]
            )
        )
        with self.assertRaises(AdapterError):
            self.prepared("remote")
        self.assertEqual(self.transport.calls, [])
        self.assertEqual(self.secret_reads, [])

    def test_absent_provider_configuration_retains_manual_api(self) -> None:
        self.server = make_server(self.workspace)
        self.server_thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}
        )
        self.server_thread.start()
        self.assertEqual(self.http("/api/adapters")[1]["configured"], False)
        self.assertEqual(
            self.http(
                "/api/adapters/prepare",
                {
                    "module_id": "manual",
                    "channel_id": "workspace",
                    "prompt": "Synthetic",
                },
            )[0],
            409,
        )
        self.assertEqual(
            self.http(
                "/api/messages",
                {
                    "channel_id": "workspace",
                    "body": "Ordinary manual message",
                    "client_id": "manual-message",
                },
            )[0],
            200,
        )
        self.assertEqual(self.transport.calls, [])

    def test_wrong_workspace_policy_and_agent_kind_fail_closed(self) -> None:
        raw = copy.deepcopy(self.raw)
        raw["workspace_config"] = str(self.root / "other-workspace.json")
        self.provider_path.write_text(json.dumps(raw))
        with self.assertRaises(AdapterError):
            self.make_controller(self.store)
        self.provider_path.write_text(json.dumps(self.raw))
        self.policy_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "human_launchers": {"manual": ["human"]},
                    "keys": "SYNTHETIC",
                }
            )
        )
        with self.assertRaises(AdapterError):
            self.make_controller(self.store)
        self.policy_path.write_text(
            json.dumps({"schema_version": 1, "human_launchers": {"manual": ["agent"]}})
        )
        with self.assertRaises(AdapterError):
            self.make_controller(self.store)
