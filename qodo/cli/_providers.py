"""Git-provider mechanics for ``qodo review`` / ``qodo pr``.

Cites ``qodo-ai/qodo-skills`` ``qodo-pr-resolver`` as the behavioral source of
truth: the provider detection, the Qodo bot identities, and the provider-CLI
commands below mirror that skill's ``resources/providers.md`` and ``SKILL.md``.
We drive the user's *existing* provider CLI (``gh`` / ``glab``) — reusing its
auth, adding no new credentials — rather than vendoring or forking the skill.
See ``docs/qodo-skills-sources.md`` for the provenance ledger.

Scope: GitHub (``gh``, incl. GitHub Enterprise) and GitLab (``glab``) are wired
end to end. Azure/Bitbucket/Gerrit are recognised but :func:`require_provider`
raises a clear "not wired yet" error rather than silently misbehaving — their
per-provider commands are captured in the provenance ledger as the follow-up map.

This module owns the deterministic mechanics — detect, find the PR/MR, fetch and
filter the Qodo bot's comments, reply, acknowledge, resolve. The *fixing* loop
the skill performs (read files, generate a fix, edit, commit) is the agent's job.
"""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess  # nosec B404 - used only with resolved absolute paths, no shell
import urllib.parse
from typing import Any

from qodo.cli._errors import EXIT_ENV_ERROR, CliError

# Logins the Qodo reviewer posts under, stored as **base names** (no `[bot]`
# suffix). Cited from qodo-pr-resolver/SKILL.md (qodo-merge, qodo-ai,
# pr-agent-pro[-staging]) plus `qodo-code-review`, observed live on GitHub.
# Why base names: `gh pr view --json comments` returns the login WITHOUT `[bot]`
# while `gh api` returns it WITH `[bot]` — _is_qodo() normalises before matching.
QODO_BOT_LOGINS = frozenset(
    {
        "qodo-code-review",
        "qodo-merge",
        "qodo-ai",
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
    """Guard: only GitHub is wired in this slice; everything else errors clearly.

    Takes the *resolved* provider (see :func:`resolve_provider`), so a GitHub
    Enterprise host already reads as ``github`` here.
    """
    if provider == "github":
        return
    if provider == "unknown":
        raise CliError(
            code=EXIT_ENV_ERROR,
            message="could not identify the git provider from 'origin'",
            remediation="point `git remote get-url origin` at GitHub (incl. a GitHub "
            "Enterprise host you've run `gh auth login` for)",
        )
    raise CliError(
        code=EXIT_ENV_ERROR,
        message=f"provider '{provider}' is not wired yet — only GitHub (gh) is supported",
        remediation="use a GitHub remote; glab/az/bitbucket are tracked follow-ups",
    )


def _host_from_remote(url: str) -> str | None:
    """Extract the hostname from an https or scp-like (``git@host:path``) remote."""
    u = (url or "").strip()
    if not u:
        return None
    if "://" in u:
        return urllib.parse.urlparse(u).hostname
    # scp-like syntax: [user@]host:path
    rest = u.split("@", 1)[-1]
    host = rest.split(":", 1)[0]
    return host or None


def gh_knows_host(host: str) -> bool:
    """True if ``gh`` is authenticated to ``host`` — i.e. a GitHub (Enterprise)
    host gh can drive. Lets us recognise GHE remotes without guessing hostnames.

    Non-raising: a missing ``gh`` or an unconfigured host is a plain ``False``.
    """
    gh = shutil.which("gh")
    if not gh or not host:
        return False
    proc = subprocess.run(  # nosec B603 - resolved absolute path, no shell
        [gh, "auth", "status", "--hostname", host],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def glab_knows_host(host: str) -> bool:
    """True if ``glab`` is authenticated to ``host`` — i.e. a self-hosted GitLab
    instance glab can drive. Lets us recognise GitLab on a custom domain without
    guessing hostnames (the GitLab analogue of :func:`gh_knows_host`).

    Non-raising: a missing ``glab`` or an unconfigured host is a plain ``False``.
    """
    glab = shutil.which("glab")
    if not glab or not host:
        return False
    proc = subprocess.run(  # nosec B603 - resolved absolute path, no shell
        [glab, "auth", "status", "--hostname", host],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def resolve_provider(url: str) -> str:
    """Classify the provider, upgrading an unknown host to ``github``/``gitlab``
    when a provider CLI is authenticated for it.

    GHE and self-hosted GitLab hostnames are arbitrary, so we don't guess from
    the URL — we ask the provider CLIs (``gh``/``glab auth status --hostname
    <host>``). The ``github.com``/``gitlab.com`` paths are unaffected (no CLI
    call). ``gh`` is consulted first; a host authenticated to both resolves to
    ``github`` (a deterministic, degenerate tie-break). NOTE: GHE and
    self-hosted GitLab support is implemented but NOT live-tested against a real
    instance — we have none. Covered by mocked tests only.
    """
    provider = detect_provider(url)
    if provider == "unknown":
        host = _host_from_remote(url)
        if host:
            if gh_knows_host(host):
                return "github"
            if glab_knows_host(host):
                return "gitlab"
    return provider


def find_open_pr(branch: str) -> dict[str, Any] | None:
    """Return the open GitHub PR for ``branch`` (``{number, title, url}``) or None."""
    out = _gh("pr", "list", "--head", branch, "--state", "open", "--json", "number,title,url")
    items = json.loads(out or "[]")
    return items[0] if items else None


def _is_qodo(login: str) -> bool:
    # Normalise the `[bot]` suffix gh's two surfaces disagree on (see above).
    return (login or "").removesuffix("[bot]") in QODO_BOT_LOGINS


_CODE_CHIP_RE = re.compile(r"<code>.*?</code>", re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_LEAD_NUM_RE = re.compile(r"^\d+\\?\.\s*")  # "1. " or markdown-escaped "1\. "

# --- structured-field extraction (cited from live Qodo inline comment bodies) ---
#
# A Qodo inline comment body looks like:
#
#     <img ...badge/Action_required... alt="Action required">
#
#     1\. <b><i>Short title</i></b> <code>📘 Rule violation</code> <code>≡ Correctness</code>
#
#     <pre>... issue description (HTML-entity-encoded) ...</pre>
#
#     <details><summary><strong>Agent Prompt</strong></summary>
#     ```
#     ## Issue description ... ## Fix Focus Areas ...
#     ```
#     </details>
#
# So: the badge ``alt`` carries the severity, the title-line ``<code>`` chips
# carry the type/category, the ``<pre>`` block is the description, and the
# fenced block under "Agent Prompt" is the remediation prompt. All extraction is
# best-effort — an unrecognised shape yields ``None`` (never a crash), so the
# caller degrades to title-only. See ``docs/qodo-skills-sources.md``.

# Severity badge alt-text → normalized severity. Extend the map as Qodo adds
# badges; a badge present but unrecognised degrades to ``LOW``, and no badge at
# all (e.g. a summary rollup) yields ``None``.
_SEVERITY_BY_BADGE = {
    "action required": "HIGH",
    "review recommended": "MEDIUM",
}
_DEFAULT_SEVERITY = "LOW"

_BADGE_ALT_RE = re.compile(r'<img\b[^>]*?\balt="([^"]*)"', re.IGNORECASE)
_CHIP_RE = re.compile(r"<code>(.*?)</code>", re.IGNORECASE | re.DOTALL)
_PRE_RE = re.compile(r"<pre>(.*?)</pre>", re.IGNORECASE | re.DOTALL)
# A leading emoji/symbol (+ following space) on a chip, e.g. "📘 Rule violation".
_LEAD_SYMBOL_RE = re.compile(r"^[^\w]+")
# The "Agent Prompt" block is scanned with plain string ops, not a regex: two
# lazy quantifiers around a ``` fence is a ReDoS footgun (Sonar python:S5852).
_AGENT_PROMPT_MARKER = "agent prompt"
_FENCE = "```"


def _first_prose_line(body: str) -> str:
    """Return the raw first line of ``body`` that carries prose (chips intact).

    Mirrors the line :func:`_title` derives from: the first line that is not pure
    HTML (badge / image / ``<pre>`` / ``<details>``). Returned verbatim so the
    title-line ``<code>`` category chips can be extracted before they are stripped.
    """
    for raw in (body or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        text = _CODE_CHIP_RE.sub("", line)
        text = _HTML_TAG_RE.sub("", text).strip()
        if text:
            return line  # the prose line, with its <code> chips intact
    return ""


def _title(body: str) -> str:
    """Derive a short, human title from a comment body.

    Drops the ``<code>`` chips and tags off the first prose line, strips any
    leading list numbering, and returns it. Display-only — :func:`_dedup` keys on
    identity, not on the title.
    """
    line = _first_prose_line(body)
    if not line:
        return "(no title)"
    text = _CODE_CHIP_RE.sub("", line)
    text = _HTML_TAG_RE.sub("", text).strip()
    text = _LEAD_NUM_RE.sub("", text.lstrip("#").strip())
    text = text.strip("*_`").strip()
    return text[:80] if text else "(no title)"


def _severity(body: str) -> str | None:
    """The normalized severity from the badge ``alt`` text, or ``None`` if absent."""
    match = _BADGE_ALT_RE.search(body or "")
    if match is None:
        return None
    return _SEVERITY_BY_BADGE.get(match.group(1).strip().lower(), _DEFAULT_SEVERITY)


def _chips(title_line: str) -> list[str]:
    """The category chips on the title line, cleaned of any leading emoji/symbol.

    e.g. ``<code>📘 Rule violation</code> <code>≡ Correctness</code>`` →
    ``["Rule violation", "Correctness"]``.
    """
    out: list[str] = []
    for raw in _CHIP_RE.findall(title_line):
        text = html.unescape(_HTML_TAG_RE.sub("", raw)).strip()
        text = _LEAD_SYMBOL_RE.sub("", text).strip()
        if text:
            out.append(text)
    return out


def _description(body: str) -> str | None:
    """The ``<pre>`` issue description, tags stripped and HTML entities decoded."""
    match = _PRE_RE.search(body or "")
    if match is None:
        return None
    text = html.unescape(_HTML_TAG_RE.sub("", match.group(1))).strip()
    return text or None


def _agent_prompt(body: str) -> str | None:
    """The fenced remediation block under the "Agent Prompt" ``<details>``.

    Scanned with plain ``str.find`` (linear time) rather than a regex with two
    lazy quantifiers around the ``` fence, which is a catastrophic-backtracking
    risk (Sonar python:S5852).
    """
    text = body or ""
    marker = text.lower().find(_AGENT_PROMPT_MARKER)
    if marker == -1:
        return None
    open_fence = text.find(_FENCE, marker)
    if open_fence == -1:
        return None
    start = open_fence + len(_FENCE)
    close_fence = text.find(_FENCE, start)
    if close_fence == -1:
        return None
    return html.unescape(text[start:close_fence]).strip() or None


def _parse_body(body: str) -> dict[str, Any]:
    """Structured triage fields from a Qodo comment body (best-effort, never raises).

    Returns ``{severity, type, categories, description, agent_prompt}``; each is
    ``None`` (or ``[]`` for ``categories``) when the structure isn't recognised.
    ``type`` is the primary chip; ``categories`` is every chip (primary first).
    """
    chips = _chips(_first_prose_line(body))
    return {
        "severity": _severity(body),
        "type": chips[0] if chips else None,
        "categories": chips,
        "description": _description(body),
        "agent_prompt": _agent_prompt(body),
    }


def _normalize_summary(comment: dict[str, Any]) -> dict[str, Any]:
    login = (comment.get("author") or {}).get("login", "")
    body = comment.get("body", "")
    parsed = _parse_body(body)
    return {
        "id": None,  # PR-level summary comments are not individually resolvable here
        "kind": "summary",
        "author": login,
        "title": _title(body),
        "severity": parsed["severity"],
        "type": parsed["type"],
        "categories": parsed["categories"],
        "description": parsed["description"],
        "agent_prompt": parsed["agent_prompt"],
        "body": body,
        "path": None,
        "line": None,
        "url": comment.get("url"),
    }


def _normalize_inline(comment: dict[str, Any]) -> dict[str, Any]:
    login = (comment.get("user") or {}).get("login", "")
    body = comment.get("body", "")
    parsed = _parse_body(body)
    return {
        "id": comment.get("id"),
        "kind": "inline",
        "author": login,
        "title": _title(body),
        "severity": parsed["severity"],
        "type": parsed["type"],
        "categories": parsed["categories"],
        "description": parsed["description"],
        "agent_prompt": parsed["agent_prompt"],
        "body": body,
        "path": comment.get("path"),
        "line": comment.get("line") or comment.get("original_line"),
        "url": comment.get("html_url"),
    }


def _dedup(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop only TRUE duplicates, keyed on stable identity — never on title.

    Distinct comments must never collapse: Qodo's inline bodies all open with the
    same ``<img ...Action_required...>`` badge line, so a title-based key would
    merge unrelated findings and the tool would under-report. Identity is the
    GitHub comment id when present (inline comments), else the comment url
    (summary comments), else a ``(kind, title)`` fallback. Order is preserved;
    only exact id/url repeats (e.g. pagination overlap) are removed.
    """
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for comment in comments:
        if comment.get("id") is not None:
            key: tuple[Any, ...] = ("id", comment["id"])
        elif comment.get("url"):
            key = ("url", comment["url"])
        else:
            key = ("kt", comment["kind"], comment["title"])
        if key in seen:
            continue
        seen.add(key)
        out.append(comment)
    return out


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


# --- true GitHub review-thread resolution (GraphQL) ------------------------
#
# The REST comment id is NOT the review-thread node id `resolveReviewThread`
# needs, so we map one to the other: list the PR's review threads (each carries
# its node id, resolved state, and member comments' databaseIds) and find the
# thread whose comments include our comment id. A single thread can hold several
# comments (original + replies); resolving it resolves them all.

_THREADS_QUERY = """\
query($owner:String!,$name:String!,$pr:Int!,$cursor:String){
  repository(owner:$owner,name:$name){
    pullRequest(number:$pr){
      reviewThreads(first:100,after:$cursor){
        nodes{ id isResolved comments(first:100){nodes{databaseId}} }
        pageInfo{ hasNextPage endCursor }
      }
    }
  }
}
"""

_RESOLVE_MUTATION = """\
mutation($threadId:ID!){
  resolveReviewThread(input:{threadId:$threadId}){ thread{ id isResolved } }
}
"""


def _repo_owner_name() -> tuple[str, str]:
    """Resolve ``(owner, name)`` for the current repo (GraphQL needs them explicit)."""
    data = json.loads(_gh("repo", "view", "--json", "owner,name") or "{}")
    owner = (data.get("owner") or {}).get("login") or ""
    name = data.get("name") or ""
    if not owner or not name:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message="could not resolve the repo owner/name for thread resolution",
            remediation="run from a GitHub repo checkout with `gh` authenticated",
        )
    return owner, name


def review_threads(pr_number: int) -> list[dict[str, Any]]:
    """All review threads on ``pr_number`` as ``{id, resolved, comment_ids}``.

    ``comment_ids`` is the set of REST/databaseIds of the comments in the thread,
    so callers can map a comment id back to its thread. Paginates the threads.
    """
    owner, name = _repo_owner_name()
    threads: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        args = [
            "api",
            "graphql",
            "-f",
            f"query={_THREADS_QUERY}",
            "-f",
            f"owner={owner}",
            "-f",
            f"name={name}",
            "-F",
            f"pr={pr_number}",
        ]
        if cursor:
            args += ["-f", f"cursor={cursor}"]
        data = json.loads(_gh(*args) or "{}")
        pull = ((data.get("data") or {}).get("repository") or {}).get("pullRequest") or {}
        rt = pull.get("reviewThreads") or {}
        for node in rt.get("nodes") or []:
            comment_ids = {
                c.get("databaseId")
                for c in (node.get("comments") or {}).get("nodes") or []
                if c.get("databaseId") is not None
            }
            threads.append(
                {
                    "id": node.get("id"),
                    "resolved": bool(node.get("isResolved")),
                    "comment_ids": comment_ids,
                }
            )
        page = rt.get("pageInfo") or {}
        if page.get("hasNextPage") and page.get("endCursor"):
            cursor = page["endCursor"]
        else:
            return threads


def thread_for_comment(
    pr_number: int,
    comment_id: int,
    *,
    threads: list[dict[str, Any]] | None = None,
) -> tuple[str | None, bool]:
    """Return ``(thread_node_id, already_resolved)`` for ``comment_id`` (or ``(None, False)``).

    Pass a pre-fetched ``threads`` list (from :func:`review_threads`) to avoid
    re-paginating the PR's threads once per comment in a batch resolve.
    """
    pool = review_threads(pr_number) if threads is None else threads
    for thread in pool:
        if comment_id in thread["comment_ids"]:
            return thread["id"], thread["resolved"]
    return None, False


def resolve_review_thread(thread_id: str) -> None:
    """Mark a GitHub review thread resolved via the GraphQL ``resolveReviewThread``."""
    _gh(
        "api",
        "graphql",
        "-f",
        f"query={_RESOLVE_MUTATION}",
        "-f",
        f"threadId={thread_id}",
    )


# --- best-effort, per-action acknowledgement -------------------------------


def _attempt(action: str, fn: Any) -> dict[str, Any]:
    """Run ``fn`` and record the outcome as ``{action, ok, detail}`` — never raises.

    Catches *any* exception (not just :class:`CliError`): the gh binary could
    vanish between ``shutil.which`` and exec (``OSError``), or gh could return
    malformed JSON (``json.JSONDecodeError``). Best-effort means none of these may
    crash the batch — each becomes a reported failed action.
    """
    try:
        fn()
        return {"action": action, "ok": True, "detail": ""}
    except CliError as err:
        return {"action": action, "ok": False, "detail": err.message}
    except Exception as err:  # noqa: BLE001 - best-effort: any failure becomes a reported action
        return {"action": action, "ok": False, "detail": f"{err.__class__.__name__}: {err}"}


def _resolve_thread_action(
    pr_number: int,
    comment_id: int,
    *,
    threads: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Map the comment to its review thread and resolve it (best-effort).

    Falls back gracefully: if no thread is found, the ``+1`` reaction already
    posted stands as the marker and this is reported (``ok=False``) rather than
    raised. An already-resolved thread is a no-op success. ``threads`` may be a
    pre-fetched thread pool to avoid re-fetching per comment.
    """
    try:
        thread_id, already = thread_for_comment(pr_number, comment_id, threads=threads)
    except CliError as err:
        return {"action": "resolve thread", "ok": False, "detail": err.message}
    except Exception as err:  # noqa: BLE001 - best-effort: never crash the batch on a thread lookup
        return {
            "action": "resolve thread",
            "ok": False,
            "detail": f"{err.__class__.__name__}: {err}",
        }
    if thread_id is None:
        return {
            "action": "resolve thread",
            "ok": False,
            "detail": "no review thread for this comment (the +1 reaction stands as the marker)",
        }
    if already:
        return {"action": "resolve thread", "ok": True, "detail": "already resolved"}
    return _attempt("resolve thread", lambda: resolve_review_thread(thread_id))


def resolve_comment(
    pr_number: int,
    comment_id: int,
    *,
    reply: str | None = None,
    resolve_thread: bool = True,
    threads: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Reply to (optional), acknowledge, and resolve the thread of a Qodo comment.

    Best-effort and **non-raising for individual actions**: returns a list of
    ``{action, ok, detail}`` records so the caller can report partial success
    (e.g. a posted reply whose acknowledgement failed) instead of a blanket
    failure. The acknowledgement is the ``+1`` reaction qodo-pr-resolver uses;
    when ``resolve_thread`` is set, the GitHub review thread is also resolved via
    GraphQL (with a graceful fallback to reaction-only). Pass a pre-fetched
    ``threads`` pool to avoid re-fetching the PR's threads per comment in a batch.
    """
    results: list[dict[str, Any]] = []
    if reply:
        results.append(
            _attempt(
                "reply",
                lambda: _gh(
                    "api",
                    f"repos/{{owner}}/{{repo}}/pulls/{pr_number}/comments/{comment_id}/replies",
                    "-X",
                    "POST",
                    "-f",
                    f"body={reply}",
                ),
            )
        )
    results.append(
        _attempt(
            "acknowledge (+1)",
            lambda: _gh(
                "api",
                f"repos/{{owner}}/{{repo}}/pulls/comments/{comment_id}/reactions",
                "-X",
                "POST",
                "-f",
                "content=+1",
            ),
        )
    )
    if resolve_thread:
        results.append(_resolve_thread_action(pr_number, comment_id, threads=threads))
    return results


# --- GitLab (glab) ---------------------------------------------------------
#
# GitLab's review model differs from GitHub's: an MR *discussion* holds one or
# more *notes*, and resolution is at the discussion level (not the individual
# note). We mirror the GitHub surface — list the Qodo bot's notes (carrying the
# parsed triage fields), and resolve = reply to + mark resolved the note's
# discussion. Commands follow qodo-pr-resolver/resources/providers.md (the
# `glab` column). IMPLEMENTED but NOT live-tested against a real GitLab (we have
# none) — covered by mocked tests mirroring the GitHub ones; the `glab` REST
# shapes are the documented contract. See docs/qodo-skills-sources.md.


def _glab(*args: str) -> str:
    return _run([require_tool("glab"), *args])


def _gitlab_project() -> str:
    """URL-encoded GitLab project path (``namespace/repo``) from the origin remote."""
    from qodo.cli._qodo_api import repo_slug  # pure helper; reuse-don't-duplicate

    path = repo_slug(remote_url())
    if not path:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message="could not resolve the GitLab project from 'origin'",
            remediation="ensure 'origin' points at a GitLab project (namespace/repo)",
        )
    return urllib.parse.quote(path, safe="")


def gitlab_find_open_pr(branch: str) -> dict[str, Any] | None:
    """Return the open GitLab MR for ``branch`` (``{number(iid), title, url}``) or None."""
    proj = _gitlab_project()
    out = _glab(
        "api",
        f"projects/{proj}/merge_requests"
        f"?source_branch={urllib.parse.quote(branch)}&state=opened",
    )
    items = json.loads(out or "[]")
    if not items:
        return None
    mr = items[0]
    return {"number": mr.get("iid"), "title": mr.get("title", ""), "url": mr.get("web_url", "")}


def _normalize_gitlab_note(discussion: dict[str, Any], note: dict[str, Any]) -> dict[str, Any]:
    body = note.get("body", "")
    parsed = _parse_body(body)
    position = note.get("position") or {}
    return {
        "id": note.get("id"),
        "discussion_id": discussion.get("id"),
        "kind": "inline" if position else "summary",
        "author": (note.get("author") or {}).get("username", ""),
        "title": _title(body),
        "severity": parsed["severity"],
        "type": parsed["type"],
        "categories": parsed["categories"],
        "description": parsed["description"],
        "agent_prompt": parsed["agent_prompt"],
        "body": body,
        "path": position.get("new_path"),
        "line": position.get("new_line"),
        "url": None,  # not present in the discussions payload
    }


def gitlab_discussions(mr_iid: int) -> list[dict[str, Any]]:
    """All discussions on MR ``mr_iid`` (GitLab), paginated. Pre-fetchable for batches."""
    proj = _gitlab_project()
    raw = _glab(
        "api",
        f"projects/{proj}/merge_requests/{mr_iid}/discussions?per_page=100",
        "--paginate",
    )
    return json.loads(raw or "[]")


def gitlab_fetch_qodo_comments(mr_iid: int) -> list[dict[str, Any]]:
    """Fetch the Qodo bot's notes on MR ``mr_iid`` (GitLab), across discussions."""
    collected: list[dict[str, Any]] = []
    for discussion in gitlab_discussions(mr_iid):
        for note in discussion.get("notes") or []:
            if note.get("system"):
                continue  # skip GitLab's automated system notes
            if _is_qodo((note.get("author") or {}).get("username", "")):
                collected.append(_normalize_gitlab_note(discussion, note))
    return _dedup(collected)


def _gitlab_discussion_for_note(
    mr_iid: int,
    note_id: int,
    *,
    discussions: list[dict[str, Any]] | None = None,
) -> tuple[str | None, bool]:
    """Map a note id to its ``(discussion_id, already_resolved)`` (or ``(None, False)``).

    Pass a pre-fetched ``discussions`` list to avoid re-paginating per note in a
    batch resolve (the GitLab analogue of GitHub's thread prefetch).
    """
    pool = gitlab_discussions(mr_iid) if discussions is None else discussions
    for discussion in pool:
        for note in discussion.get("notes") or []:
            if note.get("id") == note_id:
                return discussion.get("id"), bool(note.get("resolved"))
    return None, False


def gitlab_resolve_comment(
    mr_iid: int,
    note_id: int,
    *,
    reply: str | None = None,
    resolve_thread: bool = True,
    threads: list[dict[str, Any]] | None = None,  # pre-fetched discussions (batch N+1 fix)
) -> list[dict[str, Any]]:
    """Reply to (optional) and resolve the GitLab discussion holding ``note_id``.

    Best-effort and non-raising per action (same contract as the GitHub path).
    GitLab acknowledges by resolving the *discussion* (there is no ``+1`` marker),
    so ``resolve_thread`` controls the resolve. ``threads`` is the pre-fetched
    discussion pool (from :func:`gitlab_discussions`) reused across a batch.
    """
    proj = _gitlab_project()
    results: list[dict[str, Any]] = []
    try:
        disc_id, already = _gitlab_discussion_for_note(mr_iid, note_id, discussions=threads)
    except CliError as err:
        return [{"action": "lookup discussion", "ok": False, "detail": err.message}]
    except Exception as err:  # noqa: BLE001 - best-effort: never crash the batch
        return [
            {
                "action": "lookup discussion",
                "ok": False,
                "detail": f"{err.__class__.__name__}: {err}",
            }
        ]
    if disc_id is None:
        return [
            {
                "action": "lookup discussion",
                "ok": False,
                "detail": "no discussion found for this note id",
            }
        ]
    if reply:
        results.append(
            _attempt(
                "reply",
                lambda: _glab(
                    "api",
                    f"projects/{proj}/merge_requests/{mr_iid}/discussions/{disc_id}/notes",
                    "-X",
                    "POST",
                    "-f",
                    f"body={reply}",
                ),
            )
        )
    if resolve_thread:
        if already:
            results.append({"action": "resolve thread", "ok": True, "detail": "already resolved"})
        else:
            results.append(
                _attempt(
                    "resolve thread",
                    lambda: _glab(
                        "api",
                        f"projects/{proj}/merge_requests/{mr_iid}/discussions/{disc_id}",
                        "-X",
                        "PUT",
                        "-f",
                        "resolved=true",
                    ),
                )
            )
    return results


# --- provider gate + dispatch ----------------------------------------------

_SUPPORTED_PROVIDERS = ("github", "gitlab")


def require_provider(provider: str) -> None:
    """Generalized provider gate: allow the wired providers, error clearly otherwise.

    Supersedes the GitHub-only :func:`require_github` for the ``review`` surface
    now that GitLab is wired. Azure/Bitbucket/Gerrit remain tracked follow-ups.
    """
    if provider in _SUPPORTED_PROVIDERS:
        return
    if provider == "unknown":
        raise CliError(
            code=EXIT_ENV_ERROR,
            message="could not identify the git provider from 'origin'",
            remediation="point 'origin' at GitHub (incl. a GHE host you've run "
            "`gh auth login` for) or GitLab",
        )
    raise CliError(
        code=EXIT_ENV_ERROR,
        message=f"provider '{provider}' is not wired yet — supported: GitHub (gh), GitLab (glab)",
        remediation="use a GitHub or GitLab remote; azure/bitbucket/gerrit are tracked follow-ups",
    )


def find_pr(provider: str, branch: str) -> dict[str, Any] | None:
    """Find the open PR/MR for ``branch`` on ``provider``."""
    if provider == "gitlab":
        return gitlab_find_open_pr(branch)
    return find_open_pr(branch)


def fetch_comments(provider: str, pr_number: int) -> list[dict[str, Any]]:
    """Fetch the Qodo bot's comments on ``pr_number`` for ``provider``."""
    if provider == "gitlab":
        return gitlab_fetch_qodo_comments(pr_number)
    return fetch_qodo_comments(pr_number)


def prefetch_threads(provider: str, pr_number: int) -> list[dict[str, Any]] | None:
    """Pre-fetch the threads/discussions for a batch resolve (avoids the per-comment N+1)."""
    if provider == "github":
        return review_threads(pr_number)
    if provider == "gitlab":
        return gitlab_discussions(pr_number)
    return None


def resolve(
    provider: str,
    pr_number: int,
    comment_id: int,
    *,
    reply: str | None = None,
    resolve_thread: bool = True,
    threads: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Reply to / acknowledge / resolve a comment on ``provider`` (best-effort)."""
    if provider == "gitlab":
        return gitlab_resolve_comment(
            pr_number, comment_id, reply=reply, resolve_thread=resolve_thread, threads=threads
        )
    return resolve_comment(
        pr_number, comment_id, reply=reply, resolve_thread=resolve_thread, threads=threads
    )
