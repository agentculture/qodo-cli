"""Tests for the introspection verbs: overview, cli overview, doctor."""

from __future__ import annotations

import json

import pytest

from qodo.cli import main
from qodo.cli._commands import doctor as _doctor

# --- overview -------------------------------------------------------------


def test_overview_text(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["overview"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "# qodo-cli" in out
    assert "Identity" in out


def test_overview_json_shape(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["overview", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["subject"] == "qodo-cli"
    assert isinstance(payload["sections"], list)
    assert payload["sections"]


def test_overview_graceful_on_bad_path(capsys: pytest.CaptureFixture[str]) -> None:
    # Rubric contract: descriptive verbs never hard-fail on a missing target.
    rc = main(["overview", "/no/such/path/here"])
    assert rc == 0
    assert capsys.readouterr().out.strip()


# --- cli overview ---------------------------------------------------------


def test_cli_overview_text(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["cli", "overview"])
    assert rc == 0
    assert "# qodo-cli cli" in capsys.readouterr().out


def test_cli_overview_json_shape(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["cli", "overview", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["subject"] == "qodo-cli cli"
    assert isinstance(payload["sections"], list)


def test_cli_noun_bare_is_non_empty(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["cli"])
    assert rc == 0
    assert capsys.readouterr().out.strip()


def test_cli_overview_unknown_flag_structured_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # `cli overview` parse errors must route through the structured error
    # contract (error:/hint: + exit 1), not argparse's default stderr/exit 2.
    with pytest.raises(SystemExit) as exc:
        main(["cli", "overview", "--bogus"])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "hint:" in err


# --- doctor ---------------------------------------------------------------


def test_doctor_text(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["doctor"])
    assert rc in (0, 1)
    assert "qodo-cli doctor" in capsys.readouterr().out


def test_doctor_json_shape(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["doctor", "--json"])
    assert rc in (0, 1)
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload["healthy"], bool)
    assert isinstance(payload["checks"], list)
    assert payload["checks"]
    for check in payload["checks"]:
        assert {"id", "passed", "severity", "message", "remediation"} <= set(check)


def test_doctor_recognizes_declared_backend(capsys: pytest.CaptureFixture[str]) -> None:
    """The repo's own declared backend must be a known one — doctor stays healthy.

    Guards the backend-consistency invariant: a promotion that changes
    ``culture.yaml``'s backend without teaching ``doctor`` the matching prompt
    file would otherwise slip through (the shape tests above tolerate rc==1).
    """
    rc = main(["doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    messages = " ".join(str(c["message"]) for c in payload["checks"])
    assert "unknown backend" not in messages
    assert rc == 0
    assert payload["healthy"] is True


# --- doctor: Qodo-setup detection -----------------------------------------


def test_doctor_reports_qodo_setup_checks(capsys: pytest.CaptureFixture[str]) -> None:
    main(["doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    ids = {c["id"] for c in payload["checks"]}
    assert {
        "pr_agent_config_present",
        "best_practices_present",
        "qodo_client_config_present",
    } <= ids


def test_qodo_setup_checks_flag_missing(tmp_path) -> None:
    # Empty repo root + home with no ~/.qodo/config.json and no QODO_API_KEY.
    home = tmp_path / "home"
    home.mkdir()
    import os
    from unittest import mock

    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("QODO_API_KEY", None)
        checks = {c["id"]: c for c in _doctor._qodo_setup_checks(tmp_path, home)}
    assert checks["pr_agent_config_present"]["passed"] is False
    assert checks["best_practices_present"]["passed"] is False
    assert checks["qodo_client_config_present"]["passed"] is False
    # Advisory only — none is error-severity, and each guides setup.
    for c in checks.values():
        assert c["severity"] in ("warning", "info")
        assert c["remediation"]  # non-empty guidance


def test_qodo_setup_checks_pass_when_present(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".pr_agent.toml").write_text("[pr_reviewer]\n", encoding="utf-8")
    (tmp_path / "best_practices.md").write_text("# bp\n", encoding="utf-8")
    home = tmp_path / "home"
    (home / ".qodo").mkdir(parents=True)
    # A real API_KEY — not just an existing file (that was the bug Qodo caught).
    (home / ".qodo" / "config.json").write_text('{"API_KEY": "k"}', encoding="utf-8")
    monkeypatch.delenv("QODO_API_KEY", raising=False)
    checks = {c["id"]: c for c in _doctor._qodo_setup_checks(tmp_path, home)}
    assert all(checks[k]["passed"] for k in checks)


def test_qodo_client_config_fails_without_api_key(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An existing config.json with no API_KEY must NOT pass — `qodo rules` needs the key.
    home = tmp_path / "home"
    (home / ".qodo").mkdir(parents=True)
    (home / ".qodo" / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.delenv("QODO_API_KEY", raising=False)
    checks = {c["id"]: c for c in _doctor._qodo_setup_checks(tmp_path, home)}
    cc = checks["qodo_client_config_present"]
    assert cc["passed"] is False
    assert cc["remediation"]


def test_qodo_client_config_malformed_does_not_throw(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / ".qodo").mkdir(parents=True)
    (home / ".qodo" / "config.json").write_text("{not json", encoding="utf-8")
    monkeypatch.delenv("QODO_API_KEY", raising=False)
    # Must not raise; reports a failed advisory check with guidance.
    checks = {c["id"]: c for c in _doctor._qodo_setup_checks(tmp_path, home)}
    cc = checks["qodo_client_config_present"]
    assert cc["passed"] is False
    assert "JSON" in cc["message"] or "valid" in cc["message"]


def test_qodo_client_config_satisfied_by_env(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QODO_API_KEY", "k")
    checks = {c["id"]: c for c in _doctor._qodo_setup_checks(tmp_path, tmp_path)}
    assert checks["qodo_client_config_present"]["passed"] is True


def test_is_healthy_ignores_advisory_failures() -> None:
    checks = [
        {"id": "a", "passed": True, "severity": "error"},
        {"id": "b", "passed": False, "severity": "warning"},
        {"id": "c", "passed": False, "severity": "info"},
    ]
    assert _doctor._is_healthy(checks) is True
    checks.append({"id": "d", "passed": False, "severity": "error"})
    assert _doctor._is_healthy(checks) is False
