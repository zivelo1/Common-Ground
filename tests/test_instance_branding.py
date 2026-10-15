"""Instance labels/marks change presentation without changing identity or history."""
import http.client
import json
import unittest
from pathlib import Path

import test_backend

import branding
from store import MAX_TITLE, APIError, load_config

STATIC = Path(__file__).resolve().parents[1] / "static"
PROFILE = {"name": "Museum Common Ground", "monogram": "AM", "color": "#087f8c"}


class InstanceBrandingTests(unittest.TestCase):
    setUp = test_backend.BackendTests.setUp
    start = test_backend.BackendTests.start
    stop = test_backend.BackendTests.stop
    tearDown = test_backend.BackendTests.tearDown
    call = test_backend.BackendTests.call
    ok = test_backend.BackendTests.ok

    def fetch(self, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    def test_unconfigured_branding_preserves_generic_assets_and_workspace_shape(self):
        for route, name in (("/", "index.html"), ("/index.html", "index.html"),
                            ("/static/common-ground.svg", "common-ground.svg")):
            with self.subTest(route=route):
                status, body, _ = self.fetch(route)
                self.assertEqual(status, 200)
                self.assertEqual(body, (STATIC / name).read_bytes())
        self.assertEqual(set(self.ok("GET", "/api/state")["workspace"]), {"title", "project_label"})

    def test_configured_name_and_mark_preserve_history_across_restart(self):
        before = self.ok("POST", "/api/messages", "a", {"channel_id": "workspace", "body": "Retained museum history", "client_id": "brand-history"})
        config = json.loads(self.config.read_text())
        config["branding"] = PROFILE
        self.stop()
        self.config.write_text(json.dumps(config))
        self.start()
        for route in ("/", "/index.html"):
            status, body, headers = self.fetch(route)
            self.assertEqual(status, 200)
            self.assertIn(b"<title>Museum Common Ground</title>", body)
            self.assertIn(b'<strong id="brand-name">Museum Common Ground</strong>', body)
            self.assertEqual(headers["Cache-Control"], "no-store")
        status, mark, headers = self.fetch("/static/common-ground.svg")
        self.assertEqual((status, headers["Content-Type"]), (200, "image/svg+xml"))
        self.assertIn(b"#087f8c", mark)
        self.assertIn(b">AM</text>", mark)
        self.assertIn(b">Museum Common Ground</title>", mark)
        self.assertNotEqual(mark, (STATIC / "common-ground.svg").read_bytes())
        self.assertEqual(self.ok("GET", "/api/state", "a")["workspace"]["brand_name"], PROFILE["name"])
        after = self.ok("GET", "/api/messages?channel_id=workspace", "a")["messages"][0]
        self.assertEqual((after["id"], after["author_id"], after["body"]), (before["id"], "a", before["body"]))

    def test_malformed_branding_is_refused_without_custom_paths_or_urls(self):
        config = json.loads(self.config.read_text())
        variants = [False, [], {}, {**PROFILE, "path": "outside.svg"}, {**PROFILE, "name": " "},
                    {**PROFILE, "name": "x" * (MAX_TITLE + 1)}, {**PROFILE, "name": "Null\u0000name"},
                    {**PROFILE, "monogram": "<x>"}, {**PROFILE, "monogram": "LONG"},
                    {**PROFILE, "color": "url(https://outside.invalid)"}, {**PROFILE, "color": "red"}]
        for profile in variants:
            with self.subTest(profile=profile):
                config["branding"] = profile
                self.config.write_text(json.dumps(config))
                with self.assertRaisesRegex(APIError, "branding"):
                    load_config(self.config)

    def test_names_are_escaped_and_changed_template_slots_fail_loud(self):
        profile = branding.settings({**PROFILE, "name": '<Museum & "friends">'}, max_name=MAX_TITLE)
        self.assertIsNotNone(profile)
        page = branding.index_page((STATIC / "index.html").read_bytes(), profile)
        mark = branding.logo((STATIC / "common-ground.svg").read_bytes(), profile)
        for body in (page, mark):
            self.assertIn(b"&lt;Museum &amp; &quot;friends&quot;&gt;", body)
            self.assertNotIn(b'<Museum & "friends">', body)
        with self.assertRaisesRegex(ValueError, "slot"):
            branding.index_page(b"<html></html>", profile)
        with self.assertRaisesRegex(ValueError, "slot"):
            branding.logo(b"<svg></svg>", profile)


if __name__ == "__main__":
    unittest.main()
