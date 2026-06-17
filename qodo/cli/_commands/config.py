"""``qodo config`` — manage the repo-level Qodo reviewer config.

Maintains the repository's ``.pr_agent.toml`` (the Qodo Merge ``[pr_reviewer]``
config) and ``best_practices.md`` — the levers that make Qodo's reviews of *this*
repo accurate (a missing config is why Qodo falls back to inferred conventions
and raises false positives). This is distinct from the *client*
``~/.qodo/config.json`` that :mod:`qodo.cli._qodo_api` reads for ``qodo rules``.

``show`` / ``validate`` are read-only; ``init`` scaffolds the two files when
absent (never overwriting without ``--force``). Cite-faithful to Qodo Merge's
configuration docs; see ``docs/qodo-skills-sources.md``.
"""

from __future__ import annotations

import argparse
import tomllib
from pathlib import Path

from qodo.cli._commands.overview import emit_overview
from qodo.cli._errors import EXIT_SUCCESS, EXIT_USER_ERROR, CliError
from qodo.cli._output import add_json_flag, emit_result

_CONFIG_DOCS = "https://docs.qodo.ai/qodo-documentation/qodo-merge/configuration/configuration-file"
_BEST_PRACTICES_DOCS = "https://qodo-merge-docs.qodo.ai/core-abilities/auto_best_practices/"

_PR_AGENT_FILE = ".pr_agent.toml"
_BEST_PRACTICES_FILE = "best_practices.md"

# Minimal scaffolds (per Qodo's guidance: override only what you need).
_PR_AGENT_TEMPLATE = '''\
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

_BEST_PRACTICES_TEMPLATE = """\
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
    pr_agent = repo_root / _PR_AGENT_FILE
    best_practices = repo_root / _BEST_PRACTICES_FILE

    pr_agent_info: dict[str, object] = {"present": pr_agent.is_file(), "path": _PR_AGENT_FILE}
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
        "path": _BEST_PRACTICES_FILE,
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


def _show_pr_agent_lines(pr_agent: dict[str, object]) -> list[str]:
    if not pr_agent["present"]:
        return [f"{_PR_AGENT_FILE}: absent"]
    toml_ok = "valid" if pr_agent.get("valid_toml") else "INVALID TOML"
    lines = [f"{_PR_AGENT_FILE}: present ({toml_ok})"]
    if pr_agent.get("valid_toml"):
        sections = ", ".join(pr_agent.get("sections") or []) or "(none)"
        lines.append(f"  sections: {sections}")
    return lines


def _show_bp_line(bp: dict[str, object]) -> str:
    if not bp["present"]:
        return f"{_BEST_PRACTICES_FILE}: absent"
    if bp.get("readable") is False:
        return f"{_BEST_PRACTICES_FILE}: present, UNREADABLE ({bp.get('error')})"
    empty = "" if bp.get("non_empty") else " (empty)"
    return f"{_BEST_PRACTICES_FILE}: present, {bp.get('bytes')} bytes{empty}"


def _render_show(info: dict[str, object]) -> str:
    pr_agent = info["pr_agent"]
    bp = info["best_practices"]
    lines = [f"# Qodo reviewer config — {info['repo_root']}", ""]
    lines.extend(_show_pr_agent_lines(pr_agent))
    lines.append(_show_bp_line(bp))
    if not pr_agent["present"] and not bp["present"]:
        lines.append("")
        lines.append("No reviewer config — run `qodo config init` to scaffold it.")
    return "\n".join(lines).rstrip()


def cmd_config_show(args: argparse.Namespace) -> int:
    info = _inspect(_repo_root(Path.cwd()))
    json_mode = bool(getattr(args, "json", False))
    if json_mode:
        emit_result(info, json_mode=True)
    else:
        emit_result(_render_show(info), json_mode=False)
    return EXIT_SUCCESS


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
                else f"no {_PR_AGENT_FILE} or {_BEST_PRACTICES_FILE} found"
            ),
            "remediation": (
                "" if any_present else "run `qodo config init` to scaffold a minimal config"
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
                    f"{_PR_AGENT_FILE} parses as TOML"
                    if toml_ok
                    else f"{_PR_AGENT_FILE} is not valid TOML: {pr_agent.get('error')}"
                ),
                "remediation": "" if toml_ok else f"fix the TOML syntax in {_PR_AGENT_FILE}",
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
                    else f"no [pr_reviewer] section in {_PR_AGENT_FILE}"
                ),
                "remediation": (
                    ""
                    if has_reviewer
                    else "add a [pr_reviewer] section with extra_instructions. "
                    f"Docs: {_CONFIG_DOCS}"
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
                f"{_BEST_PRACTICES_FILE} present and non-empty"
                if bp_ok
                else f"{_BEST_PRACTICES_FILE} missing or empty"
            ),
            "remediation": (
                ""
                if bp_ok
                else f"add {_BEST_PRACTICES_FILE} with this repo's standards. "
                f"Docs: {_BEST_PRACTICES_DOCS}"
            ),
        }
    )
    return checks


def _check_mark(check: dict[str, object]) -> str:
    if check["ok"]:
        return "ok"
    return "FAIL" if check["severity"] == "error" else "warn"


def _render_validate(valid: bool, checks: list[dict[str, object]]) -> str:
    lines = [f"qodo config: {'valid' if valid else 'invalid'}", ""]
    for check in checks:
        lines.append(f"[{_check_mark(check)}] {check['id']}: {check['message']}")
        if not check["ok"] and check["remediation"]:
            lines.append(f"  hint: {check['remediation']}")
    return "\n".join(lines)


def cmd_config_validate(args: argparse.Namespace) -> int:
    checks = _validation_checks(_inspect(_repo_root(Path.cwd())))
    valid = all(c["ok"] for c in checks if c["severity"] == "error")
    json_mode = bool(getattr(args, "json", False))
    if json_mode:
        emit_result({"valid": valid, "checks": checks}, json_mode=True)
    else:
        emit_result(_render_validate(valid, checks), json_mode=False)
    return EXIT_SUCCESS if valid else EXIT_USER_ERROR


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
                "`qodo config init`",
            )
        to_write.append((name, content))
    return to_write, skipped


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
    repo_root = _repo_root(Path.cwd())
    force = bool(getattr(args, "force", False))
    targets = {
        _PR_AGENT_FILE: _PR_AGENT_TEMPLATE.format(
            docs=_CONFIG_DOCS, best_practices=_BEST_PRACTICES_FILE
        ),
        _BEST_PRACTICES_FILE: _BEST_PRACTICES_TEMPLATE.format(repo=repo_root.name),
    }
    to_write, skipped = _plan_init_writes(repo_root, targets, force)
    written: list[str] = []
    for name, content in to_write:
        (repo_root / name).write_text(content, encoding="utf-8")
        written.append(name)

    json_mode = bool(getattr(args, "json", False))
    if json_mode:
        emit_result(
            {"repo_root": str(repo_root), "written": written, "skipped": skipped, "forced": force},
            json_mode=True,
        )
    else:
        emit_result(_render_init(repo_root, written, skipped), json_mode=False)
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
                "~/.qodo/config.json that `qodo rules` reads",
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
    # `qodo config` with no sub-verb prints the noun's overview.
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
