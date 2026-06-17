"""``qodo-cli overview`` — read-only descriptive snapshot of the agent.

Describes the agent to an agent reader: identity (from culture.yaml), the verb
surface, and the sibling-pattern artifacts this template carries. The shared
section/render helpers here are reused by the ``cli`` noun's ``overview`` (see
:mod:`qodo.cli._commands.cli`).

Descriptive verbs never hard-fail on a missing target path — an optional
positional ``target`` is accepted and ignored (overview describes this agent,
not an external target), so ``overview <bogus-path>`` still exits 0.
"""

from __future__ import annotations

import argparse

from qodo.cli._commands.whoami import report
from qodo.cli._output import add_json_flag, emit_result

_ARTIFACTS = [
    "culture.yaml + AGENTS.colleague.md — mesh identity (suffix + backend)",
    ".claude/skills/ — the canonical guildmaster skill kit (cite-don't-import)",
    "docs/skill-sources.md — skill provenance ledger",
    "docs/qodo-skills-sources.md — Qodo-skills citation ledger (rules/review)",
    "pyproject.toml + .github/workflows/ — buildable, deployable package baseline",
]

_VERBS = [
    'rules get "<query>" — semantic-search your org\'s Qodo rules',
    "review (a.k.a. pr) — triage & resolve the Qodo bot's PR review comments",
    "config show|validate|init — manage the repo-level Qodo reviewer config",
    "whoami — identity probe (nick, version, backend, model)",
    "learn — structured self-teaching prompt",
    "explain <path> — markdown docs for a topic",
    "overview — this descriptive snapshot",
    "doctor — check agent-identity invariants + Qodo setup (configs), with fix guidance",
]


def agent_sections() -> list[dict[str, object]]:
    """Sections describing the agent (used by the global verb)."""
    ident = report()
    return [
        {
            "title": "Identity",
            "items": [
                f"nick: {ident['nick']}",
                f"version: {ident['version']}",
                f"backend: {ident['backend']}",
                f"model: {ident['model']}",
            ],
        },
        {"title": "Verbs", "items": list(_VERBS)},
        {"title": "Sibling-pattern artifacts", "items": list(_ARTIFACTS)},
    ]


def cli_sections() -> list[dict[str, object]]:
    """Sections describing the CLI surface itself (used by `cli overview`)."""
    return [
        {
            "title": "Verbs",
            "items": list(_VERBS) + ["cli overview — describe the CLI surface (this command)"],
        },
        {
            "title": "Conventions",
            "items": [
                "every command supports --json",
                "results to stdout, errors/diagnostics to stderr (never mixed)",
                "exit codes: 0 success, 1 user error, 2 environment error, 3+ reserved",
            ],
        },
    ]


def rules_sections() -> list[dict[str, object]]:
    """Sections describing the `rules` noun (used by `rules overview`)."""
    return [
        {
            "title": "Verbs",
            "items": [
                'rules get "<query>" [--scope S | --no-scope] — semantic-search '
                "your org's Qodo rules (scope auto-detected from the git origin)",
                "rules overview — describe the rules noun (this command)",
            ],
        },
        {
            "title": "Source",
            "items": [
                "cites qodo-ai/qodo-skills `qodo-get-rules` as the behavioral spec",
                "POST {base}/rules/search, reusing ~/.qodo/config.json (API_KEY/ENVIRONMENT_NAME)",
                "scope auto-detected from `git origin` org/repo + modules/<name>/ (cite-faithful)",
                "severity labels: ERROR, WARNING, RECOMMENDATION (relevance-ranked)",
            ],
        },
        {
            "title": "Conventions",
            "items": [
                "needs a pre-existing ~/.qodo/config.json (or QODO_API_KEY) — never prompts",
                "supports --json; exit 2 on missing config / API error",
            ],
        },
    ]


def review_sections() -> list[dict[str, object]]:
    """Sections describing the `review`/`pr` noun (used by `review overview`)."""
    return [
        {
            "title": "Verbs",
            "items": [
                "review list [--kind inline|summary] — list the Qodo bot's PR comments, "
                "with severity/type/description/agent_prompt parsed from each body",
                "review resolve <id...> | --all [--severity S] — reply (--sign), "
                "acknowledge, and resolve the review thread; best-effort per-action",
                "review overview — describe the review noun (this command)",
            ],
        },
        {
            "title": "Source",
            "items": [
                "cites qodo-ai/qodo-skills `qodo-pr-resolver` as the behavioral spec",
                "drives your existing gh — GitHub incl. Enterprise (via your gh host "
                "config); glab/az are follow-ups",
                "Qodo bots: qodo-code-review, qodo-merge, qodo-ai, pr-agent-pro(-staging)",
            ],
        },
        {
            "title": "Conventions",
            "items": [
                "reuses your provider-CLI auth — no new credentials",
                "thread resolution via GraphQL resolveReviewThread (--no-resolve-thread to skip)",
                "also reachable as `qodo pr`; supports --json",
            ],
        },
    ]


def render_text(subject: str, sections: list[dict[str, object]]) -> str:
    lines = [f"# {subject}", ""]
    for section in sections:
        lines.append(f"## {section['title']}")
        for item in section["items"]:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines).rstrip()


def emit_overview(subject: str, sections: list[dict[str, object]], *, json_mode: bool) -> None:
    if json_mode:
        emit_result({"subject": subject, "sections": sections}, json_mode=True)
    else:
        emit_result(render_text(subject, sections), json_mode=False)


def cmd_overview(args: argparse.Namespace) -> int:
    # `target` is accepted for rubric compatibility (descriptive verbs must not
    # hard-fail on a missing path) but overview describes this agent itself.
    emit_overview(
        "qodo-cli",
        agent_sections(),
        json_mode=bool(getattr(args, "json", False)),
    )
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "overview",
        help="Read-only descriptive snapshot of the agent (identity, verbs, artifacts).",
    )
    p.add_argument(
        "target",
        nargs="?",
        help="Ignored — overview always describes this agent itself. Accepted so a "
        "stray path argument never hard-fails.",
    )
    add_json_flag(p)
    p.set_defaults(func=cmd_overview)
