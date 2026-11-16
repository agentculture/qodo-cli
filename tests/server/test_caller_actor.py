"""Over HTTP the change log and the fix-request event name the caller, not only the surface.

The CLI and MCP surfaces keep recording their bare surface (``cli``, ``mcp``); a hostile identity
(a newline, a very long name) is cleaned before it is logged or published."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from qodo import api, changelog
from qodo import propose as pr
from qodo import registry as reg
from qodo.bridge.caller import MAX_LABEL_CHARS
from qodo.bridge.resolve import AuthSettings
from qodo.server.app import create_app

PR = "https://github.com/acme/widgets/pull/42"
UUID = "123e4567-e89b-12d3-a456-426614174000"
EDITOR = "api:token:editor"


class FakePublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, event_type: str, data: dict) -> dict:
        self.calls.append((event_type, json.loads(json.dumps(data))))
        return {"event_id": f"evt_T{len(self.calls)}", "topic": "events/qodo/fix/requested"}


@pytest.fixture
def pub(monkeypatch, tmp_path) -> FakePublisher:
    monkeypatch.setenv("QODO_PROPOSE_CONFIG", str(tmp_path / "absent.json"))
    monkeypatch.setenv("QODO_PROPOSE_REPOS", "acme/widgets")
    monkeypatch.delenv("QODO_FIX_REQUEST_WINDOW", raising=False)
    fake = FakePublisher()
    monkeypatch.setattr(api, "_publisher", lambda: fake)
    return fake


@pytest.fixture
def accepted(monkeypatch):
    """Both proposal gates accept a one-file change without touching a checkout."""
    ok = {"ok": True, "files": [{"path": "src/app.py", "content": "x\n"}], "warnings": []}
    monkeypatch.setattr(pr, "propose", lambda *a, **k: ok)
    monkeypatch.setattr(pr, "load_settings", lambda *a, **k: None)


def last(kind: str) -> dict:
    return [e for e in changelog.read() if e["kind"] == kind][-1]


def test_findings_fix_over_http_names_the_token_in_the_event_and_the_log(client, auth, pub):
    r = client.post(
        "/verbs/findings/fix", json={"pr_url": PR, "finding_ids": [UUID]}, headers=auth["editor"]
    )
    assert r.status_code == 200
    assert pub.calls[0][1]["requested_by"] == EDITOR
    assert last("fix.requested")["actor"] == EDITOR


@pytest.mark.parametrize(
    "path, body, status",
    [
        ("implement", {"finding_ids": [UUID]}, "implemented"),
        ("dismiss", {"finding_ids": [UUID], "reason": "deferred"}, "dismissed"),
    ],
)
def test_a_finding_write_over_http_logs_the_caller(client, auth, gateway, path, body, status):
    r = client.post(f"/verbs/findings/{path}", json=body, headers=auth["editor"])
    assert r.status_code == 200
    entry = last("finding.status_changed")
    assert entry["status"] == status and entry["actor"] == EDITOR


@pytest.mark.parametrize("noun, kind", [("config", "config.proposed"), ("fix", "fix.proposed")])
def test_both_proposals_over_http_log_the_caller(client, auth, accepted, noun, kind):
    body = {"repo": "acme/widgets", "checkout": "/srv/acme/widgets", "base": "main"}
    r = client.post(f"/verbs/{noun}/propose", json=body, headers=auth["editor"])
    assert r.status_code == 200 and r.json()["ok"] is True
    assert last(kind)["actor"] == EDITOR


def test_a_person_through_access_is_named_by_their_identity(tokens, gateway):
    # the dev identity header stands in for a verified Access identity (kind sso)
    settings = AuthSettings(insecure_dev_identity=True)
    client = TestClient(create_app(tokens=tokens, settings=settings, web_dist=None))
    r = client.post(
        "/verbs/findings/implement",
        json={"finding_ids": [UUID]},
        headers={"X-Dev-Identity": "person@example.org"},
    )
    assert r.status_code == 200
    assert last("finding.status_changed")["actor"] == "api:person@example.org"


def test_a_hostile_token_name_is_cleaned_before_it_is_logged_or_published(tokens, pub):
    hostile = 'eve\n{"kind":"fix.requested","actor":"admin"}\r' + "x" * 5000
    token = tokens.issue("t", name=hostile, roles=["editor"]).token
    client = TestClient(create_app(tokens=tokens, web_dist=None))
    r = client.post(
        "/verbs/findings/fix",
        json={"pr_url": PR, "finding_ids": [UUID]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200
    published = pub.calls[0][1]["requested_by"]
    logged = last("fix.requested")["actor"]
    assert published == logged
    assert logged.startswith('api:token:eve {"kind"')
    assert "\n" not in logged and "\r" not in logged
    assert len(logged) <= len("api:") + MAX_LABEL_CHARS
    # the log is still one JSON object per line
    raw = changelog.log_path().read_text(encoding="utf-8").splitlines()
    assert all(json.loads(line)["kind"] for line in raw)


def test_the_cli_and_mcp_still_record_their_bare_surface(pub):
    from qodo import mcp_tools
    from qodo.cli import main

    assert main(["findings", "fix", "--pr-url", PR, "--finding-ids", UUID, "--json"]) == 0
    assert pub.calls[-1][1]["requested_by"] == "cli" and last("fix.requested")["actor"] == "cli"
    (tool,) = [t for t in mcp_tools.tools() if t.name == "findings_fix"]
    tool.handler({"pr_url": PR, "finding_ids": [UUID, "x-2"]})
    assert pub.calls[-1][1]["requested_by"] == "mcp" and last("fix.requested")["actor"] == "mcp"


def test_the_caller_does_not_leak_into_a_later_call_outside_http(client, auth, pub):
    client.post(
        "/verbs/findings/fix", json={"pr_url": PR, "finding_ids": [UUID]}, headers=auth["editor"]
    )
    verb = reg.default_registry().get("findings", "fix")
    verb.handler({"pr_url": PR, "finding_ids": ["other-1"]}, surface="cli")
    assert pub.calls[-1][1]["requested_by"] == "cli"
