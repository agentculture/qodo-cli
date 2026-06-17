"""Tests for ``qodo review`` / ``qodo pr`` and the provider mechanics."""

from __future__ import annotations

import argparse
import json
from unittest import mock

import pytest

from qodo.cli import _providers, main
from qodo.cli._commands import review as _review
from qodo.cli._errors import CliError

_GH_URL = "https://github.com/owner/repo.git"

# A live-shaped Qodo inline comment body (from this repo's own PR #2).
_QODO_BODY = (
    '<img src="https://img.shields.io/badge/Action_required-x" alt="Action required">\n'
    "\n"
    "1\\. Unsigned <b><i>resolve_comment()</i></b> reply body "
    "<code>📜 Skill insight</code> <code>✧ Quality</code>\n"
    "\n"
    "<pre>\n"
    "<b><i>resolve_comment()</i></b> posts reply without the signature line "
    "`- &lt;nick&gt; (Claude)`.\n"
    "</pre>\n"
    "\n"
    "<details>\n"
    "<summary><strong>Agent Prompt</strong></summary>\n"
    "\n"
    "```\n"
    "## Issue description\n"
    "Append the signature.\n"
    "```\n"
    "</details>\n"
)


# --- pure helpers ----------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("git@github.com:o/r.git", "github"),
        ("https://github.com/o/r.git", "github"),
        ("https://gitlab.com/o/r.git", "gitlab"),
        ("https://dev.azure.com/o/p/_git/r", "azure"),
        ("https://bitbucket.org/o/r.git", "bitbucket"),
        ("https://chromium.googlesource.com/r", "gerrit"),
        ("https://example.com/o/r.git", "unknown"),
    ],
)
def test_detect_provider(url: str, expected: str) -> None:
    assert _providers.detect_provider(url) == expected


def test_require_github_passes_for_github() -> None:
    _providers.require_github("github")  # no raise


def test_require_github_rejects_other_provider() -> None:
    with pytest.raises(CliError) as exc:
        _providers.require_github("gitlab")
    assert exc.value.code == 2


def test_require_github_rejects_unknown() -> None:
    with pytest.raises(CliError) as exc:
        _providers.require_github("unknown")
    assert exc.value.code == 2


# --- provider resolution / GitHub Enterprise -------------------------------


@pytest.mark.parametrize(
    "url,host",
    [
        ("https://github.com/o/r.git", "github.com"),
        ("git@github.acme.com:o/r.git", "github.acme.com"),
        ("ssh://git@github.acme.com:22/o/r.git", "github.acme.com"),
        ("https://user@github.acme.com/o/r.git", "github.acme.com"),
        ("", None),
    ],
)
def test_host_from_remote(url: str, host: str | None) -> None:
    assert _providers._host_from_remote(url) == host


def test_resolve_provider_github_com_skips_gh() -> None:
    # github.com resolves without consulting gh at all.
    with mock.patch(
        "qodo.cli._providers.gh_knows_host",
        side_effect=AssertionError("gh must not be consulted for github.com"),
    ):
        assert _providers.resolve_provider("https://github.com/o/r.git") == "github"


def test_resolve_provider_upgrades_ghe_host() -> None:
    # An unknown host that gh is authenticated to is a GitHub Enterprise host.
    with mock.patch("qodo.cli._providers.gh_knows_host", return_value=True):
        assert _providers.resolve_provider("git@github.acme.com:o/r.git") == "github"


def test_resolve_provider_unknown_when_neither_cli_knows_host() -> None:
    with (
        mock.patch("qodo.cli._providers.gh_knows_host", return_value=False),
        mock.patch("qodo.cli._providers.glab_knows_host", return_value=False),
    ):
        assert _providers.resolve_provider("https://git.example.com/o/r.git") == "unknown"


def test_resolve_provider_upgrades_self_hosted_gitlab_host() -> None:
    # An unknown host gh doesn't know but glab is authenticated to is self-hosted GitLab.
    with (
        mock.patch("qodo.cli._providers.gh_knows_host", return_value=False),
        mock.patch("qodo.cli._providers.glab_knows_host", return_value=True),
    ):
        assert _providers.resolve_provider("https://git.company.com/o/r.git") == "gitlab"


def test_resolve_provider_prefers_github_when_both_clis_know_host() -> None:
    # Degenerate tie-break: gh is consulted first, so a host both CLIs know is github.
    with (
        mock.patch("qodo.cli._providers.gh_knows_host", return_value=True),
        mock.patch(
            "qodo.cli._providers.glab_knows_host",
            side_effect=AssertionError("glab must not be consulted once gh claims the host"),
        ),
    ):
        assert _providers.resolve_provider("git@git.both.com:o/r.git") == "github"


def test_resolve_provider_does_not_upgrade_other_providers() -> None:
    # gitlab.com is detected by host and must never be upgraded to github.
    with mock.patch("qodo.cli._providers.gh_knows_host", return_value=True):
        assert _providers.resolve_provider("https://gitlab.com/o/r.git") == "gitlab"


def test_resolve_provider_gitlab_com_skips_glab() -> None:
    # gitlab.com resolves by host without consulting glab at all.
    with mock.patch(
        "qodo.cli._providers.glab_knows_host",
        side_effect=AssertionError("glab must not be consulted for gitlab.com"),
    ):
        assert _providers.resolve_provider("https://gitlab.com/o/r.git") == "gitlab"


def test_gh_knows_host_true_on_zero_exit() -> None:
    with (
        mock.patch("qodo.cli._providers.shutil.which", return_value="/usr/bin/gh"),
        mock.patch("qodo.cli._providers.subprocess.run", return_value=mock.Mock(returncode=0)),
    ):
        assert _providers.gh_knows_host("github.acme.com") is True


def test_gh_knows_host_false_on_nonzero_exit() -> None:
    with (
        mock.patch("qodo.cli._providers.shutil.which", return_value="/usr/bin/gh"),
        mock.patch("qodo.cli._providers.subprocess.run", return_value=mock.Mock(returncode=1)),
    ):
        assert _providers.gh_knows_host("github.acme.com") is False


def test_gh_knows_host_false_when_gh_missing() -> None:
    with mock.patch("qodo.cli._providers.shutil.which", return_value=None):
        assert _providers.gh_knows_host("github.acme.com") is False


def test_glab_knows_host_true_on_zero_exit() -> None:
    with (
        mock.patch("qodo.cli._providers.shutil.which", return_value="/usr/bin/glab"),
        mock.patch("qodo.cli._providers.subprocess.run", return_value=mock.Mock(returncode=0)),
    ):
        assert _providers.glab_knows_host("git.company.com") is True


def test_glab_knows_host_false_on_nonzero_exit() -> None:
    with (
        mock.patch("qodo.cli._providers.shutil.which", return_value="/usr/bin/glab"),
        mock.patch("qodo.cli._providers.subprocess.run", return_value=mock.Mock(returncode=1)),
    ):
        assert _providers.glab_knows_host("git.company.com") is False


def test_glab_knows_host_false_when_glab_missing() -> None:
    with mock.patch("qodo.cli._providers.shutil.which", return_value=None):
        assert _providers.glab_knows_host("git.company.com") is False


@pytest.mark.parametrize(
    "login,expected",
    [
        ("qodo-code-review", True),  # gh pr view spelling (no [bot])
        ("qodo-code-review[bot]", True),  # gh api spelling (with [bot])
        ("qodo-merge[bot]", True),
        ("qodo-ai", True),
        ("pr-agent-pro", True),
        ("pr-agent-pro-staging", True),
        ("a-human", False),
        ("", False),
    ],
)
def test_is_qodo_normalizes_bot_suffix(login: str, expected: bool) -> None:
    # gh's two surfaces disagree on the `[bot]` suffix; both must match.
    assert _providers._is_qodo(login) is expected


def test_title_from_body() -> None:
    assert _providers._title("## SQL injection risk\nmore") == "SQL injection risk"
    assert _providers._title("\n\n") == "(no title)"


def test_title_skips_badge_and_extracts_real_title() -> None:
    # Real Qodo shape: an <img> badge line, then the numbered, marked-up title
    # with trailing <code> category chips. The badge must be skipped.
    body = (
        '<img src="https://img.shields.io/badge/Action_required-x" alt="Action required">\n'
        "\n"
        "1\\. Unsigned <b><i>resolve_comment()</i></b> reply body "
        "<code>📜 Skill insight</code> <code>✧ Quality</code>\n"
    )
    assert _providers._title(body) == "Unsigned resolve_comment() reply body"


def test_title_strips_h3_summary_header() -> None:
    assert _providers._title("<h3>Code Review by Qodo</h3>") == "Code Review by Qodo"


# --- structured-field parsing (issue #3) -----------------------------------


def test_parse_body_extracts_structured_fields() -> None:
    parsed = _providers._parse_body(_QODO_BODY)
    assert parsed["severity"] == "HIGH"  # "Action required" badge
    assert parsed["type"] == "Skill insight"  # first chip, emoji stripped
    assert parsed["categories"] == ["Skill insight", "Quality"]
    assert "signature line" in parsed["description"]
    assert "&lt;" not in parsed["description"]  # HTML entities decoded
    assert "<nick>" in parsed["description"]
    assert "## Issue description" in parsed["agent_prompt"]


def test_severity_maps_badges() -> None:
    review = '<img alt="Review recommended">'
    other = '<img alt="Something else">'
    assert _providers._severity(review) == "MEDIUM"
    assert _providers._severity(other) == "LOW"  # badge present but unknown
    assert _providers._severity("no badge here") is None  # no badge -> None


def test_parse_body_degrades_to_none_on_plain_text() -> None:
    parsed = _providers._parse_body("just a plain title line")
    assert parsed["severity"] is None
    assert parsed["type"] is None
    assert parsed["categories"] == []
    assert parsed["description"] is None
    assert parsed["agent_prompt"] is None


def test_normalize_inline_carries_parsed_fields() -> None:
    raw = {"id": 1, "user": {"login": "qodo-ai[bot]"}, "body": _QODO_BODY, "html_url": "h"}
    norm = _providers._normalize_inline(raw)
    assert norm["severity"] == "HIGH"
    assert norm["type"] == "Skill insight"


def test_dedup_keeps_distinct_inline_comments() -> None:
    # Qodo inline bodies share a leading badge line -> identical titles; keyed on
    # id, distinct comments must NOT collapse (this is the under-report bug fix).
    a = {"id": 1, "kind": "inline", "title": "Action required"}
    b = {"id": 2, "kind": "inline", "title": "Action required"}
    out = _providers._dedup([a, b])
    assert len(out) == 2


def test_dedup_drops_true_duplicate_by_id() -> None:
    a = {"id": 1, "kind": "inline", "title": "x"}
    dup = {"id": 1, "kind": "inline", "title": "x (refetched)"}
    out = _providers._dedup([a, dup])
    assert len(out) == 1
    assert out[0]["title"] == "x"  # first occurrence wins


def test_dedup_keeps_distinct_summaries_by_url() -> None:
    a = {"id": None, "url": "u1", "kind": "summary", "title": "Code Review by Qodo"}
    b = {"id": None, "url": "u2", "kind": "summary", "title": "PR Summary by Qodo"}
    out = _providers._dedup([a, b])
    assert len(out) == 2


def test_require_tool_missing_raises() -> None:
    with mock.patch("qodo.cli._providers.shutil.which", return_value=None):
        with pytest.raises(CliError) as exc:
            _providers.require_tool("gh")
    assert exc.value.code == 2


# --- fetch / find / resolve (with _gh stubbed) -----------------------------


def test_fetch_qodo_comments_filters_and_dedups() -> None:
    def fake_gh(*args: str) -> str:
        if args[:2] == ("pr", "view"):
            return json.dumps(
                {
                    "comments": [
                        {
                            "author": {"login": "qodo-merge[bot]"},
                            "body": "Summary issue",
                            "url": "u",
                        },
                        {"author": {"login": "a-human"}, "body": "looks good", "url": "u2"},
                    ]
                }
            )
        if args[0] == "api":
            # Two distinct Qodo inline comments that BOTH open with the same
            # badge line — the exact shape that used to collapse to one.
            badge = '<img alt="Action required">\n\n'
            return json.dumps(
                [
                    {
                        "id": 111,
                        "user": {"login": "qodo-ai[bot]"},
                        "body": badge + "1\\. SQL injection risk",
                        "path": "a.py",
                        "line": 10,
                        "html_url": "h1",
                    },
                    {
                        "id": 222,
                        "user": {"login": "qodo-code-review[bot]"},
                        "body": badge + "2\\. Missing input validation",
                        "path": "b.py",
                        "line": 5,
                        "html_url": "h2",
                    },
                    {"id": 333, "user": {"login": "someone"}, "body": "nit"},
                ]
            )
        return "[]"

    with mock.patch("qodo.cli._providers._gh", side_effect=fake_gh):
        comments = _providers.fetch_qodo_comments(5)
    # 1 summary + 2 inline kept; human/someone dropped. The two same-badge
    # inline comments must both survive (the under-report bug).
    assert len(comments) == 3
    authors = {c["author"] for c in comments}
    assert authors == {"qodo-merge[bot]", "qodo-ai[bot]", "qodo-code-review[bot]"}
    inline_titles = {c["title"] for c in comments if c["kind"] == "inline"}
    assert inline_titles == {"SQL injection risk", "Missing input validation"}


def test_find_open_pr_returns_first() -> None:
    with mock.patch(
        "qodo.cli._providers._gh",
        return_value=json.dumps([{"number": 7, "title": "t", "url": "u"}]),
    ):
        assert _providers.find_open_pr("branch")["number"] == 7


def test_find_open_pr_none_when_empty() -> None:
    with mock.patch("qodo.cli._providers._gh", return_value="[]"):
        assert _providers.find_open_pr("branch") is None


def test_resolve_comment_reply_then_react() -> None:
    with mock.patch("qodo.cli._providers._gh", return_value="") as gh:
        actions = _providers.resolve_comment(5, 111, reply="fixed", resolve_thread=False)
    assert [a["action"] for a in actions] == ["reply", "acknowledge (+1)"]
    assert all(a["ok"] for a in actions)
    assert gh.call_count == 2


def test_resolve_comment_react_only() -> None:
    with mock.patch("qodo.cli._providers._gh", return_value="") as gh:
        actions = _providers.resolve_comment(5, 111, resolve_thread=False)
    assert [a["action"] for a in actions] == ["acknowledge (+1)"]
    assert all(a["ok"] for a in actions)
    assert gh.call_count == 1


def test_attempt_catches_non_clierror() -> None:
    # The "never raises" contract must hold for ANY exception, not just CliError
    # (e.g. malformed gh JSON -> json.JSONDecodeError, or a vanished gh -> OSError).
    def boom() -> None:
        raise RuntimeError("gh exploded")

    result = _providers._attempt("acknowledge (+1)", boom)
    assert result["ok"] is False
    assert "RuntimeError" in result["detail"]


def test_resolve_comment_thread_lookup_non_clierror_is_reported() -> None:
    # A non-CliError from the thread lookup must not crash resolve (best-effort).
    def fake_gh(*args: str) -> str:
        if "reactions" in args[1]:
            return ""  # the +1 succeeds
        raise ValueError("malformed graphql")  # owner/name or threads lookup blows up

    with mock.patch("qodo.cli._providers._gh", side_effect=fake_gh):
        actions = _providers.resolve_comment(5, 111, resolve_thread=True)
    thread_action = next(a for a in actions if a["action"] == "resolve thread")
    assert thread_action["ok"] is False
    assert "ValueError" in thread_action["detail"]


def test_resolve_comment_partial_success_when_ack_fails() -> None:
    # The reply lands but the +1 reaction fails -> reported per-action, not a
    # blanket failure (issue #5). _gh raises only on the reaction call.
    def fake_gh(*args: str) -> str:
        if "reactions" in args[1]:
            raise CliError(code=2, message="reaction boom", remediation="x")
        return ""

    with mock.patch("qodo.cli._providers._gh", side_effect=fake_gh):
        actions = _providers.resolve_comment(5, 111, reply="fixed", resolve_thread=False)
    by_action = {a["action"]: a for a in actions}
    assert by_action["reply"]["ok"] is True
    assert by_action["acknowledge (+1)"]["ok"] is False
    assert "boom" in by_action["acknowledge (+1)"]["detail"]


# --- GraphQL review-thread resolution (issue #4) ---------------------------


def _threads_payload(*, resolved: bool = False) -> str:
    return json.dumps(
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "id": "THREAD_NODE_1",
                                    "isResolved": resolved,
                                    "comments": {"nodes": [{"databaseId": 111}]},
                                }
                            ],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                }
            }
        }
    )


def test_thread_for_comment_maps_comment_to_thread_node_id() -> None:
    def fake_gh(*args: str) -> str:
        if args[:2] == ("repo", "view"):
            return json.dumps({"owner": {"login": "o"}, "name": "r"})
        return _threads_payload()

    with mock.patch("qodo.cli._providers._gh", side_effect=fake_gh):
        thread_id, already = _providers.thread_for_comment(5, 111)
    assert thread_id == "THREAD_NODE_1"
    assert already is False


def test_thread_for_comment_none_when_unmatched() -> None:
    def fake_gh(*args: str) -> str:
        if args[:2] == ("repo", "view"):
            return json.dumps({"owner": {"login": "o"}, "name": "r"})
        return _threads_payload()

    with mock.patch("qodo.cli._providers._gh", side_effect=fake_gh):
        thread_id, already = _providers.thread_for_comment(5, 999)
    assert thread_id is None


def test_resolve_comment_resolves_thread_via_graphql() -> None:
    calls: list[tuple[str, ...]] = []

    def fake_gh(*args: str) -> str:
        calls.append(args)
        if args[:2] == ("repo", "view"):
            return json.dumps({"owner": {"login": "o"}, "name": "r"})
        if args[:2] == ("api", "graphql") and "resolveReviewThread" in args[3]:
            return json.dumps({"data": {"resolveReviewThread": {"thread": {"isResolved": True}}}})
        if args[:2] == ("api", "graphql"):
            return _threads_payload()
        return ""  # the +1 reaction

    with mock.patch("qodo.cli._providers._gh", side_effect=fake_gh):
        actions = _providers.resolve_comment(5, 111, resolve_thread=True)
    by_action = {a["action"]: a for a in actions}
    assert by_action["resolve thread"]["ok"] is True
    assert any("resolveReviewThread" in c[3] for c in calls if c[:2] == ("api", "graphql"))


def test_resolve_comment_thread_fallback_when_no_thread() -> None:
    # No thread matches the comment -> the +1 reaction stands as the marker. The
    # documented reaction-only fallback is a SUCCESS (ok=True, fallback=True), so
    # it does not flip the batch exit code; only a real resolve error is ok=False.
    def fake_gh(*args: str) -> str:
        if args[:2] == ("repo", "view"):
            return json.dumps({"owner": {"login": "o"}, "name": "r"})
        if args[:2] == ("api", "graphql"):
            return _threads_payload()  # only comment 111 is in a thread
        return ""

    with mock.patch("qodo.cli._providers._gh", side_effect=fake_gh):
        actions = _providers.resolve_comment(5, 222, resolve_thread=True)
    thread_action = next(a for a in actions if a["action"] == "resolve thread")
    assert thread_action["ok"] is True
    assert thread_action["fallback"] is True
    assert "no review thread" in thread_action["detail"]


def test_resolve_no_thread_fallback_keeps_exit_zero(capsys: pytest.CaptureFixture[str]) -> None:
    # End-to-end: a comment whose thread doesn't map must still exit 0 when the
    # +1 reaction succeeded (the reaction-only fallback), not exit 1.
    canned = [{"id": 222, "kind": "inline", "severity": "HIGH", "title": "x"}]

    def fake_gh(*args: str) -> str:
        if args[:2] == ("repo", "view"):
            return json.dumps({"owner": {"login": "o"}, "name": "r"})
        if args[:2] == ("api", "graphql"):
            return _threads_payload()  # 222 is NOT in any thread
        return ""

    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GH_URL),
        mock.patch("qodo.cli._providers.fetch_qodo_comments", return_value=canned),
        mock.patch("qodo.cli._providers._gh", side_effect=fake_gh),
    ):
        rc = main(["review", "resolve", "222", "--pr", "5", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True


# --- the `review` / `pr` command surface -----------------------------------


def test_review_list_json(capsys: pytest.CaptureFixture[str]) -> None:
    canned = [
        {
            "id": 1,
            "kind": "inline",
            "author": "qodo-ai[bot]",
            "title": "X",
            "path": "a.py",
            "line": 2,
        }
    ]
    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GH_URL),
        mock.patch("qodo.cli._providers.fetch_qodo_comments", return_value=canned),
    ):
        rc = main(["review", "list", "--pr", "5", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 1
    assert out["pr"]["number"] == 5


def test_review_list_text(capsys: pytest.CaptureFixture[str]) -> None:
    canned = [
        {
            "id": 1,
            "kind": "inline",
            "author": "qodo-ai[bot]",
            "title": "SQLi",
            "path": "a.py",
            "line": 2,
        }
    ]
    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GH_URL),
        mock.patch("qodo.cli._providers.fetch_qodo_comments", return_value=canned),
    ):
        rc = main(["review", "list", "--pr", "5"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PR #5" in out
    assert "SQLi" in out


def test_review_list_kind_inline_hides_summaries(capsys: pytest.CaptureFixture[str]) -> None:
    canned = [
        {"id": 1, "kind": "inline", "title": "real", "severity": "HIGH", "type": "Bug"},
        {"id": None, "kind": "summary", "title": "rollup", "severity": None, "type": None},
    ]
    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GH_URL),
        mock.patch("qodo.cli._providers.fetch_qodo_comments", return_value=canned),
    ):
        rc = main(["review", "list", "--pr", "5", "--kind", "inline", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["count"] == 1
    assert out["comments"][0]["kind"] == "inline"


def test_review_list_text_shows_severity_and_type(capsys: pytest.CaptureFixture[str]) -> None:
    canned = [{"id": 1, "kind": "inline", "title": "T", "severity": "HIGH", "type": "Bug"}]
    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GH_URL),
        mock.patch("qodo.cli._providers.fetch_qodo_comments", return_value=canned),
    ):
        rc = main(["review", "list", "--pr", "5"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "HIGH" in out
    assert "Bug" in out


def test_pr_alias_lists(capsys: pytest.CaptureFixture[str]) -> None:
    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GH_URL),
        mock.patch("qodo.cli._providers.fetch_qodo_comments", return_value=[]),
    ):
        rc = main(["pr", "list", "--pr", "9"])
    assert rc == 0
    assert "No Qodo review comments" in capsys.readouterr().out


def test_review_list_no_pr_for_branch(capsys: pytest.CaptureFixture[str]) -> None:
    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GH_URL),
        mock.patch("qodo.cli._providers.current_branch", return_value="feat/x"),
        mock.patch("qodo.cli._providers.find_open_pr", return_value=None),
    ):
        rc = main(["review", "list"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no open PR" in err
    assert "hint:" in err


def test_review_list_works_on_github_enterprise(capsys: pytest.CaptureFixture[str]) -> None:
    # A GHE remote (arbitrary host) that gh is authenticated to should work.
    with (
        mock.patch("qodo.cli._providers.remote_url", return_value="git@github.acme.com:o/r.git"),
        mock.patch("qodo.cli._providers.gh_knows_host", return_value=True),
        mock.patch("qodo.cli._providers.fetch_qodo_comments", return_value=[]),
    ):
        rc = main(["review", "list", "--pr", "5"])
    assert rc == 0
    assert "No Qodo review comments" in capsys.readouterr().out


def test_review_list_unsupported_provider_exits_env_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Azure is recognised but not wired -> clear env error (gitlab IS wired now).
    with mock.patch(
        "qodo.cli._providers.remote_url", return_value="https://dev.azure.com/o/p/_git/r"
    ):
        rc = main(["review", "list", "--pr", "5"])
    assert rc == 2
    assert "not wired yet" in capsys.readouterr().err


def test_review_resolve_json(capsys: pytest.CaptureFixture[str]) -> None:
    ok_actions = [{"action": "acknowledge (+1)", "ok": True, "detail": ""}]
    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GH_URL),
        mock.patch("qodo.cli._providers.resolve_comment", return_value=ok_actions),
    ):
        rc = main(["review", "resolve", "111", "--pr", "5", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["resolved"][0]["comment_id"] == 111
    assert out["resolved"][0]["actions"] == ok_actions


def test_review_resolve_partial_exit_one(capsys: pytest.CaptureFixture[str]) -> None:
    # An action failed -> overall ok False, exit 1, but the success is still shown.
    mixed = [
        {"action": "reply", "ok": True, "detail": ""},
        {"action": "acknowledge (+1)", "ok": False, "detail": "boom"},
    ]
    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GH_URL),
        mock.patch("qodo.cli._providers.resolve_comment", return_value=mixed),
    ):
        rc = main(["review", "resolve", "111", "--pr", "5"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "[ok] reply" in out
    assert "[fail] acknowledge (+1)" in out


def test_review_resolve_all_batches_inline(capsys: pytest.CaptureFixture[str]) -> None:
    canned = [
        {"id": 11, "kind": "inline", "severity": "HIGH", "title": "a"},
        {"id": 12, "kind": "inline", "severity": "LOW", "title": "b"},
        {"id": None, "kind": "summary", "severity": None, "title": "rollup"},
    ]
    resolved_ids: list[int] = []

    def fake_resolve(pr: int, cid: int, **kw: object) -> list[dict]:
        resolved_ids.append(cid)
        return [{"action": "acknowledge (+1)", "ok": True, "detail": ""}]

    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GH_URL),
        mock.patch("qodo.cli._providers.fetch_qodo_comments", return_value=canned),
        mock.patch("qodo.cli._providers.review_threads", return_value=[]) as threads,
        mock.patch("qodo.cli._providers.resolve_comment", side_effect=fake_resolve),
    ):
        rc = main(["review", "resolve", "--all", "--pr", "5", "--json"])
    assert rc == 0
    # both inline comments resolved; the summary (id None) skipped
    assert sorted(resolved_ids) == [11, 12]
    assert json.loads(capsys.readouterr().out)["count"] == 2
    # threads are pre-fetched ONCE for the batch, not once per comment (N+1 fix)
    assert threads.call_count == 1


def test_review_resolve_severity_filter(capsys: pytest.CaptureFixture[str]) -> None:
    canned = [
        {"id": 11, "kind": "inline", "severity": "HIGH", "title": "a"},
        {"id": 12, "kind": "inline", "severity": "LOW", "title": "b"},
    ]
    resolved_ids: list[int] = []

    def fake_resolve(pr: int, cid: int, **kw: object) -> list[dict]:
        resolved_ids.append(cid)
        return [{"action": "acknowledge (+1)", "ok": True, "detail": ""}]

    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GH_URL),
        mock.patch("qodo.cli._providers.fetch_qodo_comments", return_value=canned),
        mock.patch("qodo.cli._providers.resolve_comment", side_effect=fake_resolve),
    ):
        rc = main(["review", "resolve", "--severity", "high", "--pr", "5"])
    assert rc == 0
    assert resolved_ids == [11]  # case-insensitive severity match


def test_review_resolve_invalid_severity_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    # A typo'd --severity must fail loudly at parse time (structured error, exit 1),
    # NOT silently select nothing and exit 0.
    with pytest.raises(SystemExit) as exc:
        main(["review", "resolve", "--severity", "HGIH", "--pr", "5"])
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "invalid severity" in err
    assert "hint:" in err


def test_review_resolve_rejects_ids_and_all() -> None:
    with mock.patch("qodo.cli._providers.remote_url", return_value=_GH_URL):
        rc = main(["review", "resolve", "111", "--all", "--pr", "5"])
    assert rc == 1  # user error: ids OR filters, not both


def test_review_resolve_no_selection_errors(capsys: pytest.CaptureFixture[str]) -> None:
    with mock.patch("qodo.cli._providers.remote_url", return_value=_GH_URL):
        rc = main(["review", "resolve", "--pr", "5"])
    assert rc == 1
    assert "hint:" in capsys.readouterr().err


def test_review_resolve_no_thread_flag_skips_graphql() -> None:
    captured: dict[str, object] = {}

    def fake_resolve(pr: int, cid: int, **kw: object) -> list[dict]:
        captured.update(kw)
        return [{"action": "acknowledge (+1)", "ok": True, "detail": ""}]

    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GH_URL),
        mock.patch("qodo.cli._providers.resolve_comment", side_effect=fake_resolve),
    ):
        rc = main(["review", "resolve", "111", "--pr", "5", "--no-resolve-thread"])
    assert rc == 0
    assert captured["resolve_thread"] is False


# --- signing (issue #6) ----------------------------------------------------


def test_sign_appends_nick_signature_once() -> None:
    args = argparse.Namespace(reply="Fixed in abc123.", sign=True)
    with mock.patch(
        "qodo.cli._commands.review.read_agent_fields", return_value={"nick": "qodo-cli"}
    ):
        signed = _review._maybe_sign(args)
    assert signed.endswith("- qodo-cli (Claude)")
    assert signed.count("- qodo-cli (Claude)") == 1


def test_sign_is_idempotent_when_already_signed() -> None:
    body = "Fixed.\n\n- qodo-cli (Claude)"
    args = argparse.Namespace(reply=body, sign=True)
    with mock.patch(
        "qodo.cli._commands.review.read_agent_fields", return_value={"nick": "qodo-cli"}
    ):
        signed = _review._maybe_sign(args)
    assert signed == body  # duplicate-guarded


def test_no_sign_leaves_reply_unchanged() -> None:
    args = argparse.Namespace(reply="Fixed.", sign=False)
    assert _review._maybe_sign(args) == "Fixed."


def test_sign_without_reply_errors() -> None:
    args = argparse.Namespace(reply=None, sign=True)
    with pytest.raises(CliError) as exc:
        _review._maybe_sign(args)
    assert exc.value.code == 1


def test_review_overview(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["review", "overview"])
    assert rc == 0
    assert "qodo-pr-resolver" in capsys.readouterr().out


# --- GitLab provider (issue #10) -------------------------------------------

_GL_URL = "https://gitlab.com/group/proj.git"


def test_require_provider_allows_github_and_gitlab() -> None:
    _providers.require_provider("github")  # no raise
    _providers.require_provider("gitlab")  # no raise


@pytest.mark.parametrize("provider", ["azure", "bitbucket", "gerrit"])
def test_require_provider_rejects_unwired(provider: str) -> None:
    with pytest.raises(CliError) as exc:
        _providers.require_provider(provider)
    assert exc.value.code == 2
    assert "not wired yet" in exc.value.message


def test_require_provider_rejects_unknown() -> None:
    with pytest.raises(CliError) as exc:
        _providers.require_provider("unknown")
    assert exc.value.code == 2
    assert "could not identify" in exc.value.message


def test_gitlab_find_open_pr() -> None:
    payload = json.dumps([{"iid": 42, "title": "MR title", "web_url": "https://gl/mr/42"}])
    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GL_URL),
        mock.patch("qodo.cli._providers._glab", return_value=payload),
    ):
        mr = _providers.gitlab_find_open_pr("feat/x")
    assert mr == {"number": 42, "title": "MR title", "url": "https://gl/mr/42"}


def test_gitlab_find_open_pr_none_when_empty() -> None:
    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GL_URL),
        mock.patch("qodo.cli._providers._glab", return_value="[]"),
    ):
        assert _providers.gitlab_find_open_pr("feat/x") is None


def test_gitlab_fetch_qodo_comments_filters_and_parses() -> None:
    badge = '<img alt="Action required">\n\n'
    discussions = json.dumps(
        [
            {
                "id": "disc-1",
                "notes": [
                    {
                        "id": 1001,
                        "author": {"username": "qodo-merge"},
                        "body": badge + "1\\. SQLi risk <code>📘 Rule violation</code>",
                        "position": {"new_path": "a.py", "new_line": 10},
                        "resolved": False,
                    }
                ],
            },
            {
                "id": "disc-2",
                "notes": [
                    {"id": 1002, "author": {"username": "a-human"}, "body": "lgtm"},
                    {"id": 1003, "author": {"username": "system"}, "system": True, "body": "x"},
                ],
            },
        ]
    )
    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GL_URL),
        mock.patch("qodo.cli._providers._glab", return_value=discussions),
    ):
        comments = _providers.gitlab_fetch_qodo_comments(42)
    assert len(comments) == 1  # only the qodo note kept; human + system dropped
    c = comments[0]
    assert c["id"] == 1001
    assert c["discussion_id"] == "disc-1"
    assert c["kind"] == "inline"
    assert c["severity"] == "HIGH"
    assert c["type"] == "Rule violation"
    assert c["path"] == "a.py"


def test_gitlab_resolve_comment_replies_and_resolves_discussion() -> None:
    discussions = json.dumps([{"id": "disc-9", "notes": [{"id": 5001, "resolved": False}]}])
    calls: list[tuple[str, ...]] = []

    def fake_glab(*args: str) -> str:
        calls.append(args)
        if "discussions?per_page" in args[1]:
            return discussions
        return ""

    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GL_URL),
        mock.patch("qodo.cli._providers._glab", side_effect=fake_glab),
    ):
        actions = _providers.gitlab_resolve_comment(42, 5001, reply="fixed", resolve_thread=True)
    by_action = {a["action"]: a for a in actions}
    assert by_action["reply"]["ok"] is True
    assert by_action["resolve thread"]["ok"] is True
    # the resolve issued a PUT resolved=true on the note's discussion
    assert any("discussions/disc-9" in c[1] and "PUT" in c for c in calls if c[0] == "api")


def test_gitlab_resolve_comment_no_discussion_for_note() -> None:
    discussions = json.dumps([{"id": "disc-9", "notes": [{"id": 1, "resolved": False}]}])
    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GL_URL),
        mock.patch("qodo.cli._providers._glab", return_value=discussions),
    ):
        actions = _providers.gitlab_resolve_comment(42, 999, resolve_thread=True)
    assert actions[0]["action"] == "lookup discussion"
    assert actions[0]["ok"] is False


def test_review_list_works_on_gitlab(capsys: pytest.CaptureFixture[str]) -> None:
    discussions = json.dumps(
        [
            {
                "id": "d1",
                "notes": [
                    {
                        "id": 7,
                        "author": {"username": "qodo-ai"},
                        "body": '<img alt="Review recommended">\n\n1\\. Tighten this',
                        "position": {"new_path": "x.py", "new_line": 3},
                    }
                ],
            }
        ]
    )
    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GL_URL),
        mock.patch("qodo.cli._providers._glab", return_value=discussions),
    ):
        rc = main(["review", "list", "--pr", "42", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["provider"] == "gitlab"
    assert out["count"] == 1
    assert out["comments"][0]["severity"] == "MEDIUM"


def test_gitlab_prefetch_threads_returns_discussions() -> None:
    discussions = json.dumps([{"id": "d1", "notes": [{"id": 7}]}])
    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GL_URL),
        mock.patch("qodo.cli._providers._glab", return_value=discussions),
    ):
        out = _providers.prefetch_threads("gitlab", 42)
    assert out == [{"id": "d1", "notes": [{"id": 7}]}]


def test_gitlab_resolve_uses_prefetched_discussions_no_refetch() -> None:
    # With a pre-fetched discussion pool, the lookup must NOT re-GET discussions
    # (the batch N+1 fix mirroring GitHub's thread prefetch).
    discussions = [{"id": "d1", "notes": [{"id": 7, "resolved": False}]}]
    calls: list[tuple[str, ...]] = []

    def fake_glab(*args: str) -> str:
        calls.append(args)
        return ""

    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GL_URL),
        mock.patch("qodo.cli._providers._glab", side_effect=fake_glab),
    ):
        actions = _providers.gitlab_resolve_comment(42, 7, resolve_thread=True, threads=discussions)
    by_action = {a["action"]: a for a in actions}
    assert by_action["resolve thread"]["ok"] is True
    assert not any("discussions?per_page" in c[1] for c in calls)  # no re-fetch


def test_review_resolve_works_on_gitlab(capsys: pytest.CaptureFixture[str]) -> None:
    discussions = json.dumps([{"id": "d1", "notes": [{"id": 7, "resolved": False}]}])

    def fake_glab(*args: str) -> str:
        if "discussions?per_page" in args[1]:
            return discussions
        return ""

    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GL_URL),
        mock.patch("qodo.cli._providers._glab", side_effect=fake_glab),
    ):
        rc = main(["review", "resolve", "7", "--pr", "42", "--reply", "done", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["resolved"][0]["comment_id"] == 7
