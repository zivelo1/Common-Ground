"""Verify the exact public file manifest before staging or sharing a release."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "PUBLIC_MANIFEST.json"
LIST = ROOT / "PUBLIC_FILES.txt"
DENIED_PARTS = {".git", "state", "__pycache__", ".venv", "venv", "node_modules", ".ruff_cache", ".mypy_cache", ".pytest_cache", "source_fixtures"}
DENIED_NAMES = {"config.json", "config.local.json", "providers.json", "adapter-human-policy.json"}
DENIED_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".log", ".pem", ".key", ".bak"}
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{40,}"),
    re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{30,}"),
)
TEXT_SUFFIXES = {".py", ".js", ".css", ".html", ".json", ".md", ".txt", ".command", ".svg"}


def validate_path(name: str) -> Path:
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or any(part.casefold() in DENIED_PARTS for part in relative.parts):
        raise ValueError(f"Excluded path: {name}")
    if relative.name.casefold() in DENIED_NAMES or relative.name.casefold().endswith((".local.json", "-wal", "-shm", "-journal")) or relative.name.casefold().startswith(".env") or relative.suffix.casefold() in DENIED_SUFFIXES:
        raise ValueError(f"Private file: {name}")
    current = ROOT
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"Symlink refused: {name}")
    if not current.is_file():
        raise ValueError(f"Missing file: {name}")
    return current


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="Require the complete audited package in the Git index, with identical bytes")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    records = manifest["files"]
    listed = LIST.read_text(encoding="utf-8").splitlines()
    expected = sorted([*records, MANIFEST.name])
    if listed != expected or len(listed) != len(set(listed)):
        raise ValueError("The upload list and manifest differ")
    for name, record in records.items():
        path = validate_path(name)
        data = path.read_bytes()
        if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError(f"Changed file: {name}; regenerate and review the manifest")
        if path.suffix in TEXT_SUFFIXES:
            if any(pattern.search(data) for pattern in SECRET_PATTERNS):
                raise ValueError(f"Possible credential in {name}; inspect privately")
            data.decode("utf-8")
    # The manifest cannot hash itself. It is explicitly included in the list.
    validate_path(MANIFEST.name)
    if (ROOT / ".git").exists():
        result = subprocess.run(["git", "ls-files", "--stage", "-z"], cwd=ROOT, check=True, capture_output=True)
        entries = {}
        for value in result.stdout.split(b"\0"):
            if not value:
                continue
            metadata, raw_name = value.split(b"\t", 1)
            mode, blob, stage = metadata.decode("ascii").split()
            name = raw_name.decode("utf-8")
            if mode not in {"100644", "100755"} or stage != "0" or name in entries:
                raise ValueError("Symlink, conflict, or unsupported Git index entry")
            entries[name] = blob
        if set(entries) - set(listed):
            raise ValueError("Tracked files exist outside the approved upload list")
        if args.staged:
            if set(entries) != set(listed):
                raise ValueError("The staged package is incomplete")
            for name, blob in entries.items():
                content = subprocess.run(["git", "cat-file", "blob", blob], cwd=ROOT, check=True, capture_output=True).stdout
                if content != validate_path(name).read_bytes():
                    raise ValueError(f"Staged bytes differ from the audited file: {name}")
    elif args.staged:
        raise ValueError("No Git index exists for staged verification")
    print(f"Verified {len(listed)} public files. Pattern checks do not guarantee that every secret is absent.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        print(f"Publication check failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
