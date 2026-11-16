"""The HTTP API: routes generated from the registry, auth before handlers, errors without
tracebacks, and the policy that decides which tools are reachable at all."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from qodo import api, registry
from qodo.bridge.principal import ROLES
from qodo.cli._errors import CliError
from qodo.exposure import Exposure, ExposurePolicy, load_policy
from qodo.server import app as server_app
from qodo.server.app import MAX_BODY_BYTES, build_policy, create_app, role_for

PR = "https://github.com/org/repo/pull/1"
UUID = "123e4567-e89b-12d3-a456-426614174000"
GOOD_BODY = {
    ("findings", "list"): {"pr_url": PR},
    ("findings", "implement"): {"finding_ids": [UUID]},
    ("findings", "dismiss"): {"finding_ids": [UUID], "reason": "false_positive"},
    ("config", "propose"): {"repo": "o/r", "checkout": "/srv/o/r", "base": "main"},
}
READ_PATHS = ["/changes", "/verbs", "/openapi.json"]


def route(verb) -> str:
    return f"/verbs/{verb.noun}/{verb.name}"


def api_verbs():
    return registry.default_registry().on_surface("api")


# --- every registry verb on the api surface is one operation ---------------------------


def test_every_api_verb_is_one_post_operation_and_nothing_else_is(client):
    paths = client.app.openapi()["paths"]
    expected = {route(v) for v in api_verbs()} | {"/health", "/changes", "/verbs", "/openapi.json"}
    assert set(paths) == expected
    for verb in api_verbs():
        assert list(paths[route(verb)]) == ["post"]
        assert paths[route(verb)]["post"]["operationId"] == f"{verb.noun}_{verb.name}"


def test_a_verb_not_on_the_api_surface_has_no_route(client, auth):
    cli_only = [v for v in registry.default_registry().verbs() if "api" not in v.surfaces]
    assert cli_only, "the default registry has verbs that are not on the api surface"
    for verb in cli_only:
        r = client.post(route(verb), json={}, headers=auth["admin"])
        assert r.status_code == 404, verb.verb
        assert r.json()["error"]["code"] == "not_found"
        assert route(verb) not in client.app.openapi()["paths"]


def test_the_params_schema_is_the_request_schema(client):
    paths = client.app.openapi()["paths"]
    for verb in api_verbs():
        body = paths[route(verb)]["post"]["requestBody"]["content"]["application/json"]
        assert body["schema"] == verb.params, verb.verb


def test_an_upstream_verb_calls_the_gateway_for_the_api_surface(client, auth, gateway):
    gateway.reply = {"finding_count": 0, "findings": []}
    r = client.post("/verbs/findings/list", json={"pr_url": PR}, headers=auth["viewer"])
    assert r.status_code == 200
    assert r.json() == {"finding_count": 0, "findings": []}
    assert gateway.calls == [("get-pr-review-findings", {"pr_url": PR}, "api")]


def test_a_local_verb_runs_through_qodo_api(client, auth, monkeypatch):
    seen = {}

    def fake(query, *, top_k, scopes, auto_scope):
        seen.update(query=query, scopes=scopes, auto_scope=auto_scope)
        return [{"name": "r"}]

    monkeypatch.setattr(api, "rules_get", fake)
    r = client.post(
        "/verbs/rules/get", json={"query": "q", "no_scope": True}, headers=auth["viewer"]
    )
    assert r.status_code == 200 and r.json()["count"] == 1
    assert seen["auto_scope"] is False


def test_an_empty_body_means_no_params(client, auth, gateway):
    r = client.post("/verbs/rules/metadata", headers=auth["viewer"])
    assert r.status_code == 200
    assert gateway.calls == [("rules-metadata", {}, "api")]


def test_handlers_import_only_qodo_api_never_the_gateway_or_a_subprocess():
    import ast
    import inspect

    imported: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(server_app))):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            imported |= {base} | {f"{base}.{a.name}" for a in node.names}
    for banned in ("qodo.gateway", "qodo.cli._providers", "qodo.cli._qodo_api", "subprocess"):
        assert not any(i == banned or i.startswith(banned + ".") for i in imported), banned
    assert "qodo.api" in imported or "qodo.api" in {i.rsplit(".", 1)[0] for i in imported}


# --- 401 without a token, 403 with too low a role, both before any handler ------------


@pytest.mark.parametrize("key", sorted(GOOD_BODY, key=str), ids=lambda k: " ".join(k))
def test_findings_and_propose_routes_return_401_without_a_token(client, gateway, key):
    r = client.post(f"/verbs/{key[0]}/{key[1]}", json=GOOD_BODY[key])
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "no_credentials"
    assert gateway.calls == []


def test_every_non_public_route_returns_401_without_a_token(client, gateway):
    for verb in api_verbs():
        assert client.post(route(verb), json={}).status_code == 401, verb.verb
    for path in READ_PATHS + ["/", "/anything-else"]:
        assert client.get(path).status_code == 401, path
    assert gateway.calls == []


def test_a_bad_or_revoked_token_is_401(client, tokens):
    assert client.get("/verbs", headers={"Authorization": "Bearer nope"}).status_code == 401
    issued = tokens.issue("t", name="x", roles=["viewer"])
    tokens.revoke(issued.id, "t")
    r = client.get("/verbs", headers={"Authorization": f"Bearer {issued.token}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "bad_token"


def test_a_viewer_reads_but_cannot_change_a_finding_or_propose(client, auth, gateway):
    assert (
        client.post(
            "/verbs/findings/list", json=GOOD_BODY[("findings", "list")], headers=auth["viewer"]
        ).status_code
        == 200
    )
    gateway.calls.clear()
    for key in (("findings", "implement"), ("findings", "dismiss"), ("config", "propose")):
        r = client.post(f"/verbs/{key[0]}/{key[1]}", json=GOOD_BODY[key], headers=auth["viewer"])
        assert r.status_code == 403, key
        assert r.json()["error"]["code"] == "forbidden"
    assert gateway.calls == []


def test_an_editor_can_change_a_finding(client, auth, gateway):
    r = client.post(
        "/verbs/findings/dismiss", json=GOOD_BODY[("findings", "dismiss")], headers=auth["editor"]
    )
    assert r.status_code == 200
    assert gateway.calls[0][0] == "pr-review-dismiss"


def test_the_role_table():
    reg = registry.default_registry()
    for verb in reg.on_surface("api"):
        gates = ("config propose", "fix propose")
        expected = "editor" if verb.writes or verb.verb in gates else "viewer"
        assert role_for(verb) == expected, verb.verb
    policy = build_policy(reg)
    assert policy.required_role("GET", "/health") is None
    assert policy.required_role("GET", "/changes") == "viewer"
    assert policy.required_role("GET", "/verbs") == "viewer"
    assert policy.required_role("POST", "/verbs/findings/dismiss") == "editor"
    assert policy.required_role("POST", "/verbs/config/propose") == "editor"
    assert policy.required_role("POST", "/verbs/findings/list") == "viewer"
    # unlisted: admin (fail closed), including a method the verb route does not take
    assert policy.required_role("GET", "/verbs/findings/list") == "admin"
    assert policy.required_role("POST", "/unknown") == "admin"
    assert policy.required_role("DELETE", "/changes") == "admin"
    assert set(ROLES) >= {role_for(v) for v in reg.on_surface("api")}


# --- /health ---------------------------------------------------------------------------


def test_health_answers_without_a_token_and_carries_no_secret(client, monkeypatch, tmp_path):
    monkeypatch.setenv("QODO_API_KEY", "sk-very-secret-value")
    monkeypatch.setenv("QODO_API_KEY_EXPIRES", "2999-01-01")
    monkeypatch.setenv("HOME", str(tmp_path))
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"status", "service", "version", "key"}
    assert body["service"] == "qodo-cli"
    assert set(body["key"]) == {"present", "state", "days_left"}
    assert body["key"]["present"] is True and body["key"]["state"] == "ok"
    assert "sk-very-secret-value" not in r.text
    assert str(tmp_path) not in r.text
    assert r.headers["cache-control"] == "no-store"


def test_health_without_a_key_says_so(client, monkeypatch, tmp_path):
    monkeypatch.delenv("QODO_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    body = client.get("/health").json()
    assert body["key"] == {"present": False, "state": "missing", "days_left": None}


# --- /verbs and /changes ------------------------------------------------------------------


def test_verbs_lists_the_api_registry(client, auth):
    r = client.get("/verbs", headers=auth["viewer"])
    assert r.status_code == 200
    rows = {(v["noun"], v["name"]): v for v in r.json()["verbs"]}
    assert set(rows) == {v.path for v in api_verbs()}
    one = rows[("findings", "dismiss")]
    assert one["writes"] is True and one["role"] == "editor"
    assert one["method"] == "POST" and one["path"] == "/verbs/findings/dismiss"
    assert one["params"] == registry.default_registry().get("findings", "dismiss").params
    assert one["summary"]
    assert rows[("config", "propose")]["role"] == "editor"
    assert len(r.content) < 64 * 1024 * 4


def test_changes_returns_the_newest_entries(client, auth, gateway):
    for _ in range(3):
        client.post(
            "/verbs/findings/dismiss", json=GOOD_BODY[("findings", "dismiss")],
            headers=auth["editor"],
        )  # fmt: skip
    r = client.get("/changes", headers=auth["viewer"])
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert len(entries) == 3 and entries[0]["kind"] == "finding.status_changed"
    assert entries[0]["actor"] == "api"
    assert len(client.get("/changes?limit=2", headers=auth["viewer"]).json()["entries"]) == 2
    for bad in ("0", "-1", "x", "501"):
        assert client.get(f"/changes?limit={bad}", headers=auth["viewer"]).status_code == 400, bad


def test_changes_are_empty_before_any_change(client, auth):
    assert client.get("/changes", headers=auth["viewer"]).json() == {"entries": []}


# --- errors: JSON, a status, never a traceback --------------------------------------------


def test_a_user_error_is_400(client, auth, gateway):
    r = client.post("/verbs/findings/list", json={"pr_url": "not a url"}, headers=auth["viewer"])
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "user_error" and "pr_url" in err["message"] and err["remediation"]


def test_unknown_or_missing_params_are_400(client, auth, gateway):
    r = client.post("/verbs/findings/list", json={"pr_url": PR, "bogus": 1}, headers=auth["viewer"])
    assert r.status_code == 400
    r = client.post("/verbs/findings/list", json={}, headers=auth["viewer"])
    assert r.status_code == 400
    assert gateway.calls == []


def test_an_environment_or_upstream_failure_is_503(client, auth, monkeypatch):
    class Down:
        def call(self, *a, **k):
            raise CliError(code=2, message="Qodo MCP server answered HTTP 500", remediation="retry")

    monkeypatch.setattr(api, "_gateway", lambda: Down())
    r = client.post("/verbs/findings/list", json={"pr_url": PR}, headers=auth["viewer"])
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "environment_error"


def test_an_unexpected_exception_is_500_with_no_traceback_and_no_detail(client, auth, monkeypatch):
    class Boom:
        def call(self, *a, **k):
            raise RuntimeError("secret detail /home/x/.qodo/config.json")

    monkeypatch.setattr(api, "_gateway", lambda: Boom())
    r = client.post("/verbs/findings/list", json={"pr_url": PR}, headers=auth["viewer"])
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"
    assert "Traceback" not in r.text and "secret detail" not in r.text


def test_a_refusal_from_config_propose_is_a_normal_result(client, auth, monkeypatch):
    refusal = {"ok": False, "reason": "repo_not_allowed", "detail": "x", "paths": []}
    monkeypatch.setattr(api, "config_propose", lambda *a, **k: refusal)
    r = client.post(
        "/verbs/config/propose", json=GOOD_BODY[("config", "propose")], headers=auth["editor"]
    )
    assert r.status_code == 200 and r.json() == refusal


@pytest.mark.parametrize("raw", [b"{not json", b"[1]", b'"s"', b"\xff\xfe"])
def test_a_body_that_is_not_a_json_object_is_400(client, auth, raw):
    r = client.post(
        "/verbs/rules/metadata",
        content=raw,
        headers={**auth["viewer"], "Content-Type": "application/json"},
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_body"


@pytest.mark.parametrize("ctype", [None, "text/plain", "application/x-www-form-urlencoded"])
def test_a_body_not_sent_as_json_is_415_and_never_reaches_the_handler(client, auth, gateway, ctype):
    headers = dict(auth["viewer"])
    if ctype:
        headers["Content-Type"] = ctype
    r = client.post("/verbs/rules/show", content=b'{"rule_id": 1}', headers=headers)
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_media_type"
    assert gateway.calls == []
    ok = client.post(
        "/verbs/rules/show", content=b'{"rule_id": 1}',
        headers={**auth["viewer"], "Content-Type": "application/json; charset=utf-8"},
    )  # fmt: skip
    assert ok.status_code == 200


def test_an_oversized_body_is_413_and_never_reaches_the_handler(client, auth, gateway):
    big = json.dumps({"query": "q" * (MAX_BODY_BYTES + 10)}).encode()
    r = client.post("/verbs/rules/search", content=big, headers=auth["viewer"])
    assert r.status_code == 413 and r.json()["error"]["code"] == "body_too_large"
    assert gateway.calls == []


def test_an_oversized_chunked_body_is_413_too(client, auth, gateway):
    def chunks():
        for _ in range(MAX_BODY_BYTES // 1024 + 5):
            yield b"x" * 1024

    r = client.post("/verbs/rules/search", content=chunks(), headers=auth["viewer"])
    assert r.status_code == 413
    assert gateway.calls == []


def test_an_unknown_route_is_a_json_404_for_an_admin(client, auth):
    r = client.get("/nope", headers=auth["admin"])
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert r.headers["cache-control"] == "no-store"


def test_a_wrong_method_on_a_verb_route_is_refused(client, auth):
    assert client.get("/verbs/findings/list", headers=auth["viewer"]).status_code == 403
    r = client.get("/verbs/findings/list", headers=auth["admin"])
    assert r.status_code == 405 and r.json()["error"]["code"] == "method_not_allowed"


def test_every_reply_is_no_store(client, auth):
    for resp in (
        client.get("/health"),
        client.get("/verbs"),
        client.get("/verbs", headers=auth["viewer"]),
        client.post("/verbs/rules/metadata", headers=auth["viewer"]),
        client.get("/openapi.json", headers=auth["viewer"]),
    ):
        assert resp.headers["cache-control"] == "no-store", resp.request.url


# --- the policy decides what is reachable --------------------------------------------------


def _policy_without(tool: str) -> ExposurePolicy:
    return ExposurePolicy(e for e in load_policy().entries if e.tool != tool)


def test_a_tool_the_policy_drops_is_unreachable_on_every_surface(tokens, auth_for, monkeypatch):
    from qodo.cli import main
    from qodo.gateway import Gateway
    from tests.fake_qodo_mcp import FakeUpstream

    policy = _policy_without("rules-search")
    custom = registry.build_registry(policy)
    assert custom.get("rules", "search") is None

    # the API: no route
    app = create_app(tokens=tokens, registry=custom, web_dist=None)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/verbs/rules/search", json={"query": "q"}, headers=auth_for("admin"))
    assert r.status_code == 404
    assert "/verbs/rules/search" not in app.openapi()["paths"]
    assert "/verbs/rules/list" in app.openapi()["paths"]

    # the gateway: refused before any upstream call
    upstream = FakeUpstream()
    gw = Gateway(policy, api_key="k", opener=upstream)
    with pytest.raises(CliError):
        gw.call("rules-search", {"query": "q"}, surface="api")
    assert upstream.calls == []

    # the CLI: no command
    monkeypatch.setattr(registry, "_DEFAULT", custom)
    with pytest.raises(SystemExit) as stop:  # argparse: invalid choice
        main(["rules", "search", "--query", "q", "--json"])
    assert stop.value.code == 1


def test_a_tool_the_policy_lists_for_other_surfaces_only_has_no_api_route(tokens, auth_for):
    entries = [
        Exposure(e.tool, e.verb, ("cli",) if e.tool == "rules-list" else e.surfaces, e.allow_write)
        for e in load_policy().entries
    ]
    custom = registry.build_registry(ExposurePolicy(entries))
    app = create_app(tokens=tokens, registry=custom, web_dist=None)
    assert "/verbs/rules/list" not in app.openapi()["paths"]
    client = TestClient(app, raise_server_exceptions=False)
    assert client.post("/verbs/rules/list", json={}, headers=auth_for("admin")).status_code == 404


def test_a_route_name_with_odd_characters_is_refused_at_build_time():
    reg = registry.Registry()
    reg.add(
        registry.Verb(
            "a b", "x", "s", "local", {"type": "object"}, False, ("api",), lambda p, **k: 1
        )
    )
    with pytest.raises(ValueError, match="route"):
        build_policy(reg)


# --- static UI --------------------------------------------------------------------------------


def test_without_a_built_ui_root_is_a_json_404(tokens, auth):
    app = create_app(tokens=tokens, web_dist=None)
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/", headers=auth["admin"])
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_a_missing_default_ui_directory_does_not_stop_the_app(tokens):
    app = create_app(tokens=tokens)  # the default location; the build may not exist
    assert TestClient(app).get("/health").status_code == 200


def test_a_built_ui_is_served_behind_the_same_auth(tokens, auth, tmp_path, monkeypatch):
    monkeypatch.setenv("QODO_API_KEY", "k")
    dist = tmp_path / "web_dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>ui</title>", encoding="utf-8")
    app = create_app(tokens=tokens, web_dist=dist)
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/", headers={"Accept": "text/html"}).status_code == 401
    page = client.get("/", headers={**auth["viewer"], "Accept": "text/html"})
    assert page.status_code == 200 and "<title>ui</title>" in page.text
    # the same-origin /api prefix reaches the API, still under the role check
    assert client.get("/api/verbs", headers=auth["viewer"]).status_code == 200
    assert (
        client.post("/api/verbs/findings/dismiss", json={}, headers=auth["viewer"]).status_code
        == 403
    )
    assert client.get("/health").json()["status"] == "ok"
