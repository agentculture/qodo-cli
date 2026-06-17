"""Tests for ``qodo review`` / ``qodo pr`` and the provider mechanics."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from qodo.cli import _providers, main
from qodo.cli._errors import CliError

_GH_URL = "https://github.com/owner/repo.git"


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


def test_resolve_provider_unknown_when_gh_does_not_know_host() -> None:
    with mock.patch("qodo.cli._providers.gh_knows_host", return_value=False):
        assert _providers.resolve_provider("https://git.example.com/o/r.git") == "unknown"


def test_resolve_provider_does_not_upgrade_other_providers() -> None:
    # gitlab.com is detected by host and must never be upgraded to github.
    with mock.patch("qodo.cli._providers.gh_knows_host", return_value=True):
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
        actions = _providers.resolve_comment(5, 111, reply="fixed")
    assert actions == ["replied", "acknowledged (+1)"]
    assert gh.call_count == 2


def test_resolve_comment_react_only() -> None:
    with mock.patch("qodo.cli._providers._gh", return_value="") as gh:
        actions = _providers.resolve_comment(5, 111)
    assert actions == ["acknowledged (+1)"]
    assert gh.call_count == 1


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


def test_review_list_non_github_exits_env_error(capsys: pytest.CaptureFixture[str]) -> None:
    with mock.patch("qodo.cli._providers.remote_url", return_value="https://gitlab.com/o/r.git"):
        rc = main(["review", "list", "--pr", "5"])
    assert rc == 2
    assert "not wired yet" in capsys.readouterr().err


def test_review_resolve_json(capsys: pytest.CaptureFixture[str]) -> None:
    with (
        mock.patch("qodo.cli._providers.remote_url", return_value=_GH_URL),
        mock.patch("qodo.cli._providers.resolve_comment", return_value=["acknowledged (+1)"]),
    ):
        rc = main(["review", "resolve", "111", "--pr", "5", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["comment_id"] == 111
    assert out["actions"] == ["acknowledged (+1)"]


def test_review_overview(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["review", "overview"])
    assert rc == 0
    assert "qodo-pr-resolver" in capsys.readouterr().out
