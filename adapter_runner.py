"""Explicit provider jobs for Common Ground. No automatic wake or generation retry."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import uuid4

from providers import PROVIDERS
from providers.base import (
    AdapterError,
    Cancellation,
    DeadlineCancellation,
    Limits,
    Module,
    Pricing,
    ProviderError,
    Result,
    Transport,
    encoded,
)
from providers.openai_responses import OPENAI_RESPONSES_ENDPOINT

MAX_CONFIG_BYTES = 1_048_576
MAX_SQLITE_INTEGER = (1 << 63) - 1
MICRO_USD_PER_USD = 1_000_000
READ_CHUNK_BYTES = 16_384
DRAIN_POLL_SECONDS = 0.02
MAX_RECENT_RUNS = 50
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
RUN_ID = re.compile(r"[a-f0-9]{32}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
SECRET_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
EPHEMERAL_CONTEXT_FIELDS = {
    "generated_at",
    "checked_at",
    "age_seconds",
    "scan_age_seconds",
    "last_scan_at",
}
BRIEF_COLLECTIONS = {
    "context",
    "knowledge",
    "lessons",
    "disputed",
    "stale",
    "decisions",
    "tasks",
    "sources",
    "recent_messages",
}


def plain(value: Any) -> Any:
    if isinstance(value, (Decimal, Path)):
        return str(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(plain(value))).hexdigest()


def object_fields(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise AdapterError(f"Invalid {label} fields")
    return value


def identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or IDENTIFIER.fullmatch(value) is None:
        raise AdapterError(f"Invalid {label}")
    return value


def integer(value: Any, label: str) -> int:
    if type(value) is not int or not 0 < value <= MAX_SQLITE_INTEGER:
        raise AdapterError(f"{label} must be a positive integer")
    return value


def money(value: Any, label: str, nullable: bool = False) -> Decimal | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise AdapterError(f"{label} must be an explicit decimal string")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise AdapterError(f"Invalid {label}") from None
    if (
        not amount.is_finite()
        or amount < 0
        or amount * MICRO_USD_PER_USD > MAX_SQLITE_INTEGER
    ):
        raise AdapterError(f"Invalid {label}")
    return amount


def micros(amount: Decimal) -> int:
    return int((amount * MICRO_USD_PER_USD).to_integral_value(rounding=ROUND_CEILING))


@dataclass(frozen=True)
class Configuration:
    workspace_config: Path
    modules: dict[str, Module]
    max_concurrent: int
    daily_budget_usd: Decimal | None
    pricing_max_age_days: int
    fingerprint: str


def load_provider_config(path: Path) -> Configuration:
    path = path.resolve()
    with path.open("rb") as handle:
        raw = handle.read(MAX_CONFIG_BYTES + 1)
    if len(raw) > MAX_CONFIG_BYTES:
        raise AdapterError("Provider configuration is too large")
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        raise AdapterError("Invalid provider JSON") from None
    data = object_fields(
        data,
        {"schema_version", "workspace_config", "instance_limits", "modules"},
        "configuration",
    )
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise AdapterError("Unsupported provider schema version")
    workspace = data["workspace_config"]
    if (
        not isinstance(workspace, str)
        or not workspace
        or len(workspace) > MAX_CONFIG_BYTES
    ):
        raise AdapterError("An explicit workspace config is required")
    workspace_path = (path.parent / workspace).resolve()
    limits = object_fields(
        data["instance_limits"],
        {"max_concurrent", "daily_budget_usd", "pricing_max_age_days"},
        "instance limits",
    )
    budget = money(limits["daily_budget_usd"], "daily budget", True)
    modules: dict[str, Module] = {}
    if not isinstance(data["modules"], list) or not data["modules"]:
        raise AdapterError("Configure at least one explicit module")
    for item in data["modules"]:
        item = object_fields(
            item,
            {
                "id",
                "provider",
                "participant_id",
                "enabled",
                "model",
                "endpoint",
                "allowed_channel_ids",
                "credential_env",
                "local_only_verified",
                "local_only_evidence",
                "limits",
                "pricing",
            },
            "module",
        )
        ident = identifier(item["id"], "module id")
        if (
            ident in modules
            or not isinstance(item["provider"], str)
            or item["provider"] not in PROVIDERS
        ):
            raise AdapterError("Duplicate module or unsupported provider")
        for key in ("enabled", "local_only_verified"):
            if type(item[key]) is not bool:
                raise AdapterError(f"{key} must be a boolean")
        for key in ("model", "endpoint", "credential_env", "local_only_evidence"):
            if item[key] is not None and (
                not isinstance(item[key], str)
                or not item[key].strip()
                or len(item[key]) > MAX_CONFIG_BYTES
            ):
                raise AdapterError(f"Invalid {key}")
        if item["credential_env"] is not None and not SECRET_NAME.fullmatch(
            item["credential_env"]
        ):
            raise AdapterError("Invalid credential environment name")
        channels = item["allowed_channel_ids"]
        if (
            not isinstance(channels, list)
            or not all(isinstance(c, str) for c in channels)
            or len(channels) != len(set(channels))
        ):
            raise AdapterError("Invalid explicit channel export allowlist")
        for channel in channels:
            identifier(channel, "channel id")
        endpoint = item["endpoint"]
        if endpoint is not None:
            url = urlsplit(endpoint)
            if (
                item["provider"] == "openai_responses"
                and endpoint != OPENAI_RESPONSES_ENDPOINT
            ):
                raise AdapterError(
                    "Only the reviewed OpenAI Responses origin is supported"
                )
            if item["provider"] == "ollama":
                try:
                    port = url.port
                except ValueError:
                    raise AdapterError("Invalid Ollama port") from None
                if (
                    url.scheme != "http"
                    or url.hostname != "127.0.0.1"
                    or not port
                    or not 1 <= port <= 65535
                    or url.path != "/api/chat"
                    or url.query
                    or url.fragment
                    or url.username
                    or url.password
                ):
                    raise AdapterError(
                        "Ollama requires an exact local native chat endpoint"
                    )
            if item["provider"] == "manual":
                raise AdapterError("Manual mode does not accept a provider endpoint")
        checked_limits = None
        if item["limits"] is not None:
            ml = object_fields(
                item["limits"],
                {
                    "max_context_bytes",
                    "max_response_bytes",
                    "max_output_tokens",
                    "timeout_seconds",
                    "max_concurrent",
                    "max_run_usd",
                },
                "module limits",
            )
            seconds = ml["timeout_seconds"]
            if (
                type(seconds) not in {int, float}
                or not 0 < seconds <= MAX_SQLITE_INTEGER
                or not Decimal(str(seconds)).is_finite()
            ):
                raise AdapterError("An explicit finite provider deadline is required")
            checked_limits = Limits(
                integer(ml["max_context_bytes"], "context limit"),
                integer(ml["max_response_bytes"], "response limit"),
                integer(ml["max_output_tokens"], "output limit"),
                float(seconds),
                integer(ml["max_concurrent"], "module concurrency"),
                money(ml["max_run_usd"], "per-run budget", True),
            )
        elif item["enabled"]:
            raise AdapterError("Enabled modules require explicit limits")
        pricing = None
        if item["pricing"] is not None:
            price = object_fields(
                item["pricing"],
                {
                    "input_usd_per_million",
                    "output_usd_per_million",
                    "max_input_tokens",
                    "checked_at",
                    "evidence",
                },
                "pricing",
            )
            incoming, outgoing = (
                money(price["input_usd_per_million"], "input rate"),
                money(price["output_usd_per_million"], "output rate"),
            )
            assert incoming is not None and outgoing is not None
            if (
                not isinstance(price["checked_at"], str)
                or not isinstance(price["evidence"], str)
                or not price["evidence"].startswith("https://")
            ):
                raise AdapterError("Dated pricing and model-limit evidence is required")
            try:
                date.fromisoformat(price["checked_at"])
            except ValueError:
                raise AdapterError("Invalid pricing date") from None
            pricing = Pricing(
                incoming,
                outgoing,
                integer(price["max_input_tokens"], "model input maximum"),
                price["checked_at"],
                price["evidence"],
            )
        modules[ident] = Module(
            ident,
            item["provider"],
            identifier(item["participant_id"], "participant id"),
            item["enabled"],
            item["model"],
            endpoint,
            tuple(channels),
            item["credential_env"],
            item["local_only_verified"],
            item["local_only_evidence"],
            checked_limits,
            pricing,
        )
    normalized = {**data, "workspace_config": str(workspace_path)}
    return Configuration(
        workspace_path,
        modules,
        integer(limits["max_concurrent"], "instance concurrency"),
        budget,
        integer(limits["pricing_max_age_days"], "pricing age limit"),
        digest(normalized),
    )


class BoardAPI(Protocol):
    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...


class BoardClient:
    """Reuse the canonical client. It resolves the changing board port each request."""

    def __init__(self, config_path: Path, participant: str) -> None:
        self.config_path, self.participant = config_path, participant

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            cli = import_module("cli")
        except ImportError:
            raise AdapterError(
                "Canonical Common Ground HTTP client is unavailable"
            ) from None
        value = cli.request(self.config_path, self.participant, method, path, body)
        if not isinstance(value, dict):
            raise AdapterError("Unexpected board response")
        return value


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise ProviderError("unknown", "provider_redirect_rejected")


class HTTPTransport:
    """Bounded stdlib HTTP; never used by the synthetic packet checks."""

    def post(
        self,
        endpoint: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        limits: Limits,
        cancel: Cancellation,
    ) -> dict[str, Any]:
        bounded = DeadlineCancellation(cancel, limits.timeout_seconds)
        bounded.check()
        raw = encoded(payload)
        if len(raw) > limits.max_context_bytes:
            raise ProviderError("failed", "provider_request_too_large")
        opener = build_opener(ProxyHandler({}), NoRedirect())
        try:
            with opener.open(
                Request(endpoint, data=raw, headers=headers, method="POST"),
                timeout=limits.timeout_seconds,
            ) as response:
                chunks: list[bytes] = []
                size = 0
                while True:
                    bounded.check()
                    chunk = response.read1(
                        min(READ_CHUNK_BYTES, limits.max_response_bytes - size + 1)
                    )
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limits.max_response_bytes:
                        raise ProviderError("unknown", "provider_response_too_large")
                    chunks.append(chunk)
                bounded.check()
        except (HTTPError, URLError, OSError, TimeoutError):
            raise ProviderError("unknown", "provider_transport_unconfirmed") from None
        try:
            value = json.loads(b"".join(chunks))
        except (ValueError, RecursionError):
            raise ProviderError("unknown", "invalid_provider_json") from None
        if not isinstance(value, dict):
            raise ProviderError("unknown", "invalid_provider_object")
        return value


class Journal:
    """Private provider journal, separate from the Common Ground Store/database.

    A single SQLite transaction couples admission, reservation and operation state.
    File locks identify operations still running after a crash, without trusting PIDs.
    """

    def __init__(self, state: Path) -> None:
        self.directory = state / "adapter_runs"
        if state.is_symlink() or self.directory.is_symlink():
            raise AdapterError("Adapter state must not be a symbolic link")
        state.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.directory.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.operations = self.directory / "operations"
        if self.operations.is_symlink():
            raise AdapterError(
                "Adapter operation directory must not be a symbolic link"
            )
        self.operations.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.operations, 0o700)
        self.path = self.directory / "journal.sqlite3"
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
        with self.connection() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value INTEGER NOT NULL);
                INSERT OR IGNORE INTO meta VALUES('stopping',0);
                CREATE TABLE IF NOT EXISTS jobs(
                run_id TEXT PRIMARY KEY,module_id TEXT NOT NULL,actor TEXT NOT NULL,channel_id TEXT NOT NULL,
                prepared TEXT NOT NULL,prepared_sha256 TEXT NOT NULL,status TEXT NOT NULL,result TEXT,
                quota_day TEXT,reserved_microusd INTEGER NOT NULL DEFAULT 0,charged_microusd INTEGER,
                publication_status TEXT NOT NULL DEFAULT 'unpublished',publication_body TEXT,message_id INTEGER,
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS active(run_id TEXT PRIMARY KEY,kind TEXT NOT NULL);""")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @contextmanager
    def operation(self, run_id: str, blocking: bool = False) -> Iterator[None]:
        if RUN_ID.fullmatch(run_id) is None:
            raise AdapterError("Invalid run id")
        descriptor = os.open(
            self.operations / run_id, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            try:
                fcntl.flock(
                    descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
                )
            except BlockingIOError:
                raise AdapterError("This operation is already active") from None
            yield
        finally:
            os.close(descriptor)

    def stopping(self) -> bool:
        with self.connection() as db:
            row = db.execute("SELECT value FROM meta WHERE key='stopping'").fetchone()
            return bool(row[0])

    def admit(self, db: sqlite3.Connection) -> None:
        if db.execute("SELECT value FROM meta WHERE key='stopping'").fetchone()[0]:
            raise AdapterError("Adapter jobs are stopping; explicit resume is required")

    def create(self, prepared: dict[str, Any]) -> dict[str, Any]:
        run_id, at = uuid4().hex, datetime.now(timezone.utc).isoformat()
        with self.connection() as db:
            self.admit(db)
            db.execute(
                "INSERT INTO jobs(run_id,module_id,actor,channel_id,prepared,prepared_sha256,status,created_at,updated_at) VALUES(?,?,?,?,?,?,'prepared',?,?)",
                (
                    run_id,
                    prepared["module_id"],
                    prepared["participant_id"],
                    prepared["channel_id"],
                    encoded(prepared).decode(),
                    digest(prepared),
                    at,
                    at,
                ),
            )
        return self.get(run_id, prepared["participant_id"], prepared["module_id"])

    def get(self, run_id: str, actor: str, module_id: str) -> dict[str, Any]:
        if not isinstance(run_id, str) or RUN_ID.fullmatch(run_id) is None:
            raise AdapterError("Invalid run id")
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE run_id=? AND actor=? AND module_id=?",
                (run_id, actor, module_id),
            ).fetchone()
            if row is None:
                raise AdapterError("Run unavailable to this module and participant")
            value = dict(row)
        value["prepared"] = json.loads(value["prepared"])
        value["result"] = json.loads(value["result"]) if value["result"] else None
        if digest(value["prepared"]) != value["prepared_sha256"]:
            raise AdapterError("Prepared journal fingerprint mismatch")
        return value

    def claim(
        self, run_id: str, module: Module, config: Configuration, reservation: int
    ) -> None:
        day = datetime.now(timezone.utc).date().isoformat()
        assert module.limits is not None
        with self.connection() as db:
            self.admit(db)
            row = db.execute(
                "SELECT status FROM jobs WHERE run_id=?", (run_id,)
            ).fetchone()
            if row is None or row[0] != "prepared":
                raise AdapterError(
                    "Generation is one-shot; create a new reviewed request"
                )
            count_all = db.execute("SELECT COUNT(*) FROM active").fetchone()[0]
            count_module = db.execute(
                "SELECT COUNT(*) FROM active a JOIN jobs j ON j.run_id=a.run_id WHERE j.module_id=?",
                (module.id,),
            ).fetchone()[0]
            if (
                count_all >= config.max_concurrent
                or count_module >= module.limits.max_concurrent
            ):
                raise AdapterError("Configured adapter concurrency is full")
            spent = db.execute(
                "SELECT COALESCE(SUM(COALESCE(charged_microusd,reserved_microusd)),0) FROM jobs WHERE quota_day=?",
                (day,),
            ).fetchone()[0]
            if reservation and (
                config.daily_budget_usd is None
                or spent + reservation > micros(config.daily_budget_usd)
            ):
                raise AdapterError("The daily adapter budget cannot admit this request")
            db.execute(
                "UPDATE jobs SET status='running',quota_day=?,reserved_microusd=?,updated_at=? WHERE run_id=?",
                (day, reservation, datetime.now(timezone.utc).isoformat(), run_id),
            )
            db.execute("INSERT INTO active VALUES(?, 'generate')", (run_id,))

    def finish(self, run_id: str, result: Result, charged: int | None) -> None:
        with self.connection() as db:
            db.execute(
                "UPDATE jobs SET status=?,result=?,charged_microusd=?,updated_at=? WHERE run_id=?",
                (
                    result.status,
                    encoded(asdict(result)).decode(),
                    charged,
                    datetime.now(timezone.utc).isoformat(),
                    run_id,
                ),
            )
            db.execute("DELETE FROM active WHERE run_id=?", (run_id,))

    def begin_stop(self) -> None:
        with self.connection() as db:
            db.execute("UPDATE meta SET value=1 WHERE key='stopping'")

    def recover_orphans(self) -> None:
        with self.connection() as db:
            rows = [dict(row) for row in db.execute("SELECT run_id,kind FROM active")]
        for row in rows:
            try:
                with self.operation(row["run_id"]), self.connection() as db:
                    current = db.execute(
                        "SELECT kind FROM active WHERE run_id=?", (row["run_id"],)
                    ).fetchone()
                    if current is None:
                        continue
                    if current[0] == "generate":
                        unknown = Result(
                            "unknown",
                            "",
                            None,
                            None,
                            warning="interrupted_provider_request",
                        )
                        db.execute(
                            "UPDATE jobs SET status='unknown',result=? WHERE run_id=?",
                            (encoded(asdict(unknown)).decode(), row["run_id"]),
                        )
                    else:
                        db.execute(
                            "UPDATE jobs SET publication_status='unconfirmed' WHERE run_id=?",
                            (row["run_id"],),
                        )
                    db.execute("DELETE FROM active WHERE run_id=?", (row["run_id"],))
            except AdapterError as error:
                if str(error) != "This operation is already active":
                    raise

    def drain(self, seconds: float) -> bool:
        if not 0 <= seconds < float("inf"):
            raise AdapterError("A finite drain deadline is required")
        deadline = time.monotonic() + seconds
        while True:
            self.recover_orphans()
            with self.connection() as db:
                active = db.execute("SELECT COUNT(*) FROM active").fetchone()[0]
            if not active:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(DRAIN_POLL_SECONDS, deadline - time.monotonic()))

    def resume(self) -> None:
        self.recover_orphans()
        with self.connection() as db:
            if db.execute("SELECT COUNT(*) FROM active").fetchone()[0]:
                raise AdapterError("Cannot resume while adapter work is active")
            db.execute("UPDATE meta SET value=0 WHERE key='stopping'")


class StopCancellation:
    def __init__(self, journal: Journal) -> None:
        self.journal = journal

    def check(self) -> None:
        if self.journal.stopping():
            raise ProviderError("unknown", "adapter_stop_requested")


def semantic(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: semantic(v)
            for k, v in value.items()
            if k not in EPHEMERAL_CONTEXT_FIELDS
        }
    if isinstance(value, list):
        return [semantic(v) for v in value]
    return value


class Runner:
    """UI-ready synchronous interface; orchestration owns no model authority."""

    def __init__(
        self,
        config: Configuration,
        journal: Journal,
        board: BoardAPI,
        transport: Transport | None = None,
        secret_reader: Callable[[str], str] | None = None,
    ) -> None:
        self.config, self.journal, self.board = config, journal, board
        self.transport = transport or HTTPTransport()
        self.secret_reader = secret_reader or (lambda name: os.environ.get(name, ""))
        journal.recover_orphans()

    def module(self, module_id: str) -> Module:
        if module_id not in self.config.modules:
            raise AdapterError("Unknown configured module")
        return self.config.modules[module_id]

    def activated(self, module: Module) -> int:
        if not module.enabled:
            raise AdapterError("This adapter module is disabled")
        if not module.allowed_channel_ids or module.limits is None:
            raise AdapterError(
                "Explicit channel export permission and limits are required"
            )
        if module.provider == "manual":
            return 0
        if not module.model or not module.endpoint:
            raise AdapterError(
                "Choose an explicit provider model and endpoint before activation"
            )
        if module.provider == "ollama":
            if not module.local_only_verified or not module.local_only_evidence:
                raise AdapterError(
                    "Local-only Ollama activation needs explicit operator evidence"
                )
            return 0
        price = module.pricing
        if (
            price is None
            or not module.credential_env
            or module.limits.max_run_usd is None
            or self.config.daily_budget_usd is None
        ):
            raise AdapterError(
                "Remote activation requires explicit credential reference, pricing and spend limits"
            )
        age = (
            datetime.now(timezone.utc).date() - date.fromisoformat(price.checked_at)
        ).days
        if not 0 <= age <= self.config.pricing_max_age_days:
            raise AdapterError("Pricing or model-limit evidence is outdated")
        reservation = price.cost(
            price.max_input_tokens, module.limits.max_output_tokens
        )
        if reservation > micros(module.limits.max_run_usd) or reservation > micros(
            self.config.daily_budget_usd
        ):
            raise AdapterError(
                "Configured spending limits cannot cover the pessimistic quote"
            )
        return reservation

    def authorize(self, module: Module, channel_id: str) -> None:
        if channel_id not in module.allowed_channel_ids:
            raise AdapterError("Channel has no permission for this provider module")
        try:
            state = self.board.request("GET", "/api/state")
        except (RuntimeError, OSError, ValueError):
            raise AdapterError(
                "The board could not confirm actor and channel permission"
            ) from None
        me, channels = state.get("me"), state.get("channels")
        if (
            not isinstance(me, dict)
            or me.get("id") != module.participant_id
            or me.get("kind") != "agent"
            or not isinstance(channels, list)
        ):
            raise AdapterError("A module must use its registered agent identity")
        target = [
            c for c in channels if isinstance(c, dict) and c.get("id") == channel_id
        ]
        if (
            len(target) != 1
            or not isinstance(target[0].get("members"), list)
            or module.participant_id not in target[0]["members"]
        ):
            raise AdapterError(
                "This agent has no current access to the selected channel"
            )

    def context(self, module: Module, channel_id: str) -> dict[str, Any]:
        self.authorize(module, channel_id)
        try:
            brief = self.board.request(
                "GET", "/api/brief?" + urlencode({"channel_id": channel_id})
            )
        except (RuntimeError, OSError, ValueError):
            raise AdapterError("The selected joining brief is unavailable") from None
        if (
            not isinstance(brief.get("me"), dict)
            or brief.get("scope") != {"channel_ids": [channel_id]}
            or brief.get("me", {}).get("id") != module.participant_id
        ):
            raise AdapterError("The board brief scope does not match this request")
        for key in BRIEF_COLLECTIONS:
            entries = brief.get(key, [])
            if not isinstance(entries, list):
                raise AdapterError("Invalid brief collection")
            for entry in entries:
                if (
                    not isinstance(entry, dict)
                    or entry.get("channel_id") != channel_id
                    or entry.get("channel_id") not in module.allowed_channel_ids
                ):
                    raise AdapterError(
                        "Brief evidence is outside the selected channel or has no export permission"
                    )
        source_ids = {
            entry["id"]
            for entry in brief.get("sources", [])
            if isinstance(entry.get("id"), str)
        }
        source_context = brief.get("source_context", [])
        scan = brief.get("source_scan", {})
        if (
            not isinstance(source_context, list)
            or not isinstance(scan, dict)
            or not isinstance(scan.get("issues", []), list)
        ):
            raise AdapterError("Invalid attributed source context")
        for entry in [*source_context, *scan.get("issues", [])]:
            if not isinstance(entry, dict) or entry.get("source_id") not in source_ids:
                raise AdapterError(
                    "Source summary or scan issue has no allowed citation"
                )
        # Export only the frozen brief contract. Unknown response fields never travel.
        allowed = BRIEF_COLLECTIONS | {
            "workspace",
            "me",
            "scope",
            "source_context",
            "source_scan",
            "limitations",
            "truncation",
        }
        return semantic({k: v for k, v in brief.items() if k in allowed})

    def record(
        self, module_id: str, run_id: str, expected_hash: str | None = None
    ) -> tuple[Module, dict[str, Any]]:
        module = self.module(module_id)
        record = self.journal.get(run_id, module.participant_id, module.id)
        self.authorize(module, record["channel_id"])
        if expected_hash is not None and (
            not isinstance(expected_hash, str)
            or not HASH.fullmatch(expected_hash)
            or expected_hash != record["prepared_sha256"]
        ):
            raise AdapterError(
                "Prepared request hash changed; review the original packet"
            )
        return module, record

    def prepare(self, module_id: str, channel_id: str, prompt: str) -> dict[str, Any]:
        module = self.module(module_id)
        reservation = self.activated(module)
        assert module.limits is not None
        if not isinstance(prompt, str) or not prompt.strip():
            raise AdapterError("An explicit nonempty request is required")
        context = self.context(module, channel_id)
        request_payload = PROVIDERS[module.provider].prepare(module, context, prompt)
        if len(encoded(request_payload)) > module.limits.max_context_bytes:
            raise AdapterError("The exact provider request exceeds its byte limit")
        prepared = {
            "module_id": module.id,
            "participant_id": module.participant_id,
            "channel_id": channel_id,
            "provider": module.provider,
            "model": module.model,
            "endpoint": module.endpoint,
            "configuration_sha256": self.config.fingerprint,
            "context_sha256": digest(context),
            "context": context,
            "prompt": prompt,
            "limits": plain(asdict(module.limits)),
            "reserved_quote_microusd": reservation,
            "request_payload": request_payload,
            "request_sha256": digest(request_payload),
            "cost_basis": "configured maximum accepted input and maximum output; unknown external account costs excluded",
        }
        if len(encoded(prepared)) > module.limits.max_context_bytes:
            raise AdapterError("The prepared context exceeds its configured byte limit")
        return self.journal.create(prepared)

    def status(self, module_id: str, run_id: str) -> dict[str, Any]:
        return self.record(module_id, run_id)[1]

    def recent(
        self,
        module_id: str,
        channel_ids: list[str],
        limit: int = MAX_RECENT_RUNS,
        before: str | None = None,
    ) -> dict[str, Any]:
        """Bounded summaries only, narrowed by caller scope before pagination."""
        module = self.module(module_id)
        if type(limit) is not int or not 1 <= limit <= MAX_RECENT_RUNS:
            raise AdapterError("Recent run limit is outside its bound")
        if not isinstance(channel_ids, list) or len(channel_ids) != len(
            set(channel_ids)
        ):
            raise AdapterError("Invalid recent run channel scope")
        for channel_id in channel_ids:
            self.authorize(module, channel_id)
        if not channel_ids:
            return {"runs": [], "next_before": None}
        cursor = None
        if before is not None:
            cursor = self.status(module_id, before)
            if cursor["channel_id"] not in channel_ids:
                raise AdapterError("Run cursor is outside the selected scope")
        with self.journal.connection() as db:
            query = (
                "SELECT run_id,module_id,actor,channel_id,prepared_sha256,status,publication_status,created_at,updated_at FROM jobs WHERE module_id=? AND actor=? AND channel_id IN ("
                + ",".join("?" for _ in channel_ids)
                + ")"
            )
            parameters: list[Any] = [module.id, module.participant_id, *channel_ids]
            if cursor:
                query += " AND (created_at<? OR (created_at=? AND run_id<?))"
                parameters.extend([cursor["created_at"], cursor["created_at"], before])
            query += " ORDER BY created_at DESC,run_id DESC LIMIT ?"
            parameters.append(limit + 1)
            rows = [dict(row) for row in db.execute(query, parameters)]
        more = len(rows) > limit
        rows = rows[:limit]
        return {"runs": rows, "next_before": rows[-1]["run_id"] if more else None}

    def run(self, module_id: str, run_id: str, expected_hash: str) -> dict[str, Any]:
        module, record = self.record(module_id, run_id, expected_hash)
        reservation = self.activated(module)
        if record["prepared"]["configuration_sha256"] != self.config.fingerprint:
            raise AdapterError(
                "Provider settings changed; prepare and review a new request"
            )
        current = self.context(module, record["channel_id"])
        if digest(current) != record["prepared"]["context_sha256"]:
            raise AdapterError(
                "Source or conversation context changed; prepare a new request"
            )
        current_payload = PROVIDERS[module.provider].prepare(
            module, current, record["prepared"]["prompt"]
        )
        if digest(current_payload) != record["prepared"]["request_sha256"]:
            raise AdapterError(
                "Provider translation changed; review a new prepared request"
            )
        secret = None
        if module.provider == "openai_responses":
            assert module.credential_env is not None
            try:
                secret = self.secret_reader(module.credential_env)
            except (RuntimeError, OSError, ValueError):
                raise AdapterError(
                    "The configured provider credential is unavailable"
                ) from None
            if (
                not isinstance(secret, str)
                or not secret.strip()
                or "\r" in secret
                or "\n" in secret
            ):
                raise AdapterError(
                    "The configured provider credential is missing or invalid"
                )
        assert module.limits is not None
        with self.journal.operation(run_id):
            self.journal.claim(run_id, module, self.config, reservation)
            cancel = DeadlineCancellation(
                StopCancellation(self.journal), module.limits.timeout_seconds
            )
            try:
                result = PROVIDERS[module.provider].generate(
                    module,
                    current,
                    record["prepared"]["prompt"],
                    self.transport,
                    cancel,
                    secret,
                )
                cancel.check()
                if secret and secret in encoded(asdict(result)).decode():
                    raise ProviderError("unknown", "provider_returned_credential")
                if len(encoded(asdict(result))) > module.limits.max_response_bytes or (
                    result.output_tokens is not None
                    and result.output_tokens > module.limits.max_output_tokens
                ):
                    raise ProviderError("unknown", "provider_result_exceeds_limit")
                charged: int | None = 0
                if module.provider == "openai_responses":
                    assert module.pricing is not None
                    if (
                        result.input_tokens is None
                        or result.output_tokens is None
                        or result.input_tokens > module.pricing.max_input_tokens
                    ):
                        raise ProviderError("unknown", "provider_usage_exceeds_quote")
                    charged = module.pricing.cost(
                        result.input_tokens, result.output_tokens
                    )
                    if charged > reservation:
                        raise ProviderError("unknown", "provider_cost_exceeds_quote")
            except ProviderError as error:
                result, charged = (
                    Result(
                        error.status,
                        "",
                        None,
                        None,
                        warning="provider_error_unconfirmed"
                        if secret and secret in str(error)
                        else str(error),
                    ),
                    None,
                )
            except (RuntimeError, OSError, ValueError, TypeError, RecursionError):
                result, charged = (
                    Result(
                        "unknown", "", None, None, warning="provider_result_unconfirmed"
                    ),
                    None,
                )
            self.journal.finish(run_id, result, charged)
        return self.status(module_id, run_id)

    def import_result(
        self, module_id: str, run_id: str, expected_hash: str, text: str
    ) -> dict[str, Any]:
        module, _record = self.record(module_id, run_id, expected_hash)
        self.activated(module)
        assert module.limits is not None
        if module.provider != "manual":
            raise AdapterError("Only a manual handoff accepts an explicit result")
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text.encode()) > module.limits.max_response_bytes
        ):
            raise AdapterError(
                "Manual result must be nonempty and within the configured byte limit"
            )
        result = asdict(
            Result("completed", text, None, None, warning="explicit_manual_result")
        )
        # A lost response must not strand an already committed import. Compare
        # inside the existing run lock and transaction; never overwrite a result.
        with self.journal.operation(run_id), self.journal.connection() as db:
            self.journal.admit(db)
            current = db.execute(
                "SELECT status,result,charged_microusd FROM jobs WHERE run_id=? AND actor=? AND module_id=?",
                (run_id, module.participant_id, module.id),
            ).fetchone()
            if current is None:
                raise AdapterError("Run unavailable to this module and participant")
            if current["status"] == "completed":
                if (
                    not current["result"]
                    or json.loads(current["result"]) != result
                    or current["charged_microusd"] != 0
                ):
                    raise AdapterError("Manual result differs from the committed result")
            elif current["status"] == "awaiting_manual":
                changed = db.execute(
                    "UPDATE jobs SET status='completed',result=?,charged_microusd=0,updated_at=? WHERE run_id=? AND status='awaiting_manual'",
                    (encoded(result).decode(), datetime.now(timezone.utc).isoformat(), run_id),
                ).rowcount
                if changed != 1:
                    raise AdapterError("Manual result state changed")
            else:
                raise AdapterError(
                    "Only an awaiting manual handoff accepts an explicit result"
                )
        return self.status(module_id, run_id)

    def publish(
        self, module_id: str, run_id: str, expected_hash: str
    ) -> dict[str, Any]:
        module, record = self.record(module_id, run_id, expected_hash)
        if (
            record["status"] != "completed"
            or not record["result"]
            or not record["result"]["text"].strip()
        ):
            raise AdapterError("Only a completed reviewed text result can be published")
        if record["publication_status"] == "published":
            return record
        body = record["publication_body"] or (
            "Provider draft — "
            + module.id
            + " ("
            + record["prepared"]["provider"]
            + ")\nRun: "
            + run_id
            + "\nContext SHA256: "
            + record["prepared"]["context_sha256"]
            + "\nEvidence is not approval.\n\n"
            + record["result"]["text"]
        )
        with self.journal.operation(run_id):
            with self.journal.connection() as db:
                self.journal.admit(db)
                db.execute(
                    "UPDATE jobs SET publication_status='publishing',publication_body=? WHERE run_id=?",
                    (body, run_id),
                )
                db.execute("INSERT INTO active VALUES(?, 'publish')", (run_id,))
            try:
                queued = self.board.request(
                    "POST",
                    "/api/personal/enqueue",
                    {
                        "channel_id": record["channel_id"],
                        "body": body,
                        "client_id": "adapter:" + run_id,
                    },
                )
                message = queued.get("delivered") or self.board.request(
                    "POST", "/api/personal/send", {"client_id": "adapter:" + run_id}
                )
                if (
                    not isinstance(message, dict)
                    or type(message.get("id")) is not int
                    or message["id"] <= 0
                    or message.get("author_id") != module.participant_id
                    or message.get("channel_id") != record["channel_id"]
                    or message.get("body") != body
                ):
                    raise AdapterError(
                        "Publication acknowledgement does not match this result"
                    )
                with self.journal.connection() as db:
                    db.execute(
                        "UPDATE jobs SET publication_status='published',message_id=? WHERE run_id=?",
                        (message["id"], run_id),
                    )
                    db.execute("DELETE FROM active WHERE run_id=?", (run_id,))
            except (RuntimeError, OSError, ValueError, TypeError):
                with self.journal.connection() as db:
                    db.execute(
                        "UPDATE jobs SET publication_status='unconfirmed' WHERE run_id=?",
                        (run_id,),
                    )
                    db.execute("DELETE FROM active WHERE run_id=?", (run_id,))
                raise AdapterError(
                    "Publication is unconfirmed; retry this result with its original run id"
                ) from None
        return self.status(module_id, run_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--providers", required=True, type=Path)
    parser.add_argument("--module", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--channel", required=True)
    prepare.add_argument("--prompt-from-stdin", required=True, action="store_true")
    for name in ("run", "status", "import-result", "publish"):
        command = sub.add_parser(name)
        command.add_argument("--run", required=True)
        if name != "status":
            command.add_argument("--prepared-sha256", required=True)
        if name == "import-result":
            command.add_argument("--from-stdin", required=True, action="store_true")
    args = parser.parse_args()
    config = load_provider_config(args.providers)
    if args.module not in config.modules:
        raise AdapterError("Unknown configured module")
    module = config.modules[args.module]
    # Disabled/unresolved examples fail before reading any workspace credential.
    if not module.enabled:
        raise AdapterError("This adapter module is disabled")
    try:
        workspace = import_module("store").load_config(config.workspace_config)
    except ImportError:
        raise AdapterError(
            "Canonical Common Ground configuration loader is unavailable"
        ) from None
    runner = Runner(
        config,
        Journal(workspace["state_dir"]),
        BoardClient(config.workspace_config, module.participant_id),
    )
    if args.command == "prepare":
        value = runner.prepare(
            args.module,
            args.channel,
            sys.stdin.read(module.limits.max_context_bytes + 1)
            if module.limits
            else "",
        )
    elif args.command == "status":
        value = runner.status(args.module, args.run)
    elif args.command == "import-result":
        value = runner.import_result(
            args.module,
            args.run,
            args.prepared_sha256,
            sys.stdin.read(module.limits.max_response_bytes + 1)
            if module.limits
            else "",
        )
    elif args.command == "run":
        value = runner.run(args.module, args.run, args.prepared_sha256)
    else:
        value = runner.publish(args.module, args.run, args.prepared_sha256)
    print(json.dumps(value, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AdapterError, OSError, ValueError) as error:
        print(
            "Adapter operation failed: "
            + (str(error) if isinstance(error, AdapterError) else type(error).__name__),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
