"""The real launcher switches provider controls on only from files the operator placed beside the instance config.

Measured 12/10/2026: app.py accepts --providers and --adapter-human-policy, but launch.py (the path start.command uses)
passed neither, so every launched instance answered "Provider controls are not configured". The launcher now passes
the private provider and human-grant files when they sit beside config.json; without them nothing changes.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import unittest
from pathlib import Path

from qa_support import temporary_directory

import cli
from launch import HUMAN_POLICY_FILE, PROVIDER_FILE

APPLICATION = Path(__file__).resolve().parents[1]
MODULE = "manual-desk"


class LauncherProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(temporary_directory(self, prefix="launch-providers-").name)
        self.config = self.root / "config.json"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.config.write_text(json.dumps({
            "title": "Synthetic provider launch", "project_label": "Launcher regression", "state_dir": "state",
            "port": port,
            "participants": [{"id": "owner", "name": "Synthetic owner", "kind": "human"},
                             {"id": "desk", "name": "Synthetic desk agent", "kind": "agent"}]}))
        self.providers = self.root / PROVIDER_FILE
        self.policy = self.root / HUMAN_POLICY_FILE
        self.addCleanup(self.launch, "--stop")

    def write_providers(self) -> None:
        self.providers.write_text(json.dumps({
            "schema_version": 1, "workspace_config": "config.json",
            "instance_limits": {"max_concurrent": 1, "daily_budget_usd": None, "pricing_max_age_days": 30},
            "modules": [{"id": MODULE, "provider": "manual", "participant_id": "desk", "enabled": True, "model": None,
                         "endpoint": None, "allowed_channel_ids": ["workspace"], "credential_env": None,
                         "local_only_verified": False, "local_only_evidence": None,
                         "limits": {"max_context_bytes": 100000, "max_response_bytes": 100000,
                                    "max_output_tokens": 100, "timeout_seconds": 5, "max_concurrent": 1,
                                    "max_run_usd": None},
                         "pricing": None}]}))

    def write_policy(self) -> None:
        self.policy.write_text(json.dumps({"schema_version": 1, "human_launchers": {MODULE: ["owner"]}}))

    def launch(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-B", str(APPLICATION / "launch.py"), "--config", str(self.config),
                               "--no-browser", *extra], text=True, capture_output=True, timeout=30, check=False)

    def adapters(self) -> dict:
        started = self.launch()
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        return cli.request(self.config, "owner", "GET", "/api/adapters")

    def test_provider_files_beside_the_config_switch_the_controls_on(self) -> None:
        self.write_providers()
        self.write_policy()
        result = self.adapters()
        self.assertTrue(result["configured"])
        [module] = result["modules"]
        self.assertEqual((module["id"], module["participant_id"], module["human_launch"]), (MODULE, "desk", True))

    def test_without_provider_files_the_launch_is_exactly_as_before(self) -> None:
        self.assertEqual(self.adapters(), {"configured": False, "modules": [], "stopping": False})

    def test_a_human_policy_without_providers_refuses_to_start(self) -> None:
        self.write_policy()
        started = self.launch()
        self.assertNotEqual(started.returncode, 0)
        self.assertIn(PROVIDER_FILE, started.stderr)
        self.assertFalse((self.root / "state/runtime.json").exists())


if __name__ == "__main__":
    unittest.main()
