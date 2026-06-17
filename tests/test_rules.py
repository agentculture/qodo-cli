"""Tests for ``qodo rules`` and the zero-dep Qodo rules API client."""

from __future__ import annotations

import json
import urllib.error
from unittest import mock

import pytest

from qodo.cli import _qodo_api, main
from qodo.cli._errors import CliError


class _FakeResp:
    """Minimal context-manager stand-in for urlopen()'s return value."""

    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # Isolate from the developer's real ~/.qodo and Qodo env vars.
    monkeypatch.setenv("HOME", str(tmp_path))
    for var in ("QODO_API_KEY", "QODO_API_URL", "QODO_ENVIRONMENT_NAME", "TRACE_ID"):
        monkeypatch.delenv(var, raising=False)


# --- base-URL construction -------------------------------------------------


def test_base_url_default_is_production() -> None:
    assert _qodo_api.resolve_base_url({}) == "https://qodo-platform.qodo.ai/rules/v1"


def test_base_url_staging_from_config() -> None:
    url = _qodo_api.resolve_base_url({"ENVIRONMENT_NAME": "staging"})
    assert url == "https://qodo-platform.staging.qodo.ai/rules/v1"


def test_base_url_explicit_override_wins() -> None:
    url = _qodo_api.resolve_base_url({"QODO_API_URL": "https://example.test/api/"})
    assert url == "https://example.test/api/rules/v1"


def test_base_url_env_overrides_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QODO_ENVIRONMENT_NAME", "qodost.st")
    url = _qodo_api.resolve_base_url({"ENVIRONMENT_NAME": "staging"})
    assert url == "https://qodo-platform.qodost.st.qodo.ai/rules/v1"


# --- credentials -----------------------------------------------------------


def test_api_key_env_beats_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QODO_API_KEY", "from-env")
    assert _qodo_api.resolve_api_key({"API_KEY": "from-config"}) == "from-env"


def test_api_key_missing_raises_env_error() -> None:
    with pytest.raises(CliError) as exc:
        _qodo_api.resolve_api_key({})
    assert exc.value.code == 2


def test_load_config_absent_returns_empty() -> None:
    # HOME points at an empty tmp dir, so ~/.qodo/config.json does not exist.
    assert _qodo_api.load_qodo_config() == {}


def test_load_config_invalid_json_raises(tmp_path) -> None:
    cfg = tmp_path / ".qodo"
    cfg.mkdir()
    (cfg / "config.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(CliError) as exc:
        _qodo_api.load_qodo_config()
    assert exc.value.code == 2


# --- search_rules ----------------------------------------------------------


def test_search_rules_posts_and_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QODO_API_KEY", "k")
    payload = {"rules": [{"id": "1", "name": "N", "content": "C", "severity": "ERROR"}]}
    with mock.patch(
        "qodo.cli._qodo_api.urllib.request.urlopen",
        return_value=_FakeResp(json.dumps(payload)),
    ) as urlopen:
        rules = _qodo_api.search_rules("validate input", top_k=5)
    assert rules == payload["rules"]
    # The request carried the bearer auth header and JSON body.
    req = urlopen.call_args.args[0]
    assert req.get_header("Authorization") == "Bearer k"
    assert json.loads(req.data.decode())["top_k"] == 5
    assert "scopes" not in json.loads(req.data.decode())


def test_search_rules_includes_scopes_when_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QODO_API_KEY", "k")
    with mock.patch(
        "qodo.cli._qodo_api.urllib.request.urlopen",
        return_value=_FakeResp(json.dumps({"rules": []})),
    ) as urlopen:
        _qodo_api.search_rules("q", scopes=["org/repo"])
    body = json.loads(urlopen.call_args.args[0].data.decode())
    assert body["scopes"] == ["org/repo"]


def test_search_rules_http_error_is_env_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QODO_API_KEY", "k")
    err = urllib.error.HTTPError("http://x", 401, "Unauthorized", {}, None)
    with mock.patch("qodo.cli._qodo_api.urllib.request.urlopen", side_effect=err):
        with pytest.raises(CliError) as exc:
            _qodo_api.search_rules("q")
    assert exc.value.code == 2


# --- the `rules` command surface ------------------------------------------


def test_rules_get_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = [{"id": "1", "name": "No raw SQL", "content": "Use params", "severity": "ERROR"}]
    with mock.patch("qodo.cli._qodo_api.search_rules", return_value=fake):
        rc = main(["rules", "get", "sql safety", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 1
    assert out["rules"][0]["severity"] == "ERROR"


def test_rules_get_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = [{"id": "1", "name": "No raw SQL", "content": "Use params", "severity": "WARNING"}]
    with mock.patch("qodo.cli._qodo_api.search_rules", return_value=fake):
        rc = main(["rules", "get", "sql safety"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "No raw SQL" in out
    assert "[WARNING]" in out


def test_rules_get_empty(capsys: pytest.CaptureFixture[str]) -> None:
    with mock.patch("qodo.cli._qodo_api.search_rules", return_value=[]):
        rc = main(["rules", "get", "nothing matches"])
    assert rc == 0
    assert "No relevant rules" in capsys.readouterr().out


def test_rules_get_missing_config_exits_env_error(capsys: pytest.CaptureFixture[str]) -> None:
    # No ~/.qodo/config.json and no QODO_API_KEY -> environment error, no prompt.
    rc = main(["rules", "get", "anything"])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "hint:" in err


def test_rules_overview(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["rules", "overview"])
    assert rc == 0
    assert "qodo-get-rules" in capsys.readouterr().out


def test_rules_no_verb_prints_overview(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["rules"])
    assert rc == 0
    assert "rules get" in capsys.readouterr().out
