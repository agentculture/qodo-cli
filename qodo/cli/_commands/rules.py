"""``qodo rules`` — surface your org's Qodo coding rules by semantic search.

Native reimplementation of ``qodo-ai/qodo-skills`` ``qodo-get-rules``: it reads
the API key already in ``~/.qodo/config.json`` and POSTs a semantic query to the
Qodo rules API, returning the relevance-ranked rules with their severity. The
HTTP mechanics live in :mod:`qodo.cli._qodo_api`; this module is the CLI surface
(argument parsing + rendering). See ``docs/qodo-skills-sources.md`` for the
citation ledger.
"""

from __future__ import annotations

import argparse

from qodo.cli import _qodo_api
from qodo.cli._commands.overview import emit_overview, rules_sections
from qodo.cli._output import emit_result


def _render_rules(query: str, rules: list[dict[str, object]]) -> str:
    lines = [f"# Qodo Rules — {query}", ""]
    if not rules:
        lines.append("No relevant rules found for this task.")
        return "\n".join(lines).rstrip()
    lines.append(f"{len(rules)} rule(s), most relevant first:")
    lines.append("")
    for rule in rules:
        name = rule.get("name", "(unnamed)")
        severity = rule.get("severity", "")
        content = rule.get("content", "")
        label = f" [{severity}]" if severity else ""
        lines.append(f"- **{name}**{label}: {content}")
    return "\n".join(lines).rstrip()


def cmd_rules_get(args: argparse.Namespace) -> int:
    query = args.query
    json_mode = bool(getattr(args, "json", False))
    rules = _qodo_api.search_rules(query, top_k=args.top_k, scopes=args.scopes)
    if json_mode:
        emit_result(
            {"query": query, "count": len(rules), "rules": rules},
            json_mode=True,
        )
    else:
        emit_result(_render_rules(query, rules), json_mode=False)
    return 0


def cmd_rules_overview(args: argparse.Namespace) -> int:
    emit_overview(
        "qodo-cli rules",
        rules_sections(),
        json_mode=bool(getattr(args, "json", False)),
    )
    return 0


def _no_verb(args: argparse.Namespace) -> int:
    # `qodo rules` with no sub-verb prints the noun's overview.
    return cmd_rules_overview(args)


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "rules",
        help="Surface your org's Qodo rules by semantic search (cites qodo-get-rules).",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=_no_verb, json=False)
    # Propagate the structured-error parser class to the nested subparsers.
    verb = p.add_subparsers(dest="rules_command", parser_class=type(p))

    get = verb.add_parser(
        "get",
        help="Semantic-search the Qodo rules API and print ranked rules.",
    )
    get.add_argument("query", help="The task / concern to search rules for.")
    get.add_argument(
        "--top-k",
        type=int,
        default=_qodo_api.DEFAULT_TOP_K,
        help=f"Max rules to request (default {_qodo_api.DEFAULT_TOP_K}).",
    )
    get.add_argument(
        "--scope",
        action="append",
        dest="scopes",
        metavar="SCOPE",
        help="Repository/module scope to pass through (repeatable).",
    )
    get.add_argument("--json", action="store_true", help="Emit structured JSON.")
    get.set_defaults(func=cmd_rules_get)

    ov = verb.add_parser("overview", help="Describe the qodo-cli rules noun.")
    ov.add_argument("target", nargs="?", help="Ignored — overview describes the rules noun.")
    ov.add_argument("--json", action="store_true", help="Emit structured JSON.")
    ov.set_defaults(func=cmd_rules_overview)
