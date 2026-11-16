"""The CLI verbs the registry yields: --json, CliError failures, explain, learn, overview."""

from __future__ import annotations

import json

import pytest

from qodo import api
from qodo import gateway as gw
from qodo import registry as reg
from qodo.cli import main
from qodo.explain import resolve
from tests.fake_qodo_mcp import FakeUpstream

KEY = "sk-test-SECRET-key-123"
PR = "https://github.com/o/r/pull/1"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("QODO_API_KEY", KEY)


@pytest.fixture
def up(monkeypatch):
    fake = FakeUpstream()
    monkeypatch.setattr(api, "_gateway", lambda: gw.Gateway(api_key=KEY, opener=fake))
    return fake


def run(capsys, *argv):
    rc = main(list(argv))
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def cli_verbs():
    return [v for v in reg.default_registry().verbs() if "cli" in v.surfaces]


# --- every registry verb is a CLI command ---------------------------------------------


def test_every_cli_verb_has_a_help_screen_with_json(capsys):
    assert cli_verbs()
    for v in cli_verbs():
        with pytest.raises(SystemExit) as exit_:
            main([v.noun, v.name, "--help"])
        assert exit_.value.code == 0, v.verb
        assert "--json" in capsys.readouterr().out, v.verb


def test_every_cli_verb_and_noun_overview_has_an_explain_entry():
    for v in cli_verbs():
        assert resolve((v.noun, v.name)), v.verb
        assert resolve((v.noun,)), v.noun
        assert resolve((v.noun, "overview")), v.noun


def test_learn_and_overview_mention_every_cli_verb(capsys):
    _, learn_text, _ = run(capsys, "learn")
    _, learn_json, _ = run(capsys, "learn", "--json")
    _, ov_text, _ = run(capsys, "overview")
    commands = json.loads(learn_json)["commands"]
    paths = {tuple(c["path"]) for c in commands}
    for v in cli_verbs():
        assert f"{v.noun} {v.name}" in learn_text, v.verb
        assert (v.noun, v.name) in paths, v.verb
        assert f"{v.noun} {v.name}" in ov_text, v.verb


def test_each_new_noun_has_a_noun_overview(capsys):
    for noun in ("findings", "history"):
        rc, out, _ = run(capsys, noun, "overview", "--json")
        assert rc == 0
        payload = json.loads(out)
        assert payload["subject"] == f"qodo-cli {noun}"
        items = " ".join(i for s in payload["sections"] for i in s["items"])
        for v in cli_verbs():
            if v.noun == noun:
                assert f"{noun} {v.name}" in items
        rc, out, _ = run(capsys, noun)  # a bare noun prints its overview
        assert rc == 0 and out.strip()


def test_rules_and_config_overviews_list_the_registry_verbs(capsys):
    _, out, _ = run(capsys, "rules", "overview")
    for name in ("rules get", "rules search", "rules list", "rules show", "rules metadata"):
        assert name in out
    _, out, _ = run(capsys, "config", "overview")
    assert "config propose" in out


# --- running the generated verbs ------------------------------------------------------------


def test_findings_list_json(up, capsys):
    rc, out, err = run(capsys, "findings", "list", "--pr-url", PR, "--json")
    assert rc == 0 and err == ""
    assert json.loads(out)["finding_count"] == 2
    assert up.calls == [("get-pr-review-findings", {"pr_url": PR})]


def test_findings_list_text(up, capsys):
    rc, out, _ = run(capsys, "findings", "list", "--pr-url", PR)
    assert rc == 0 and "00000000-0000-0000-0000-000000000001" in out


def test_findings_list_extended_flag_is_sent(up, capsys):
    run(capsys, "findings", "list", "--pr-url", PR, "--extended", "--json")
    assert up.calls[-1] == ("get-pr-review-findings", {"pr_url": PR, "extended": True})


def test_findings_list_summary(up, capsys):
    rc, out, _ = run(capsys, "findings", "list", "--pr-url", PR, "--summary", "--json")
    assert rc == 0
    payload = json.loads(out)
    assert payload["finding_count"] == 2 and payload["counts"]["by_status"]
    assert all(set(f) <= {"id", "status", "url"} for f in payload["findings"])
    assert up.calls == [("get-pr-review-findings", {"pr_url": PR})]  # summary is not sent
    rc, out, _ = run(capsys, "findings", "list", "--pr-url", PR, "--summary")
    assert (
        rc == 0
        and "example-org/example-repo/pull/1" in out
        and "implemented" in out
        and "description" not in out
    )


def test_findings_list_summary_of_100_findings_is_under_64_kib(monkeypatch, capsys):
    reply = {
        "finding_count": 100,
        "findings": [
            {
                "id": f"id-{i}",
                "attribution_status": ["pending", "dismissed"][i % 2],
                "title": "t" * 300,
                "description": "d" * 4000,
            }
            for i in range(100)
        ],
        "pr_url": PR,
        "review_session": {"status": "completed"},
    }

    class Big:
        def call(self, name, arguments=None, *, surface=None):
            return reply

    monkeypatch.setattr(api, "_gateway", lambda: Big())
    _, full, _ = run(capsys, "findings", "list", "--pr-url", PR, "--json")
    assert len(full.encode()) > 400_000
    for flags in (["--json"], []):
        rc, out, _ = run(capsys, "findings", "list", "--pr-url", PR, "--summary", *flags)
        assert rc == 0 and len(out.encode()) < 64 * 1024
    assert (
        len(
            json.loads(run(capsys, "findings", "list", "--pr-url", PR, "--summary", "--json")[1])[
                "findings"
            ]
        )
        == 100
    )


def test_findings_dismiss_sends_ids_and_reason(up, capsys):
    rc, out, _ = run(
        capsys,
        "findings",
        "dismiss",
        "--finding-ids",
        "a",
        "--finding-ids",
        "b",
        "--reason",
        "deferred",
        "--explanation",
        "later",
        "--json",
    )
    assert rc == 0
    assert up.calls == [
        (
            "pr-review-dismiss",
            {"finding_ids": ["a", "b"], "reason": "deferred", "explanation": "later"},
        )
    ]


def test_findings_implement(up, capsys):
    rc, _, _ = run(capsys, "findings", "implement", "--finding-ids", "a", "--json")
    assert rc == 0 and up.calls == [("pr-review-mark-implemented", {"finding_ids": ["a"]})]


def test_rules_search_list_show_metadata(up, capsys):
    assert run(capsys, "rules", "search", "--query", "Name: x", "--top-k", "3", "--json")[0] == 0
    assert run(capsys, "rules", "list", "--severity", "error", "--page-size", "5", "--json")[0] == 0
    assert run(capsys, "rules", "show", "--rule-id", "7", "--json")[0] == 0
    assert run(capsys, "rules", "metadata", "--json")[0] == 0
    assert up.calls == [
        ("rules-search", {"query": "Name: x", "top_k": 3}),
        ("rules-list", {"severity": "error", "page_size": 5}),
        ("rules-get", {"rule_id": 7}),
        ("rules-metadata", {}),
    ]


def test_history_show_similar_stats(up, capsys):
    # the recorded reply of this tool is the upstream's own error, so the call fails cleanly
    rc, out, err = run(capsys, "history", "show", "--repo", "o/r", "--file-path", "a.py", "--json")
    assert rc == 1 and out == "" and "MT-UPSTREAM-DOWN" in json.loads(err)["message"]
    assert run(capsys, "history", "similar", "--repo", "o/r", "--query", "q", "--json")[0] == 0
    assert run(capsys, "history", "stats", "--repo", "o/r", "--json")[0] == 0
    assert [c[0] for c in up.calls] == [
        "pull-request-review-history",
        "pull-request-similar-suggestions",
        "pull-request-stats",
    ]
    assert up.calls[0][1] == {"repo": "o/r", "file_path": "a.py"}


def test_a_deprecated_upstream_param_has_no_flag(capsys):
    with pytest.raises(SystemExit) as exit_:
        main(["findings", "list", "--pr-url", PR, "--git-provider", "github", "--json"])
    assert exit_.value.code == 1
    assert json.loads(capsys.readouterr().err)["code"] == 1


def test_the_old_rules_get_still_means_the_http_search(monkeypatch, capsys):
    monkeypatch.setattr(api, "rules_get", lambda q, **k: [{"name": "r", "severity": "ERROR"}])
    monkeypatch.setattr(api, "rules_scopes", lambda **k: None)
    rc, out, _ = run(capsys, "rules", "get", "a query", "--json")
    assert rc == 0 and json.loads(out)["rules"][0]["name"] == "r"


# --- failures are CliErrors, never tracebacks ---------------------------------------------------


def test_a_missing_required_flag_is_a_structured_user_error(capsys):
    with pytest.raises(SystemExit) as exit_:
        main(["rules", "show", "--json"])
    rc = exit_.value.code
    err = json.loads(capsys.readouterr().err)
    assert rc == 1 and err["code"] == 1 and err["remediation"]


def test_a_schema_violation_is_a_clierror(up, capsys):
    rc, out, err = run(capsys, "rules", "show", "--rule-id", "0", "--json")
    assert rc == 1 and out == "" and json.loads(err)["code"] == 1
    rc, _, err = run(capsys, "rules", "show", "--rule-id", "0")
    assert rc == 1 and err.startswith("error:") and "hint:" in err
    assert up.calls == []


def test_a_bad_reason_is_refused_before_any_call(up, capsys):
    with pytest.raises(SystemExit) as exit_:
        main(["findings", "dismiss", "--finding-ids", "a", "--reason", "bogus", "--json"])
    assert exit_.value.code == 1 and up.calls == []


def test_an_upstream_failure_is_an_environment_error(monkeypatch, capsys):
    import urllib.error

    fake = FakeUpstream()
    fake.override["initialize"] = urllib.error.URLError("down")
    monkeypatch.setattr(api, "_gateway", lambda: gw.Gateway(api_key=KEY, opener=fake))
    rc, out, err = run(capsys, "rules", "metadata", "--json")
    assert rc == 2 and out == "" and json.loads(err)["code"] == 2


def test_the_cli_calls_the_gateway_for_the_cli_surface(monkeypatch, capsys):
    seen = []

    class G:
        def call(self, name, arguments=None, *, surface=None):
            seen.append(surface)
            return {}

    monkeypatch.setattr(api, "_gateway", lambda: G())
    run(capsys, "rules", "metadata", "--json")
    assert seen == ["cli"]


def test_config_propose_is_a_command_that_says_it_is_not_implemented(capsys):
    rc, out, err = run(capsys, "config", "propose", "--json")
    assert rc == 1 and out == ""
    payload = json.loads(err)
    assert "not implemented" in payload["message"] and payload["remediation"]
    rc, _, err = run(capsys, "config", "propose")
    assert rc == 1 and err.startswith("error:") and "hint:" in err


def test_a_verb_the_policy_does_not_put_on_the_cli_has_no_command(monkeypatch, capsys):
    from qodo import exposure

    policy = exposure.parse_policy(
        {"version": 1, "tools": [{"tool": "rules-get", "verb": "rules show", "surfaces": ["api"]}]}
    )
    monkeypatch.setattr(reg, "default_registry", lambda: reg.build_registry(policy))
    with pytest.raises(SystemExit):
        main(["rules", "show", "--rule-id", "1", "--json"])
    assert "invalid choice" in capsys.readouterr().err
