"""Key expiry (``qodo.health``) and the doctor check built on it."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from qodo import health
from qodo.cli._commands import doctor as _doctor

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in ("QODO_API_KEY", "QODO_API_KEY_EXPIRES"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / ".qodo").mkdir()
    return tmp_path


def _config(home: Path, **fields: str) -> None:
    (home / ".qodo" / "config.json").write_text(json.dumps(fields), encoding="utf-8")


def _check(home: Path) -> dict:
    return _doctor._key_expiry_check(home, NOW)


@pytest.mark.parametrize(
    "expires, passed, days",
    [
        ("2026-11-29", False, 13),
        ("2026-11-30", True, 14),
        ("2026-12-01", True, 15),
        ("2026-11-15", False, -1),
    ],
)
def test_doctor_key_expiry_threshold(home, expires, passed, days):
    _config(home, API_KEY="sekret-key", API_KEY_EXPIRES=expires)
    c = _check(home)
    assert c["passed"] is passed
    assert c["id"] == "qodo_key_expiry"
    assert health.key_status(now=NOW, home=home)["days_left"] == days
    assert "sekret-key" not in json.dumps(c)
    if not passed:
        assert "configuration" in c["remediation"] and "QODO_API_KEY_EXPIRES" in c["remediation"]


def test_expired_says_expired(home):
    _config(home, API_KEY="k", API_KEY_EXPIRES="2026-01-01")
    assert "expired" in _check(home)["message"]


def test_doctor_fails_when_key_missing(home):
    c = _check(home)
    assert c["passed"] is False and c["remediation"]


def test_env_expiry_wins_over_config(home, monkeypatch):
    _config(home, API_KEY="k", API_KEY_EXPIRES="2026-11-17")
    monkeypatch.setenv("QODO_API_KEY_EXPIRES", "2027-06-01")
    s = health.key_status(now=NOW, home=home)
    assert s["state"] == "ok" and s["days_left"] == 197


def test_key_from_env_with_expiry_from_env(home, monkeypatch):
    monkeypatch.setenv("QODO_API_KEY", "k")
    monkeypatch.setenv("QODO_API_KEY_EXPIRES", "2026-12-25")
    assert _check(home)["passed"] is True


def test_unknown_expiry_passes_but_says_so(home):
    _config(home, API_KEY="k")
    s = health.key_status(now=NOW, home=home)
    assert s == {
        "present": True,
        "state": "unknown",
        "expires": None,
        "days_left": None,
        "detail": s["detail"],
    }
    c = _check(home)
    assert c["passed"] is True
    assert "unknown" in c["message"] and "QODO_API_KEY_EXPIRES" in c["message"]


@pytest.mark.parametrize("bad", ["soon", "2026-13-40", "11/30/2026", ""])
def test_unparseable_expiry_fails(home, bad):
    _config(home, API_KEY="k", API_KEY_EXPIRES=bad)
    c = _check(home)
    if bad == "":  # an empty value is "not set"
        assert c["passed"] is True
    else:
        assert c["passed"] is False and "ISO" in c["remediation"]


def test_datetime_expiry_accepted(home):
    _config(home, API_KEY="k", API_KEY_EXPIRES="2026-12-31T23:00:00Z")
    assert health.key_status(now=NOW, home=home)["days_left"] == 45


def test_key_status_never_raises_on_bad_config(home):
    (home / ".qodo" / "config.json").write_text("{nope", encoding="utf-8")
    s = health.key_status(now=NOW, home=home)
    assert s["present"] is False and s["state"] == "missing"


def test_doctor_json_includes_check(home, capsys):
    from qodo.cli import main

    _config(home, API_KEY="k", API_KEY_EXPIRES="2026-01-01")
    main(["doctor", "--json"])
    ids = {c["id"] for c in json.loads(capsys.readouterr().out)["checks"]}
    assert "qodo_key_expiry" in ids
