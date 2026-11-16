"""Shared fixtures: no test may write the real change log."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_change_log(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    path: Path = tmp_path_factory.mktemp("changelog") / "changes.jsonl"
    monkeypatch.setenv("QODO_CHANGE_LOG", str(path))
    yield
