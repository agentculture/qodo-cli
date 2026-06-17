"""``qodo review`` (a.k.a. ``qodo pr``) — triage the Qodo bot's PR comments.

Native reimplementation of ``qodo-ai/qodo-skills`` ``qodo-pr-resolver``: it
drives the user's existing provider CLI (``gh``) to find the open PR for the
current branch, list the Qodo bot's review comments, and reply to / acknowledge
them. The provider mechanics live in :mod:`qodo.cli._providers`; this module is
the CLI surface. The *fixing* loop (read files, generate a fix, edit) stays with
the calling agent. See ``docs/qodo-skills-sources.md`` for the citation ledger.
"""

from __future__ import annotations

import argparse

from qodo.cli import _providers
from qodo.cli._commands.overview import emit_overview, review_sections
from qodo.cli._errors import EXIT_USER_ERROR, CliError
from qodo.cli._output import emit_result


def _resolve_pr_number(args: argparse.Namespace) -> tuple[int, dict | None]:
    """Return ``(pr_number, pr_record)``; honour an explicit ``--pr`` override."""
    if getattr(args, "pr", None):
        return int(args.pr), None
    branch = _providers.current_branch()
    pr = _providers.find_open_pr(branch)
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
        loc = ""
        if comment.get("path"):
            loc = f" ({comment['path']}:{comment.get('line')})"
        rid = f" [id {comment['id']}]" if comment.get("id") is not None else ""
        lines.append(f"{i}. [{comment['kind']}]{loc} {comment['title']}{rid}")
    return "\n".join(lines).rstrip()


def cmd_review_list(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    provider = _providers.detect_provider(_providers.remote_url())
    _providers.require_github(provider)
    pr_number, pr = _resolve_pr_number(args)
    comments = _providers.fetch_qodo_comments(pr_number)
    if json_mode:
        emit_result(
            {
                "provider": provider,
                "pr": pr or {"number": pr_number},
                "count": len(comments),
                "comments": comments,
            },
            json_mode=True,
        )
    else:
        emit_result(_render_comments(pr_number, pr, comments), json_mode=False)
    return 0


def cmd_review_resolve(args: argparse.Namespace) -> int:
    json_mode = bool(getattr(args, "json", False))
    provider = _providers.detect_provider(_providers.remote_url())
    _providers.require_github(provider)
    pr_number, _ = _resolve_pr_number(args)
    actions = _providers.resolve_comment(pr_number, int(args.comment_id), reply=args.reply)
    if json_mode:
        emit_result(
            {"pr": pr_number, "comment_id": int(args.comment_id), "actions": actions},
            json_mode=True,
        )
    else:
        emit_result(
            f"comment {args.comment_id} on PR #{pr_number}: {', '.join(actions)}",
            json_mode=False,
        )
    return 0


def cmd_review_overview(args: argparse.Namespace) -> int:
    emit_overview(
        "qodo-cli review",
        review_sections(),
        json_mode=bool(getattr(args, "json", False)),
    )
    return 0


def _no_verb(args: argparse.Namespace) -> int:
    # `qodo review` with no sub-verb prints the noun's overview.
    return cmd_review_overview(args)


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "review",
        aliases=["pr"],
        help="Triage the Qodo bot's PR review comments (cites qodo-pr-resolver).",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=_no_verb, json=False)
    verb = p.add_subparsers(dest="review_command", parser_class=type(p))

    listp = verb.add_parser("list", help="List the Qodo bot's comments on the PR.")
    listp.add_argument("--pr", type=int, help="PR number (default: detect from branch).")
    listp.add_argument("--json", action="store_true", help="Emit structured JSON.")
    listp.set_defaults(func=cmd_review_list)

    resolvep = verb.add_parser("resolve", help="Reply to and acknowledge a Qodo comment.")
    resolvep.add_argument("comment_id", help="The inline review comment id to resolve.")
    resolvep.add_argument("--reply", help="Optional reply body to post before acknowledging.")
    resolvep.add_argument("--pr", type=int, help="PR number (default: detect from branch).")
    resolvep.add_argument("--json", action="store_true", help="Emit structured JSON.")
    resolvep.set_defaults(func=cmd_review_resolve)

    ov = verb.add_parser("overview", help="Describe the qodo-cli review noun.")
    ov.add_argument("target", nargs="?", help="Ignored — overview describes the review noun.")
    ov.add_argument("--json", action="store_true", help="Emit structured JSON.")
    ov.set_defaults(func=cmd_review_overview)
