"""``qodo review`` (a.k.a. ``qodo pr``) — triage the Qodo bot's PR comments.

Native reimplementation of ``qodo-ai/qodo-skills`` ``qodo-pr-resolver``: it
drives the user's existing provider CLI (``gh``) to find the open PR for the
current branch, list the Qodo bot's review comments, and reply to / acknowledge
/ resolve them. The provider mechanics live in :mod:`qodo.cli._providers`; this
module is the CLI surface. The *fixing* loop (read files, generate a fix, edit)
stays with the calling agent. See ``docs/qodo-skills-sources.md`` for the
citation ledger.
"""

from __future__ import annotations

import argparse

from qodo.cli import _providers
from qodo.cli._commands.overview import emit_overview, review_sections
from qodo.cli._commands.whoami import read_agent_fields
from qodo.cli._errors import EXIT_SUCCESS, EXIT_USER_ERROR, CliError
from qodo.cli._output import add_json_flag, emit_result


def _resolve_pr_number(args: argparse.Namespace, provider: str) -> tuple[int, dict | None]:
    """Return ``(pr_number, pr_record)``; honour an explicit ``--pr`` override."""
    if getattr(args, "pr", None):
        return int(args.pr), None
    branch = _providers.current_branch()
    pr = _providers.find_pr(provider, branch)
    if pr is None:
        raise CliError(
            code=EXIT_USER_ERROR,
            message=f"no open PR found for branch '{branch}'",
            remediation="open a PR for this branch first, or pass --pr <number>",
        )
    return int(pr["number"]), pr


def _render_comments(pr_number: int, pr: dict | None, comments: list[dict]) -> str:
    title = (pr or {}).get("title", "")
    url = (pr or {}).get("url", "")
    header = f"# Qodo review — PR #{pr_number}"
    if title:
        header += f": {title}"
    lines = [header]
    if url:
        lines.append(url)
    lines.append("")
    if not comments:
        lines.append("No Qodo review comments found on this PR.")
        return "\n".join(lines).rstrip()
    lines.append(f"{len(comments)} Qodo comment(s):")
    lines.append("")
    for i, comment in enumerate(comments, 1):
        tags = [f"[{comment['kind']}]"]
        if comment.get("severity"):
            tags.append(str(comment["severity"]))
        if comment.get("type"):
            tags.append(str(comment["type"]))
        loc = f" ({comment['path']}:{comment.get('line')})" if comment.get("path") else ""
        rid = f" [id {comment['id']}]" if comment.get("id") is not None else ""
        lines.append(f"{i}. {' '.join(tags)}: {comment['title']}{loc}{rid}")
    return "\n".join(lines).rstrip()


def cmd_review_list(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    provider = _providers.resolve_provider(_providers.remote_url())
    _providers.require_provider(provider)
    pr_number, pr = _resolve_pr_number(args, provider)
    comments = _providers.fetch_comments(provider, pr_number)
    kind = getattr(args, "kind", "all")
    if kind != "all":
        comments = [c for c in comments if c.get("kind") == kind]
    if json_mode:
        emit_result(
            {
                "provider": provider,
                "pr": pr or {"number": pr_number},
                "kind": kind,
                "count": len(comments),
                "comments": comments,
            },
            json_mode=True,
        )
    else:
        emit_result(_render_comments(pr_number, pr, comments), json_mode=False)
    return EXIT_SUCCESS


def _maybe_sign(args: argparse.Namespace) -> str | None:
    """Apply ``--sign`` to ``--reply`` (opt-in), appending the nick signature once."""
    reply = getattr(args, "reply", None)
    if not getattr(args, "sign", False):
        return reply
    if not reply:
        raise CliError(
            code=EXIT_USER_ERROR,
            message="--sign requires --reply",
            remediation='pass --reply "..." to sign, or drop --sign',
        )
    nick = read_agent_fields().get("nick") or "qodo-cli"
    signature = f"- {nick} (Claude)"
    if reply.rstrip().endswith(signature):  # duplicate guard — append at most once
        return reply
    return f"{reply.rstrip()}\n\n{signature}"


def _select_targets(args: argparse.Namespace, pr_number: int, provider: str) -> list[int]:
    """Resolve which inline comment ids to act on, from explicit ids or filters."""
    raw = list(getattr(args, "comment_id", None) or [])
    use_all = bool(getattr(args, "all", False))
    severity = getattr(args, "severity", None)
    if raw and (use_all or severity):
        raise CliError(
            code=EXIT_USER_ERROR,
            message="pass comment id(s) OR --all/--severity, not both",
            remediation="give explicit ids, or select with --all / --severity",
        )
    if raw:
        try:
            return [int(c) for c in raw]
        except ValueError as err:
            raise CliError(
                code=EXIT_USER_ERROR,
                message=f"comment id must be an integer: {err}",
                remediation="pass numeric inline review comment id(s)",
            ) from err
    if not (use_all or severity):
        raise CliError(
            code=EXIT_USER_ERROR,
            message="no comment selected",
            remediation="give a comment id, or use --all / --severity to select inline comments",
        )
    targets: list[int] = []
    for comment in _providers.fetch_comments(provider, pr_number):
        # only inline review comments are individually resolvable (summaries have no id)
        if comment.get("kind") != "inline" or comment.get("id") is None:
            continue
        if severity and (comment.get("severity") or "").upper() != severity.upper():
            continue
        targets.append(int(comment["id"]))
    return targets


def _render_resolve(pr_number: int, items: list[dict]) -> str:
    if not items:
        return f"No comments selected to resolve on PR #{pr_number}."
    lines = [f"# Qodo resolve — PR #{pr_number}", ""]
    for item in items:
        lines.append(f"comment {item['comment_id']}: {'ok' if item['ok'] else 'partial'}")
        for action in item["actions"]:
            mark = "ok" if action["ok"] else "fail"
            detail = f" ({action['detail']})" if action["detail"] else ""
            lines.append(f"  [{mark}] {action['action']}{detail}")
    return "\n".join(lines).rstrip()


def cmd_review_resolve(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    provider = _providers.resolve_provider(_providers.remote_url())
    _providers.require_provider(provider)
    pr_number, _ = _resolve_pr_number(args, provider)
    reply = _maybe_sign(args)
    resolve_thread = not bool(getattr(args, "no_resolve_thread", False))
    targets = _select_targets(args, pr_number, provider)

    # Pre-fetch the PR's review threads ONCE for a batch so each resolve doesn't
    # re-paginate them (the N+1 fix; GitHub only — None elsewhere). Best-effort:
    # if the fetch fails, fall back to per-comment lookup, which reports its own
    # failure. A single target needs no prefetch — the per-comment lookup is one
    # call anyway.
    threads = None
    if resolve_thread and len(targets) > 1:
        try:
            threads = _providers.prefetch_threads(provider, pr_number)
        except Exception:  # noqa: BLE001 - fall back to per-comment lookup
            threads = None

    items: list[dict] = []
    for comment_id in targets:
        actions = _providers.resolve(
            provider,
            pr_number,
            comment_id,
            reply=reply,
            resolve_thread=resolve_thread,
            threads=threads,
        )
        items.append(
            {
                "comment_id": comment_id,
                "ok": all(a["ok"] for a in actions),
                "actions": actions,
            }
        )
    overall_ok = all(item["ok"] for item in items)  # empty selection -> True

    if json_mode:
        emit_result(
            {"pr": pr_number, "count": len(items), "ok": overall_ok, "resolved": items},
            json_mode=True,
        )
    else:
        emit_result(_render_resolve(pr_number, items), json_mode=False)
    return EXIT_SUCCESS if overall_ok else EXIT_USER_ERROR


def cmd_review_overview(args: argparse.Namespace) -> int:
    emit_overview(
        "qodo-cli review",
        review_sections(),
        json_mode=bool(getattr(args, "json", False)),
    )
    return EXIT_SUCCESS


def _no_verb(args: argparse.Namespace) -> int:
    # `qodo review` with no sub-verb prints the noun's overview.
    return cmd_review_overview(args)


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "review",
        aliases=["pr"],
        help="Triage the Qodo bot's PR review comments (cites qodo-pr-resolver).",
    )
    add_json_flag(p)
    p.set_defaults(func=_no_verb, json=False)
    verb = p.add_subparsers(dest="review_command", parser_class=type(p))

    listp = verb.add_parser("list", help="List the Qodo bot's comments on the PR.")
    listp.add_argument("--pr", type=int, help="PR number (default: detect from branch).")
    listp.add_argument(
        "--kind",
        choices=["inline", "summary", "all"],
        default="all",
        help="Filter by comment kind; 'inline' hides the summary rollups (default: all).",
    )
    add_json_flag(listp)
    listp.set_defaults(func=cmd_review_list)

    resolvep = verb.add_parser(
        "resolve",
        help="Reply to, acknowledge, and resolve Qodo comment(s).",
    )
    resolvep.add_argument(
        "comment_id",
        nargs="*",
        help="Inline review comment id(s) to resolve (omit when using --all/--severity).",
    )
    resolvep.add_argument(
        "--all",
        action="store_true",
        help="Resolve every inline Qodo comment on the PR.",
    )
    resolvep.add_argument(
        "--severity",
        help="Only resolve inline comments of this severity (HIGH/MEDIUM/LOW).",
    )
    resolvep.add_argument("--reply", help="Optional reply body to post before acknowledging.")
    resolvep.add_argument(
        "--sign",
        action="store_true",
        help="Append the culture.yaml nick signature to --reply (at most once).",
    )
    resolvep.add_argument(
        "--no-resolve-thread",
        action="store_true",
        help="Skip GraphQL review-thread resolution (post the +1 reaction only).",
    )
    resolvep.add_argument("--pr", type=int, help="PR number (default: detect from branch).")
    add_json_flag(resolvep)
    resolvep.set_defaults(func=cmd_review_resolve)

    ov = verb.add_parser("overview", help="Describe the qodo-cli review noun.")
    ov.add_argument("target", nargs="?", help="Ignored — overview describes the review noun.")
    add_json_flag(ov)
    ov.set_defaults(func=cmd_review_overview)
