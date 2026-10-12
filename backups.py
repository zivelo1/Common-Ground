"""Consistent, private, verifiable SQLite backups of one Common Ground instance.

A backup is the instance database copied with SQLite's online backup API: one consistent snapshot, taken while the
app keeps writing. The copy passes PRAGMA integrity_check before it is published, and its manifest (sha256, size and
check result) is written last as the commit mark: a database file without its manifest is not a backup. Nothing here
deletes a file. Restore never overwrites anything: it writes a verified copy to a NEW path, which the human puts in
place while the app is stopped.

Command line (the app does not need to run):
    python -B backups.py verify <backup file>
    python -B backups.py restore <backup file> <new database path>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

# The default interval can be changed by a human in the app.
DEFAULT_INTERVAL_DAYS = 3
MIN_INTERVAL_DAYS = 1
# Input bound only: a year is the longest interval the settings form accepts.
MAX_INTERVAL_DAYS = 365
# How often the server asks whether a backup is due.
POLL_SECONDS = 60
# After a failed scheduled attempt, wait this long before the next one, so a full or failing disk does not get a
# new partial file every minute (partial files are kept, never deleted).
ERROR_RETRY_SECONDS = 60 * 60
DIRECTORY_NAME = "backups"
DATABASE_SUFFIX = ".sqlite3"
MANIFEST_SUFFIX = ".json"
PARTIAL_SUFFIX = ".partial"
NAME = re.compile(r"collaboration-\d{8}T\d{12}Z\.sqlite3\Z")
MANIFEST_SCHEMA = 1
HASH_BLOCK_BYTES = 1024 * 1024
SQLITE_BUSY_SECONDS = 10


class BackupError(Exception):
    """A backup could not be made, verified or restored. The message names the reason, never private content."""


def directory(state_dir: Path) -> Path:
    """The instance's private backup folder (0700), created on first use. A symbolic link is refused."""
    path = Path(state_dir) / DIRECTORY_NAME
    if path.is_symlink():
        raise BackupError("The backup folder must not be a symbolic link")
    path.mkdir(mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def due_at(newest: datetime | None, interval_days: int) -> datetime | None:
    """When the next scheduled backup is due; None means now (no complete backup exists)."""
    return None if newest is None else newest + timedelta(days=interval_days)


def retry_at(failed: datetime) -> datetime:
    return failed + timedelta(seconds=ERROR_RETRY_SECONDS)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(HASH_BLOCK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def _create_private(path: Path) -> None:
    # O_EXCL: never reuse or overwrite an existing path.
    os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))


def _fsync(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_only(path: Path) -> sqlite3.Connection:
    # immutable=1: a finished backup is never written, and SQLite must not create -wal/-shm files beside it.
    return sqlite3.connect(f"file:{quote(str(path))}?mode=ro&immutable=1", uri=True)


def _integrity(connection: sqlite3.Connection) -> str:
    rows = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    return "ok" if rows == ["ok"] else "; ".join(str(row) for row in rows[:5])


def _copy(source: sqlite3.Connection, target_path: Path) -> str:
    """Copy one consistent snapshot of `source` into the new private file `target_path`; return its integrity check."""
    target = sqlite3.connect(target_path)
    try:
        # pages=-1 copies the whole database in one step, inside one read snapshot of the source.
        source.backup(target)
        # The live database uses WAL. A backup is one self-contained file, so it is switched to rollback mode.
        target.execute("PRAGMA journal_mode=DELETE")
        return _integrity(target)
    finally:
        target.close()


def _manifest(path: Path) -> dict[str, Any] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    if (type(record.get("schema")) is not int or record["schema"] != MANIFEST_SCHEMA
            or not isinstance(record.get("name"), str) or not NAME.fullmatch(record["name"])
            or type(record.get("bytes")) is not int or record["bytes"] < 0
            or not isinstance(record.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
            or record.get("integrity_check") != "ok" or not isinstance(record.get("created_at"), str)):
        return None
    try:
        created = datetime.fromisoformat(record["created_at"])
    except ValueError:
        return None
    return record if created.tzinfo is not None else None


def create(database: Path, folder: Path, moment: datetime) -> dict[str, Any]:
    """Back up `database` into `folder` and return the manifest. Raises BackupError or OSError/sqlite3.Error.

    On any failure only `<name>.partial` files remain; they are reported as incomplete and never deleted.
    """
    if Path(database).is_symlink():
        raise BackupError("The instance database must not be a symbolic link")
    if not Path(database).is_file():
        raise BackupError("The instance database does not exist")
    name = f"collaboration-{_stamp(moment)}{DATABASE_SUFFIX}"
    final = folder / name
    partial = folder / f"{name}{PARTIAL_SUFFIX}"
    manifest_path = folder / f"{name}{MANIFEST_SUFFIX}"
    manifest_partial = folder / f"{name}{MANIFEST_SUFFIX}{PARTIAL_SUFFIX}"
    if any(path.exists() or path.is_symlink() for path in (final, partial, manifest_path, manifest_partial)):
        raise BackupError("A backup with this time stamp already exists")
    _create_private(partial)
    source = sqlite3.connect(database, timeout=SQLITE_BUSY_SECONDS)
    try:
        check = _copy(source, partial)
    finally:
        source.close()
    if check != "ok":
        raise BackupError(f"The copy failed its integrity check: {check}")
    _fsync(partial)
    os.rename(partial, final)
    _fsync(folder)
    manifest = {"schema": MANIFEST_SCHEMA, "name": name, "created_at": moment.astimezone(timezone.utc).isoformat(),
                "bytes": final.stat().st_size, "sha256": _sha256(final), "integrity_check": check,
                "sqlite_version": sqlite3.sqlite_version}
    _create_private(manifest_partial)
    with manifest_partial.open("w", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=1)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(manifest_partial, manifest_path)
    _fsync(folder)
    return manifest


def inventory(folder: Path) -> dict[str, list[dict[str, Any]]]:
    """What is on disk now. Complete: database plus a manifest naming it with the same size. No hashing here; the
    full hash and integrity check run at creation, `verify` and `restore`."""
    complete: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    for path in sorted(folder.iterdir()):
        name = path.name
        if path.is_symlink():
            incomplete.append({"name": name, "reason": "symbolic link, ignored"})
        elif NAME.fullmatch(name):
            record = _manifest(folder / f"{name}{MANIFEST_SUFFIX}")
            if record is None:
                incomplete.append({"name": name, "reason": "no readable manifest"})
            elif record.get("name") != name or record.get("bytes") != path.stat().st_size:
                incomplete.append({"name": name, "reason": "size or name differs from its manifest"})
            else:
                complete.append({key: record[key] for key in ("name", "created_at", "bytes", "sha256")})
        elif name.endswith(PARTIAL_SUFFIX):
            incomplete.append({"name": name, "reason": "interrupted before completion"})
        elif name.endswith(MANIFEST_SUFFIX) and NAME.fullmatch(name.removesuffix(MANIFEST_SUFFIX)):
            if not (folder / name.removesuffix(MANIFEST_SUFFIX)).exists():
                incomplete.append({"name": name, "reason": "manifest without its database file"})
        else:
            incomplete.append({"name": name, "reason": "not a backup file"})
    complete.sort(key=lambda item: item["created_at"], reverse=True)
    return {"complete": complete, "incomplete": incomplete}


def verify(path: Path) -> dict[str, Any]:
    """Return the manifest of a complete, unchanged, internally consistent backup; raise BackupError otherwise."""
    path = Path(path)
    if not NAME.fullmatch(path.name) or path.is_symlink() or not path.is_file():
        raise BackupError("Not a backup file")
    manifest = _manifest(path.with_name(f"{path.name}{MANIFEST_SUFFIX}"))
    if manifest is None:
        raise BackupError("The backup has no readable manifest, so it is incomplete")
    if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("name") != path.name:
        raise BackupError("The manifest does not describe this backup")
    if path.stat().st_size != manifest.get("bytes") or _sha256(path) != manifest.get("sha256"):
        raise BackupError("The backup file changed after it was made")
    connection = _read_only(path)
    try:
        check = _integrity(connection)
    except sqlite3.DatabaseError as error:
        raise BackupError("The backup is not a readable database") from error
    finally:
        connection.close()
    if check != "ok":
        raise BackupError(f"The backup failed its integrity check: {check}")
    return manifest


def restore(backup: Path, target: Path) -> dict[str, Any]:
    """Write a verified copy of `backup` to the NEW path `target` (mode 0600). Existing paths are refused."""
    manifest = verify(backup)
    target = Path(target)
    for path in (target, *(Path(f"{target}{suffix}") for suffix in ("-wal", "-shm", "-journal"))):
        if path.exists() or path.is_symlink():
            raise BackupError("Restore writes only to a new path; the target or its SQLite side file already exists")
    _create_private(target)
    source = _read_only(Path(backup))
    try:
        check = _copy(source, target)
    finally:
        source.close()
    if check != "ok":
        raise BackupError(f"The restored copy failed its integrity check: {check}")
    _fsync(target)
    return {"restored_from": manifest["name"], "sha256": manifest["sha256"], "target": str(target),
            "integrity_check": check}


class Scheduler:
    """Calls `tick` once at start and then every `poll_seconds` until `stop`."""

    def __init__(self, tick: Callable[[], object], poll_seconds: float = POLL_SECONDS):
        self._tick = tick
        self._poll_seconds = poll_seconds
        self._stopping = threading.Event()
        # The server's close stops and joins this thread, so a normal stop never cuts a copy short. As a daemon it
        # cannot hold the process open if the server dies without closing; a cut copy stays an incomplete .partial.
        self._thread = threading.Thread(target=self._run, name="common-ground-backups", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                self._tick()
            except Exception as error:  # noqa: BLE001 -- the tick records backup errors itself; this keeps
                # scheduling alive if recording fails too, and says so without private values.
                print(f"Scheduled backup check failed: {type(error).__name__}", file=sys.stderr, flush=True)
            self._stopping.wait(self._poll_seconds)

    def stop(self) -> None:
        self._stopping.set()
        if self._thread.is_alive():
            self._thread.join()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify or restore a Common Ground backup.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("verify", help="check a backup against its manifest and SQLite's integrity check").add_argument("backup", type=Path)
    restore_parser = commands.add_parser("restore", help="write a verified copy to a new database path")
    restore_parser.add_argument("backup", type=Path)
    restore_parser.add_argument("target", type=Path)
    args = parser.parse_args(argv)
    try:
        result = verify(args.backup) if args.command == "verify" else restore(args.backup, args.target)
    except (BackupError, OSError, sqlite3.Error) as error:
        reason = str(error) if isinstance(error, BackupError) else type(error).__name__
        print(f"Refused: {reason}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
