"""The upstream gateway: MCP transport, exposure policy, default-deny, and the qodo.api calls.

Everything runs against a fake opener built from the recorded schemas and replies in
``tests/fixtures/qodo_mcp/``; nothing touches the network. Tests marked ``schema_only`` build on
the input schema alone: the upstream tool failed when probed (or was never called).
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from qodo import api
from qodo import exposure as exposure_mod
from qodo import gateway as gw
from qodo.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from tests.fake_qodo_mcp import FakeResponse, FakeUpstream, schemas, sse

KEY = "sk-test-SECRET-key-123"
schema_only = pytest.mark.schema_only


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("QODO_API_KEY", KEY)


def make(upstream=None, policy=None, **kw):
    up = upstream or FakeUpstream()
    return gw.Gateway(policy=policy, api_key=KEY, opener=up, **kw), up


# --- transport ----------------------------------------------------------------


def test_lists_all_tools_following_next_cursor():
    g, up = make(FakeUpstream(page_size=10))
    tools = g.list_tools()
    assert len(tools) == 71
    assert sum(1 for _, b in up.requests if b["method"] == "tools/list") == 8


def test_handshake_then_call_and_headers():
    g, up = make()
    out = g.call("rules-metadata", {})
    assert "structuredContent" not in out and isinstance(out, dict)
    methods = [b["method"] for _, b in up.requests]
    assert methods[:2] == ["initialize", "notifications/initialized"]
    assert methods[-1] == "tools/call"
    req, body = up.requests[0]
    assert req.full_url == "https://sdk.qodo.ai/v1/tools/mcp/"
    assert body["params"]["protocolVersion"] == "2025-03-26"
    assert req.get_header("Authorization") == f"Bearer {KEY}"
    assert "text/event-stream" in req.get_header("Accept")
    assert "Mcp-session-id" not in {k for k in req.headers}


def test_call_returns_structured_content():
    g, up = make()
    out = g.call("get-pr-review-findings", {"pr_url": "https://github.com/o/r/pull/1"})
    assert out["finding_count"] == 2
    assert up.calls == [("get-pr-review-findings", {"pr_url": "https://github.com/o/r/pull/1"})]


def test_plain_json_reply_is_handled():
    g, up = make()
    plain = FakeResponse(
        json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26"}}
        ).encode(),
        ctype="application/json",
    )
    up.override["initialize"] = plain
    g.call("rules-metadata", {})  # does not raise


def test_last_data_line_wins_with_earlier_noise():
    g, up = make()
    real = up.open

    def open_(req, timeout=None):
        resp = real(req, timeout)
        body = json.loads(req.data.decode())
        if body.get("method") == "tools/list":
            noise = sse({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}})
            return FakeResponse(noise + resp.read())
        return resp

    g._opener = type("O", (), {"open": staticmethod(open_)})()
    assert len(g.list_tools()) == 71


def test_rejects_non_https_endpoint():
    with pytest.raises(CliError) as e:
        gw.Gateway(api_key=KEY, endpoint="http://sdk.qodo.ai/v1/tools/mcp/", opener=FakeUpstream())
    assert e.value.code == EXIT_ENV_ERROR
    assert "HTTPS" in e.value.message


def test_missing_key_is_environment_error(monkeypatch):
    monkeypatch.delenv("QODO_API_KEY")
    with pytest.raises(CliError) as e:
        gw.Gateway(opener=FakeUpstream())
    assert e.value.code == EXIT_ENV_ERROR


def test_http_error_never_leaks_key():
    g, up = make()
    err = urllib.error.HTTPError(
        "https://sdk.qodo.ai/v1/tools/mcp/",
        401,
        f"bad {KEY}",
        {"Authorization": f"Bearer {KEY}"},
        None,
    )
    up.override["initialize"] = err
    with pytest.raises(CliError) as e:
        g.list_tools()
    assert e.value.code == EXIT_ENV_ERROR
    assert "401" in e.value.message
    assert KEY not in json.dumps(e.value.to_dict())
    assert "Authorization" not in json.dumps(e.value.to_dict())


def test_network_error_and_timeout_are_environment_errors():
    for exc in (urllib.error.URLError(f"boom {KEY}"), TimeoutError(f"slow {KEY}")):
        g, up = make()
        up.override["initialize"] = exc
        with pytest.raises(CliError) as e:
            g.list_tools()
        assert e.value.code == EXIT_ENV_ERROR
        assert KEY not in json.dumps(e.value.to_dict())


def test_jsonrpc_error_becomes_clierror():
    g, up = make()
    up.override["tools/list"] = FakeResponse(
        sse({"jsonrpc": "2.0", "id": 2, "error": {"code": -32000, "message": f"x {KEY}"}})
    )
    with pytest.raises(CliError) as e:
        g.list_tools()
    assert e.value.code == EXIT_ENV_ERROR
    assert KEY not in json.dumps(e.value.to_dict())


def test_oversize_reply_is_refused():
    g, up = make(max_bytes=64)
    with pytest.raises(CliError) as e:
        g.list_tools()
    assert e.value.code == EXIT_ENV_ERROR
    assert "too large" in e.value.message


def test_timeout_is_passed_to_the_opener():
    seen = []
    up = FakeUpstream()
    real = up.open
    up.open = lambda req, timeout=None: (seen.append(timeout), real(req, timeout))[1]
    g = gw.Gateway(api_key=KEY, opener=up, timeout=7)
    g.list_tools()
    assert set(seen) == {7}


def test_redirects_are_not_followed():
    handler = gw._NoRedirect()
    assert handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/") is None


def test_repr_hides_the_key():
    g, _ = make()
    assert KEY not in repr(g) and KEY not in str(vars(g).get("_endpoint", ""))


def test_tool_error_reply_becomes_clierror():
    g, up = make()
    with pytest.raises(CliError) as e:
        g.call("pull-request-review-history", {"repo": "o/r", "file_path": "a.py"})
    assert e.value.code == EXIT_USER_ERROR
    assert "MT-UPSTREAM-DOWN" in e.value.message


# --- policy and default-deny ----------------------------------------------------


def test_default_policy_names_exactly_eleven_tools_with_two_writes():
    p = exposure_mod.load_policy()
    assert {e.tool for e in p.entries} == {
        "get-pr-review-findings",
        "pr-review-mark-implemented",
        "pr-review-dismiss",
        "rules-search",
        "rules-list",
        "rules-get",
        "rules-metadata",
        "pull-request-review-history",
        "pull-request-similar-suggestions",
        "pull-request-stats",
        "litegit-list-prs",  # read-only: the Findings screen's open PRs by repo
    }
    assert {e.tool for e in p.entries if e.allow_write} == {
        "pr-review-mark-implemented",
        "pr-review-dismiss",
    }
    assert all(e.surfaces and e.verb for e in p.entries)


def test_default_policy_covers_only_known_tools_and_writes_match_annotations():
    s = schemas()
    for e in exposure_mod.load_policy().entries:
        assert e.tool in s
        assert exposure_mod.is_read_only(s[e.tool]["annotations"]) == (not e.allow_write)


def test_unnamed_tool_is_refused_even_if_read_only():
    g, up = make()
    assert exposure_mod.is_read_only(schemas()["litegit-ls"]["annotations"])
    with pytest.raises(CliError) as e:
        g.call("litegit-ls", {})
    assert e.value.code == EXIT_USER_ERROR
    assert "not exposed" in e.value.message
    assert up.calls == []


def test_every_unnamed_upstream_tool_is_refused():
    g, up = make()
    named = {e.tool for e in g.policy.entries}
    for name in schemas():
        if name not in named:
            with pytest.raises(CliError):
                g.call(name, {})
    assert up.calls == []


def test_non_read_only_tool_refused_unless_allowed_write():
    s = schemas()
    assert not exposure_mod.is_read_only(s["litegit-post-comment"]["annotations"])
    policy = exposure_mod.parse_policy(
        {
            "version": 1,
            "tools": [{"tool": "litegit-post-comment", "verb": "x", "surfaces": ["cli"]}],
        }
    )
    g, up = make(policy=policy)
    with pytest.raises(CliError) as e:
        g.call("litegit-post-comment", {})
    assert "not read-only" in e.value.message
    assert up.calls == []


def test_allowed_write_is_called():
    g, up = make()
    g.call("pr-review-dismiss", {"finding_ids": ["a"], "reason": "deferred"})
    assert up.calls[0][0] == "pr-review-dismiss"


def test_missing_annotations_count_as_not_read_only():
    tools = schemas()
    tools["rules-search"] = {k: v for k, v in tools["rules-search"].items() if k != "annotations"}
    g, up = make(FakeUpstream(tools=tools))
    with pytest.raises(CliError) as e:
        g.call("rules-search", {"query": "q"})
    assert "not read-only" in e.value.message
    assert not exposure_mod.is_read_only(None)
    assert not exposure_mod.is_read_only({})
    assert not exposure_mod.is_read_only({"readOnlyHint": "true"})


def test_tool_unknown_upstream_is_refused():
    tools = schemas()
    del tools["rules-list"]
    g, _ = make(FakeUpstream(tools=tools))
    with pytest.raises(CliError) as e:
        g.call("rules-list", {})
    assert "not offered" in e.value.message


def test_arguments_are_checked_against_the_schema():
    g, up = make()
    with pytest.raises(CliError) as e:
        g.call("get-pr-review-findings", {})
    assert "pr_url" in e.value.message
    with pytest.raises(CliError) as e:
        g.call("rules-get", {"rule_id": "x", "bogus": 1})
    assert "bogus" in e.value.message
    assert up.calls == []


@pytest.mark.parametrize(
    "bad",
    [
        {"version": 2, "tools": []},
        {"version": 1, "tools": [{"tool": "a", "verb": "v", "surfaces": ["ftp"]}]},
        {"version": 1, "tools": [{"tool": "a", "verb": "v", "surfaces": []}]},
        {
            "version": 1,
            "tools": [{"tool": "a", "verb": "v", "surfaces": ["cli"], "allow_write": 1}],
        },
        {"version": 1, "tools": [{"tool": "a", "verb": "v", "surfaces": ["cli"], "x": 1}]},
        {
            "version": 1,
            "tools": [
                {"tool": "a", "verb": "v", "surfaces": ["cli"]},
                {"tool": "a", "verb": "w", "surfaces": ["cli"]},
            ],
        },
        {
            "version": 1,
            "tools": [
                {"tool": "a", "verb": "v", "surfaces": ["cli"]},
                {"tool": "b", "verb": "v", "surfaces": ["cli"]},
            ],
        },
    ],
)
def test_malformed_policy_is_refused(bad):
    with pytest.raises(CliError) as e:
        exposure_mod.parse_policy(bad)
    assert e.value.code == EXIT_ENV_ERROR


def test_policy_loads_from_a_file_and_surfaces_filter(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(
        json.dumps(
            {"version": 1, "tools": [{"tool": "rules-list", "verb": "rl", "surfaces": ["mcp"]}]}
        )
    )
    p = exposure_mod.load_policy(f)
    assert [e.verb for e in p.on_surface("mcp")] == ["rl"]
    assert p.on_surface("cli") == []
    assert p.for_verb("rl").tool == "rules-list"
    with pytest.raises(CliError):
        exposure_mod.load_policy(tmp_path / "missing.json")


# --- rules search API wrapper ---------------------------------------------------


def test_gateway_wraps_the_rules_search_api(monkeypatch):
    seen = {}

    def fake(query, *, top_k, scopes, config=None):
        seen.update(query=query, top_k=top_k, scopes=scopes)
        return [{"name": "r"}]

    monkeypatch.setattr(gw._qodo_api, "search_rules", fake)
    g, _ = make()
    assert g.search_rules_api("q", top_k=3, scopes=["o/r"]) == [{"name": "r"}]
    assert seen == {"query": "q", "top_k": 3, "scopes": ["o/r"]}


# --- qodo.api -------------------------------------------------------------------


@pytest.fixture
def up(monkeypatch):
    fake = FakeUpstream()
    monkeypatch.setattr(api, "_gateway", lambda: gw.Gateway(api_key=KEY, opener=fake))
    return fake


def test_findings_list(up):
    out = api.findings_list("https://github.com/o/r/pull/1")
    assert out["finding_count"] == 2 and out["findings"][0]["id"]
    assert up.calls == [("get-pr-review-findings", {"pr_url": "https://github.com/o/r/pull/1"})]


def test_findings_list_extended_and_validation(up):
    api.findings_list("https://github.com/o/r/pull/1", extended=True)
    assert up.calls[0][1]["extended"] is True
    for bad in ("", "  ", "o/r#1"):
        with pytest.raises(CliError) as e:
            api.findings_list(bad)
        assert e.value.code == EXIT_USER_ERROR


def test_findings_implement_batches_ids(up):
    api.findings_implement(["a", "b"])
    assert up.calls == [("pr-review-mark-implemented", {"finding_ids": ["a", "b"]})]


def test_findings_implement_validates_ids(up):
    for bad in ([], [""], ["a"] * 101, "abc"):
        with pytest.raises(CliError) as e:
            api.findings_implement(bad)
        assert e.value.code == EXIT_USER_ERROR
    assert up.calls == []


def test_findings_dismiss_needs_a_known_reason(up):
    api.findings_dismiss(["a"], "false_positive")
    assert up.calls == [("pr-review-dismiss", {"finding_ids": ["a"], "reason": "false_positive"})]
    for bad in ("", "because", None):
        with pytest.raises(CliError) as e:
            api.findings_dismiss(["a"], bad)
        assert e.value.code == EXIT_USER_ERROR
    assert len(up.calls) == 1


def test_rules_search_goes_through_mcp(up):
    out = api.rules_search("error handling", top_k=5, scopes=["o/r"])
    assert out["rules"][0]["severity"] == "warning"
    assert up.calls == [
        ("rules-search", {"query": "error handling", "top_k": 5, "scopes": ["o/r"]})
    ]
    with pytest.raises(CliError):
        api.rules_search("  ")


def test_rules_list_passes_only_given_filters(up):
    api.rules_list(severity="error", page=2)
    assert up.calls == [("rules-list", {"severity": "error", "page": 2})]


def test_review_history_needs_a_file_path_schema_only(up):
    with pytest.raises(CliError) as e:
        api.review_history("o/r", "")
    assert e.value.code == EXIT_USER_ERROR
    assert up.calls == []


@schema_only
def test_review_history_call_shape_schema_only(up):
    # the upstream tool failed (MT-UPSTREAM-DOWN) when probed: only the argument shape is checked,
    # and the recorded error reply is what surfaces
    with pytest.raises(CliError) as e:
        api.review_history("o/r", "src/a.py", limit=5)
    assert "MT-UPSTREAM-DOWN" in e.value.message
    assert up.calls == [
        ("pull-request-review-history", {"repo": "o/r", "file_path": "src/a.py", "limit": 5})
    ]


def test_api_calls_are_refused_when_policy_drops_the_tool(monkeypatch):
    fake = FakeUpstream()
    empty = exposure_mod.parse_policy({"version": 1, "tools": []})
    monkeypatch.setattr(api, "_gateway", lambda: gw.Gateway(policy=empty, api_key=KEY, opener=fake))
    with pytest.raises(CliError):
        api.rules_list()
    assert fake.calls == []


def test_gateway_is_stdlib_only():
    import ast
    import sys
    from pathlib import Path

    for name in ("gateway.py", "exposure.py"):
        tree = ast.parse((Path(gw.__file__).parent / name).read_text())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                mods = [node.module or ""]
            for m in mods:
                top = m.split(".")[0]
                assert top in sys.stdlib_module_names or top == "qodo", (name, m)


# --- review fixes -----------------------------------------------------------------


def test_a_destructive_hint_overrides_a_read_only_hint():
    both = {"readOnlyHint": True, "destructiveHint": True}
    assert not exposure_mod.is_read_only(both)
    assert exposure_mod.is_read_only({"readOnlyHint": True, "destructiveHint": False})
    assert exposure_mod.is_read_only({"readOnlyHint": True})


def test_a_call_from_a_surface_the_policy_does_not_list_is_refused():
    policy = exposure_mod.parse_policy(
        {
            "version": 1,
            "tools": [
                {
                    "tool": "rules-metadata",
                    "verb": "rules metadata",
                    "surfaces": ["mcp"],
                    "allow_write": False,
                }
            ],
        }
    )
    fake = FakeUpstream()
    g = gw.Gateway(policy, api_key=KEY, opener=fake)
    with pytest.raises(CliError) as ei:
        g.call("rules-metadata", surface="api")
    assert ei.value.code == 1 and "surface" in ei.value.message
    assert fake.requests == []  # refused before any request
    assert isinstance(g.call("rules-metadata", surface="mcp"), dict)


def test_the_rules_search_api_is_refused_when_the_policy_does_not_name_rules_search(monkeypatch):
    monkeypatch.setattr(gw._qodo_api, "search_rules", lambda *a, **k: [{"name": "r"}])
    empty = exposure_mod.parse_policy({"version": 1, "tools": []})
    g = gw.Gateway(empty, api_key=KEY, opener=FakeUpstream())
    with pytest.raises(CliError) as ei:
        g.search_rules_api("q")
    assert ei.value.code == 1 and "not exposed" in ei.value.message
