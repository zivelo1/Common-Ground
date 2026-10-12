"""Provider logo beside the participant name (Collaboration): config.json names a logo per participant; only
local files whose bytes match their provenance record are shown and served; everyone else keeps initials."""
import hashlib
import http.client
import json
import shutil
import unittest
from pathlib import Path

import test_backend
from qa_support import temporary_directory

from store import APIError, load_config, logo_registry

LOGOS = Path(__file__).resolve().parents[1] / "static" / "logos"


class ProviderLogoTests(unittest.TestCase):
    # Reuse the backend server fixture's methods. Never bind its TestCase class to a module name here: the loader
    # collects every TestCase class in this module's namespace, so its tests would run a second time.
    start = test_backend.BackendTests.start
    stop = test_backend.BackendTests.stop
    tearDown = test_backend.BackendTests.tearDown
    call = test_backend.BackendTests.call
    ok = test_backend.BackendTests.ok

    def setUp(self):
        self.temp = temporary_directory(self, prefix="logo-test-")
        self.root = Path(self.temp.name)
        self.config = self.root / "config.json"
        self.write_config({"id": "human", "name": "Human", "kind": "human"},
                          {"id": "a", "name": "Agent A", "kind": "agent", "logo": "codex-color"},
                          {"id": "b", "name": "Agent B", "kind": "agent"})
        self.server = None
        self.thread = None
        self.addCleanup(self.stop)
        self.start()
        self.tokens = {ident: json.loads((self.root / "state" / "credentials" / f"{ident}.json").read_text())["token"]
                       for ident in ("human", "a", "b")}

    def fetch(self, path):
        """A plain GET without credentials, as the browser asks for an image; the body stays bytes."""
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        connection.request("GET", path)
        response = connection.getresponse()
        result = (response.status, response.read(), dict(response.getheaders()))
        connection.close()
        return result

    def write_config(self, *people):
        self.config.write_text(json.dumps({"title": "Logo Board", "project_label": "Generic", "state_dir": "state",
                                           "participants": list(people)}))

    def test_state_names_each_configured_logo_and_everyone_else_keeps_initials(self):
        for viewer in ("human", "a"):
            state = self.ok("GET", "/api/state", viewer)
            self.assertEqual({person["id"]: person["logo"] for person in state["participants"]},
                             {"human": None, "a": "codex-color", "b": None})
            self.assertNotIn("logo", state["me"])          # "me" keeps its shape; the page reads the participants list

    def test_the_server_serves_only_verified_logo_files_from_memory(self):
        status, body, headers = self.fetch("/static/logos/codex-color.svg")
        self.assertEqual(status, 200)
        self.assertEqual(body, (LOGOS / "codex-color.svg").read_bytes())
        self.assertEqual(headers.get("Content-Type"), "image/svg+xml")
        for path in ("/static/logos/unknown.svg", "/static/logos/PROVENANCE.json", "/static/logos/../app.js",
                     "/static/logos/codex-color.svg.bak"):
            with self.subTest(path=path):
                status, body, _ = self.fetch(path)
                self.assertNotEqual(status, 200)
                self.assertNotIn(b"function", body)

    def test_an_unknown_or_malformed_logo_in_config_is_refused(self):
        for logo in ("unknown", 5, "", "../codex-color", None):
            with self.subTest(logo=logo):
                self.write_config({"id": "human", "name": "Human", "kind": "human"},
                                  {"id": "a", "name": "Agent A", "kind": "agent", "logo": logo})
                if logo is None:
                    self.assertIsNone(load_config(self.config)["participants"][1]["logo"])
                    continue
                with self.assertRaisesRegex(APIError, "logo"):
                    load_config(self.config)

    def test_a_logo_whose_bytes_differ_from_its_provenance_is_refused(self):
        copy = Path(temporary_directory(self, prefix="logo-copy-").name) / "logos"
        shutil.copytree(LOGOS, copy)
        self.assertEqual(set(logo_registry(copy)), {"claude-color", "codex-color", "gemini-color"})
        (copy / "codex-color.svg").write_bytes((copy / "codex-color.svg").read_bytes().replace(b"Codex", b"Codey"))
        with self.assertRaisesRegex(APIError, "codex-color"):
            logo_registry(copy)

    def test_every_logo_records_its_source_license_and_hash(self):
        record = json.loads((LOGOS / "PROVENANCE.json").read_text())
        self.assertEqual(record["license"], "MIT")
        self.assertIn("@lobehub/icons-static-svg@1.95.1", record["source"])
        for name, entry in record["logos"].items():
            data = (LOGOS / entry["file"]).read_bytes()
            self.assertEqual((entry["sha256"], entry["bytes"]), (hashlib.sha256(data).hexdigest(), len(data)), name)
            self.assertNotIn(b"<script", data.lower())


if __name__ == "__main__":
    unittest.main()
