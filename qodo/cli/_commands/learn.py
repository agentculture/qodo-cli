"""``qodo-cli learn`` — the learnability affordance.

Prints a structured self-teaching prompt. Must satisfy the agent-first rubric:
>=200 chars and mention purpose, command map, exit codes, --json, and explain.
"""

from __future__ import annotations

import argparse

from qodo import __version__
from qodo.cli._output import emit_result

_TEXT = """\
qodo-cli — an unofficial, community CLI to manage Qodo from your terminal.

Purpose
-------
Run Qodo's core jobs natively, in zero-dependency Python. `qodo rules` surfaces
your org's coding rules by semantic search (reusing the API key already in
~/.qodo/config.json); `qodo review` (a.k.a. `qodo pr`) triages and resolves the
Qodo bot's PR review comments through your existing provider CLI (gh). Each verb
cites qodo-ai/qodo-skills as its behavioral source of truth — we point at the
skills, we do not fork, vendor, or npx-install them. Unofficial: not affiliated
with, authorized, or endorsed by Qodo.

Commands
--------
  qodo-cli rules get "<query>"  Semantic-search your org's Qodo rules.
  qodo-cli review list          List the Qodo bot's PR review comments.
  qodo-cli review resolve <id>  Reply to and acknowledge a Qodo comment.
  qodo-cli pr ...               Alias for `review`.
  qodo-cli whoami               Identity from culture.yaml.
  qodo-cli learn                This self-teaching prompt.
  qodo-cli explain <path>...    Markdown docs for any noun/verb path.
  qodo-cli overview             Descriptive snapshot of the agent.
  qodo-cli doctor               Check the agent-identity invariants.
  qodo-cli cli overview         Describe the CLI surface itself.

Machine-readable output
-----------------------
Every command supports --json. Errors in JSON mode emit
{"code", "message", "remediation"} to stderr. Stdout and stderr never mix.

Exit-code policy
----------------
  0 success
  1 user-input error (bad flag, bad path, missing arg)
  2 environment / setup error (missing ~/.qodo/config.json, gh absent, API error)
  3+ reserved

More detail
-----------
  qodo-cli explain rules
  qodo-cli explain review
"""


def _as_json_payload() -> dict[str, object]:
    return {
        "tool": "qodo-cli",
        "version": __version__,
        "purpose": "Unofficial community CLI to manage Qodo (rules + PR review).",
        "commands": [
            {"path": ["rules", "get"], "summary": "Semantic-search your org's Qodo rules."},
            {"path": ["review", "list"], "summary": "List the Qodo bot's PR comments."},
            {"path": ["review", "resolve"], "summary": "Reply to / acknowledge a comment."},
            {"path": ["whoami"], "summary": "Identity probe from culture.yaml."},
            {"path": ["learn"], "summary": "Self-teaching prompt."},
            {"path": ["explain"], "summary": "Markdown docs by path."},
            {"path": ["overview"], "summary": "Descriptive snapshot of the agent."},
            {"path": ["doctor"], "summary": "Check the agent-identity invariants."},
            {"path": ["cli", "overview"], "summary": "Describe the CLI surface."},
        ],
        "exit_codes": {
            "0": "success",
            "1": "user-input error",
            "2": "environment/setup error",
        },
        "json_support": True,
        "explain_pointer": "qodo-cli explain <path>",
    }


def cmd_learn(args: argparse.Namespace) -> int:
    if getattr(args, "json", False):
        emit_result(_as_json_payload(), json_mode=True)
    else:
        emit_result(_TEXT, json_mode=False)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "learn",
        help="Print a structured self-teaching prompt for agent consumers.",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_learn)
