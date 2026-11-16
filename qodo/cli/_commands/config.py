"""``qodo-cli config`` — manage the repo-level Qodo reviewer config.

Maintains the repository's ``.pr_agent.toml`` (the Qodo Merge ``[pr_reviewer]``
config) and ``best_practices.md`` — the levers that make Qodo's reviews of *this*
repo accurate (a missing config is why Qodo falls back to inferred conventions
and raises false positives). This is distinct from the *client*
``~/.qodo/config.json`` that :mod:`qodo.api` (rules) reads for ``qodo-cli rules``.

``show`` / ``validate`` are read-only; ``init`` scaffolds the two files when
absent (never overwriting without ``--force``). Cite-faithful to Qodo Merge's
configuration docs; see ``docs/qodo-skills-sources.md``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from qodo import api
from qodo.cli._commands.overview import emit_overview
from qodo.cli._errors import EXIT_SUCCESS, EXIT_USER_ERROR
from qodo.cli._output import add_json_flag, emit_result


def _show_pr_agent_lines(pr_agent: dict[str, object]) -> list[str]:
    if not pr_agent["present"]:
        return [f"{api.PR_AGENT_FILE}: absent"]
    toml_ok = "valid" if pr_agent.get("valid_toml") else "INVALID TOML"
    lines = [f"{api.PR_AGENT_FILE}: present ({toml_ok})"]
    if pr_agent.get("valid_toml"):
        sections = ", ".join(pr_agent.get("sections") or []) or "(none)"
        lines.append(f"  sections: {sections}")
    return lines


def _show_bp_line(bp: dict[str, object]) -> str:
    if not bp["present"]:
        return f"{api.BEST_PRACTICES_FILE}: absent"
    if bp.get("readable") is False:
        return f"{api.BEST_PRACTICES_FILE}: present, UNREADABLE ({bp.get('error')})"
    empty = "" if bp.get("non_empty") else " (empty)"
    return f"{api.BEST_PRACTICES_FILE}: present, {bp.get('bytes')} bytes{empty}"


def _render_show(info: dict[str, object]) -> str:
    pr_agent = info["pr_agent"]
    bp = info["best_practices"]
    lines = [f"# Qodo reviewer config — {info['repo_root']}", ""]
    lines.extend(_show_pr_agent_lines(pr_agent))
    lines.append(_show_bp_line(bp))
    if not pr_agent["present"] and not bp["present"]:
        lines.append("")
        lines.append("No reviewer config — run `qodo-cli config init` to scaffold it.")
    return "\n".join(lines).rstrip()


def cmd_config_show(args: argparse.Namespace) -> int:
    info = api.config_show()
    json_mode = bool(getattr(args, "json", False))
    if json_mode:
        emit_result(info, json_mode=True)
    else:
        emit_result(_render_show(info), json_mode=False)
    return EXIT_SUCCESS


def _check_mark(check: dict[str, object]) -> str:
    if check["ok"]:
        return "ok"
    return "FAIL" if check["severity"] == "error" else "warn"


def _render_validate(valid: bool, checks: list[dict[str, object]]) -> str:
    lines = [f"qodo-cli config: {'valid' if valid else 'invalid'}", ""]
    for check in checks:
        lines.append(f"[{_check_mark(check)}] {check['id']}: {check['message']}")
        if not check["ok"] and check["remediation"]:
            lines.append(f"  hint: {check['remediation']}")
    return "\n".join(lines)


def cmd_config_validate(args: argparse.Namespace) -> int:
    result = api.config_validate()
    checks, valid = result["checks"], result["valid"]
    json_mode = bool(getattr(args, "json", False))
    if json_mode:
        emit_result({"valid": valid, "checks": checks}, json_mode=True)
    else:
        emit_result(_render_validate(valid, checks), json_mode=False)
    return EXIT_SUCCESS if valid else EXIT_USER_ERROR


def _render_init(repo_root: Path, written: list[str], skipped: list[str]) -> str:
    lines = [f"# Qodo reviewer config — {repo_root}", ""]
    for name in written:
        lines.append(f"wrote {name}")
    for name in skipped:
        lines.append(f"skipped {name} (exists; use --force to overwrite)")
    if not written:
        lines.append("nothing written — both files already exist (use --force to overwrite)")
    return "\n".join(lines)


def cmd_config_init(args: argparse.Namespace) -> int:
    result = api.config_init(force=bool(getattr(args, "force", False)))
    json_mode = bool(getattr(args, "json", False))
    if json_mode:
        emit_result(result, json_mode=True)
    else:
        emit_result(
            _render_init(Path(result["repo_root"]), result["written"], result["skipped"]),
            json_mode=False,
        )
    return EXIT_SUCCESS


def _config_sections() -> list[dict[str, object]]:
    return [
        {
            "title": "Verbs",
            "items": [
                "config show — show the repo reviewer config (.pr_agent.toml + best_practices.md)",
                "config validate — validate the reviewer config (exit 1 if invalid)",
                "config init [--force] — scaffold a minimal reviewer config when absent",
                "config overview — describe the config noun (this command)",
            ],
        },
        {
            "title": "Scope",
            "items": [
                "the repo-level Qodo Merge reviewer config — distinct from the client "
                "~/.qodo/config.json that `qodo-cli rules` reads",
                ".pr_agent.toml ([pr_reviewer] extra_instructions) + best_practices.md",
                "read from the current git repo root (where Qodo reads it)",
            ],
        },
        {
            "title": "Conventions",
            "items": [
                "show/validate are read-only; init never overwrites without --force",
                "cite-faithful to Qodo Merge's configuration docs; supports --json",
            ],
        },
    ]


def cmd_config_overview(args: argparse.Namespace) -> int:
    emit_overview(
        "qodo-cli config",
        _config_sections(),
        json_mode=bool(getattr(args, "json", False)),
    )
    return EXIT_SUCCESS


def _no_verb(args: argparse.Namespace) -> int:
    # `qodo-cli config` with no sub-verb prints the noun's overview.
    return cmd_config_overview(args)


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "config",
        help="Manage the repo-level Qodo reviewer config (.pr_agent.toml / best_practices.md).",
    )
    add_json_flag(p)
    p.set_defaults(func=_no_verb, json=False)
    verb = p.add_subparsers(dest="config_command", parser_class=type(p))

    showp = verb.add_parser("show", help="Show the repo reviewer config.")
    add_json_flag(showp)
    showp.set_defaults(func=cmd_config_show)

    validatep = verb.add_parser("validate", help="Validate the repo reviewer config.")
    add_json_flag(validatep)
    validatep.set_defaults(func=cmd_config_validate)

    initp = verb.add_parser("init", help="Scaffold a minimal repo reviewer config when absent.")
    initp.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing config files instead of skipping them.",
    )
    add_json_flag(initp)
    initp.set_defaults(func=cmd_config_init)

    ov = verb.add_parser("overview", help="Describe the qodo-cli config noun.")
    ov.add_argument("target", nargs="?", help="Ignored — overview describes the config noun.")
    add_json_flag(ov)
    ov.set_defaults(func=cmd_config_overview)
