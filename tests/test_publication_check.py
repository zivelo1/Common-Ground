"""Publication-boundary controls use fictional files and a simulated read-only Git index."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from qa_support import temporary_directory

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/publication_check.py"
spec = importlib.util.spec_from_file_location("publication_check", SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError("Cannot load publication checker")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class PublicationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(temporary_directory(self, prefix="publication-boundary-").name)
        self.files = ["PUBLIC_FILES.txt", "PUBLIC_MANIFEST.json", "README.md"]
        (self.root / "README.md").write_text("Fictional museum project\n")
        (self.root / "PUBLIC_FILES.txt").write_text("\n".join(self.files) + "\n")
        records = {}
        for name in self.files:
            if name != "PUBLIC_MANIFEST.json":
                data = (self.root / name).read_bytes()
                records[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        (self.root / "PUBLIC_MANIFEST.json").write_text(json.dumps({"files": records}))
        self.patches = [
            patch.object(checker, "ROOT", self.root),
            patch.object(checker, "MANIFEST", self.root / "PUBLIC_MANIFEST.json"),
            patch.object(checker, "LIST", self.root / "PUBLIC_FILES.txt"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def run_check(self, *args):
        with patch.object(sys, "argv", ["publication_check.py", *args]), contextlib.redirect_stdout(io.StringIO()):
            checker.main()

    def test_exact_fictional_working_tree_passes(self):
        self.run_check()

    def test_modified_working_file_is_refused(self):
        (self.root / "README.md").write_text("Changed after review")
        with self.assertRaisesRegex(ValueError, "Changed file"):
            self.run_check()

    def test_upload_list_tampering_is_refused(self):
        (self.root / "PUBLIC_FILES.txt").write_text("README.md\n")
        with self.assertRaisesRegex(ValueError, "list and manifest differ"):
            self.run_check()

    def test_case_aliases_and_local_config_names_are_refused_before_reading(self):
        for name in ("CONFIG.JSON", "Providers.LOCAL.JSON", ".ENV.private", "STATE/data.json", "../private.txt", "journal.db-wal", "journal.sqlite3-shm", "journal.db-journal", ".Ruff_Cache/data.json", "tests/source_fixtures/private.json"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                checker.validate_path(name)

    def test_symlinked_public_file_is_refused(self):
        (self.root / "README.md").unlink()
        (self.root / "outside.txt").write_text("Not selected")
        (self.root / "README.md").symlink_to(self.root / "outside.txt")
        with self.assertRaisesRegex(ValueError, "Symlink refused"):
            self.run_check()

    def fake_index(self, *, changed=False, missing=False, private=False, mode="100644", stage="0"):
        (self.root / ".git").mkdir()
        names = self.files[:-1] if missing else self.files
        if private:
            names = [*names, "config.json"]
        blobs = {str(index): (self.root / name).read_bytes() for index, name in enumerate(names) if name in self.files}
        if changed:
            blobs["2"] = b"Older staged README"
        entries = b"".join(f"{mode} {index} {stage}\t{name}\0".encode() for index, name in enumerate(names))

        def read_only_git(command, **kwargs):
            if command[:3] == ["git", "ls-files", "--stage"]:
                return subprocess.CompletedProcess(command, 0, entries)
            if command[:3] == ["git", "cat-file", "blob"]:
                return subprocess.CompletedProcess(command, 0, blobs[command[3]])
            raise AssertionError(f"Unexpected command: {command}")

        return patch.object(checker.subprocess, "run", side_effect=read_only_git)

    def test_identical_complete_staged_package_passes(self):
        with self.fake_index():
            self.run_check("--staged")

    def test_older_staged_bytes_are_refused(self):
        with self.fake_index(changed=True), self.assertRaisesRegex(ValueError, "Staged bytes differ"):
            self.run_check("--staged")

    def test_missing_staged_file_is_refused(self):
        with self.fake_index(missing=True), self.assertRaisesRegex(ValueError, "incomplete"):
            self.run_check("--staged")

    def test_private_index_path_is_refused_without_reading_its_blob(self):
        with self.fake_index(private=True), self.assertRaisesRegex(ValueError, "outside"):
            self.run_check("--staged")

    def test_symlink_index_mode_is_refused(self):
        with self.fake_index(mode="120000"), self.assertRaisesRegex(ValueError, "unsupported"):
            self.run_check("--staged")


if __name__ == "__main__":
    unittest.main()
