"""Onboard a fictional project using only the exact public archive's real code."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

from qa_support import temporary_directory

APP = Path(__file__).resolve().parents[1]
SUBPROCESS_TIMEOUT_SECONDS = 30
JOIN_CHECK = r'''
import json
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
import app
assert Path(app.__file__).resolve() == root / "app.py"
config = root / "config.json"
config.write_text(json.dumps({"title": "Fresh museum", "project_label": "Archive onboarding",
    "state_dir": "instance_data", "participants": [
        {"id": "curator", "name": "Synthetic curator", "kind": "human"},
        {"id": "catalogue", "name": "Synthetic catalogue", "kind": "agent"}]}))
server = app.make_server(config, 0)
worker = threading.Thread(target=server.serve_forever, daemon=True)
worker.start()
origin = "http://127.0.0.1:" + str(server.server_address[1])
opener = build_opener(ProxyHandler({}))

def call(identity, path, body=None):
    credential = json.loads((root / "instance_data/credentials" / (identity + ".json")).read_text())
    headers = {"Authorization": "Bearer " + credential["token"]}
    raw = None
    if body is not None:
        headers.update({"X-Collab-Request": "1", "Content-Type": "application/json"})
        raw = json.dumps(body).encode()
    try:
        with opener.open(Request(origin + path, data=raw, headers=headers), timeout=app.REQUEST_TIMEOUT_SECONDS) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)

try:
    status, state = call("curator", "/api/state")
    assert status == 200 and state["me"]["id"] == "curator" and state["tasks"] == []
    assert [item["id"] for item in state["channels"]] == ["workspace"]
    assert call("curator", "/api/adapters")[1] == {"configured": False, "modules": [], "stopping": False}
    status, joined = call("curator", "/api/participants", {"id": "visitor", "name": "Synthetic visitor", "kind": "agent"})
    assert status == 200 and joined["id"] == "visitor"
    status, state = call("visitor", "/api/state")
    assert status == 200 and state["me"]["id"] == "visitor" and state["me"]["kind"] == "agent"
    assert state["channels"][0]["members"] == ["catalogue", "curator", "visitor"]
    status, message = call("visitor", "/api/messages", {"channel_id": "workspace", "body": "Fresh project joined.", "client_id": "archive-join"})
    assert status == 200 and message["author_id"] == "visitor"
    status, brief = call("catalogue", "/api/brief?channel_id=workspace")
    assert status == 200 and brief["scope"]["channel_ids"] == ["workspace"]
    assert any(item["id"] == message["id"] and item["body"] == "Fresh project joined." for item in brief["recent_messages"])
    assert call("visitor", "/api/participants", {"id": "unauthorized", "name": "Synthetic extra", "kind": "agent"})[0] == 403
    print(json.dumps({"extracted_real_app": True, "fresh_project": True, "human_and_agent_identities": True,
        "new_agent_joined": True, "message_and_brief": True, "agent_admin_denied": True, "providers_unconfigured": True}))
finally:
    server.shutdown()
    worker.join(app.REQUEST_TIMEOUT_SECONDS)
    server.server_close()
    if worker.is_alive():
        raise RuntimeError("Disposable onboarding server did not stop")
'''


class PublicPackageOnboardingTests(unittest.TestCase):
    def test_exact_manifest_archive_onboards_a_fresh_project(self) -> None:
        root = Path(temporary_directory(self, prefix="public-onboarding-").name)
        names = (APP / "PUBLIC_FILES.txt").read_text().splitlines()
        manifest = json.loads((APP / "PUBLIC_MANIFEST.json").read_text())
        self.assertEqual(names, sorted([*manifest["files"], "PUBLIC_MANIFEST.json"]))
        archive = root / "package.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
            for name in names:
                relative = Path(name)
                self.assertFalse(relative.is_absolute())
                self.assertNotIn("..", relative.parts)
                self.assertFalse((APP / relative).is_symlink())
                package.write(APP / relative, name)
        extracted = root / "extracted"
        with zipfile.ZipFile(archive) as package:
            self.assertEqual(package.namelist(), names)
            for name, record in manifest["files"].items():
                data = package.read(name)
                self.assertEqual(len(data), record["bytes"], name)
                self.assertEqual(hashlib.sha256(data).hexdigest(), record["sha256"], name)
            package.extractall(extracted)
        result = subprocess.run([sys.executable, "-I", "-B", "-c", JOIN_CHECK, str(extracted)],
                                cwd=extracted, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                                text=True, capture_output=True, timeout=SUBPROCESS_TIMEOUT_SECONDS, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        receipt = json.loads(result.stdout)
        self.assertTrue(receipt)
        self.assertTrue(all(receipt.values()), receipt)


if __name__ == "__main__":
    unittest.main()
