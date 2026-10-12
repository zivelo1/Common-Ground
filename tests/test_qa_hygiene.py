"""Real setup-failure cleanup and import-side-effect checks for shipped tests."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path

from qa_support import STATE_QA, WORKSPACE, temporary_directory

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import app  # noqa: E402 — project path is set above for standalone execution.


class HygieneTests(unittest.TestCase):
    def test_setup_failure_closes_real_server_and_removes_own_instance(self):
        observed = {}

        class FailingSetup(unittest.TestCase):
            def runTest(self):
                raise AssertionError("A failed setup must not execute its test")

            def setUp(self):
                temp = temporary_directory(self, prefix="failure-cleanup-")
                root = Path(temp.name)
                observed["root"] = root
                config = root / "config.json"
                config.write_text(json.dumps({"title": "Cleanup fixture", "project_label": "Synthetic only", "state_dir": "instance_data", "participants": [{"id": "codex", "name": "Synthetic Codex", "kind": "agent"}, {"id": "human", "name": "Synthetic human", "kind": "human"}]}))
                server = app.make_server(config, 0)
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                observed["worker"] = worker

                def close():
                    try:
                        server.shutdown()
                        worker.join(app.REQUEST_TIMEOUT_SECONDS)
                    finally:
                        server.server_close()
                    if worker.is_alive():
                        raise RuntimeError("Synthetic worker did not stop")

                self.addCleanup(close)
                raise RuntimeError("Expected synthetic setup failure")

        result = unittest.TestResult()
        FailingSetup().run(result)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("Expected synthetic setup failure", result.errors[0][1])
        self.assertFalse(observed["worker"].is_alive())
        self.assertFalse(observed["root"].exists())

    def test_real_browser_test_import_has_no_environment_or_fixture_side_effect(self):
        code = "import os,pathlib,sys; sys.path.insert(0,sys.argv[1]); from qa_support import STATE_QA; before=dict(os.environ); root=STATE_QA/'disposable_tests'; paths=set(root.glob('*')); import test_conversation_real_backend; assert dict(os.environ)==before; assert set(root.glob('*'))==paths; print('Import preserves environment and disposable paths')"
        result = subprocess.run([sys.executable, "-B", "-c", code, str(APP / "tests")], cwd=WORKSPACE, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(STATE_QA)}, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
