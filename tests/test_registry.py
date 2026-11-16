"""The verb registry: built from the exposure policy plus the local verbs (plan task t5)."""

from __future__ import annotations

import json

import pytest

from qodo import api
from qodo import exposure as exposure_mod
from qodo import registry as reg
from qodo.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from tests.fake_qodo_mcp import schemas

UPSTREAM_VERBS = {
    "findings list": "get-pr-review-findings",
    "findings implement": "pr-review-mark-implemented",
    "findings dismiss": "pr-review-dismiss",
    "rules search": "rules-search",
    "rules list": "rules-list",
    "rules show": "rules-get",
    "rules metadata": "rules-metadata",
    "history show": "pull-request-review-history",
    "history similar": "pull-request-similar-suggestions",
    "history stats": "pull-request-stats",
}
LOCAL_VERBS = {
    "rules get",
    "review list",
    "review resolve",
    "config show",
    "config validate",
    "config init",
    "config propose",
}
# Upstream tools that write to a pull request or change a rule: the shipped policy names none.
FORBIDDEN_TOOLS = {
    "litegit-post-comment",
    "litegit-post-inline-comment",
    "litegit-post-inline-comments",
    "litegit-approve",
    "litegit-set-labels",
    "litegit-update-description",
    "rules-create",
    "rules-update",
    "rules-bulk",
    "rules-set-scope",
    "rules-set-state",
}
FINDING_WRITES = {"pr-review-mark-implemented", "pr-review-dismiss"}


class StubGateway:
    """Records ``call`` and answers with a canned reply."""

    def __init__(self, reply=None):
        self.reply = reply if reply is not None else {"ok": True}
        self.calls = []

    def call(self, name, arguments=None, *, surface=None):
        self.calls.append((name, dict(arguments or {}), surface))
        return self.reply


@pytest.fixture
def stub(monkeypatch):
    gw = StubGateway()
    monkeypatch.setattr(api, "_gateway", lambda: gw)
    return gw


# --- one registry lists every bridge verb with its params schema -----------------


def test_the_registry_lists_the_policy_verbs_beside_the_local_verbs():
    r = reg.default_registry()
    assert {v.verb for v in r.verbs() if v.kind == "upstream"} == set(UPSTREAM_VERBS)
    assert {v.verb for v in r.verbs() if v.kind == "local"} == LOCAL_VERBS
    for verb, tool in UPSTREAM_VERBS.items():
        assert r.get(*verb.split(" ")).tool == tool


def test_the_contract_names_are_present():
    r = reg.default_registry()
    assert r.get("history", "show").kind == "upstream"  # renamed from "history file"
    assert r.get("rules", "get").kind == "local"  # the older HTTP search keeps its name
    assert r.get("rules", "show").tool == "rules-get"  # upstream rules-get is `rules show`
    assert r.get("history", "file") is None


def test_each_upstream_verb_takes_its_params_from_the_recorded_input_schema():
    recorded = schemas()
    for verb in reg.default_registry().verbs():
        if verb.kind != "upstream":
            continue
        want = recorded[verb.tool]["inputSchema"]
        got = verb.params
        assert got["type"] == "object"
        extra = set(got["properties"]) - set(want["properties"])
        assert extra <= {"summary"}, f"{verb.verb}: unexpected extra params {extra}"
        for name, prop in want["properties"].items():
            assert got["properties"][name] == prop
        assert got["required"] == want.get("required", [])
        assert got["additionalProperties"] is False


def test_findings_list_has_the_local_summary_param():
    props = reg.default_registry().get("findings", "list").params["properties"]
    assert props["summary"]["type"] == "boolean"
    assert "pr_url" in props


def test_every_entry_has_the_registry_shape():
    for v in reg.default_registry().verbs():
        assert v.noun and v.name and v.summary
        assert v.kind in ("upstream", "local")
        assert v.params["type"] == "object" and isinstance(v.params["properties"], dict)
        assert isinstance(v.writes, bool)
        assert v.surfaces and set(v.surfaces) <= {"cli", "api", "mcp"}
        assert callable(v.handler)
        json.dumps(v.params)  # a plain, JSON-ready schema


def test_writes_follow_the_upstream_annotation_and_the_policy():
    policy = exposure_mod.load_policy()
    for verb in reg.default_registry().verbs():
        if verb.kind == "upstream":
            entry = policy.get(verb.tool)
            assert verb.writes is entry.allow_write
            assert verb.surfaces == entry.surfaces


def test_the_snapshot_matches_the_recorded_schemas_for_the_policys_tools():
    snap = reg.load_schema_snapshot()
    recorded = schemas()
    tools = {e.tool for e in exposure_mod.load_policy().entries}
    assert set(snap) == tools
    for name in tools:
        assert snap[name]["inputSchema"] == recorded[name]["inputSchema"]
        assert snap[name]["annotations"] == recorded[name]["annotations"]
        assert snap[name]["description"] == recorded[name]["description"]


def test_the_registry_follows_the_policy_it_is_built_from():
    policy = exposure_mod.parse_policy(
        {
            "version": 1,
            "tools": [
                {"tool": "rules-get", "verb": "rules fetch", "surfaces": ["api"]},
                {"tool": "pull-request-stats", "verb": "history numbers", "surfaces": ["cli"]},
            ],
        }
    )
    r = reg.build_registry(policy)
    assert {v.verb for v in r.verbs() if v.kind == "upstream"} == {
        "rules fetch",
        "history numbers",
    }
    assert r.get("rules", "fetch").surfaces == ("api",)
    assert [v.verb for v in r.on_surface("cli") if v.kind == "upstream"] == ["history numbers"]


def test_a_policy_tool_without_a_recorded_schema_is_an_environment_error():
    policy = exposure_mod.parse_policy(
        {"version": 1, "tools": [{"tool": "brand-new", "verb": "x y", "surfaces": ["cli"]}]}
    )
    with pytest.raises(CliError) as err:
        reg.build_registry(policy)
    assert err.value.code == EXIT_ENV_ERROR and "brand-new" in err.value.message


def test_a_live_schema_listing_can_replace_the_snapshot():
    live = {n: {**t, "inputSchema": {**t["inputSchema"]}} for n, t in schemas().items()}
    live["pull-request-stats"]["inputSchema"] = {
        "type": "object",
        "properties": {"repo": {"type": "string"}, "extra": {"type": "integer"}},
        "required": ["repo"],
    }
    r = reg.build_registry(schemas=live)
    assert "extra" in r.get("history", "stats").params["properties"]


def test_a_duplicate_path_is_refused():
    r = reg.Registry()
    verb = reg.default_registry().get("rules", "get")
    r.add(verb)
    with pytest.raises(ValueError):
        r.add(verb)


# --- the shipped policy writes to no pull request and changes no rule ------------


def test_the_shipped_policy_names_no_pr_write_and_no_rule_change_tool():
    recorded = schemas()
    assert FORBIDDEN_TOOLS <= set(recorded), "the forbidden list names a tool upstream lacks"
    policy = exposure_mod.load_policy()
    named = {e.tool for e in policy.entries}
    assert not named & FORBIDDEN_TOOLS
    # the only allowed writes are the two finding-status tools
    assert {e.tool for e in policy.entries if e.allow_write} == FINDING_WRITES
    assert {t for t in named if not exposure_mod.is_read_only(recorded[t]["annotations"])} == (
        FINDING_WRITES
    )
    # and so the registry has no other upstream write verb
    assert {
        v.tool for v in reg.default_registry().verbs() if v.kind == "upstream" and v.writes
    } == (FINDING_WRITES)


# --- handlers ------------------------------------------------------------------------


def test_an_upstream_handler_calls_the_gateway_for_its_surface(stub):
    verb = reg.default_registry().get("rules", "show")
    out = verb.handler({"rule_id": 7}, surface="mcp")
    assert out == {"ok": True}
    assert stub.calls == [("rules-get", {"rule_id": 7}, "mcp")]
    verb.handler({"rule_id": 7})
    assert stub.calls[-1][2] == "cli"


def test_an_upstream_handler_checks_params_against_the_schema(stub):
    r = reg.default_registry()
    bad = [
        (("rules", "show"), {}),  # a required param is missing
        (("rules", "show"), {"rule_id": 0}),  # below the minimum
        (("rules", "show"), {"rule_id": "7"}),  # wrong type
        (("rules", "show"), {"rule_id": 1, "nope": 1}),  # unknown param
        (("findings", "dismiss"), {"finding_ids": ["a"], "reason": "bogus"}),  # not in the enum
        (("findings", "dismiss"), {"finding_ids": [], "reason": "deferred"}),  # minItems
        (("findings", "dismiss"), {"finding_ids": ["a"] * 101, "reason": "deferred"}),  # maxItems
        (("findings", "list"), {"pr_url": "https://github.com/o/r/pull/1", "extended": "yes"}),
        (("findings", "list"), {"pr_url": "o/r#1"}),  # a PR URL, not a slug
        (("findings", "implement"), {"finding_ids": ["  "]}),  # a blank id
    ]
    for path, params in bad:
        with pytest.raises(CliError) as err:
            r.get(*path).handler(params)
        assert err.value.code == EXIT_USER_ERROR, (path, params)
    assert stub.calls == []  # nothing reached the gateway


def test_the_gateway_is_still_called_with_only_the_given_params(stub):
    r = reg.default_registry()
    r.get("findings", "list").handler({"pr_url": "https://github.com/o/r/pull/1"})
    assert stub.calls == [
        ("get-pr-review-findings", {"pr_url": "https://github.com/o/r/pull/1"}, "cli")
    ]


def test_local_handlers_return_plain_objects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".git").mkdir()
    r = reg.default_registry()
    shown = r.get("config", "show").handler({})
    assert shown["pr_agent"]["present"] is False
    json.dumps(shown)
    assert r.get("config", "validate").handler({})["valid"] is False


def test_rules_get_is_the_older_http_search(monkeypatch):
    seen = {}

    def fake(query, *, top_k, scopes, auto_scope):
        seen.update(query=query, top_k=top_k, scopes=scopes, auto_scope=auto_scope)
        return [{"name": "r"}]

    monkeypatch.setattr(api, "rules_get", fake)
    out = reg.default_registry().get("rules", "get").handler({"query": "q", "scopes": ["a/b"]})
    assert out["rules"] == [{"name": "r"}] and out["count"] == 1
    assert seen["query"] == "q" and seen["scopes"] == ["a/b"]


def test_config_propose_is_registered_and_not_implemented():
    verb = reg.default_registry().get("config", "propose")
    assert verb is not None and verb.kind == "local"
    with pytest.raises(CliError) as err:
        verb.handler({})
    assert "not implemented" in err.value.message and err.value.remediation


# --- findings list --summary -------------------------------------------------------------


def _findings(n: int) -> dict:
    statuses = ["pending", "dismissed", "full_implementation", "not_implemented"]
    return {
        "finding_count": n,
        "findings": [
            {
                "id": f"00000000-0000-0000-0000-{i:012d}",
                "attribution_status": statuses[i % 4],
                "title": "t" * 200,
                "description": "d" * 3000,
                "category": "Correctness",
                "action_level": "remediation_recommended",
                "git_sha": "0" * 40,
                "comment_id": str(i),
            }
            for i in range(n)
        ],
        "pr_url": "https://github.com/o/r/pull/1",
        "review_session": {"status": "completed", "commit_sha": "a" * 40, "run_id": 3},
    }


def test_summary_prints_counts_ids_statuses_and_urls_only(stub):
    stub.reply = _findings(100)
    out = (
        reg.default_registry()
        .get("findings", "list")
        .handler({"pr_url": "https://github.com/o/r/pull/1", "summary": True})
    )
    # the summary flag is local: it is not sent upstream
    assert stub.calls == [
        ("get-pr-review-findings", {"pr_url": "https://github.com/o/r/pull/1"}, "cli")
    ]
    assert out["pr_url"] == "https://github.com/o/r/pull/1"
    assert out["finding_count"] == 100
    assert out["counts"]["by_status"] == {
        "pending": 25,
        "dismissed": 25,
        "full_implementation": 25,
        "not_implemented": 25,
    }
    assert out["counts"]["open"] == 50 and out["counts"]["closed"] == 50
    assert len(out["findings"]) == 100
    for f in out["findings"]:
        assert set(f) <= {"id", "status", "url"}
        assert f["id"] and f["status"]
    text = json.dumps(out)
    assert "ddd" not in text and "ttt" not in text  # no descriptions or titles
    assert len(json.dumps(stub.reply)) > 300_000  # the full reply is far larger


def test_summary_of_100_findings_is_under_64_kib(stub):
    stub.reply = _findings(100)
    out = (
        reg.default_registry()
        .get("findings", "list")
        .handler({"pr_url": "https://github.com/o/r/pull/1", "summary": True})
    )
    assert len(json.dumps(out, ensure_ascii=False).encode("utf-8")) < 64 * 1024


def test_summary_keeps_a_finding_url_when_upstream_gives_one(stub):
    reply = _findings(2)
    reply["findings"][0]["url"] = "https://github.com/o/r/pull/1#discussion_r1"
    stub.reply = reply
    out = (
        reg.default_registry()
        .get("findings", "list")
        .handler({"pr_url": "https://github.com/o/r/pull/1", "summary": True})
    )
    assert out["findings"][0]["url"].endswith("discussion_r1")
    assert "url" not in out["findings"][1]


def test_summary_of_a_pr_with_no_review(stub):
    stub.reply = {
        "finding_count": 0,
        "findings": [],
        "pr_url": "https://github.com/o/r/pull/1",
        "review_session": None,
    }
    out = (
        reg.default_registry()
        .get("findings", "list")
        .handler({"pr_url": "https://github.com/o/r/pull/1", "summary": True})
    )
    assert out["findings"] == [] and out["counts"]["by_status"] == {}


def test_without_summary_the_full_reply_is_returned(stub):
    stub.reply = _findings(3)
    out = (
        reg.default_registry()
        .get("findings", "list")
        .handler({"pr_url": "https://github.com/o/r/pull/1"})
    )
    assert out == stub.reply
