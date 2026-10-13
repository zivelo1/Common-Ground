"""Authenticated UI boundary for explicit Common Ground provider jobs.

The Runner owns preparation, generation, budget and delivery. This controller
owns caller grants and bounded workers. Nothing polls or dispatches a model here.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from adapter_runner import (
    Configuration,
    Journal,
    Runner,
    identifier,
    load_provider_config,
    object_fields,
    plain,
)
from providers.base import AdapterError, Module, Transport
from store import APIError, Store

MAX_POLICY_BYTES = 65_536
MAX_UI_WORKERS = 32
MAX_OPERATION_ERRORS = 100


class StoreBoard:
    """A trusted grant selects a registered agent; Store still enforces its ACL."""

    def __init__(
        self, store: Store, agent: dict[str, Any], check: Callable[[], None]
    ) -> None:
        self.store, self.agent, self.check = store, agent, check

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.check()
        if method == "GET" and path == "/api/state":
            return self.store.state(self.agent)
        if method == "GET" and path.startswith("/api/brief?"):
            query = parse_qs(urlsplit(path).query)
            if set(query) != {"channel_id"} or len(query["channel_id"]) != 1:
                raise AdapterError("Invalid agent brief request")
            return self.store.brief(self.agent, query["channel_id"][0])
        if method == "POST" and path == "/api/personal/enqueue":
            return self.store.enqueue(self.agent, body)
        if method == "POST" and path == "/api/personal/send":
            return self.store.send_queued(self.agent, body)
        raise AdapterError("Unsupported internal agent request")


class AdapterController:
    def __init__(
        self,
        store: Store,
        workspace_path: Path,
        provider_path: Path,
        policy_path: Path | None = None,
        *,
        transport: Transport | None = None,
        secret_reader: Callable[[str], str] | None = None,
    ) -> None:
        self.store = store
        self.config: Configuration = load_provider_config(provider_path)
        if self.config.workspace_config != workspace_path.resolve():
            raise AdapterError("Provider workspace must match this server instance")
        if self.config.max_concurrent > MAX_UI_WORKERS:
            raise AdapterError("Provider UI concurrency exceeds its safety bound")
        self.human_launchers = self._policy(policy_path)
        self.journal = Journal(store.config["state_dir"])
        # Explicit server start reopens admission after orphan recovery. It never
        # replays jobs, starts a model or sends an existing result.
        self.journal.resume()
        self.transport, self.secret_reader = transport, secret_reader
        self.lock = threading.Lock()
        self.idle = threading.Condition(self.lock)
        self.inflight = 0
        self.slots = threading.BoundedSemaphore(self.config.max_concurrent)
        self.threads: set[threading.Thread] = set()
        self.active: set[str] = set()
        self.errors: OrderedDict[str, str] = OrderedDict()
        self.stopping = False

    def _policy(self, path: Path | None) -> dict[str, tuple[str, ...]]:
        self.policy_sha256 = None
        if path is None:
            return {}
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > MAX_POLICY_BYTES
        ):
            raise AdapterError("Invalid human launch policy file")
        try:
            raw = path.read_bytes()
            value = json.loads(raw)
            self.policy_sha256 = hashlib.sha256(raw).hexdigest()
        except (ValueError, RecursionError, UnicodeError):
            raise AdapterError("Invalid human launch policy JSON") from None
        value = object_fields(
            value, {"schema_version", "human_launchers"}, "human launch policy"
        )
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            raise AdapterError("Unsupported human launch policy version")
        grants = value["human_launchers"]
        if not isinstance(grants, dict):
            raise AdapterError("Invalid human launch grants")
        result: dict[str, tuple[str, ...]] = {}
        for module, people in grants.items():
            identifier(module, "policy module")
            if (
                module not in self.config.modules
                or not isinstance(people, list)
                or not people
            ):
                raise AdapterError(
                    "Human grant must name a configured module and humans"
                )
            if not all(isinstance(p, str) for p in people) or len(people) != len(
                set(people)
            ):
                raise AdapterError("Invalid human grant identities")
            registered_humans = {
                p["id"]
                for p in self.store.config["participants"]
                if p["kind"] == "human"
            }
            if any(person not in registered_humans for person in people):
                raise AdapterError(
                    "Launch grants must name registered human identities"
                )
            result[module] = tuple(identifier(p, "human identity") for p in people)
        return result

    def _grant(
        self, actor: dict[str, Any], module_id: str, channel_id: str | None = None
    ) -> tuple[Module, dict[str, Any], list[dict[str, Any]]]:
        module_id = identifier(module_id, "module id")
        module = self.config.modules.get(module_id)
        if module is None:
            raise APIError(403, "Module unavailable to this account")
        own = actor.get("kind") == "agent" and actor.get("id") == module.participant_id
        human = actor.get("kind") == "human" and actor.get(
            "id"
        ) in self.human_launchers.get(module.id, ())
        if not (own or human):
            raise APIError(403, "Module unavailable to this account")
        state = self.store.state(actor)
        agent = next(
            (
                p
                for p in state["participants"]
                if p["id"] == module.participant_id and p["kind"] == "agent" and p["active"]
            ),
            None,
        )
        caller = next(
            (
                p
                for p in state["participants"]
                if p["id"] == actor["id"] and p["kind"] == actor["kind"] and p["active"]
            ),
            None,
        )
        if agent is None or caller is None:
            raise APIError(403, "Configured agent or caller is unavailable")
        channels = [
            c
            for c in state["channels"]
            if c["id"] in module.allowed_channel_ids
            and agent["id"] in c["members"]
            and actor["id"] in c["members"]
        ]
        if channel_id is not None and not any(c["id"] == channel_id for c in channels):
            raise APIError(
                403, "Both caller and agent need explicit conversation export access"
            )
        return module, agent, channels

    def _runner(
        self, actor: dict[str, Any], module_id: str, channel_id: str | None = None
    ) -> Runner:
        _, agent, _ = self._grant(actor, module_id, channel_id)

        def check() -> None:
            self._grant(actor, module_id, channel_id)

        board = StoreBoard(self.store, agent, check)
        kwargs: dict[str, Any] = {}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        if self.secret_reader is not None:
            kwargs["secret_reader"] = self.secret_reader
        return Runner(self.config, self.journal, board, **kwargs)

    def descriptors(self, actor: dict[str, Any]) -> dict[str, Any]:
        modules = []
        for module in self.config.modules.values():
            try:
                _, _, channels = self._grant(actor, module.id)
            except APIError:
                continue
            runner = self._runner(actor, module.id)
            reasons = []
            try:
                runner.activated(module)
            except AdapterError as error:
                reasons.append(str(error))
            if not channels:
                reasons.append(
                    "No conversation is authorized for both caller and agent"
                )
            if self.stopping or self.journal.stopping():
                reasons.append("Adapter admission is stopping")
            modules.append(
                {
                    "id": module.id,
                    "provider": module.provider,
                    "participant_id": module.participant_id,
                    "model": module.model,
                    "endpoint": module.endpoint,
                    "enabled": module.enabled,
                    "allowed_channel_ids": [c["id"] for c in channels],
                    "blocked_reasons": reasons,
                    "limits": plain(asdict(module.limits)) if module.limits else None,
                    "human_launch": actor["kind"] == "human",
                }
            )
        return {
            "configured": True,
            "modules": modules,
            "stopping": self.stopping or self.journal.stopping(),
        }

    def _admit(self) -> None:
        with self.lock:
            if self.stopping or self.journal.stopping():
                raise APIError(503, "Adapter admission is stopping")
            if not self.slots.acquire(blocking=False):
                raise APIError(409, "Provider UI worker capacity is full")
            self.inflight += 1

    def _release_locked(self) -> None:
        self.inflight -= 1
        self.slots.release()
        self.idle.notify_all()

    def _release(self) -> None:
        with self.lock:
            self._release_locked()

    def prepare(self, actor: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        data = object_fields(
            data, {"module_id", "channel_id", "prompt"}, "prepare request"
        )
        runner = self._runner(actor, data["module_id"], data["channel_id"])
        self._admit()
        try:
            return runner.prepare(data["module_id"], data["channel_id"], data["prompt"])
        finally:
            self._release()

    def status(
        self, actor: dict[str, Any], module_id: str, run_id: str
    ) -> dict[str, Any]:
        runner = self._runner(actor, module_id)
        # Hold the worker-state lock while reading the durable row. A worker
        # cannot clear active between this snapshot and the returned flag.
        with self.lock:
            try:
                row = runner.status(module_id, run_id)
                self._grant(actor, module_id, row["channel_id"])
            except (AdapterError, APIError):
                raise APIError(403, "Run unavailable to this account") from None
            row["operation"] = {
                "active": run_id in self.active,
                "error": self.errors.get(run_id),
            }
        return row

    def recent(
        self,
        actor: dict[str, Any],
        module_id: str,
        channel_id: str | None,
        limit: int,
        before: str | None,
    ) -> dict[str, Any]:
        runner = self._runner(actor, module_id, channel_id)
        # Caller-visible channels narrow agent-owned results before pagination.
        _, _, channels = self._grant(actor, module_id, channel_id)
        scope = [
            c["id"] for c in channels if channel_id is None or c["id"] == channel_id
        ]
        if before is not None:
            cursor = self.status(actor, module_id, before)
            if cursor["channel_id"] not in scope:
                raise APIError(403, "Run unavailable to this account")
        return runner.recent(module_id, scope, limit, before)

    def import_result(
        self, actor: dict[str, Any], data: dict[str, Any]
    ) -> dict[str, Any]:
        data = object_fields(
            data, {"module_id", "run_id", "prepared_sha256", "text"}, "result import"
        )
        row = self.status(actor, data["module_id"], data["run_id"])
        runner = self._runner(actor, data["module_id"], row["channel_id"])
        self._admit()
        try:
            return runner.import_result(
                data["module_id"], data["run_id"], data["prepared_sha256"], data["text"]
            )
        finally:
            self._release()

    def schedule(
        self, actor: dict[str, Any], data: dict[str, Any], operation: str
    ) -> dict[str, Any]:
        data = object_fields(
            data, {"module_id", "run_id", "prepared_sha256"}, "explicit adapter action"
        )
        row = self.status(actor, data["module_id"], data["run_id"])
        runner = self._runner(actor, data["module_id"], row["channel_id"])
        runner.record(data["module_id"], data["run_id"], data["prepared_sha256"])
        if operation == "run" and row["status"] != "prepared":
            raise APIError(409, "Generation is one-shot; this run cannot be retried")
        if operation == "publish" and row["status"] != "completed":
            raise APIError(409, "Only a completed result can be published")
        if operation not in {"run", "publish"}:
            raise AdapterError("Unsupported controller operation")
        self._admit()
        with self.lock:
            if self.stopping or data["run_id"] in self.active:
                self._release_locked()
                raise APIError(
                    503 if self.stopping else 409,
                    "Adapter admission is stopping"
                    if self.stopping
                    else "This operation is already active",
                )
            self.active.add(data["run_id"])
            self.errors.pop(data["run_id"], None)

            def work() -> None:
                try:
                    self._grant(actor, data["module_id"], row["channel_id"])
                    action = runner.run if operation == "run" else runner.publish
                    action(data["module_id"], data["run_id"], data["prepared_sha256"])
                except (AdapterError, APIError) as error:
                    with self.lock:
                        self.errors[data["run_id"]] = str(error)
                except Exception:  # noqa: BLE001 -- never expose provider payloads or credentials.
                    with self.lock:
                        self.errors[data["run_id"]] = (
                            "Adapter operation failed; review its durable status"
                        )
                finally:
                    with self.lock:
                        while len(self.errors) > MAX_OPERATION_ERRORS:
                            self.errors.popitem(last=False)
                        self.active.discard(data["run_id"])
                        self.threads.discard(threading.current_thread())
                        self._release_locked()

            worker = threading.Thread(
                target=work, name="common-ground-adapter", daemon=False
            )
            self.threads.add(worker)
            worker.start()
        return self.status(actor, data["module_id"], data["run_id"])

    def begin_stop(self) -> None:
        with self.lock:
            self.stopping = True
            self.journal.begin_stop()

    def close(self, seconds: float) -> bool:
        self.begin_stop()
        deadline = time.monotonic() + seconds
        with self.idle:
            # Includes synchronous preview/import and the admission-to-worker gap.
            while self.inflight:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.idle.wait(remaining)
        return self.journal.drain(max(0, deadline - time.monotonic()))
