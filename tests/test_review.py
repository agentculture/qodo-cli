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


def test_dedup_prefers_inline() -> None:
    summary = {"title": "Bug X", "kind": "summary"}
    inline = {"title": "bug x", "kind": "inline"}
    out = _providers._dedup([summary, inline])
    assert len(out) == 1
    assert out[0]["kind"] == "inline"


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
            return json.dumps(
                [
                    {
                        "id": 111,
                        "user": {"login": "qodo-ai[bot]"},
                        "body": "SQL injection risk",
                        "path": "a.py",
                        "line": 10,
                        "html_url": "h",
                    },
                    {"id": 112, "user": {"login": "someone"}, "body": "nit"},
                ]
            )
        return "[]"

    with mock.patch("qodo.cli._providers._gh", side_effect=fake_gh):
        comments = _providers.fetch_qodo_comments(5)
    # Two Qodo comments kept (summary + inline); the human/someone ones dropped.
    assert len(comments) == 2
    authors = {c["author"] for c in comments}
    assert authors == {"qodo-merge[bot]", "qodo-ai[bot]"}
    inline = next(c for c in comments if c["kind"] == "inline")
    assert inline["id"] == 111
    assert inline["path"] == "a.py"


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
