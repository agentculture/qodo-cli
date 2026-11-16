"""Public Python API for in-process integrators (qodo-cli issue 18).

Plain functions returning plain objects (``dict`` / ``list`` — the same shapes the
CLI emits under ``--json``), so an integrator calls a function instead of driving
``qodo.cli.main`` and re-parsing stdout, and never reaches into ``qodo.cli._*``.
The CLI handlers for ``rules``, ``review`` and ``config`` are thin renderers over
this module: it is the single source of truth.

The provider/HTTP mechanics still live in ``qodo.cli._providers`` and
``qodo.cli._qodo_api`` (kept in place so their tests and patch points stay
valid); this module is the only caller of them. Failures raise
:class:`qodo.cli._errors.CliError` (``code`` is the CLI exit code: 1 user error,
2 environment error). Standard library only.
"""

from __future__ import annotations

import tomllib
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from qodo import changelog as _changelog
from qodo import gateway as _gateway_mod
from qodo import health as _health
from qodo.cli import _providers, _qodo_api
from qodo.cli._errors import EXIT_USER_ERROR, CliError

DEFAULT_TOP_K = _qodo_api.DEFAULT_TOP_K
CommentKind = str  # "all" | "summary" | "inline"

# --- rules -------------------------------------------------------------------


def rules_get(
    query: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    scopes: list[str] | None = None,
    auto_scope: bool = True,
) -> list[dict[str, Any]]:
    """Semantic-search the Qodo rules API; relevance-ranked rules (``name``,
    ``severity``, ``content``, ...).

    Scopes: explicit ``scopes`` win, else (when ``auto_scope``) the git origin is
    auto-detected. See :func:`rules_scopes` for what would be sent.
    """
    return _qodo_api.search_rules(
        query, top_k=top_k, scopes=rules_scopes(scopes=scopes, auto_scope=auto_scope)
    )


def rules_scopes(*, scopes: list[str] | None = None, auto_scope: bool = True) -> list[str] | None:
    """The scopes ``rules_get`` sends: explicit > auto-detected > ``None`` (omitted)."""
    if scopes:
        return list(scopes)
    if not auto_scope:
        return None
    return _qodo_api.detect_scopes() or None


# --- upstream gateway (Qodo's MCP server, behind the exposure policy) ---------------

DISMISS_REASONS = ("false_positive", "intentional", "deferred", "rejected")
MAX_FINDING_IDS = 100


def _gateway() -> _gateway_mod.Gateway:
    """The gateway these functions call through (one patch point for tests and integrators)."""
    return _gateway_mod.Gateway()


def _need(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"{what} is required",
            remediation=f"pass a non-empty {what}",
        )
    return value.strip()


def _finding_ids(ids: Any) -> list[str]:
    if not isinstance(ids, (list, tuple)) or not ids:
        raise CliError(
            code=EXIT_USER_ERROR,
            message="finding ids are required",
            remediation="pass 1 to 100 finding ids, from `findings list`",
        )
    if len(ids) > MAX_FINDING_IDS:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"at most {MAX_FINDING_IDS} finding ids per call, got {len(ids)}",
            remediation="split the ids into batches (one call per PR)",
        )
    return [_need(i, "finding id") for i in ids]


def _given(**kwargs: Any) -> dict[str, Any]:
    return {k: v for k, v in kwargs.items() if v is not None}


def findings_list(pr_url: str, *, extended: bool = False) -> dict[str, Any]:
    """Qodo's findings on a PR, addressed by its URL (``get-pr-review-findings``).

    Returns ``{"finding_count", "findings", "pr_url", "review_session"}``; ``extended`` adds
    locations, snippets and dismissal details.
    """
    url = _need(pr_url, "pr_url")
    if urllib.parse.urlparse(url).scheme not in ("http", "https"):
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"pr_url must be a PR URL, got {url!r}",
            remediation="pass the pull request's web URL, e.g. https://github.com/org/repo/pull/1",
        )
    args = {"pr_url": url}
    if extended:
        args["extended"] = True
    return _gateway().call("get-pr-review-findings", args)


def findings_implement(ids: list[str], *, actor: str | None = None) -> dict[str, Any]:
    """Mark findings implemented, 1-100 ids in one call (``pr-review-mark-implemented``).

    A write. Returns the upstream reply (its shape was never recorded). After a successful
    call one line goes to the change log (``qodo.changelog``); a log failure only warns.
    """
    clean = _finding_ids(ids)
    reply = _gateway().call("pr-review-mark-implemented", {"finding_ids": clean})
    _changelog.record_finding_status(clean, "implemented", actor=actor)
    return reply


def key_status() -> dict[str, Any]:
    """The Qodo API key's health: ``{present, state, expires, days_left, detail}``.

    Never includes the key. The expiry comes from configuration (see ``qodo.health``).
    """
    return _health.key_status()


def findings_dismiss(ids: list[str], reason: str, *, actor: str | None = None) -> dict[str, Any]:
    """Dismiss findings with a reason (``pr-review-dismiss``): one of ``DISMISS_REASONS``.

    A write. Returns the upstream reply (its shape was never recorded). After a successful
    call one line goes to the change log; a log failure only warns.
    """
    if reason not in DISMISS_REASONS:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"dismiss reason must be one of {', '.join(DISMISS_REASONS)}",
            remediation="pass --reason with one of the allowed values",
        )
    clean = _finding_ids(ids)
    reply = _gateway().call("pr-review-dismiss", {"finding_ids": clean, "reason": reason})
    _changelog.record_finding_status(clean, "dismissed", reason=reason, actor=actor)
    return reply


def rules_search(
    query: str, *, top_k: int | None = None, scopes: list[str] | None = None
) -> dict[str, Any]:
    """Semantic rules search through the MCP ``rules-search`` tool: ``{"rules": [...]}``.

    Unlike :func:`rules_get` (the older HTTP path, auto-scoped from the git origin), this goes
    through the exposure policy; scopes are only those passed.
    """
    return _gateway().call(
        "rules-search", _given(query=_need(query, "query"), top_k=top_k, scopes=scopes)
    )


def rules_list(
    *,
    state: str | None = None,
    categories: list[str] | None = None,
    severity: str | None = None,
    scopes: list[str] | None = None,
    name_contains: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> dict[str, Any]:
    """List rules, filtered (``rules-list``); only the filters given are sent."""
    return _gateway().call(
        "rules-list",
        _given(
            state=state,
            categories=categories,
            severity=severity,
            scopes=scopes,
            name_contains=name_contains,
            page=page,
            page_size=page_size,
        ),
    )


def review_history(repo: str, path: str, *, limit: int | None = None) -> dict[str, Any]:
    """Past review suggestions for a file or directory (``pull-request-review-history``).

    ``path`` is required: history is per file, searched upward. Built against the input schema
    only: the upstream tool failed when probed, so its reply shape is unconfirmed.
    """
    return _gateway().call(
        "pull-request-review-history",
        _given(repo=_need(repo, "repo"), file_path=_need(path, "file path"), limit=limit),
    )


# --- review ------------------------------------------------------------------


def _provider() -> str:
    provider = _providers.resolve_provider(_providers.remote_url())
    _providers.require_provider(provider)
    return provider


def _resolve_pr(pr: int | None, provider: str) -> tuple[int, dict[str, Any] | None]:
    """``(number, record)``; an explicit ``pr`` has no record."""
    if pr:
        return int(pr), None
    branch = _providers.current_branch()
    found = _providers.find_pr(provider, branch)
    if found is None:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"no open PR found for branch '{branch}'",
            remediation="open a PR for this branch first, or pass --pr <number>",
        )
    return int(found["number"]), found


def review_list(pr: int | None = None, kind: CommentKind = "all") -> dict[str, Any]:
    """The Qodo bot's comments on a PR (default: the current branch's open PR).

    Returns ``{"provider", "pr", "kind", "count", "comments"}``; ``kind`` is
    ``all``, ``summary`` or ``inline``.
    """
    provider = _provider()
    number, record = _resolve_pr(pr, provider)
    comments = _providers.fetch_comments(provider, number)
    if kind != "all":
        comments = [c for c in comments if c.get("kind") == kind]
    return {
        "provider": provider,
        "pr": record or {"number": number},
        "kind": kind,
        "count": len(comments),
        "comments": comments,
    }


def _explicit_ids(raw: list[Any]) -> list[int]:
    try:
        return [int(c) for c in raw]
    except ValueError as err:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"comment id must be an integer: {err}",
            remediation="pass numeric inline review comment id(s)",
        ) from err


def _filtered_comment_ids(provider: str, pr_number: int, severity: str | None) -> list[int]:
    targets: list[int] = []
    for comment in _providers.fetch_comments(provider, pr_number):
        # only inline review comments are individually resolvable (summaries have no id)
        if comment.get("kind") != "inline" or comment.get("id") is None:
            continue
        if severity and (comment.get("severity") or "").upper() != severity.upper():
            continue
        targets.append(int(comment["id"]))
    return targets


def _select_targets(
    ids: list[Any], select_all: bool, severity: str | None, pr_number: int, provider: str
) -> list[int]:
    if ids and (select_all or severity):
        raise CliError(
            code=EXIT_USER_ERROR,
            message="pass comment id(s) OR --all/--severity, not both",
            remediation="give explicit ids, or select with --all / --severity",
        )
    if ids:
        return _explicit_ids(ids)
    if not (select_all or severity):
        raise CliError(
            code=EXIT_USER_ERROR,
            message="no comment selected",
            remediation="give a comment id, or use --all / --severity to select inline comments",
        )
    return _filtered_comment_ids(provider, pr_number, severity)


def review_resolve(
    ids: list[Any] | None = None,
    *,
    pr: int | None = None,
    all: bool = False,  # noqa: A002 - mirrors the CLI's --all
    severity: str | None = None,
    reply: str | None | Callable[[], str | None] = None,
    resolve_thread: bool = True,
) -> dict[str, Any]:
    """Reply to / acknowledge / resolve Qodo inline comments.

    Select by explicit ``ids`` OR ``all`` / ``severity`` (not both, not neither).
    ``reply`` may be a callable, evaluated after the PR is resolved (so a signing
    error surfaces in the same order as the CLI's). Returns ``{"pr", "count",
    "ok", "resolved": [{"comment_id", "ok", "actions"}]}``; ``ok`` is False when
    any action failed (best-effort, partial results are kept).
    """
    provider = _provider()
    number, _ = _resolve_pr(pr, provider)
    reply_text = reply() if callable(reply) else reply
    targets = _select_targets(list(ids or []), all, severity, number, provider)

    # Pre-fetch the PR's review threads ONCE for a batch so each resolve doesn't
    # re-paginate them (the N+1 fix; None for providers without it). Best-effort:
    # on failure fall back to per-comment lookup, which reports its own failure.
    threads = None
    if resolve_thread and len(targets) > 1:
        try:
            threads = _providers.prefetch_threads(provider, number)
        except Exception:  # noqa: BLE001 - fall back to per-comment lookup
            threads = None

    items: list[dict[str, Any]] = []
    for comment_id in targets:
        actions = _providers.resolve(
            provider,
            number,
            comment_id,
            reply=reply_text,
            resolve_thread=resolve_thread,
            threads=threads,
        )
        items.append(
            {"comment_id": comment_id, "ok": _all_ok(actions), "actions": actions},
        )
    return {
        "pr": number,
        "count": len(items),
        "ok": _all_ok(items),  # an empty selection is ok
        "resolved": items,
    }


def _all_ok(entries: list[dict[str, Any]]) -> bool:
    """True when every entry's ``ok`` is truthy (vacuously true when empty)."""
    return all(e["ok"] for e in entries)


# --- config (repo-level reviewer config) --------------------------------------

CONFIG_DOCS = "https://docs.qodo.ai/qodo-documentation/qodo-merge/configuration/configuration-file"
BEST_PRACTICES_DOCS = "https://qodo-merge-docs.qodo.ai/core-abilities/auto_best_practices/"

PR_AGENT_FILE = ".pr_agent.toml"
BEST_PRACTICES_FILE = "best_practices.md"

# Minimal scaffolds (per Qodo's guidance: override only what you need).
PR_AGENT_TEMPLATE = '''\
# Qodo Merge / PR-Agent configuration.
# Docs: {docs}
#
# Keep this minimal — override only what you need. This repo's coding
# conventions belong in {best_practices} at the root (the reviewer
# auto-references it); this file reinforces a few intentional patterns.

[pr_reviewer]
extra_instructions = """
Describe this repo's intentional patterns here so the Qodo reviewer does not
flag them as issues. See {best_practices} at the repo root for the full
conventions.
"""
'''

BEST_PRACTICES_TEMPLATE = """\
# Best practices for {repo}

Repository-specific coding standards for the Qodo reviewer — and for any agent
working in this repo. The reviewer auto-references this file when reviewing PRs.

## Conventions

- List this repo's intentional patterns and coding standards here so the Qodo
  reviewer evaluates PRs against them rather than against generic defaults.
"""


def _repo_root(start: Path) -> Path:
    """The git repo root at/above ``start`` (where Qodo reads its config), or ``start``."""
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    return start


def _inspect(repo_root: Path) -> dict[str, object]:
    """Read-only snapshot of the repo reviewer config under ``repo_root``."""
    pr_agent = repo_root / PR_AGENT_FILE
    best_practices = repo_root / BEST_PRACTICES_FILE

    pr_agent_info: dict[str, object] = {"present": pr_agent.is_file(), "path": PR_AGENT_FILE}
    if pr_agent.is_file():
        try:
            data = tomllib.loads(pr_agent.read_text(encoding="utf-8"))
            pr_agent_info["valid_toml"] = True
            pr_agent_info["sections"] = sorted(k for k, v in data.items() if isinstance(v, dict))
            pr_agent_info["has_pr_reviewer"] = "pr_reviewer" in data
        except (tomllib.TOMLDecodeError, OSError) as err:
            pr_agent_info["valid_toml"] = False
            pr_agent_info["error"] = str(err)

    bp_info: dict[str, object] = {
        "present": best_practices.is_file(),
        "path": BEST_PRACTICES_FILE,
    }
    if best_practices.is_file():
        try:
            text = best_practices.read_text(encoding="utf-8")
            bp_info["bytes"] = len(text.encode("utf-8"))
            bp_info["non_empty"] = bool(text.strip())
        except (OSError, UnicodeDecodeError) as err:
            # Read-only verbs (show/validate) must degrade gracefully on an
            # unreadable/bad-encoding file, not crash with a generic exit 1 —
            # mirrors the guarded .pr_agent.toml read above.
            bp_info["readable"] = False
            bp_info["error"] = str(err)

    return {
        "repo_root": str(repo_root),
        "pr_agent": pr_agent_info,
        "best_practices": bp_info,
    }


def _validation_checks(info: dict[str, object]) -> list[dict[str, object]]:
    pr_agent = info["pr_agent"]
    bp = info["best_practices"]
    checks: list[dict[str, object]] = []

    any_present = bool(pr_agent["present"]) or bool(bp["present"])
    checks.append(
        {
            "id": "config_present",
            "ok": any_present,
            "severity": "error",
            "message": (
                "a Qodo reviewer config is present"
                if any_present
                else f"no {PR_AGENT_FILE} or {BEST_PRACTICES_FILE} found"
            ),
            "remediation": (
                "" if any_present else "run `qodo-cli config init` to scaffold a minimal config"
            ),
        }
    )

    if pr_agent["present"]:
        toml_ok = bool(pr_agent.get("valid_toml"))
        checks.append(
            {
                "id": "pr_agent_valid_toml",
                "ok": toml_ok,
                "severity": "error",
                "message": (
                    f"{PR_AGENT_FILE} parses as TOML"
                    if toml_ok
                    else f"{PR_AGENT_FILE} is not valid TOML: {pr_agent.get('error')}"
                ),
                "remediation": "" if toml_ok else f"fix the TOML syntax in {PR_AGENT_FILE}",
            }
        )
        has_reviewer = bool(pr_agent.get("has_pr_reviewer"))
        checks.append(
            {
                "id": "pr_reviewer_section",
                "ok": has_reviewer,
                "severity": "warning",
                "message": (
                    "[pr_reviewer] section present"
                    if has_reviewer
                    else f"no [pr_reviewer] section in {PR_AGENT_FILE}"
                ),
                "remediation": (
                    ""
                    if has_reviewer
                    else "add a [pr_reviewer] section with extra_instructions. "
                    f"Docs: {CONFIG_DOCS}"
                ),
            }
        )

    bp_ok = bool(bp["present"]) and bool(bp.get("non_empty"))
    checks.append(
        {
            "id": "best_practices_non_empty",
            "ok": bp_ok,
            "severity": "warning",
            "message": (
                f"{BEST_PRACTICES_FILE} present and non-empty"
                if bp_ok
                else f"{BEST_PRACTICES_FILE} missing or empty"
            ),
            "remediation": (
                ""
                if bp_ok
                else f"add {BEST_PRACTICES_FILE} with this repo's standards. "
                f"Docs: {BEST_PRACTICES_DOCS}"
            ),
        }
    )
    return checks


def _plan_init_writes(
    repo_root: Path, targets: dict[str, str], force: bool
) -> tuple[list[tuple[str, str]], list[str]]:
    """Classify each target into (to_write, skipped) *before* any write happens.

    Raises ``CliError`` on a path we must not clobber (a symlink — which could
    escape the repo root — or a non-regular path), so a refusal aborts the whole
    init cleanly rather than leaving a half-scaffold.
    """
    skipped: list[str] = []
    to_write: list[tuple[str, str]] = []
    for name, content in targets.items():
        path = repo_root / name
        # `is_symlink()` catches broken symlinks that `exists()` (which follows
        # links) misses; together they mean "something is already here".
        occupied = path.is_symlink() or path.exists()
        if occupied and not force:
            skipped.append(name)
            continue
        if occupied and (path.is_symlink() or not path.is_file()):
            kind = "symlink" if path.is_symlink() else "non-regular path"
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"refusing to overwrite {name}: it is a {kind}, not a regular file",
                remediation=f"remove {name} from {repo_root} by hand, then re-run "
                "`qodo-cli config init`",
            )
        to_write.append((name, content))
    return to_write, skipped


def config_show(root: Path | None = None) -> dict[str, Any]:
    """Snapshot of the repo reviewer config (``root`` defaults to the git root at/above cwd)."""
    return _inspect(_repo_root(Path.cwd()) if root is None else root)


def config_validate(root: Path | None = None) -> dict[str, Any]:
    """``{"valid", "checks": [{id, ok, severity, message, remediation}]}``.

    ``valid`` is False when any ``error``-severity check fails (warnings don't count).
    """
    checks = _validation_checks(config_show(root))
    return {"valid": all(c["ok"] for c in checks if c["severity"] == "error"), "checks": checks}


def config_init(root: Path | None = None, *, force: bool = False) -> dict[str, Any]:
    """Scaffold ``.pr_agent.toml`` and ``best_practices.md`` when absent.

    Never overwrites without ``force``; refuses a symlink or non-regular path
    before writing anything. Returns ``{"repo_root", "written", "skipped", "forced"}``.
    """
    repo_root = _repo_root(Path.cwd()) if root is None else root
    targets = {
        PR_AGENT_FILE: PR_AGENT_TEMPLATE.format(
            docs=CONFIG_DOCS, best_practices=BEST_PRACTICES_FILE
        ),
        BEST_PRACTICES_FILE: BEST_PRACTICES_TEMPLATE.format(repo=repo_root.name),
    }
    to_write, skipped = _plan_init_writes(repo_root, targets, force)
    written: list[str] = []
    for name, content in to_write:
        (repo_root / name).write_text(content, encoding="utf-8")
        written.append(name)
    return {"repo_root": str(repo_root), "written": written, "skipped": skipped, "forced": force}


def config_propose(
    repo: str,
    checkout: str,
    base: str,
    *,
    settings: Any = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Is the change ``checkout``'s committed HEAD makes against ``base`` a safe Qodo config
    change for ``repo``? A read: nothing is written to the checkout or anywhere else.

    Success: ``{"ok": True, "repo", "base", "base_sha", "head", "files": [{"path", "content"}],
    "warnings": [{"code", "path", "detail"}]}``. Refusal: ``{"ok": False, "reason", "detail",
    "paths"}``, never with file contents (the reasons are ``qodo.propose.REASONS``). A broken
    environment raises :class:`CliError`. ``settings`` defaults to
    :func:`qodo.propose.load_settings` (the repo, checkout-root and path allowlists). After a
    success one line (the repo and the paths) goes to the change log, with ``actor``.
    """
    from qodo import propose as _propose

    result = _propose.propose(
        repo, checkout, base, settings if settings is not None else _propose.load_settings()
    )
    if result["ok"]:
        _changelog.record_config_proposal(repo, [f["path"] for f in result["files"]], actor=actor)
    return result
