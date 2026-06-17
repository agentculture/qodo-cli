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

An unofficial, community CLI to manage **Qodo** from your terminal, in
zero-dependency Python. It runs Qodo's core jobs natively — `rules` (semantic
rule search) and `review`/`pr` (triage the Qodo bot's PR comments) — each citing
`qodo-ai/qodo-skills` as its behavioral source of truth (we point at the skills,
we do not fork or vendor them). It also carries an agent-first introspection
surface (`whoami`/`learn`/`explain`/`overview`/`doctor`) and a mesh identity
(`culture.yaml` + `AGENTS.colleague.md`). Not affiliated with Qodo Ltd.

## Verbs

- `qodo-cli rules get "<query>"` — semantic-search your org's Qodo rules.
- `qodo-cli review list` — list the Qodo bot's PR review comments.
- `qodo-cli review resolve <id>` — reply to and acknowledge a comment.
- `qodo-cli pr ...` — alias for `review`.
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

- `qodo-cli explain rules`
- `qodo-cli explain review`
- `qodo-cli explain doctor`
"""

_RULES = """\
# qodo-cli rules

Surface your org's Qodo coding rules by semantic search. Native reimplementation
of `qodo-ai/qodo-skills` `qodo-get-rules` (cited, not vendored): it reads the API
key already in `~/.qodo/config.json` and POSTs your query to the Qodo rules API,
returning the relevance-ranked rules with their severity
(`ERROR`/`WARNING`/`RECOMMENDATION`).

Reuses existing credentials — it never prompts. Errors (exit 2) when no API key
is available (`~/.qodo/config.json` absent and `QODO_API_KEY` unset).

## Usage

    qodo-cli rules get "validate all user input at trust boundaries"
    qodo-cli rules get "<query>" --top-k 10 --scope org/repo
    qodo-cli rules get "<query>" --json
    qodo-cli rules overview

## See also

- `qodo-cli explain review`
- `docs/qodo-skills-sources.md` — the citation ledger
"""

_REVIEW = """\
# qodo-cli review (a.k.a. qodo-cli pr)

Triage the Qodo bot's review comments on the current branch's PR. Native
reimplementation of `qodo-ai/qodo-skills` `qodo-pr-resolver` (cited, not
vendored): it drives your existing provider CLI (`gh`) to find the open PR, list
the comments authored by a Qodo bot (`qodo-code-review`, `qodo-merge`,
`qodo-ai`, `pr-agent-pro`), and reply to / acknowledge them. Reuses your
provider-CLI auth — no new credentials.

GitHub — including GitHub Enterprise, recognised via your `gh` host config — is
wired; GitLab/Azure/Bitbucket are recognised but deferred (see the citation
ledger). The code-fixing loop stays with the calling agent — this surface is the
deterministic detect/list/reply/acknowledge slice.

## Usage

    qodo-cli review list
    qodo-cli review list --pr 123 --json
    qodo-cli review resolve <comment-id> --reply "Fixed in <sha>."
    qodo-cli review overview

## See also

- `qodo-cli explain rules`
- `docs/qodo-skills-sources.md` — the citation ledger
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

Two groups of checks, emitted as
`{healthy, checks: [{id, passed, severity, message, remediation}]}`:

- **agent identity** (in a source checkout) — prompt-file-present and
  backend-consistency, mirroring `steward doctor` (this repo's backend is
  `colleague` → `AGENTS.colleague.md`), plus a `.claude/skills/` check.
- **Qodo setup** (any repo, against the current git root) — whether
  `.pr_agent.toml` and `best_practices.md` are present (tune Qodo's PR reviews)
  and whether `~/.qodo/config.json` / `QODO_API_KEY` is available (for
  `qodo rules`). These are advisory; each carries a `remediation` that guides
  setup.

`healthy` is true when every **error**-severity check passes; advisory
(`warning`/`info`) checks surface guidance without flipping it. Exits 1 only on
an error-severity failure.

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
    ("rules",): _RULES,
    ("rules", "get"): _RULES,
    ("rules", "overview"): _RULES,
    ("review",): _REVIEW,
    ("review", "list"): _REVIEW,
    ("review", "resolve"): _REVIEW,
    ("review", "overview"): _REVIEW,
    ("pr",): _REVIEW,  # `pr` is an alias for `review`
    ("whoami",): _WHOAMI,
    ("learn",): _LEARN,
    ("explain",): _EXPLAIN,
    ("overview",): _OVERVIEW,
    ("doctor",): _DOCTOR,
    ("cli",): _CLI,
    ("cli", "overview"): _CLI,
}
