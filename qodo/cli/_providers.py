"""Git-provider mechanics for ``qodo review`` / ``qodo pr``.

Cites ``qodo-ai/qodo-skills`` ``qodo-pr-resolver`` as the behavioral source of
truth: the provider detection, the Qodo bot identities, and the provider-CLI
commands below mirror that skill's ``resources/providers.md`` and ``SKILL.md``.
We drive the user's *existing* provider CLI (``gh``) — reusing its auth, adding
no new credentials — rather than vendoring or forking the skill. See
``docs/qodo-skills-sources.md`` for the provenance ledger.

Scope of this slice: GitHub (``gh``) is wired end to end. Other detected
providers (GitLab/Azure/Bitbucket/Gerrit) are recognised but raise a clear
"not wired yet" error rather than silently misbehaving — the per-provider
commands are captured in the provenance ledger as the follow-up map.

This module owns the deterministic mechanics — detect, find the PR, fetch and
filter the Qodo bot's comments, reply, acknowledge. The *fixing* loop the skill
performs (read files, generate a fix, edit, commit) is the calling agent's job.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - used only with resolved absolute paths, no shell
from typing import Any

from qodo.cli._errors import EXIT_ENV_ERROR, CliError

# Cited from qodo-pr-resolver/SKILL.md — the logins the Qodo reviewer posts under.
QODO_BOT_LOGINS = frozenset(
    {
        "qodo-merge[bot]",
        "qodo-ai[bot]",
        "pr-agent-pro",
        "pr-agent-pro-staging",
    }
)


def require_tool(tool: str) -> str:
    """Resolve ``tool`` to an absolute path on PATH or raise an environment error."""
    path = shutil.which(tool)
    if path is None:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"required tool not found on PATH: {tool}",
            remediation=f"install {tool} and authenticate it (e.g. `{tool} auth login`)",
        )
    return path


def _run(args: list[str]) -> str:
    """Run a resolved-absolute-path command, returning stdout (raise on failure)."""
    # args[0] is always an absolute path (from require_tool), so no partial-path
    # execution and no shell. B603 is project-skipped; B404 noted at import.
    proc = subprocess.run(args, capture_output=True, text=True)  # nosec B603
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"command failed (exit {proc.returncode}): {detail or args[0]}",
            remediation="check the command output above and your provider-CLI auth",
        )
    return proc.stdout


def _git(*args: str) -> str:
    return _run([require_tool("git"), *args])


def _gh(*args: str) -> str:
    return _run([require_tool("gh"), *args])


def current_branch() -> str:
    """The current git branch name."""
    return _git("rev-parse", "--abbrev-ref", "HEAD").strip()


def remote_url() -> str:
    """The ``origin`` remote URL (raises if there is no origin)."""
    return _git("remote", "get-url", "origin").strip()


def detect_provider(url: str) -> str:
    """Classify the git host from an ``origin`` URL.

    Returns one of ``github``/``gitlab``/``azure``/``bitbucket``/``gerrit`` or
    ``unknown``. Mirrors qodo-pr-resolver's ``git remote get-url origin`` match.
    """
    u = url.lower()
    if "github.com" in u:
        return "github"
    if "gitlab" in u:
        return "gitlab"
    if "dev.azure.com" in u or "visualstudio.com" in u:
        return "azure"
    if "bitbucket.org" in u:
        return "bitbucket"
    if "googlesource.com" in u or ":29418" in u:
        return "gerrit"
    return "unknown"


def require_github(provider: str) -> None:
    """Guard: only GitHub is wired in this slice; everything else errors clearly."""
    if provider == "github":
        return
    if provider == "unknown":
        raise CliError(
            code=EXIT_ENV_ERROR,
            message="could not identify the git provider from 'origin'",
            remediation="ensure `git remote get-url origin` points at a supported host",
        )
    raise CliError(
        code=EXIT_ENV_ERROR,
        message=f"provider '{provider}' is not wired yet — only GitHub (gh) is supported",
        remediation="use a GitHub remote; glab/az/bitbucket are tracked follow-ups",
    )


def find_open_pr(branch: str) -> dict[str, Any] | None:
    """Return the open GitHub PR for ``branch`` (``{number, title, url}``) or None."""
    out = _gh("pr", "list", "--head", branch, "--state", "open", "--json", "number,title,url")
    items = json.loads(out or "[]")
    return items[0] if items else None


def _is_qodo(login: str) -> bool:
    return login in QODO_BOT_LOGINS


def _title(body: str) -> str:
    """Derive a short title from a comment body (first meaningful line)."""
    for line in (body or "").splitlines():
        stripped = line.strip().lstrip("#").strip().strip("*_`").strip()
        if stripped:
            return stripped[:80]
    return "(no title)"


def _normalize_summary(comment: dict[str, Any]) -> dict[str, Any]:
    login = (comment.get("author") or {}).get("login", "")
    body = comment.get("body", "")
    return {
        "id": None,  # PR-level summary comments are not individually resolvable here
        "kind": "summary",
        "author": login,
        "title": _title(body),
        "body": body,
        "path": None,
        "line": None,
        "url": comment.get("url"),
    }


def _normalize_inline(comment: dict[str, Any]) -> dict[str, Any]:
    login = (comment.get("user") or {}).get("login", "")
    body = comment.get("body", "")
    return {
        "id": comment.get("id"),
        "kind": "inline",
        "author": login,
        "title": _title(body),
        "body": body,
        "path": comment.get("path"),
        "line": comment.get("line") or comment.get("original_line"),
        "url": comment.get("html_url"),
    }


def _dedup(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge duplicate issues by title, preferring inline (located) over summary."""
    by_title: dict[str, dict[str, Any]] = {}
    for comment in comments:
        key = comment["title"].lower()
        existing = by_title.get(key)
        if existing is None or (existing["kind"] == "summary" and comment["kind"] == "inline"):
            by_title[key] = comment
    return list(by_title.values())


def fetch_qodo_comments(pr_number: int) -> list[dict[str, Any]]:
    """Fetch the Qodo bot's review comments on ``pr_number`` (GitHub).

    Combines the PR-level summary comments (``gh pr view``) with the inline
    review comments (``gh api .../pulls/<n>/comments``), keeps only those
    authored by a Qodo bot, and dedupes by title.
    """
    collected: list[dict[str, Any]] = []

    view = json.loads(_gh("pr", "view", str(pr_number), "--json", "comments") or "{}")
    for comment in view.get("comments", []):
        login = (comment.get("author") or {}).get("login", "")
        if _is_qodo(login):
            collected.append(_normalize_summary(comment))

    inline_raw = _gh(
        "api",
        f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/comments",
        "--paginate",
    )
    for comment in json.loads(inline_raw or "[]"):
        login = (comment.get("user") or {}).get("login", "")
        if _is_qodo(login):
            collected.append(_normalize_inline(comment))

    return _dedup(collected)


def resolve_comment(pr_number: int, comment_id: int, *, reply: str | None = None) -> list[str]:
    """Reply to (optional) and acknowledge a Qodo inline review comment (GitHub).

    The acknowledgement is a ``+1`` reaction — the lightweight marker
    qodo-pr-resolver uses. (True GitHub review-thread resolution is a GraphQL
    mutation and is a tracked follow-up.) Returns the actions taken.
    """
    actions: list[str] = []
    if reply:
        _gh(
            "api",
            f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/comments/{comment_id}/replies",
            "-X",
            "POST",
            "-f",
            f"body={reply}",
        )
        actions.append("replied")
    _gh(
        "api",
        f"repos/{{owner}}/{{repo}}/pulls/comments/{comment_id}/reactions",
        "-X",
        "POST",
        "-f",
        "content=+1",
    )
    actions.append("acknowledged (+1)")
    return actions
