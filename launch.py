"""Open or stop one local collaboration instance, without exposing its key."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote, urlsplit

import cli
from store import APIError, load_config

STARTUP_TIMEOUT_SECONDS = 15
LAUNCH_POLL_INTERVAL_SECONDS = 0.15


def instance(config_path: Path) -> tuple[Path, str]:
    data = load_config(config_path)
    state = data["state_dir"]
    humans = [p["id"] for p in data["participants"] if p["kind"] == "human"]
    if len(humans) != 1:
        raise ValueError("The launcher needs exactly one human owner in this instance")
    return state.resolve(), humans[0]


def live(config_path: Path, human: str) -> bool:
    try:
        cli.request(config_path, human, "GET", "/api/state")
    except (OSError, ValueError, RuntimeError):
        return False
    return True


def stopped(state: Path, previous_boot: str) -> bool:
    runtime_path = state / "runtime.json"
    try:
        current = json.loads(runtime_path.read_text())
    except FileNotFoundError:
        current = None
    if current is not None:
        return current.get("boot_id") != previous_boot
    # Runtime removal precedes lock release. Wait for both before a restart.
    descriptor = os.open(state / "server.lock", os.O_RDWR)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return True
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("config.json"))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    config_path = args.config.resolve()
    state, human = instance(config_path)
    port = load_config(config_path)["port"]
    if args.stop:
        if live(config_path, human):
            runtime_path = state / "runtime.json"
            previous = json.loads(runtime_path.read_text())
            cli.request(config_path, human, "POST", "/api/shutdown", {})
            deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
            while not stopped(state, previous["boot_id"]):
                if time.monotonic() >= deadline:
                    raise RuntimeError("The workspace did not finish stopping")
                time.sleep(LAUNCH_POLL_INTERVAL_SECONDS)
            print("Stopped this local collaboration workspace.")
        else:
            print("This workspace is already stopped.")
        return 0
    if not live(config_path, human):
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(state, 0o700)
        log_path = state / "server.log"
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.chmod(log_path, 0o600)
        with os.fdopen(fd, "ab") as log:
            child = subprocess.Popen([sys.executable, "-B", str(Path(__file__).with_name("app.py")),
                                      "--config", str(config_path), "--port", str(port)],
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     start_new_session=True, close_fds=True)
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline and not live(config_path, human):
            if child.poll() is not None:
                raise RuntimeError(f"Local app could not start. Read {log_path}")
            time.sleep(LAUNCH_POLL_INTERVAL_SECONDS)
        if not live(config_path, human):
            child.terminate()
            raise RuntimeError(f"Local app did not become ready. Read {log_path}")
    runtime = json.loads((state / "runtime.json").read_text())
    origin = runtime["origin"]
    url = urlsplit(origin)
    if url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port:
        raise ValueError("Runtime must point to this Mac's loopback address")
    if not args.no_browser:
        credential = json.loads((state / "credentials" / f"{human}.json").read_text())
        # A fragment stays in the browser; the UI clears it before any request.
        webbrowser.open(origin + "#token=" + quote(credential["token"], safe=""))
    print(f"Local workspace ready: {origin}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (APIError, OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
