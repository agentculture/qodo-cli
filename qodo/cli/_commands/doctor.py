"""``qodo-cli doctor`` — check agent-identity invariants and Qodo setup.

Two groups of checks, reported in one rubric-shaped contract
``{healthy, checks: [{id, passed, severity, message, remediation}]}``:

* **agent identity** (only in a source checkout with ``culture.yaml``):
  prompt-file-present + backend-consistency (mirrors ``steward doctor``), plus a
  ``.claude/skills/`` present check.
* **Qodo setup** (always, against the current repo): whether the repo carries a
  Qodo reviewer config (``.pr_agent.toml`` / ``best_practices.md``) and whether
  client API credentials (``~/.qodo/config.json`` / ``QODO_API_KEY``) are
  available for ``qodo rules``. These are advisory — their ``remediation`` guides
  an agent through setting them up.

``healthy`` is true when every **error**-severity check passes; ``warning`` /
``info`` checks surface guidance without failing the command (so ``doctor`` is
useful in any repo without hard-failing when optional configs are absent).
Read-only.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from qodo.cli._commands.whoami import find_culture_yaml, read_agent_fields
from qodo.cli._output import add_json_flag, emit_result

# backend → required prompt file (the backend-consistency mapping).
_PROMPT_FILE = {
    "claude": "CLAUDE.md",
    "colleague": "AGENTS.colleague.md",
    "acp": "AGENTS.md",
    "gemini": "GEMINI.md",
}

_CONFIG_DOCS = "https://docs.qodo.ai/qodo-documentation/qodo-merge/configuration/configuration-file"
_BEST_PRACTICES_DOCS = "https://qodo-merge-docs.qodo.ai/core-abilities/auto_best_practices/"


def _identity_checks(root: Path, backend: str) -> list[dict[str, object]]:
    """Agent-identity invariants (prompt-file-present, backend-consistency, skills)."""
    checks: list[dict[str, object]] = []

    expected = _PROMPT_FILE.get(backend)
    if expected is None:
        checks.append(
            {
                "id": "backend_consistency",
                "passed": False,
                "severity": "error",
                "message": f"unknown backend '{backend}' in culture.yaml",
                "remediation": f"set backend to one of: {', '.join(sorted(_PROMPT_FILE))}",
            }
        )
    else:
        present = (root / expected).is_file()
        checks.append(
            {
                "id": "prompt_file_present",
                "passed": present,
                "severity": "error",
                "message": (
                    f"backend '{backend}' requires {expected} — "
                    + ("present" if present else "missing")
                ),
                "remediation": "" if present else f"create {expected} at the repo root",
            }
        )

    skills_dir = root / ".claude" / "skills"
    has_skills = skills_dir.is_dir() and any(skills_dir.iterdir())
    checks.append(
        {
            "id": "skills_present",
            "passed": has_skills,
            "severity": "warning",
            "message": (
                ".claude/skills/ vendored" if has_skills else ".claude/skills/ missing or empty"
            ),
            "remediation": (
                "" if has_skills else "vendor the skill kit (see docs/skill-sources.md)"
            ),
        }
    )
    return checks


def _qodo_setup_checks(repo_root: Path, home: Path) -> list[dict[str, object]]:
    """Detect the Qodo configs a repo using qodo-cli should carry.

    Advisory (``warning`` / ``info``): the remediation text guides an agent
    through creating each one. None of these flip ``healthy``.
    """
    checks: list[dict[str, object]] = []

    pr_agent = (repo_root / ".pr_agent.toml").is_file()
    checks.append(
        {
            "id": "pr_agent_config_present",
            "passed": pr_agent,
            "severity": "warning",
            "message": (
                ".pr_agent.toml present (Qodo reviewer config)"
                if pr_agent
                else "no .pr_agent.toml — Qodo reviews this repo using only inferred conventions"
            ),
            "remediation": (
                ""
                if pr_agent
                else (
                    "add a minimal .pr_agent.toml with a [pr_reviewer] section "
                    "(extra_instructions) describing this repo's intentional patterns. "
                    f"Docs: {_CONFIG_DOCS}"
                )
            ),
        }
    )

    best_practices = (repo_root / "best_practices.md").is_file()
    checks.append(
        {
            "id": "best_practices_present",
            "passed": best_practices,
            "severity": "warning",
            "message": (
                "best_practices.md present"
                if best_practices
                else "no best_practices.md — Qodo won't flag this repo's best-practice violations"
            ),
            "remediation": (
                ""
                if best_practices
                else (
                    "add best_practices.md at the repo root listing this repo's coding "
                    f"standards (auto-referenced by the reviewer). Docs: {_BEST_PRACTICES_DOCS}"
                )
            ),
        }
    )

    has_client_cfg = (home / ".qodo" / "config.json").is_file() or bool(
        os.environ.get("QODO_API_KEY")
    )
    checks.append(
        {
            "id": "qodo_client_config_present",
            "passed": has_client_cfg,
            "severity": "info",
            "message": (
                "Qodo API credentials available (for 'qodo rules')"
                if has_client_cfg
                else "no ~/.qodo/config.json and QODO_API_KEY unset (needed for 'qodo rules')"
            ),
            "remediation": (
                ""
                if has_client_cfg
                else (
                    'create ~/.qodo/config.json with an "API_KEY" (or export QODO_API_KEY). '
                    "Only 'qodo rules' needs it; 'qodo review' uses your provider-CLI auth."
                )
            ),
        }
    )
    return checks


def _repo_root(start: Path) -> Path:
    """The git repo root at/above ``start`` (where Qodo reads its config), or ``start``."""
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    return start


def _is_healthy(checks: list[dict[str, object]]) -> bool:
    """Healthy iff every error-severity check passed (advisory checks never fail)."""
    return all(c["passed"] for c in checks if c["severity"] == "error")


def _diagnose() -> dict[str, object]:
    checks: list[dict[str, object]] = []

    cfg = find_culture_yaml()
    if cfg is not None:
        checks.extend(_identity_checks(cfg.parent, str(read_agent_fields()["backend"])))
    else:
        checks.append(
            {
                "id": "source_checkout",
                "passed": True,
                "severity": "info",
                "message": "no culture.yaml alongside the package; agent-identity checks skipped",
                "remediation": "",
            }
        )

    checks.extend(_qodo_setup_checks(_repo_root(Path.cwd()), Path.home()))

    return {"healthy": _is_healthy(checks), "checks": checks}


def _mark(check: dict[str, object]) -> str:
    if check["passed"]:
        return "ok"
    return {"error": "FAIL", "warning": "WARN"}.get(str(check["severity"]), "note")


def cmd_doctor(args: argparse.Namespace) -> int:
    report = _diagnose()
    json_mode = bool(getattr(args, "json", False))
    if json_mode:
        emit_result(report, json_mode=True)
    else:
        status = "healthy" if report["healthy"] else "unhealthy"
        lines = [f"qodo-cli doctor: {status}", ""]
        for check in report["checks"]:  # type: ignore[attr-defined]
            lines.append(f"[{_mark(check)}] {check['id']}: {check['message']}")
            if not check["passed"] and check["remediation"]:
                lines.append(f"  hint: {check['remediation']}")
        emit_result("\n".join(lines), json_mode=False)
    return 0 if report["healthy"] else 1


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "doctor",
        help="Check agent-identity invariants and Qodo setup (configs); guides any fixes.",
    )
    add_json_flag(p)
    p.set_defaults(func=cmd_doctor)
