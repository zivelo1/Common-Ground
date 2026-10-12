"""Voice messages (): a recording is stored like an image and served only to the conversation's members, at
its own route. The server recognises the recording's container (WebM, Ogg, MP4) and never decodes or transcribes it."""
import base64
import http.client
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_backend

import attachments

WEBM = b"\x1a\x45\xdf\xa3" + b"synthetic webm body"
OGG = b"OggS" + b"synthetic ogg body"
MP4 = b"\x00\x00\x00\x18ftypM4A " + b"synthetic mp4 body"
PNG_1X1 = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==")


class VoiceMessageTests(unittest.TestCase):
    # Reuse the backend server fixture's methods. Never subclass or bind its TestCase class here: the loader would
    # collect and run every backend test a second time.
    setUp = test_backend.BackendTests.setUp
    start = test_backend.BackendTests.start
    stop = test_backend.BackendTests.stop
    tearDown = test_backend.BackendTests.tearDown
    call = test_backend.BackendTests.call
    ok = test_backend.BackendTests.ok

    def fetch(self, path, actor):
        """GET returning the raw bytes (the shared call() parses JSON; a recording is not JSON)."""
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        connection.request("GET", path, headers={"Authorization": "Bearer " + self.tokens[actor]})
        response = connection.getresponse()
        result = (response.status, response.read(), response.getheader("Content-Type"))
        connection.close()
        return result

    def upload(self, raw, actor="a", channel="workspace", client="v1", path="/api/audio"):
        return self.call("POST", path, actor, {"channel_id": channel, "client_id": client, "name": "Voice message 0:03",
                                                "data": base64.b64encode(raw).decode()})

    def test_each_recording_container_is_stored_and_served_to_members(self):
        for index, (raw, content_type) in enumerate(((WEBM, "audio/webm"), (OGG, "audio/ogg"), (MP4, "audio/mp4"))):
            with self.subTest(content_type=content_type):
                status, result, _ = self.upload(raw, client=f"v{index}")
                self.assertEqual(status, 200, result)
                self.assertEqual((result["content_type"], result["bytes"]), (content_type, len(raw)))
                self.assertRegex(result["url"], r"^/api/audio/[a-f0-9]{32}$")
                self.assertEqual(self.fetch(result["url"], "b"), (200, raw, content_type))

    def test_a_retry_returns_the_same_recording_and_a_reused_id_is_refused(self):
        first = self.upload(WEBM)[1]
        self.assertEqual(self.upload(WEBM)[1]["id"], first["id"])
        status, result, _ = self.upload(OGG)
        self.assertEqual(status, 409, result)
        self.assertIn("recording", result["error"])

    def test_recordings_stay_in_their_conversation_and_route(self):
        private = self.ok("POST", "/api/channels", "human", {"title": "Human and A", "members": ["human", "a"]})["id"]
        voice = self.upload(WEBM, channel=private)[1]
        self.assertEqual(self.fetch(voice["url"], "b")[0], 404)                             # not a member
        self.assertEqual(self.fetch(voice["url"], "human"), (200, WEBM, "audio/webm"))
        self.assertEqual(self.fetch("/api/images/" + voice["id"], "a")[0], 404)              # a recording is not an image
        image = self.upload(PNG_1X1, client="i1", path="/api/images")[1]
        self.assertEqual(self.fetch("/api/audio/" + image["id"], "a")[0], 404)               # an image is not a recording
        self.assertEqual(self.fetch(image["url"], "a"), (200, PNG_1X1, "image/png"))
        self.assertEqual(self.upload(WEBM, actor="b", channel=private, client="v9")[0], 404)  # cannot post into it
        self.assertEqual(self.upload(WEBM, actor=None, client="v8")[0], 401)

    def test_anything_but_a_recording_container_is_refused(self):
        for label, raw in (("text", b"plain text, not audio"), ("png", PNG_1X1), ("empty", b"")):
            with self.subTest(label=label):
                status, result, _ = self.upload(raw, client=f"bad-{label}")
                self.assertEqual(status, 400, result)
        status, result, _ = self.call("POST", "/api/audio", "a", {"channel_id": "workspace", "client_id": "x", "name": "n",
                                                                  "data": "not base64!"})
        self.assertEqual(status, 400, result)
        self.assertEqual(self.call("POST", "/api/audio", "a", {"channel_id": "workspace", "client_id": "x", "name": "n",
                                                               "data": base64.b64encode(WEBM).decode(), "extra": 1})[0], 400)
        with self.assertRaises(ValueError):
            attachments.validate_audio("A" * (((attachments.MAX_AUDIO_BYTES + 2) // 3) * 4 + 4))   # over the 8 MB limit


if __name__ == "__main__":
    unittest.main()
