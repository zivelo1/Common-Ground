"""Confine disposable app-test state and public source fixtures to this workspace."""
from __future__ import annotations

import os
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

WORKSPACE = Path(__file__).resolve().parents[1]
STATE_QA = WORKSPACE / "state/qa"
PUBLIC_SOURCES = WORKSPACE / "tests/source_fixtures"


def temporary_directory(owner: unittest.TestCase | None = None, *, prefix: str = "common-ground-", public_sources: bool = False) -> tempfile.TemporaryDirectory[str]:
    # Public source fixtures cannot have a state ancestor: the real privacy guard
    # blocks it. They contain text only, while instances stay in ignored state/qa.
    root = PUBLIC_SOURCES if public_sources else STATE_QA / "disposable_tests"
    root.mkdir(parents=True, exist_ok=True)
    result = tempfile.TemporaryDirectory(prefix=prefix, dir=root)
    if owner is not None:
        owner.addCleanup(result.cleanup)
    return result


def evidence_root() -> Path:
    root = STATE_QA / "test_receipts"
    root.mkdir(parents=True, exist_ok=True)
    return root


@contextmanager
def confined_playwright() -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with temporary_directory(prefix="browser-profile-") as folder, patch.dict(os.environ, {"TMPDIR": folder, "PYTHONDONTWRITEBYTECODE": "1"}), sync_playwright() as driver:
        yield driver
