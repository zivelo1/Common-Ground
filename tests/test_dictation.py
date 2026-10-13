"""Dictation: a human's spoken memo becomes text for their own message box. The server checks the WAV,
runs the configured local speech program with no shell and an empty environment, deletes the memo, and returns the
text; nothing is stored or posted. The program here is a small stand-in script, so no model runs in the suite; the
real whisper.cpp run is recorded separately as evidence."""
import base64
import hashlib
import io
import json
import math
import os
import sqlite3
import stat
import sys
import time
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import test_backend

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from store import APIError, load_config

RATE = 16000
SECRET_NAME = "COMMON_GROUND_TEST_SECRET"
# The stand-in program: behaves as the "mode" file beside it says and records what it was given.
STAND_IN = """#!{python}
import hashlib, json, os, pathlib, sys, time
here = pathlib.Path(__file__).parent
args = sys.argv[1:]
memo = pathlib.Path(args[args.index("-f") + 1])
(here / "record.json").write_text(json.dumps({{"args": args, "env": sorted(os.environ),
    "memo_sha256": hashlib.sha256(memo.read_bytes()).hexdigest(), "memo_folder_mode": oct(memo.parent.stat().st_mode & 0o777)}}))
mode = (here / "mode").read_text()
if mode == "fail":
    sys.exit(3)
if mode in ("slow", "hold"):
    time.sleep(3 if mode == "slow" else 1.5)
print("[BLANK_AUDIO]" if mode == "blank" else " Hello Agent A,\\n please draft the table.")
"""


def wav(seconds=1.0, rate=RATE, channels=1, amplitude=0.3):
    out = io.BytesIO()
    with wave.open(out, "wb") as clip:
        clip.setnchannels(channels)
        clip.setsampwidth(2)
        clip.setframerate(rate)
        frames = int(seconds * rate)
        level = int(amplitude * 32767)
        clip.writeframes(b"".join(int(level * math.sin(2 * math.pi * 440 * i / rate)).to_bytes(2, "little", signed=True) * channels
                                  for i in range(frames)))
    return out.getvalue()


def encoded(data):
    return base64.b64encode(data).decode()


class DictationTests(unittest.TestCase):
    # Reuse the backend server fixture's methods; never bind its TestCase class here (its tests would run twice).
    setUp = test_backend.BackendTests.setUp
    start = test_backend.BackendTests.start
    stop = test_backend.BackendTests.stop
    tearDown = test_backend.BackendTests.tearDown
    call = test_backend.BackendTests.call
    ok = test_backend.BackendTests.ok

    def configure(self, mode="text", **overrides):
        tool = self.root / "speech"
        tool.mkdir(exist_ok=True)
        program = tool / "whisper-stand-in"
        program.write_text(STAND_IN.format(python=sys.executable))
        program.chmod(stat.S_IRWXU)
        (tool / "model.bin").write_bytes(b"stand-in model")
        (tool / "mode").write_text(mode)
        section = {"command": str(program), "model": str(tool / "model.bin"), "language": "en", "threads": 2,
                   "timeout_seconds": 1, "max_seconds": 30, **overrides}
        config = json.loads(self.config.read_text())
        config["dictation"] = section
        self.config.write_text(json.dumps(config))
        self.stop()
        self.start()
        return tool

    def dictate(self, data, actor="human", **extra):
        return self.call("POST", "/api/dictation", actor, {"data": data, **extra})

    def stored_rows(self):
        with sqlite3.connect(self.root / "state" / "collaboration.sqlite3") as db:
            return {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("messages", "message_images")}

    def test_a_human_gets_the_text_and_nothing_is_stored_or_posted(self):
        tool = self.configure()
        before = self.stored_rows()
        clip = wav()
        with mock.patch.dict(os.environ, {SECRET_NAME: "must not reach the program"}):
            status, result, _ = self.dictate(encoded(clip))
        self.assertEqual((status, result), (200, {"text": "Hello Agent A, please draft the table."}))
        record = json.loads((tool / "record.json").read_text())
        memo = Path(record["args"][record["args"].index("-f") + 1])
        self.assertEqual(record["args"], ["-m", str(tool / "model.bin"), "-f", str(memo), "-nt", "-np", "-l", "en", "-t", "2",
                                          "--prompt", "Test Board, Generic. Participants: Agent A, Agent B, Human."])
        self.assertEqual(record["memo_sha256"], hashlib.sha256(clip).hexdigest())
        self.assertEqual(record["memo_folder_mode"], "0o700")
        self.assertFalse(memo.exists())
        self.assertFalse(memo.parent.exists())
        self.assertNotIn(SECRET_NAME, record["env"])
        self.assertEqual(self.stored_rows(), before)

    def test_only_a_human_on_a_configured_board_is_offered_dictation(self):
        self.assertIsNone(self.ok("GET", "/api/state")["capabilities"]["dictation"])
        self.configure()
        self.assertEqual(self.ok("GET", "/api/state")["capabilities"]["dictation"], {"max_seconds": 30})
        self.assertIsNone(self.ok("GET", "/api/state", "a")["capabilities"]["dictation"])

    def test_without_the_program_dictation_is_refused(self):
        self.assertEqual(self.dictate(encoded(wav()))[0], 409)

    def test_refused_recordings_never_reach_the_program(self):
        tool = self.configure()
        refusals = {
            "anonymous": (self.dictate(encoded(wav()), actor=None)[0], 401),
            "agent": (self.dictate(encoded(wav()), actor="a")[0], 403),
            "extra field": (self.dictate(encoded(wav()), channel_id="workspace")[0], 400),
            "not base64": (self.dictate("not base64!")[0], 400),
            "not a WAV": (self.dictate(encoded(b"\x1a\x45\xdf\xa3 webm"))[0], 400),
            "stereo": (self.dictate(encoded(wav(channels=2)))[0], 400),
            "8 kHz": (self.dictate(encoded(wav(rate=8000)))[0], 400),
            "too short": (self.dictate(encoded(wav(seconds=0.2)))[0], 400),
            "too long": (self.dictate(encoded(wav(seconds=31)))[0], 400),
            "silence": (self.dictate(encoded(wav(amplitude=0.0)))[0], 422),
        }
        self.assertEqual({case: got for case, (got, _) in refusals.items()},
                         {case: want for case, (_, want) in refusals.items()})
        self.assertFalse((tool / "record.json").exists())

    def test_program_failures_are_reported_and_the_memo_is_still_deleted(self):
        for mode, want in (("fail", 502), ("blank", 422), ("slow", 504)):
            with self.subTest(mode=mode):
                tool = self.configure(mode)
                (tool / "record.json").unlink(missing_ok=True)
                status, result, _ = self.dictate(encoded(wav()))
                self.assertEqual(status, want, result)
                record = json.loads((tool / "record.json").read_text())
                self.assertFalse(Path(record["args"][record["args"].index("-f") + 1]).exists())

    def test_one_dictation_at_a_time(self):
        tool = self.configure("hold", timeout_seconds=5)
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(self.dictate, encoded(wav()))
            deadline = time.monotonic() + 5
            while not (tool / "record.json").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(self.dictate(encoded(wav()))[0], 429)
            self.assertEqual(first.result()[0], 200)

    def test_a_removed_participant_leaves_the_spelling_list(self):
        tool = self.configure()
        self.assertEqual(self.call("POST", "/api/participants/remove", "human",
                                   {"id": "a", "seen_name": "Agent A", "reason": "Left the project"})[0], 200)
        self.assertEqual(self.dictate(encoded(wav()))[0], 200)
        args = json.loads((tool / "record.json").read_text())["args"]
        self.assertEqual(args[-1], "Test Board, Generic. Participants: Agent B, Human.")

    def test_the_configuration_is_checked_at_start(self):
        tool = self.configure()
        good = json.loads(self.config.read_text())["dictation"]
        plain = tool / "not-executable"
        plain.write_text("")
        cases = {"missing key": {k: v for k, v in good.items() if k != "threads"},
                 "extra key": {**good, "url": "https://example.org"},
                 "relative command": {**good, "command": "whisper-cli"},
                 "not executable": {**good, "command": str(plain)},
                 "missing model": {**good, "model": str(tool / "absent.bin")},
                 "bad language": {**good, "language": "English"},
                 "no threads": {**good, "threads": 0},
                 "long timeout": {**good, "timeout_seconds": 601},
                 "long memo": {**good, "max_seconds": 241},
                 "not an object": "on"}
        for case, section in cases.items():
            with self.subTest(case=case):
                config = json.loads(self.config.read_text())
                config["dictation"] = section
                broken = self.root / "broken.json"
                broken.write_text(json.dumps(config))
                with self.assertRaises(APIError) as caught:
                    load_config(broken)
                self.assertEqual(caught.exception.status, 400)


if __name__ == "__main__":
    unittest.main()
