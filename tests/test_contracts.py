"""Contract + opt-in live-smoke tests closing the integration-verification gaps (#8).

Two implemented paths had only mocked coverage:

* the Qodo ``/rules/search`` response parser (no recorded-shape contract test);
* GitHub Enterprise provider resolution (never run against a real GHE host).

This module adds an **offline contract test** that asserts the parser against a
recorded ``/rules/search`` response fixture, plus **opt-in live smokes** that run
only when the relevant credentials / remote are available (skipped by default in
CI). See ``docs/manual-verification.md`` for the full manual checklist.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

import pytest

from qodo.cli import _providers, _qodo_api, main

_FIXTURES = Path(__file__).parent / "fixtures"


class _FakeResp:
    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _recorded_rules_response() -> str:
    return (_FIXTURES / "rules_search_response.json").read_text(encoding="utf-8")


# --- offline contract: /rules/search response shape ------------------------


def test_rules_search_parses_recorded_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """The parser must handle a real-shaped /rules/search payload."""
    monkeypatch.setenv("QODO_API_KEY", "k")
    with mock.patch(
        "qodo.cli._qodo_api.urllib.request.urlopen",
        return_value=_FakeResp(_recorded_rules_response()),
    ):
        rules = _qodo_api.search_rules("validate input", top_k=20)

    # relevance order preserved; the documented fields present on every rule
    assert [r["id"] for r in rules] == ["rule-001", "rule-002", "rule-003"]
    assert [r["severity"] for r in rules] == ["ERROR", "WARNING", "RECOMMENDATION"]
    for rule in rules:
        assert {"id", "name", "content", "severity"} <= set(rule)
        assert rule["severity"] in _qodo_api.SEVERITIES
    # unknown extra fields (score) pass through untouched, not dropped or fatal
    assert rules[0]["score"] == 0.94


def test_rules_get_renders_recorded_response(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """End-to-end: the recorded payload renders through the `rules get` surface."""
    monkeypatch.setenv("QODO_API_KEY", "k")
    monkeypatch.setenv("HOME", "/nonexistent-home-for-test")
    with (
        mock.patch("qodo.cli._qodo_api.detect_scopes", return_value=[]),
        mock.patch(
            "qodo.cli._qodo_api.urllib.request.urlopen",
            return_value=_FakeResp(_recorded_rules_response()),
        ),
    ):
        rc = main(["rules", "get", "validate input", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 3
    assert out["rules"][0]["name"] == "Validate input at trust boundaries"


def test_rules_search_tolerates_missing_rules_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A response without a `rules` key is an empty result, not a crash."""
    monkeypatch.setenv("QODO_API_KEY", "k")
    with mock.patch(
        "qodo.cli._qodo_api.urllib.request.urlopen",
        return_value=_FakeResp(json.dumps({"total": 0})),
    ):
        assert _qodo_api.search_rules("q") == []


# --- opt-in live smokes (skipped by default) -------------------------------
#
# Live smokes are gated on an explicit opt-in switch, *not* merely on the
# presence of credentials. A qodo-cli developer almost always exports
# ``QODO_API_KEY`` so real ``qodo rules`` works, so gating only on the key would
# fire a real network call during every ``pytest`` run in that shell —
# non-deterministic and flaky. ``QODO_LIVE_SMOKE`` is the deliberate switch.


def _live_smoke_opt_in() -> bool:
    """True only when live smokes are explicitly requested via ``QODO_LIVE_SMOKE``.

    Treats unset / ``""`` / ``0`` / ``false`` / ``no`` as off so a stray export
    doesn't enable network calls.
    """
    return os.environ.get("QODO_LIVE_SMOKE", "").strip().lower() not in (
        "",
        "0",
        "false",
        "no",
    )


def _ghe_smoke_ready() -> bool:
    """True only when the GHE smoke's *full* prerequisites are met.

    ``resolve_provider()`` shells out to ``gh`` (``gh auth status --hostname``),
    so without ``gh`` on PATH and authenticated to the remote's host the test
    would *fail* (resolution → ``"unknown"``) rather than skip. Gate on the
    whole chain — opt-in, remote set, and ``gh`` actually able to drive the host
    — so an unprepared environment skips cleanly instead of erroring.
    """
    if not _live_smoke_opt_in():
        return False
    url = os.environ.get("QODO_CLI_GHE_REMOTE", "")
    host = _providers._host_from_remote(url)
    return bool(host and _providers.gh_knows_host(host))


@pytest.mark.skipif(
    not (_live_smoke_opt_in() and os.environ.get("QODO_API_KEY")),
    reason="live smoke: set QODO_LIVE_SMOKE=1 and QODO_API_KEY to hit the real Qodo /rules/search",
)
def test_live_rules_search_smoke() -> None:
    """Hits the real Qodo rules API (only when explicitly opted in via QODO_LIVE_SMOKE)."""
    rules = _qodo_api.search_rules("validate user input", top_k=1)
    assert isinstance(rules, list)
    for rule in rules:
        assert "severity" in rule


@pytest.mark.skipif(
    not _ghe_smoke_ready(),
    reason="live smoke: set QODO_LIVE_SMOKE=1 + QODO_CLI_GHE_REMOTE to a GHE origin URL "
    "that `gh` is authenticated to",
)
def test_live_ghe_resolves_to_github() -> None:
    """A real GHE remote (that `gh` is authenticated to) resolves to `github`."""
    url = os.environ["QODO_CLI_GHE_REMOTE"]
    assert _providers.resolve_provider(url) == "github"
