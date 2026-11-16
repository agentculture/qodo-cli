"""The routes the web UI's three screens call: roles, refusals and sizes over HTTP."""

from __future__ import annotations

import json

import pytest

from qodo import api, registry
from qodo.server.app import build_policy, role_for

PR = "https://github.com/o/r/pull/1"
REPO = "acme/widgets"


@pytest.fixture
def allowlist(tmp_path, monkeypatch):
    root = tmp_path / "roots"
    path = root / "acme" / "widgets"
    (path / ".git").mkdir(parents=True)
    (path / ".pr_agent.toml").write_text("[pr_reviewer]\nx = 1\n# SECRET-BODY\n", "utf-8")
    monkeypatch.setenv("QODO_PROPOSE_REPOS", REPO)
    monkeypatch.setenv("QODO_PROPOSE_CHECKOUT_ROOTS", str(root))
    monkeypatch.setenv("QODO_PROPOSE_CONFIG", str(tmp_path / "none.json"))
    return root


ROUTES = {
    "/verbs/config/status": {"repo": REPO},
    "/verbs/prs/list": {"repo": "o/r", "state": "open"},
    "/verbs/findings/list": {"pr_url": PR, "offset": 0, "limit": 20},
}


@pytest.mark.parametrize("path", sorted(ROUTES))
def test_each_ui_read_route_needs_a_credential(client, gateway, path):
    r = client.post(path, json=ROUTES[path])
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "no_credentials"
    assert gateway.calls == []


@pytest.mark.parametrize("path", sorted(ROUTES))
def test_each_ui_read_route_is_a_viewer_read(client, auth, gateway, allowlist, path):
    gateway.reply = {"finding_count": 0, "findings": [], "pr_url": PR, "review_session": None}
    for role in ("viewer", "editor", "admin"):
        r = client.post(path, json=ROUTES[path], headers=auth[role])
        assert r.status_code == 200, (path, role, r.text)
    assert build_policy().required_role("POST", path) == "viewer"


def test_the_new_verbs_need_viewer_and_the_writes_still_need_editor():
    reg = registry.default_registry()
    assert role_for(reg.get("config", "status")) == "viewer"
    assert role_for(reg.get("prs", "list")) == "viewer"
    assert role_for(reg.get("findings", "implement")) == "editor"
    assert role_for(reg.get("findings", "dismiss")) == "editor"


def test_a_viewer_cannot_mark_or_dismiss(client, auth, gateway):
    uuid = "123e4567-e89b-12d3-a456-426614174000"
    for path, body in (
        ("/verbs/findings/implement", {"finding_ids": [uuid]}),
        ("/verbs/findings/dismiss", {"finding_ids": [uuid], "reason": "deferred"}),
    ):
        r = client.post(path, json=body, headers=auth["viewer"])
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
    assert gateway.calls == []


def test_config_status_refuses_a_repo_outside_the_allowlist(client, auth, allowlist):
    r = client.post("/verbs/config/status", json={"repo": "evil/repo"}, headers=auth["viewer"])
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "user_error"
    assert "allowlist" in r.json()["error"]["message"]


def test_config_status_refuses_a_path_param(client, auth, allowlist):
    r = client.post(
        "/verbs/config/status", json={"repo": REPO, "checkout": "/etc"}, headers=auth["viewer"]
    )
    assert r.status_code == 400 and "checkout" in r.json()["error"]["message"]


def test_config_status_returns_no_contents_and_no_paths(client, auth, allowlist):
    r = client.post("/verbs/config/status", json={}, headers=auth["viewer"])
    assert r.status_code == 200
    assert "SECRET-BODY" not in r.text and str(allowlist) not in r.text
    body = r.json()
    assert body["configured"] is True and body["repos"][0]["repo"] == REPO
    assert len(r.content) < 64 * 1024


def test_a_findings_page_over_http_stays_under_64_kib(client, auth, gateway):
    gateway.reply = {
        "finding_count": 40,
        "findings": [
            {"id": f"id-{i}", "attribution_status": "pending", "description": "d" * 9000}
            for i in range(40)
        ],
        "pr_url": PR,
        "review_session": None,
    }
    r = client.post(
        "/verbs/findings/list", json={"pr_url": PR, "limit": 50}, headers=auth["viewer"]
    )
    assert r.status_code == 200 and len(r.content) < 64 * 1024
    assert r.json()["counts"]["open"] == 40
    assert gateway.calls[-1] == ("get-pr-review-findings", {"pr_url": PR}, "api")


def test_the_pinned_contract_names_the_new_routes():
    from pathlib import Path

    doc = json.loads(
        (Path(__file__).resolve().parents[2] / "api" / "openapi.json").read_text("utf-8")
    )
    for path in ROUTES:
        assert "post" in doc["paths"][path], path
    status = doc["paths"]["/verbs/config/status"]["post"]
    assert status["x-role"] == "viewer"
    schema = status["requestBody"]["content"]["application/json"]["schema"]
    assert set(schema["properties"]) == {"repo"}
    assert api  # the module the handlers call
