"""Read and write the board through its authenticated HTTP API."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

from store import MAX_PAGE, APIError, load_config
from store import participant_id as validate_participant_id

REQUEST_TIMEOUT_SECONDS = 10
MAX_WAIT_SECONDS = 60
POLL_SECONDS = 1


class RequestError(RuntimeError):
    def __init__(self, message, status=None):
        self.status = status
        super().__init__(message)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RequestError("The local collaboration API must not redirect", code)


def request(config_path: Path, participant_id: str, method: str, path: str, body=None):
    """Use one configured participant credential, without returning its token."""
    try:
        config = load_config(config_path)
        ident = validate_participant_id(participant_id)
    except APIError as error:
        raise RequestError(str(error), error.status) from error
    state_dir = config["state_dir"]
    try:
        with (state_dir / "runtime.json").open(encoding="utf-8") as handle:
            runtime = json.load(handle)
        credential_path = state_dir / "credentials" / f"{ident}.json"
        if credential_path.is_symlink():
            raise RequestError("Credential path must not be a symbolic link")
        with credential_path.open(encoding="utf-8") as handle:
            credential = json.load(handle)
    except FileNotFoundError as error:
        raise RequestError("Start this board and configure the participant first") from error
    if not isinstance(runtime, dict) or not isinstance(credential, dict):
        raise RequestError("Invalid instance runtime or credential file")
    origin = runtime.get("origin")
    if not isinstance(origin, str) or not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", origin):
        raise RequestError("Invalid local API endpoint")
    port = urlsplit(origin).port
    if not port or not 1 <= port <= 65535:
        raise RequestError("Invalid local API endpoint")
    if credential.get("participant_id") != ident or not isinstance(credential.get("token"), str):
        raise RequestError("Invalid participant credential")
    if method not in {"GET", "POST"}:
        raise RequestError("Only GET and POST requests are supported")
    if not isinstance(path, str) or not path.startswith("/api/") or urlsplit(path).netloc or urlsplit(path).fragment or "\r" in path or "\n" in path:
        raise RequestError("Use a local /api/ path")
    if body is not None and not isinstance(body, dict):
        raise RequestError("Request JSON must be an object")
    if method == "GET" and body is not None:
        raise RequestError("GET requests do not accept a JSON body")
    raw = json.dumps(body if body is not None else {}, ensure_ascii=False).encode("utf-8") if method == "POST" else None
    headers = {"Authorization": "Bearer " + credential["token"]}
    if method == "POST":
        headers.update({"X-Collab-Request": "1", "Content-Type": "application/json"})
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(Request(origin + path, data=raw, headers=headers, method=method), timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.load(response)
    except HTTPError as error:
        try:
            payload = json.load(error)
            message = payload.get("error", "Collaboration request failed")
        except (ValueError, AttributeError):
            message = "Collaboration request failed"
        raise RequestError(message, error.code) from error
    except URLError as error:
        raise RequestError("The collaboration board is unavailable; start it first") from error


def poll_messages(config_path, ident, channel, after):
    channels = [channel] if channel else [item["id"] for item in request(config_path, ident, "GET", "/api/state")["channels"]]
    messages = []
    bounded_cursors = []
    for channel_id in channels:
        result = request(config_path, ident, "GET", "/api/messages?" + urlencode({"channel_id": channel_id, "after": after}))
        messages.extend(result["messages"])
        if len(result["messages"]) == MAX_PAGE:
            bounded_cursors.append(result["cursor"])
    # A busy channel's first page must not skip earlier messages in other channels.
    cursor = min(bounded_cursors) if bounded_cursors else max([after] + [item["id"] for item in messages])
    return {"messages": sorted((item for item in messages if item["id"] <= cursor), key=lambda item: item["id"]), "cursor": cursor}


def wait(config_path, ident, channel, after, timeout):
    if type(after) is not int or after < 0:
        raise RequestError("after must be a non-negative integer")
    if not 0 <= timeout <= MAX_WAIT_SECONDS:
        raise RequestError(f"timeout must be between 0 and {MAX_WAIT_SECONDS} seconds")
    deadline = time.monotonic() + timeout
    while True:
        result = poll_messages(config_path, ident, channel, after)
        if result["messages"] or time.monotonic() >= deadline:
            return result
        time.sleep(min(POLL_SECONDS, max(0, deadline - time.monotonic())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--as", dest="ident", required=True, help="Configured participant id")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "channels", "decisions", "tasks", "export"):
        commands.add_parser(name)
    for name in ("brief", "knowledge"):
        command = commands.add_parser(name)
        command.add_argument("--channel")
    commands.add_parser("refresh-sources")
    remember = commands.add_parser("remember")
    remember.add_argument("--from-stdin", action="store_true", required=True)
    update = commands.add_parser("update-knowledge")
    update.add_argument("--id", required=True)
    update.add_argument("--version", required=True, type=int)
    update.add_argument("--from-stdin", action="store_true", required=True)
    for name in ("knowledge-history", "source-history"):
        command = commands.add_parser(name)
        command.add_argument("--id", required=True)
    check = commands.add_parser("knowledge-check")              # record the daily knowledge check (JSON on stdin)
    check.add_argument("--from-stdin", action="store_true", required=True)
    check_status = commands.add_parser("knowledge-check-status")
    check_status.add_argument("--channel", required=True)
    messages = commands.add_parser("messages")
    messages.add_argument("--channel", required=True)
    messages.add_argument("--after", type=int, default=0)
    send = commands.add_parser("send")
    send.add_argument("--channel", required=True)
    source = send.add_mutually_exclusive_group(required=True)
    source.add_argument("--body")
    source.add_argument("--from-stdin", action="store_true")
    send.add_argument("--client-id", default=None, help="Reuse this id when retrying the same message")
    waiting = commands.add_parser("wait")
    waiting.add_argument("--channel")
    waiting.add_argument("--after", type=int, default=0)
    waiting.add_argument("--timeout", type=float, default=MAX_WAIT_SECONDS)
    generic = commands.add_parser("request")
    generic.add_argument("--method", choices=("GET", "POST"), default="GET")
    generic.add_argument("--path", required=True)
    json_source = generic.add_mutually_exclusive_group()
    json_source.add_argument("--json")
    json_source.add_argument("--from-stdin", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "request":
            body = json.loads(sys.stdin.read()) if args.from_stdin else json.loads(args.json) if args.json is not None else None
            result = request(args.config, args.ident, args.method, args.path, body)
        elif args.command in {"brief", "knowledge"}:
            path = "/api/" + args.command + ("?" + urlencode({"channel_id": args.channel}) if args.channel else "")
            result = request(args.config, args.ident, "GET", path)
        elif args.command == "refresh-sources":
            result = request(args.config, args.ident, "POST", "/api/sources/refresh", {})
        elif args.command in {"remember", "update-knowledge"}:
            body = json.loads(sys.stdin.read())
            if not isinstance(body, dict):
                raise RequestError("Knowledge JSON must be an object")
            path = "/api/knowledge"
            if args.command == "update-knowledge":
                if not re.fullmatch(r"[a-f0-9]{32}", args.id):
                    raise RequestError("Invalid knowledge id")
                path += f"/{args.id}/update"
                body["version"] = args.version
            result = request(args.config, args.ident, "POST", path, body)
        elif args.command == "knowledge-check":
            body = json.loads(sys.stdin.read())
            if not isinstance(body, dict):
                raise RequestError("Knowledge check JSON must be an object")
            result = request(args.config, args.ident, "POST", "/api/knowledge-checks", body)
        elif args.command == "knowledge-check-status":
            result = request(args.config, args.ident, "GET", "/api/knowledge-checks?" + urlencode({"channel_id": args.channel}))
        elif args.command in {"knowledge-history", "source-history"}:
            validate_participant_id(args.id)
            collection = "knowledge" if args.command == "knowledge-history" else "sources"
            result = request(args.config, args.ident, "GET", f"/api/{collection}/{args.id}/history")
        elif args.command == "send":
            body = sys.stdin.read() if args.from_stdin else args.body
            result = request(args.config, args.ident, "POST", "/api/messages", {"channel_id": args.channel, "body": body, "client_id": args.client_id or uuid4().hex})
        elif args.command == "messages":
            result = request(args.config, args.ident, "GET", "/api/messages?" + urlencode({"channel_id": args.channel, "after": args.after}))
        elif args.command == "wait":
            result = wait(args.config, args.ident, args.channel, args.after, args.timeout)
        elif args.command == "export":
            result = request(args.config, args.ident, "GET", "/api/export")
        else:
            state = request(args.config, args.ident, "GET", "/api/state")
            result = state if args.command == "status" else state[args.command]
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (APIError, RequestError, ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
