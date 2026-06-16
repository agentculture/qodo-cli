"""Markdown catalog for ``qodo-cli explain <path>``.

Each entry is verbatim markdown. Keys are command-path tuples. The empty tuple
and ``("qodo-cli",)`` both resolve to the root entry.

Keep bodies self-contained: an agent reading one entry should get enough
context without chaining reads.
"""

from __future__ import annotations

# The console script is `qodo` while the CLI self-identifies (argparse prog) and
# the dist are `qodo-cli`. The agent-first rubric runs `explain <script-name>`,
# i.e. `explain qodo`, so the root entry is keyed under both spellings below.
_ROOT = """\
# qodo-cli

A clonable template for AgentCulture mesh agents. It carries an agent-first CLI
(cited from the teken `python-cli` reference), a mesh identity (`culture.yaml` +
`CLAUDE.md`), the canonical guildmaster skill kit under `.claude/skills/`, and a
buildable/deployable package baseline. Clone it, rename the package, edit
`culture.yaml`, and you have a new agent.

## Verbs

- `qodo-cli whoami` — identity probe from `culture.yaml`.
- `qodo-cli learn` — structured self-teaching prompt.
- `qodo-cli explain <path>` — markdown docs for any noun/verb.
- `qodo-cli overview` — descriptive snapshot of the agent.
- `qodo-cli doctor` — check the agent-identity invariants.
- `qodo-cli cli overview` — describe the CLI surface.

## Exit-code policy

- `0` success
- `1` user-input error
- `2` environment / setup error
- `3+` reserved

## See also

- `qodo-cli explain whoami`
- `qodo-cli explain doctor`
"""

_WHOAMI = """\
# qodo-cli whoami

Reports the agent's identity from `culture.yaml`: nick (`suffix`), backend,
served model, and the package version. Read-only.

## Usage

    qodo-cli whoami
    qodo-cli whoami --json
"""

_LEARN = """\
# qodo-cli learn

Prints a structured self-teaching prompt covering purpose, command map,
exit-code policy, `--json` support, and the `explain` pointer.

## Usage

    qodo-cli learn
    qodo-cli learn --json
"""

_EXPLAIN = """\
# qodo-cli explain <path>

Prints markdown documentation for any noun/verb path. Unlike `--help` (terse,
positional), `explain` is global and addressable by path.

## Usage

    qodo-cli explain qodo-cli
    qodo-cli explain whoami
    qodo-cli explain --json <path>
"""

_OVERVIEW = """\
# qodo-cli overview

Read-only descriptive snapshot of the agent: identity (from `culture.yaml`), the
verb surface, and the sibling-pattern artifacts the template carries. Accepts an
ignored `target` so a stray path never hard-fails.

## Usage

    qodo-cli overview
    qodo-cli overview --json
"""

_DOCTOR = """\
# qodo-cli doctor

Checks the agent-identity invariants `steward doctor` verifies:
prompt-file-present and backend-consistency (`claude` → `CLAUDE.md`), plus a
skills-present check. Exits 1 when unhealthy.

## Usage

    qodo-cli doctor
    qodo-cli doctor --json
"""

_CLI = """\
# qodo-cli cli

Noun group for CLI-surface introspection. `cli overview` describes the CLI
itself (distinct from the global `overview`, which describes the agent).

## Usage

    qodo-cli cli overview
    qodo-cli cli overview --json
"""


ENTRIES: dict[tuple[str, ...], str] = {
    (): _ROOT,
    ("qodo-cli",): _ROOT,
    ("qodo",): _ROOT,  # console-script name; satisfies the rubric's `explain <self>` check
    ("whoami",): _WHOAMI,
    ("learn",): _LEARN,
    ("explain",): _EXPLAIN,
    ("overview",): _OVERVIEW,
    ("doctor",): _DOCTOR,
    ("cli",): _CLI,
    ("cli", "overview"): _CLI,
}
