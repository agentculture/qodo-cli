"""``qodo-cli serve`` and ``qodo-cli tokens``: parser, errors and the self-description."""

from __future__ import annotations

import json
import stat
import sys

import pytest

from qodo import explain
from qodo.cli import main


def run(capsys, *argv):
    try:
        code = main(list(argv))
    except SystemExit as stop:  # an argparse refusal
        code = stop.code
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def tokens_path(tmp_path, monkeypatch):
    path = tmp_path / "state" / "tokens.json"
    monkeypatch.setenv("QODO_SERVER_TOKENS", str(path))
    return path


def test_serve_without_the_extra_is_a_clear_environment_error(capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "fastapi", None)
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    code, out, err = run(capsys, "serve", "--json")
    assert code == 2 and out == ""
    payload = json.loads(err)
    assert "qodo-cli[server]" in payload["message"] + payload["remediation"]
    assert "Traceback" not in err


def test_serve_refuses_a_non_loopback_host_as_a_user_error(capsys):
    code, out, err = run(capsys, "serve", "--host", "0.0.0.0", "--json")
    assert code == 1
    assert "loopback" in json.loads(err)["message"]


def test_serve_refuses_a_partial_access_configuration(capsys, monkeypatch):
    monkeypatch.setenv("QODO_ACCESS_AUD", "x")
    code, _, err = run(capsys, "serve", "--json")
    assert code == 1 and "QODO_ACCESS" in json.loads(err)["message"]


def test_serve_flags_exist(capsys):
    with pytest.raises(SystemExit) as stop:
        main(["serve", "--help"])
    assert stop.value.code == 0
    text = capsys.readouterr().out
    for flag in ("--host", "--port", "--lan-host", "--lan-port", "--tokens", "--json"):
        assert flag in text


# --- tokens ------------------------------------------------------------------------------------


def test_issue_prints_the_token_once_and_list_never_shows_it(capsys, tokens_path):
    code, out, err = run(capsys, "tokens", "issue", "--name", "rules-engine", "--role", "editor",
                         "--json")  # fmt: skip
    assert code == 0 and err == ""
    issued = json.loads(out)
    assert issued["token"].startswith("qcl_") and issued["roles"] == ["editor"]
    assert issued["identity"] == "rules-engine"
    on_disk = tokens_path.read_text(encoding="utf-8")
    assert issued["token"] not in on_disk and issued["token"].split(".", 1)[1] not in on_disk
    assert stat.S_IMODE(tokens_path.stat().st_mode) == 0o600

    code, out, _ = run(capsys, "tokens", "list", "--json")
    assert code == 0
    listed = json.loads(out)["tokens"]
    assert [t["id"] for t in listed] == [issued["id"]]
    assert "token" not in listed[0] and "hash" not in listed[0]
    assert issued["token"] not in out


def test_issue_text_mode_says_the_token_is_shown_once(capsys, tokens_path):
    code, out, err = run(capsys, "tokens", "issue", "--name", "n", "--role", "viewer")
    assert code == 0
    assert "qcl_" in out and "once" in (out + err).lower()


def test_the_role_is_required_and_checked(capsys, tokens_path):
    code, _, err = run(capsys, "tokens", "issue", "--name", "n", "--role", "root", "--json")
    assert code == 1 and "role" in json.loads(err)["message"]
    code, _, _ = run(capsys, "tokens", "issue", "--name", "n", "--json")
    assert code == 1
    code, _, _ = run(capsys, "tokens", "issue", "--role", "viewer", "--json")
    assert code == 1
    code, _, err = run(capsys, "tokens", "issue", "--name", "  ", "--role", "viewer", "--json")
    assert code == 1


def test_revoke_works_once_and_a_second_time_is_a_user_error(capsys, tokens_path):
    _, out, _ = run(capsys, "tokens", "issue", "--name", "n", "--role", "viewer", "--json")
    token_id = json.loads(out)["id"]
    code, out, _ = run(capsys, "tokens", "revoke", token_id, "--json")
    assert code == 0 and json.loads(out)["revoked_at"]
    code, _, err = run(capsys, "tokens", "revoke", token_id, "--json")
    assert code == 1 and "revoked" in json.loads(err)["message"]
    code, _, err = run(capsys, "tokens", "revoke", "nope", "--json")
    assert code == 1 and "does not exist" in json.loads(err)["message"]


def test_a_token_path_flag_beats_the_environment(capsys, tokens_path, tmp_path):
    other = tmp_path / "other.json"
    run(capsys, "tokens", "issue", "--name", "n", "--role", "viewer", "--tokens", str(other))
    assert other.exists() and not tokens_path.exists()


def test_the_default_path_is_under_xdg_state(capsys, tmp_path, monkeypatch):
    monkeypatch.delenv("QODO_SERVER_TOKENS", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    code, _, _ = run(capsys, "tokens", "issue", "--name", "n", "--role", "viewer", "--json")
    assert code == 0 and (tmp_path / "xdg" / "qodo-cli" / "service_tokens.json").is_file()


def test_a_corrupt_token_file_is_an_environment_error(capsys, tokens_path):
    tokens_path.parent.mkdir(parents=True)
    tokens_path.write_text("{oops", encoding="utf-8")
    code, _, err = run(capsys, "tokens", "list", "--json")
    assert code == 2 and "Traceback" not in err


def test_tokens_overview_exists(capsys):
    code, out, _ = run(capsys, "tokens", "overview", "--json")
    assert code == 0 and "tokens" in out


# --- self-description ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [("serve",), ("tokens",), ("tokens", "issue"), ("tokens", "list"), ("tokens", "revoke"),
     ("tokens", "overview")],
)  # fmt: skip
def test_explain_covers_the_new_commands(path):
    text = explain.resolve(path)
    assert text.strip()


def test_learn_and_overview_name_serve_and_tokens(capsys):
    _, out, _ = run(capsys, "learn")
    for needle in ("serve", "tokens issue", "tokens list", "tokens revoke"):
        assert needle in out, needle
    _, out, _ = run(capsys, "overview", "--json")
    assert "serve" in out and "tokens" in out
    _, out, _ = run(capsys, "learn", "--json")
    paths = [c["path"] for c in json.loads(out)["commands"]]
    assert ["serve"] in paths and ["tokens", "issue"] in paths
