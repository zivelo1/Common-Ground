"""The real launcher must retain the instance's configured address across restarts."""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import unittest
from pathlib import Path

from qa_support import temporary_directory

import cli
from store import APIError, load_config

APPLICATION = Path(__file__).resolve().parents[1]


class ConfiguredPortTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(temporary_directory(self, prefix="fixed-launch-port-").name)
        self.config = self.root / "config.json"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.data = {
            "title": "Synthetic stable address", "project_label": "Port regression",
            "state_dir": "state", "port": self.port,
            "participants": [{"id": "owner", "name": "Synthetic owner", "kind": "human"}],
        }
        self.write_config()

    def write_config(self) -> None:
        self.config.write_text(json.dumps(self.data))

    def command(self, *extra: str) -> list[str]:
        return [sys.executable, "-B", str(APPLICATION / "launch.py"),
                "--config", str(self.config), "--no-browser", *extra]

    def test_real_launcher_keeps_the_configured_port_and_messages_across_restart(self) -> None:
        try:
            for sequence in range(2):
                started = subprocess.run(self.command(), text=True, capture_output=True, timeout=30, check=False)
                self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
                runtime = json.loads((self.root / "state/runtime.json").read_text())
                self.assertEqual(runtime["origin"], f"http://127.0.0.1:{self.port}")
                if sequence == 0:
                    cli.request(self.config, "owner", "POST", "/api/messages", {
                        "channel_id": "workspace", "body": "Retained across restart", "client_id": "fixed-port-test"})
                else:
                    rows = cli.request(self.config, "owner", "GET", "/api/messages?channel_id=workspace")["messages"]
                    self.assertEqual([row["body"] for row in rows], ["Retained across restart"])
                stopped = subprocess.run(self.command("--stop"), text=True, capture_output=True, timeout=30, check=False)
                self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        finally:
            subprocess.run(self.command("--stop"), text=True, capture_output=True, timeout=30, check=False)

    def test_invalid_port_is_refused_and_legacy_config_retains_ephemeral_port(self) -> None:
        for value in (True, False, -1, 65536, "55774", None):
            with self.subTest(value=value):
                self.data["port"] = value
                self.write_config()
                with self.assertRaisesRegex(APIError, "port"):
                    load_config(self.config)
        self.data.pop("port")
        self.write_config()
        self.assertEqual(load_config(self.config)["port"], 0)
