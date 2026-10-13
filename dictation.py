"""Optional local dictation: a short spoken memo becomes text that the human checks in the message box before sending.

The page converts its recording to 16 kHz mono 16-bit WAV. The server checks it, hands it to a locally installed
whisper.cpp program (`whisper-cli`) and deletes it at once. Nothing is stored or posted, and no network is used: the
program and its model are files on this computer, named in the configuration. Without that configuration dictation is
off and the page shows no microphone button.
"""
from __future__ import annotations

import base64
import binascii
import io
import os
import re
import subprocess
import sys
import tempfile
import threading
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

SAMPLE_RATE = 16_000            # whisper.cpp's input rate; the page resamples to it
SAMPLE_WIDTH_BYTES = 2          # 16-bit PCM
MIN_SECONDS = 0.5
MAX_SECONDS_LIMIT = 240         # keeps the WAV (32 000 bytes a second) under the 8 MB upload limit
WAV_HEADER_SLACK_BYTES = 1024
MAX_WAV_BYTES = MAX_SECONDS_LIMIT * SAMPLE_RATE * SAMPLE_WIDTH_BYTES + WAV_HEADER_SLACK_BYTES
MAX_TIMEOUT_SECONDS = 600
MAX_THREADS = 64
# Whisper writes a word such as "you" for pure silence (measured 12 October on 2 s of zeros), so a recording whose
# loudest sample stays below 1% of full scale is refused as "no speech" before the program runs.
SILENCE_PEAK_FRACTION = 0.01
FULL_SCALE = 32767
MAX_PROMPT_CHARS = 600          # whisper's initial prompt is short; names beyond this are left out
SETTINGS = ("command", "model", "language", "threads", "timeout_seconds", "max_seconds")
LANGUAGE = re.compile(r"auto|[a-z]{2,3}")


class DictationError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


@dataclass(frozen=True)
class Settings:
    command: Path
    model: Path
    language: str
    threads: int
    timeout_seconds: int
    max_seconds: int


def _whole(value: object, name: str, highest: int) -> int:
    if type(value) is not int or not 1 <= value <= highest:
        raise ValueError(f"dictation {name} must be a whole number from 1 to {highest}")
    return value


def _file(value: object, name: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"dictation {name} must be a path")
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"dictation {name} must be an absolute path to an existing file")
    return path


def settings(value: object) -> Settings | None:
    """The `dictation` configuration section, checked; None when the section is absent (dictation off)."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != set(SETTINGS):
        raise ValueError("dictation needs exactly " + ", ".join(SETTINGS))
    command = _file(value["command"], "command")
    if not os.access(command, os.X_OK):
        raise ValueError("dictation command must be executable")
    language = value["language"]
    if not isinstance(language, str) or not LANGUAGE.fullmatch(language):
        raise ValueError("dictation language must be a two- or three-letter code or auto")
    return Settings(command, _file(value["model"], "model"), language,
                    _whole(value["threads"], "threads", MAX_THREADS),
                    _whole(value["timeout_seconds"], "timeout_seconds", MAX_TIMEOUT_SECONDS),
                    _whole(value["max_seconds"], "max_seconds", MAX_SECONDS_LIMIT))


def speech_wav(encoded: object, max_seconds: int) -> bytes:
    """The decoded WAV, refused unless it is 16 kHz mono 16-bit PCM between MIN_SECONDS and max_seconds, with sound."""
    if not isinstance(encoded, str) or not encoded or len(encoded) > ((MAX_WAV_BYTES + 2) // 3) * 4:
        raise DictationError(400, f"Dictation is limited to {max_seconds} seconds")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise DictationError(400, "Invalid recording encoding") from error
    try:
        with wave.open(io.BytesIO(raw), "rb") as clip:
            shape = (clip.getnchannels(), clip.getsampwidth(), clip.getframerate(), clip.getcomptype())
            frames = clip.getnframes()
            samples = clip.readframes(frames)
    except (wave.Error, EOFError) as error:
        raise DictationError(400, "Send the recording as 16 kHz mono 16-bit WAV") from error
    if shape != (1, SAMPLE_WIDTH_BYTES, SAMPLE_RATE, "NONE") or len(samples) != frames * SAMPLE_WIDTH_BYTES:
        raise DictationError(400, "Send the recording as 16 kHz mono 16-bit WAV")
    if frames < MIN_SECONDS * SAMPLE_RATE:
        raise DictationError(400, "The recording is too short")
    if frames > max_seconds * SAMPLE_RATE:
        raise DictationError(400, f"Dictation is limited to {max_seconds} seconds")
    levels = array("h", samples)
    if sys.byteorder == "big":        # WAV samples are little-endian
        levels.byteswap()
    if max(max(levels), -min(levels)) < SILENCE_PEAK_FRACTION * FULL_SCALE:
        raise DictationError(422, "No speech was heard")
    return raw


def prompt(topics: list[str], names: list[str]) -> str:
    """Whisper's initial prompt: the words it should spell as written (measured: without it a participant's name came
    back misspelled)."""
    text = ", ".join(topics)[:MAX_PROMPT_CHARS // 2] + ". Participants:"
    for name in names:
        if len(text) + len(name) + 2 > MAX_PROMPT_CHARS:
            break
        text += (" " if text.endswith(":") else ", ") + name
    return text + "."


class Dictation:
    def __init__(self, config: Settings) -> None:
        self.config = config
        self._busy = threading.Lock()      # one transcription at a time: it uses most of the machine for a moment

    def transcribe(self, encoded: object, topics: list[str], names: list[str]) -> str:
        audio = speech_wav(encoded, self.config.max_seconds)
        if not self._busy.acquire(blocking=False):
            raise DictationError(429, "Another dictation is being turned into text; try again in a moment")
        try:
            with tempfile.TemporaryDirectory(prefix="common-ground-dictation-") as folder:   # private (0700), removed after
                memo = Path(folder) / "memo.wav"
                memo.write_bytes(audio)
                command = [str(self.config.command), "-m", str(self.config.model), "-f", str(memo), "-nt", "-np",
                           "-l", self.config.language, "-t", str(self.config.threads), "--prompt", prompt(topics, names)]
                try:
                    # No shell and an empty environment: the program gets the memo, never the app's tokens or settings.
                    done = subprocess.run(command, capture_output=True, text=True, env={}, check=False,
                                          timeout=self.config.timeout_seconds)
                except subprocess.TimeoutExpired as error:
                    raise DictationError(504, "Turning the speech into text took too long") from error
        finally:
            self._busy.release()
        if done.returncode != 0:
            raise DictationError(502, "The local speech program failed")
        text = " ".join(done.stdout.split())
        if not text or text == "[BLANK_AUDIO]":
            raise DictationError(422, "No speech was heard")
        return text
