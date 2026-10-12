"""Loopback-only, authenticated local collaboration HTTP API."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import attachments
import backups
import store as store_module
from adapter_controller import AdapterController
from adapter_runner import MAX_RECENT_RUNS
from providers.base import AdapterError
from store import APIError, Store, load_config, logo_registry, now

MAX_BODY_BYTES = 65536
REQUEST_TIMEOUT_SECONDS = 10
ASSET_ROOT = Path(__file__).resolve().parent / "static"
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/static/style.css": ("style.css", "text/css; charset=utf-8"),
    "/static/common-ground.svg": ("common-ground.svg", "image/svg+xml"),
}
CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"


class CollaborationServer(ThreadingHTTPServer):
    # server_close joins workers before removing runtime state or releasing the lock.
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = True

    def __init__(self, config_path, port, *, providers=None, human_policy=None, controller_factory=None, backup_schedule=False):
        if type(port) is not int or not 0 <= port <= 65535:
            raise APIError(400, "Invalid port")
        config = load_config(config_path)
        # Logo bytes verified against their provenance once, then served from memory only.
        self.logos = {f"/static/logos/{name}.svg": data for name, data in logo_registry().items()}
        config["state_dir"].mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(config["state_dir"], 0o700)
        lock_path = config["state_dir"] / "server.lock"
        self._instance_lock = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        os.chmod(lock_path, 0o600)
        try:
            fcntl.flock(self._instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(self._instance_lock)
            self._instance_lock = None
            raise APIError(409, "This instance is already running") from error
        self.runtime_path = config["state_dir"] / "runtime.json"
        self.boot_id = uuid4().hex
        self._closed = False
        self._stopping = False
        self.admission_lock = threading.Lock()
        self.adapters = None
        self._shutdown_worker = None
        self.adapter_drain_seconds = REQUEST_TIMEOUT_SECONDS
        self.backup_scheduler = None
        try:
            self.store = Store(config)
            if human_policy is not None and providers is None:
                raise APIError(400, "A human policy requires explicit provider configuration")
            if controller_factory is not None:
                self.adapters = controller_factory(self.store)
            elif providers is not None:
                self.adapters = AdapterController(self.store, Path(config_path), providers, human_policy)
            if self.adapters is not None:
                self.adapter_drain_seconds += max((m.limits.timeout_seconds for m in self.adapters.config.modules.values() if m.limits), default=0)
            super().__init__(("127.0.0.1", port), Handler)
            self.origin = f"http://127.0.0.1:{self.server_address[1]}"
            self.host = self.origin.removeprefix("http://")
            self.cookie_name = f"collab_session_{self.server_address[1]}"
            self.runtime = {"origin": self.origin, "pid": os.getpid(), "started_at": now(), "boot_id": self.boot_id}
            self.runtime["adapter_configuration"] = {"provider_sha256": self.adapters.config.fingerprint, "human_policy_sha256": self.adapters.policy_sha256} if self.adapters is not None else None
            fd = os.open(self.runtime_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            os.chmod(self.runtime_path, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.runtime, handle)
                handle.write("\n")
            if backup_schedule:
                # Started last, so a failed start above never leaves a scheduler running.
                self.backup_scheduler = backups.Scheduler(self.store.scheduled_backup)
                self.backup_scheduler.start()
        except BaseException:
            if hasattr(self, "socket"):
                self.socket.close()
            if self._instance_lock is not None:
                fcntl.flock(self._instance_lock, fcntl.LOCK_UN)
                os.close(self._instance_lock)
                self._instance_lock = None
            raise

    def server_close(self):
        if self._closed:
            return
        if self.adapters is not None and not self.adapters.close(self.adapter_drain_seconds):
            raise APIError(503, "Provider work did not drain; runtime and instance lock remain held")
        if self.backup_scheduler is not None:
            # After the drain check, so a server that stays open keeps its schedule. Waits for a backup in progress.
            self.backup_scheduler.stop()
        self._closed = True
        try:
            super().server_close()
            if self.runtime_path.exists():
                with self.runtime_path.open(encoding="utf-8") as handle:
                    runtime = json.load(handle)
                if runtime.get("boot_id") == self.boot_id:
                    self.runtime_path.unlink()
        finally:
            if self._instance_lock is not None:
                fcntl.flock(self._instance_lock, fcntl.LOCK_UN)
                os.close(self._instance_lock)
                self._instance_lock = None

    def get_request(self):
        connection, address = super().get_request()
        # Bound partial HTTP headers too; _run starts only after headers arrive.
        connection.settimeout(REQUEST_TIMEOUT_SECONDS)
        return connection, address

    def begin_shutdown(self):
        with self.admission_lock:
            self._stopping = True
            if self._shutdown_worker is not None and self._shutdown_worker.is_alive():
                return
            if self.adapters is not None:
                self.adapters.begin_stop()

            def finish():
                if self.adapters is not None and not self.adapters.close(self.adapter_drain_seconds):
                    print("Provider work did not drain; workspace shutdown is incomplete", file=sys.stderr, flush=True)
                    return
                self.shutdown()

            self._shutdown_worker = threading.Thread(target=finish, daemon=True)
            self._shutdown_worker.start()


class Handler(BaseHTTPRequestHandler):
    server: CollaborationServer

    def log_message(self, format, *args):
        # Request paths and bodies may contain private material. Do not log them.
        return

    def _send(self, status, payload, *, content_type="application/json; charset=utf-8", cookie=None):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8") if content_type.startswith("application/json") else payload
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(raw)

    def send_error(self, code, message=None, explain=None):
        self._send(code, {"error": "Invalid request" if code == 400 else "Request not supported"})

    def _check_request(self, *, mutation=False):
        hosts = self.headers.get_all("Host", [])
        origins = self.headers.get_all("Origin", [])
        if hosts != [self.server.host]:
            raise APIError(403, "Invalid request host")
        if origins and origins != [self.server.origin]:
            raise APIError(403, "Foreign origins are not allowed")
        if mutation:
            if self.headers.get_all("X-Collab-Request", []) != ["1"]:
                raise APIError(403, "Missing collaboration request header")
            content_types = self.headers.get_all("Content-Type", [])
            if len(content_types) != 1 or content_types[0].split(";", 1)[0].strip().lower() != "application/json":
                raise APIError(415, "Content-Type must be application/json")
        route = urlsplit(self.path)
        if route.scheme or route.netloc or route.fragment:
            raise APIError(400, "Invalid request path")
        return route

    def _body(self, maximum=MAX_BODY_BYTES):
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
            raise APIError(400, "A single Content-Length is required")
        try:
            length = int(lengths[0])
        except ValueError as error:
            raise APIError(400, "Invalid Content-Length") from error
        if length < 0 or length > maximum:
            raise APIError(413, "Request body is too large")
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise APIError(400, "Incomplete request body")
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError, RecursionError) as error:
            raise APIError(400, "Invalid JSON body") from error
        if not isinstance(data, dict):
            raise APIError(400, "JSON body must be an object")
        return data

    def _actor(self):
        authorization = self.headers.get_all("Authorization", [])
        if authorization:
            if len(authorization) != 1 or not authorization[0].startswith("Bearer "):
                raise APIError(401, "Authentication required")
            return self.server.store.authenticate(authorization[0][7:])
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception as error:
            raise APIError(401, "Authentication required") from error
        session = cookie.get(self.server.cookie_name)
        if not session:
            raise APIError(401, "Authentication required")
        return self.server.store.browser_session_actor(session.value)

    @staticmethod
    def _query(route, key, default=None):
        values = parse_qs(route.query, keep_blank_values=True).get(key)
        if values is None:
            return default
        if len(values) != 1:
            raise APIError(400, f"Supply only one {key}")
        return values[0]

    def _dispatch_get(self):
        route = self._check_request()
        if route.path in self.server.logos:
            self._send(200, self.server.logos[route.path], content_type="image/svg+xml")
            return
        if route.path in ASSETS:
            filename, content_type = ASSETS[route.path]
            path = ASSET_ROOT / filename
            if not path.is_file():
                raise APIError(404, "UI asset not available")
            self._send(200, path.read_bytes(), content_type=content_type)
            return
        actor = self._actor()
        store = self.server.store
        if route.path == "/api/adapters":
            result = self.server.adapters.descriptors(actor) if self.server.adapters is not None else {"configured": False, "modules": [], "stopping": False}
        elif route.path == "/api/adapters/runs" or re.fullmatch(r"/api/adapters/runs/[a-f0-9]{32}", route.path):
            if self.server.adapters is None:
                raise APIError(409, "Provider controls are not configured")
            query = parse_qs(route.query, keep_blank_values=True)
            allowed = {"module_id", "channel_id", "limit", "before"} if route.path == "/api/adapters/runs" else {"module_id"}
            if set(query) - allowed:
                raise APIError(400, "Unknown adapter query field")
            module = self._query(route, "module_id")
            if route.path != "/api/adapters/runs":
                result = self.server.adapters.status(actor, module, route.path.split("/")[-1])
            else:
                raw_limit = self._query(route, "limit", str(MAX_RECENT_RUNS))
                if not re.fullmatch(r"[0-9]{1,2}", raw_limit):
                    raise APIError(400, "Invalid recent run limit")
                result = self.server.adapters.recent(actor, module, self._query(route, "channel_id"), int(raw_limit), self._query(route, "before"))
        elif route.path in {"/api/work-permissions", "/api/file-claims"}:
            query = parse_qs(route.query, keep_blank_values=True)
            allowed = {"task_id"} if route.path == "/api/work-permissions" else {"task_id", "limit", "before"}
            if set(query) - allowed:
                raise APIError(400, "Unknown ownership query field")
            task = self._query(route, "task_id")
            if route.path == "/api/work-permissions":
                result = store.work_permissions(actor, task)
            else:
                raw_limit = self._query(route, "limit", str(store_module.MAX_PAGE))
                if len(raw_limit) > len(str(store_module.MAX_PAGE)) or not re.fullmatch(r"[0-9]+", raw_limit):
                    raise APIError(400, "Invalid file claim page limit")
                result = store.file_claims(actor, task, int(raw_limit), self._query(route, "before"))
        elif re.fullmatch(r"/api/file-claims/[a-f0-9]{32}/history", route.path):
            result = store.file_claim_history(actor, route.path.split("/")[3])
        elif route.path == "/api/state":
            result = store.state(actor)
        elif re.fullmatch(r"/api/(images|audio)/[a-f0-9]{32}", route.path):
            serve = store.image if route.path.startswith("/api/images/") else store.audio
            raw, content_type = serve(actor, route.path.split("/")[-1])
            self._send(200, raw, content_type=content_type)
            return
        elif route.path == "/api/bookmarks":
            result = store.bookmarks(actor)
        elif route.path == "/api/backups":
            result = store.backup_status(actor)
        elif route.path == "/api/personal":
            result = store.personal(actor)
        elif route.path == "/api/messages":
            raw_after = self._query(route, "after", "0")
            if not re.fullmatch(r"[0-9]{1,19}", raw_after):
                raise APIError(400, "after must be a non-negative integer")
            raw_changes = self._query(route, "changes_after", "0")
            if not re.fullmatch(r"[0-9]{1,19}", raw_changes):
                raise APIError(400, "changes_after must be a non-negative integer")
            result = store.messages(actor, self._query(route, "channel_id"), int(raw_after), int(raw_changes))
        elif re.fullmatch(r"/api/messages/[0-9]{1,19}/receipts", route.path):
            result = store.receipts(actor, int(route.path.split("/")[3]))
        elif route.path == "/api/knowledge":
            result = store.knowledge(actor, self._query(route, "channel_id"))
        elif route.path == "/api/brief":
            result = store.brief(actor, self._query(route, "channel_id"))
        elif route.path == "/api/knowledge-checks":
            result = store.knowledge_check_status(actor, self._query(route, "channel_id"))
        elif re.fullmatch(r"/api/knowledge/[a-f0-9]{32}/history", route.path):
            result = store.knowledge_history(actor, route.path.split("/")[3])
        elif re.fullmatch(r"/api/sources/[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}/history", route.path):
            result = store.source_history(actor, route.path.split("/")[3])
        elif route.path == "/api/rules":
            result = store.rules(actor)
        elif route.path == "/api/search":
            result = store.search(actor, self._query(route, "q"))
        elif route.path == "/api/export":
            result = store.export(actor)
        elif re.fullmatch(r"/api/decisions/[a-f0-9]{32}/history", route.path):
            result = store.history(actor, route.path.split("/")[3])
        else:
            raise APIError(404, "Endpoint not found")
        self._send(200, result)

    def _dispatch_post(self):
        route = self._check_request(mutation=True)
        upload = route.path in {"/api/images", "/api/audio"}
        if upload:
            self._actor()  # Refuse anonymous uploads before reading their payload.
        data = self._body(attachments.MAX_IMAGE_REQUEST_BYTES if upload else MAX_BODY_BYTES)
        store = self.server.store
        if route.path == "/api/login":
            actor, session_id, duration = store.issue_browser_session(data.get("token"))
            self._send(200, {"me": actor}, cookie=f"{self.server.cookie_name}={session_id}; HttpOnly; SameSite=Strict; Path=/; Max-Age={duration}")
            return
        actor = self._actor()
        endpoints = {"/api/images": store.add_image, "/api/audio": store.add_audio, "/api/bookmarks": store.bookmark,
            "/api/participants": store.add_participant, "/api/participants/rename": store.rename_participant,
            "/api/channels": store.add_channel,
            "/api/messages": store.add_message, "/api/read": store.mark_read,
            "/api/decisions": store.add_decision, "/api/tasks": store.add_task,
            "/api/knowledge": store.add_knowledge, "/api/sources/refresh": store.refresh_sources,
            "/api/knowledge-checks": store.record_knowledge_check,
            "/api/personal/draft": store.save_draft, "/api/personal/preferences": store.save_preferences,
            "/api/personal/enqueue": store.enqueue, "/api/personal/send": store.send_queued,
            "/api/personal/restore": store.restore_queued, "/api/personal/discard": store.discard_queued,
            "/api/backups/settings": store.save_backup_settings, "/api/backups/run": store.run_backup,
            "/api/rules": store.save_rules}
        if route.path in {"/api/adapters/prepare", "/api/adapters/run", "/api/adapters/import-result", "/api/adapters/publish"}:
            if self.server.adapters is None:
                raise APIError(409, "Provider controls are not configured")
            if route.path == "/api/adapters/prepare":
                result = self.server.adapters.prepare(actor, data)
            elif route.path == "/api/adapters/import-result":
                result = self.server.adapters.import_result(actor, data)
            else:
                result = self.server.adapters.schedule(actor, data, "run" if route.path == "/api/adapters/run" else "publish")
        elif route.path == "/api/file-claims":
            result = store.claim_file(actor, data)
        elif re.fullmatch(r"/api/file-claims/[a-f0-9]{32}/(release|check)", route.path):
            action = store.release_file_claim if route.path.endswith("/release") else store.check_file_claim
            result = action(actor, route.path.split("/")[3], data)
        elif route.path in endpoints:
            result = endpoints[route.path](actor, data)
        elif re.fullmatch(r"/api/messages/[0-9]{1,19}/edit", route.path):
            result = store.edit_message(actor, int(route.path.split("/")[3]), data)
        elif re.fullmatch(r"/api/knowledge/[a-f0-9]{32}/update", route.path):
            result = store.update_knowledge(actor, route.path.split("/")[3], data)
        elif re.fullmatch(r"/api/decisions/[a-f0-9]{32}/status", route.path):
            result = store.decision_status(actor, route.path.split("/")[3], data)
        elif re.fullmatch(r"/api/tasks/[a-f0-9]{32}/status", route.path):
            result = store.task_status(actor, route.path.split("/")[3], data)
        elif re.fullmatch(r"/api/tasks/[a-f0-9]{32}/details", route.path):
            result = store.task_details(actor, route.path.split("/")[3], data)
        elif re.fullmatch(r"/api/tasks/[a-f0-9]{32}/move", route.path):
            result = store.task_move(actor, route.path.split("/")[3], data)
        elif route.path == "/api/shutdown":
            store._human(actor)
            self.server.begin_shutdown()
            self._send(200, {"stopping": True})
            return
        elif route.path == "/api/logout":
            cookie = SimpleCookie()
            cookie.load(self.headers.get("Cookie", ""))
            session = cookie.get(self.server.cookie_name)
            if session:
                self.server.store.revoke_browser_session(session.value)
            self._send(200, {"logged_out": True}, cookie=f"{self.server.cookie_name}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
            return
        else:
            raise APIError(404, "Endpoint not found")
        self._send(200, result)

    def _run(self, operation):
        self.connection.settimeout(REQUEST_TIMEOUT_SECONDS)
        try:
            # Admission and the stop flag share a lock. Previously admitted work
            # completes; later requests cannot start another mutation or scan.
            with self.server.admission_lock:
                if self.server._stopping and not (self.command == "POST" and self.path == "/api/shutdown"):
                    raise APIError(503, "Workspace is stopping; pending requests are finishing")
            operation()
        except APIError as error:
            self._send(error.status, {"error": str(error)})
        except AdapterError as error:
            self._send(409, {"error": str(error)})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return
        except Exception:  # noqa: BLE001 -- API boundary returns errors without logging private values.
            # Fail visibly without exposing request text, tokens, or filesystem paths.
            print("Collaboration request failed internally", file=sys.stderr, flush=True)
            self._send(500, {"error": "Internal server error"})

    def do_GET(self):
        self._run(self._dispatch_get)

    def do_POST(self):
        self._run(self._dispatch_post)

    def do_HEAD(self):
        self._run(self._dispatch_get)

    def _unsupported(self):
        self._check_request()
        raise APIError(405, "Method not allowed")

    def do_OPTIONS(self):
        self._run(self._unsupported)

    def do_PUT(self):
        self._run(self._unsupported)

    def do_DELETE(self):
        self._run(self._unsupported)

    def do_PATCH(self):
        self._run(self._unsupported)


def make_server(config_path, port=0, *, providers=None, human_policy=None, controller_factory=None, backup_schedule=False):
    return CollaborationServer(config_path, port, providers=providers, human_policy=human_policy, controller_factory=controller_factory, backup_schedule=backup_schedule)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--providers", type=Path)
    parser.add_argument("--adapter-human-policy", type=Path)
    args = parser.parse_args()
    # The running app makes scheduled backups; servers built directly (tests, tools) do not unless asked.
    server = make_server(args.config, args.port, providers=args.providers, human_policy=args.adapter_human_policy, backup_schedule=True)
    print(f"Collaboration board: {server.origin}", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("Stopping collaboration board", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
