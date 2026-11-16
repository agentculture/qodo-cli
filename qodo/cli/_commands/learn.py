"""``qodo-cli learn`` — the learnability affordance.

Prints a structured self-teaching prompt. Must satisfy the agent-first rubric:
>=200 chars and mention purpose, command map, exit codes, --json, and explain.
"""

from __future__ import annotations

import argparse

from qodo import __version__
from qodo.cli import _build
from qodo.cli._output import add_json_flag, emit_result

_TEXT = """\
qodo-cli — an unofficial, community CLI to manage Qodo from your terminal.

Purpose
-------
Run Qodo's core jobs natively, in zero-dependency Python. `qodo-cli rules` surfaces
your org's coding rules by semantic search (reusing the API key already in
~/.qodo/config.json); `qodo-cli review` (a.k.a. `qodo-cli pr`) triages and resolves the
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
  qodo-cli config show          Show/validate/init the repo Qodo reviewer config.
  qodo-cli config propose       Check a committed Qodo config change against a base and
                                return it only if safe (read-only; never opens a PR).
  qodo-cli whoami               Identity from culture.yaml.
  qodo-cli learn                This self-teaching prompt.
  qodo-cli explain <path>...    Markdown docs for any noun/verb path.
  qodo-cli overview             Descriptive snapshot of the agent.
  qodo-cli doctor               Check agent-identity invariants + Qodo setup.
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
  qodo-cli explain config propose
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
            {"path": ["config", "show"], "summary": "Manage the repo Qodo reviewer config."},
            {
                "path": ["config", "propose"],
                "summary": "Check a committed Qodo config change against a base; return it "
                "only if safe (read-only).",
            },
            {"path": ["whoami"], "summary": "Identity probe from culture.yaml."},
            {"path": ["learn"], "summary": "Self-teaching prompt."},
            {"path": ["explain"], "summary": "Markdown docs by path."},
            {"path": ["overview"], "summary": "Descriptive snapshot of the agent."},
            {"path": ["doctor"], "summary": "Check agent-identity invariants + Qodo setup."},
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


def _bridge_text() -> str:
    """The registry's verbs that the text above does not name (built from the exposure policy)."""
    lines = _build.missing_lines(_TEXT)
    if not lines:
        return ""
    return "\nMore verbs (from the verb registry)\n-----------------------------------\n" + "".join(
        f"  qodo-cli {line}\n" for line in lines
    )


def _bridge_commands(payload: dict[str, object]) -> list[dict[str, object]]:
    known = {tuple(c["path"]) for c in payload["commands"]}  # type: ignore[union-attr]
    return [
        {"path": [v.noun, v.name], "summary": v.summary}
        for v in _build.cli_verbs()
        if (v.noun, v.name) not in known
    ]


def cmd_learn(args: argparse.Namespace) -> int:
    if getattr(args, "json", False):
        payload = _as_json_payload()
        commands = [*payload["commands"], *_bridge_commands(payload)]  # type: ignore[misc]
        payload["commands"] = commands
        emit_result(payload, json_mode=True)
    else:
        emit_result(_TEXT + _bridge_text(), json_mode=False)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "learn",
        help="Print a structured self-teaching prompt for agent consumers.",
    )
    add_json_flag(p)
    p.set_defaults(func=cmd_learn)
