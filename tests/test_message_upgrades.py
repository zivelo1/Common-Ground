"""Real HTTP checks for image privacy, personal bookmarks, and durable login."""
from __future__ import annotations

import base64
import http.client
import io
import json
import threading
import unittest

import test_backend as fixture
from PIL import Image

from app import make_server
from store import APIError, load_config


class UpgradeBackendTests(unittest.TestCase):
    setUp = fixture.BackendTests.setUp
    stop = fixture.BackendTests.stop
    tearDown = fixture.BackendTests.tearDown
    call = fixture.BackendTests.call
    ok = fixture.BackendTests.ok
    message = fixture.BackendTests.message

    def start(self):
        self.server = make_server(self.config, port=getattr(self, "restart_port", 0))
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()

    def image_body(self, channel="workspace", client="image", colour="red"):
        raw = io.BytesIO()
        Image.new("RGB", (20, 10), colour).save(raw, format="PNG")
        return {"channel_id": channel, "client_id": client, "name": "chart.png", "data": base64.b64encode(raw.getvalue()).decode()}

    def binary_get(self, path, actor="human"):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        headers = {"Authorization": "Bearer " + self.tokens[actor]} if actor is not None else {}
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        result = response.status, response.read(), dict(response.getheaders())
        connection.close()
        return result

    def test_cookie_survives_restart_and_logout_revokes_it_after_restart(self):
        status, _, headers = self.call("POST", "/api/login", actor=None, body={"token": self.tokens["human"]})
        self.assertEqual(status, 200)
        cookie = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
        self.restart_port = self.server.server_address[1]
        self.stop()
        self.start()
        self.assertEqual(self.call("GET", "/api/state", actor=None, headers=cookie)[1]["me"]["id"], "human")
        self.assertEqual(self.call("POST", "/api/logout", actor=None, headers=cookie)[0], 200)
        self.stop()
        self.start()
        self.assertEqual(self.call("GET", "/api/state", actor=None, headers=cookie)[0], 401)

    def test_expiry_and_credential_rotation_refuse_existing_cookie(self):
        for change in ("expiry", "credential"):
            with self.subTest(change=change):
                _, _, headers = self.call("POST", "/api/login", actor=None, body={"token": self.tokens["a"]})
                cookie = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
                with self.server.store.connection(write=True) as db:
                    if change == "expiry":
                        db.execute("UPDATE browser_sessions SET expires_at=0")
                    else:
                        db.execute("UPDATE participants SET token_hash=? WHERE id='a'", ("revoked",))
                self.assertEqual(self.call("GET", "/api/state", actor=None, headers=cookie)[0], 401)

    def test_only_hashes_are_stored_and_unknown_cookie_is_refused(self):
        _, _, headers = self.call("POST", "/api/login", actor=None, body={"token": self.tokens["human"]})
        cookie = headers["Set-Cookie"].split(";", 1)[0].split("=", 1)[1]
        with self.server.store.connection() as db:
            row = dict(db.execute("SELECT * FROM browser_sessions").fetchone())
        self.assertNotIn(cookie, json.dumps(row))
        self.assertNotIn(self.tokens["human"], json.dumps(row))
        self.assertEqual(self.call("GET", "/api/state", actor=None, headers={"Cookie": self.server.cookie_name + "=unknown"})[0], 401)

    def test_private_image_bytes_are_member_only_and_survive_restart(self):
        channel = self.ok("POST", "/api/channels", body={"title": "Private images", "members": ["a"]})["id"]
        body = self.image_body(channel)
        image = self.ok("POST", "/api/images", "a", body)
        status, raw, headers = self.binary_get(image["url"], "a")
        self.assertEqual(status, 200)
        self.assertEqual(raw, base64.b64decode(body["data"]))
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(self.binary_get(image["url"], "b")[0], 404)
        self.assertEqual(self.binary_get(image["url"], None)[0], 401)
        self.stop()
        self.start()
        self.assertEqual(self.binary_get(image["url"], "human")[1], raw)

    def test_invalid_images_are_rejected_and_membership_is_checked_before_decode(self):
        for raw in (b"<svg><script>alert(1)</script></svg>", b"not an image", b"\xff\xd8\xffcorrupt"):
            body = self.image_body()
            body["data"] = base64.b64encode(raw).decode()
            self.assertEqual(self.call("POST", "/api/images", body=body)[0], 400)
        body["data"] = "!not base64!"
        self.assertEqual(self.call("POST", "/api/images", body=body)[0], 400)
        channel = self.ok("POST", "/api/channels", body={"title": "Private", "members": ["a"]})["id"]
        body["channel_id"] = channel
        self.assertEqual(self.call("POST", "/api/images", "b", body)[0], 404)
        with self.server.store.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM message_images").fetchone()[0], 0)

    def test_image_retry_is_idempotent_and_changed_retry_is_refused(self):
        body = self.image_body()
        first = self.ok("POST", "/api/images", body=body)
        self.assertEqual(self.ok("POST", "/api/images", body=body), first)
        self.assertEqual(self.call("POST", "/api/images", body=self.image_body(colour="blue"))[0], 409)

    def test_saved_messages_are_personal_and_hide_removed_memberships(self):
        channel = self.ok("POST", "/api/channels", body={"title": "Private bookmarks", "members": ["a"]})["id"]
        message = self.message("a", channel, "private bookmark", "saved")
        self.ok("POST", "/api/bookmarks", "a", {"message_id": message["id"], "saved": True})
        self.assertEqual(self.ok("GET", "/api/bookmarks", "a")["messages"], [message])
        self.assertEqual(self.ok("GET", "/api/bookmarks", "b")["messages"], [])
        self.assertEqual(self.call("POST", "/api/bookmarks", "b", {"message_id": message["id"], "saved": True})[0], 404)
        self.stop()
        self.start()
        self.assertEqual(self.ok("GET", "/api/bookmarks", "a")["messages"], [message])
        with self.server.store.connection(write=True) as db:
            db.execute("DELETE FROM members WHERE channel_id=? AND participant_id='a'", (channel,))
        self.assertEqual(self.ok("GET", "/api/bookmarks", "a")["messages"], [])

    def test_browser_session_config_validates_without_guessing(self):
        original = json.loads(self.config.read_text())
        for value in (True, 0, -1, 366, "30", None):
            self.config.write_text(json.dumps({**original, "browser_session_days": value}))
            with self.assertRaises(APIError):
                load_config(self.config)
        self.config.write_text(json.dumps(original))


if __name__ == "__main__":
    unittest.main()
