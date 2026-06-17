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
- `qodo-cli config show|validate|init` — manage the repo Qodo reviewer config.
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

When `--scope` is omitted, the scope is **auto-detected** like `qodo-get-rules`
does: the `org/repo` slug from the git `origin` (SSH + HTTPS forms) and the
module name from a `modules/<name>/` path. Nothing detectable → `scopes` is
omitted entirely (never sent empty). `--scope` overrides; `--no-scope` forces
omission.

## Usage

    qodo-cli rules get "validate all user input at trust boundaries"
    qodo-cli rules get "<query>" --top-k 10 --scope org/repo
    qodo-cli rules get "<query>" --no-scope
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
vendored): it drives your existing provider CLI (`gh` / `glab`) to find the open
PR/MR, list the comments authored by a Qodo bot (`qodo-code-review`,
`qodo-merge`, `qodo-ai`, `pr-agent-pro`), and reply to / acknowledge / resolve
them. Reuses your provider-CLI auth — no new credentials.

GitHub (incl. GitHub Enterprise via your `gh` host config) and GitLab (via
`glab`) are wired; Azure/Bitbucket/Gerrit are recognised but deferred (see the
citation ledger). On GitLab the resolvable unit is the MR *discussion*: `resolve`
replies to and marks the note's discussion resolved (GitLab has no `+1` marker).
The code-fixing loop stays with the calling agent — this surface is the
deterministic detect/list/reply/acknowledge/resolve slice.

`review list` parses each comment body into structured triage fields —
`severity` (from the badge: HIGH/MEDIUM/LOW), `type` and `categories` (the
`<code>` chips), `description` (the `<pre>` block), and `agent_prompt` (the
remediation block) — degrading to title-only when a body isn't recognised.
`--kind {inline,summary,all}` filters out the non-actionable summary rollups.

`review resolve` is best-effort and reports every action (reply / acknowledge /
resolve-thread) per comment, so a posted reply whose acknowledgement failed
reads as partial success, not total failure (exit 1). It posts the `+1` reaction
and, by default, resolves the GitHub review **thread** via the GraphQL
`resolveReviewThread` mutation (`--no-resolve-thread` to skip; falls back to the
reaction when no thread maps to the comment).

## Usage

    qodo-cli review list
    qodo-cli review list --pr 123 --kind inline --json
    qodo-cli review resolve <comment-id> --reply "Fixed in <sha>." --sign
    qodo-cli review resolve --all --severity HIGH
    qodo-cli review resolve <id> --no-resolve-thread
    qodo-cli review overview

`--sign` appends the `culture.yaml` nick signature (`- <nick> (Claude)`) to
`--reply`, at most once. `--all` / `--severity` resolve every matching inline
comment in one call.

## See also

- `qodo-cli explain rules`
- `docs/qodo-skills-sources.md` — the citation ledger
"""

_CONFIG = """\
# qodo-cli config

Manage the **repo-level** Qodo reviewer config — `.pr_agent.toml` (the Qodo Merge
`[pr_reviewer]` section) and `best_practices.md`. These are the levers that make
Qodo's reviews of *this* repo accurate: without them Qodo falls back to inferred
conventions and raises false positives. Distinct from the *client*
`~/.qodo/config.json` that `qodo rules` reads.

`show` and `validate` are read-only; `init` scaffolds the two files when absent
and never overwrites without `--force`. Read from the current git repo root
(where Qodo reads its config). Cite-faithful to Qodo Merge's configuration docs.

## Usage

    qodo-cli config show
    qodo-cli config validate          # exit 1 if invalid
    qodo-cli config init [--force]
    qodo-cli config overview
    qodo-cli config show --json

## See also

- `qodo-cli explain review`
- `qodo-cli explain doctor` — `doctor` also reports whether these files exist
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
    ("config",): _CONFIG,
    ("config", "show"): _CONFIG,
    ("config", "validate"): _CONFIG,
    ("config", "init"): _CONFIG,
    ("config", "overview"): _CONFIG,
    ("whoami",): _WHOAMI,
    ("learn",): _LEARN,
    ("explain",): _EXPLAIN,
    ("overview",): _OVERVIEW,
    ("doctor",): _DOCTOR,
    ("cli",): _CLI,
    ("cli", "overview"): _CLI,
}
