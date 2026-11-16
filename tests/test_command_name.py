"""The installed command is ``qodo-cli``; the import package stays ``qodo``."""

from __future__ import annotations

import importlib
import json
import re
import tomllib
from pathlib import Path

import pytest

from qodo.cli import main
from qodo.explain.catalog import ENTRIES

_ROOT = Path(__file__).resolve().parent.parent
# `qodo <verb>` / `uv run qodo` as a command invocation (not `qodo/cli` paths).
_OLD_INVOCATION = re.compile(
    r"(?<![\w./-])qodo (rules|review|pr|config|whoami|learn|explain|overview|doctor|cli|--)"
    r"|uv run qodo\b(?!-)"
)


def test_entry_point_is_qodo_cli_only() -> None:
    data = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]
    assert scripts == {"qodo-cli": "qodo.cli:main"}


def test_import_package_name_unchanged() -> None:
    assert importlib.import_module("qodo").__name__ == "qodo"


def test_help_usage_names_qodo_cli(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage: qodo-cli" in capsys.readouterr().out


def test_learn_names_qodo_cli(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["learn", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["explain_pointer"] == "qodo-cli explain <path>"
    assert not _OLD_INVOCATION.search(json.dumps(payload))


def test_learn_text_has_no_old_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["learn"]) == 0
    out = capsys.readouterr().out
    assert "qodo-cli rules get" in out
    assert not _OLD_INVOCATION.search(out)


def test_overview_has_no_old_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["overview", "--json"]) == 0
    assert not _OLD_INVOCATION.search(capsys.readouterr().out)


def test_explain_catalog_has_no_old_command() -> None:
    for path, body in ENTRIES.items():
        assert not _OLD_INVOCATION.search(body), path
    assert () in ENTRIES and ("qodo-cli",) in ENTRIES
    assert ("qodo",) not in ENTRIES


@pytest.mark.parametrize("name", ["README.md", "CLAUDE.md", "AGENTS.colleague.md"])
def test_docs_name_qodo_cli(name: str) -> None:
    text = (_ROOT / name).read_text(encoding="utf-8")
    assert not _OLD_INVOCATION.search(text), name
