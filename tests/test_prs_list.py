"""``prs list``: Qodo's read-only ``litegit-list-prs`` tool, exposed for the Findings screen.

No reply of this tool was ever recorded: these tests pin the exposure (read-only, viewer, the
recorded input schema) and the call, not a reply shape.
"""

from __future__ import annotations

import json

import pytest

from qodo import api
from qodo import exposure as exposure_mod
from qodo import gateway as gw
from qodo import registry as reg
from qodo.cli import main
from qodo.cli._errors import CliError
from tests.fake_qodo_mcp import FakeUpstream, schemas

KEY = "sk-test-SECRET-key-123"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("QODO_API_KEY", KEY)


@pytest.fixture
def up(monkeypatch):
    fake = FakeUpstream()
    monkeypatch.setattr(api, "_gateway", lambda: gw.Gateway(api_key=KEY, opener=fake))
    return fake


def test_the_policy_exposes_litegit_list_prs_read_only_as_prs_list():
    entry = exposure_mod.load_policy().get("litegit-list-prs")
    assert entry is not None
    assert entry.verb == "prs list" and entry.allow_write is False
    assert entry.surfaces == ("cli", "api", "mcp")
    assert exposure_mod.is_read_only(schemas()["litegit-list-prs"]["annotations"])


def test_the_verb_params_are_the_recorded_input_schema():
    verb = reg.default_registry().get("prs", "list")
    want = schemas()["litegit-list-prs"]["inputSchema"]
    assert verb.kind == "upstream" and verb.tool == "litegit-list-prs"
    assert verb.writes is False
    assert verb.params["properties"] == want["properties"]
    assert verb.params["required"] == ["repo"]


def test_the_packaged_snapshot_holds_the_tool():
    snapshot = reg.load_schema_snapshot()
    assert (
        snapshot["litegit-list-prs"]["inputSchema"] == schemas()["litegit-list-prs"]["inputSchema"]
    )


def test_the_cli_calls_the_tool_with_the_given_filters(up, capsys):
    rc = main(["prs", "list", "--repo", "o/r", "--state", "open", "--max-results", "50", "--json"])
    assert rc == 0
    json.loads(capsys.readouterr().out)
    assert up.calls == [("litegit-list-prs", {"repo": "o/r", "state": "open", "max_results": 50})]


def test_a_bad_state_is_refused_before_any_call(up):
    with pytest.raises(CliError):
        reg.default_registry().get("prs", "list").handler({"repo": "o/r", "state": "draft"})
    assert up.calls == []
