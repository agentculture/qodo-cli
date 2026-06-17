# qodo-cli

An unofficial, community CLI and agent to manage **Qodo** — the AI code reviewer
and Qodo's other agents — from your terminal, in zero-dependency Python.

> **Unofficial.** Not affiliated with, authorized, or endorsed by Qodo. The Qodo
> name and trademark belong to Qodo Ltd. Using the Qodo surface (`rules`,
> `review`) requires your own Qodo subscription and credentials.

## What it does

`qodo-cli` runs Qodo's core jobs natively over the Python standard library — no
third-party runtime dependencies. Each Qodo command **cites**
[`qodo-ai/qodo-skills`](https://github.com/qodo-ai/qodo-skills) as its behavioral
source of truth and reimplements the deterministic mechanics; it does not fork,
vendor, or `npx`-install those skills (see
[`docs/qodo-skills-sources.md`](docs/qodo-skills-sources.md)).

| Command | What it does | Needs |
|---------|--------------|-------|
| `rules get "<query>"` | Semantic-search your org's Qodo coding rules, relevance-ranked with severity. | `~/.qodo/config.json` (or `QODO_API_KEY`) |
| `review` (alias `pr`) | List, reply to, acknowledge, and resolve the Qodo bot's PR review comments. | `gh` / `glab`, authenticated |
| `config` | Manage the repo-level Qodo reviewer config (`.pr_agent.toml` + `best_practices.md`). | a git repo |
| `whoami` `learn` `explain` `overview` `doctor` `cli` | Agent-first introspection: identity, self-teaching prompt, docs, health checks. | — |

The CLI is **agent-first**: every command is self-describing, machine-readable
(`--json`), and emits structured errors, so a coding agent can discover and drive
it without a human in the loop.

## Requirements

- **Python ≥ 3.12**
- [`uv`](https://docs.astral.sh/uv/) (recommended for the dev workflow)
- For `qodo review`: the [`gh`](https://cli.github.com/) (GitHub, incl.
  Enterprise) or `glab` (GitLab) CLI on your `PATH`, already authenticated.
- For `qodo rules`: a Qodo API key in `~/.qodo/config.json` (`{"API_KEY": "..."}`)
  or the `QODO_API_KEY` environment variable.

## Install

The package is published to PyPI as **`qodo-cli`**; the installed console script
is **`qodo`** (run commands as `qodo …`).

```bash
uv tool install qodo-cli      # or: pipx install qodo-cli
qodo --help
```

Run it ad-hoc without installing (note the script name differs from the package):

```bash
uvx --from qodo-cli qodo --help
```

From source (for development):

```bash
git clone https://github.com/agentculture/qodo-cli
cd qodo-cli
uv sync                       # install runtime + dev deps into .venv
uv run qodo --help
```

## Quickstart

```bash
uv run qodo whoami                 # who am I? (identity from culture.yaml)
uv run qodo learn                  # structured self-teaching prompt (add --json)
uv run qodo doctor                 # health: agent-identity invariants + Qodo setup

uv run qodo rules get "how should errors be handled in the CLI layer?"
uv run qodo review list            # Qodo's comments on the PR for the current branch
uv run qodo config show            # is this repo's Qodo reviewer config in place?
```

(Outside the dev checkout, drop the `uv run` prefix and just call `qodo …`.)

## Commands

### `qodo rules` — surface your org's coding rules

Semantic-searches the Qodo rules API and prints the relevance-ranked rules with
their severity (`ERROR` / `WARNING` / `RECOMMENDATION`). Reuses the API key
already in `~/.qodo/config.json` (or `QODO_API_KEY`) — it never prompts. Scope is
auto-detected from your `git origin`; override or disable it explicitly. Cites
`qodo-get-rules`.

```bash
qodo rules get "auth token storage"                       # auto-detected scope
qodo rules get "logging conventions" --top-k 5            # cap the result count
qodo rules get "naming" --scope agentculture/qodo-cli     # force a scope (repeatable)
qodo rules get "error handling" --no-scope --json         # disable scope; JSON out
```

### `qodo review` (alias `qodo pr`) — triage the Qodo bot's PR comments

Drives your existing provider CLI (`gh` / `glab`) to find the open PR for the
current branch, list the Qodo reviewer's comments (severity, type, description,
and `agent_prompt` parsed from each body), and reply / acknowledge / resolve
them. It reuses your provider-CLI auth — no new credentials. The *fixing* loop
(read files, write a fix) stays with the calling agent. Cites `qodo-pr-resolver`.

```bash
qodo review list                       # all Qodo comments on the current branch's PR
qodo review list --kind inline         # hide the summary rollups
qodo review list --pr 42 --json        # explicit PR number, machine-readable

qodo review resolve 123456789                          # resolve one inline comment by id
qodo review resolve 123 456 --reply "Fixed in abc1234" --sign
qodo review resolve --all                              # every inline Qodo comment
qodo review resolve --all --severity LOW               # only LOW-severity comments
qodo review resolve 123 --no-resolve-thread            # +1 reaction only, skip GraphQL
```

- `--sign` appends your `culture.yaml` nick signature to `--reply` (at most once).
- Thread resolution uses GitHub's GraphQL `resolveReviewThread`; `--no-resolve-thread`
  posts the acknowledgement reaction only.
- GitHub (incl. Enterprise via your `gh` host config) and GitLab are wired
  end-to-end. Azure / Bitbucket / Gerrit are recognized but raise a clear "not
  wired yet" error rather than misbehaving.

### `qodo config` — manage the repo's Qodo reviewer config

Maintains the two files that make Qodo's reviews of *this* repo accurate:
`.pr_agent.toml` (the Qodo Merge `[pr_reviewer]` config) and `best_practices.md`.
A missing config is why Qodo falls back to inferred conventions and raises false
positives. This is distinct from the *client* `~/.qodo/config.json` that
`qodo rules` reads.

```bash
qodo config show          # read-only snapshot of both files
qodo config validate      # exit 1 if the config is invalid/absent
qodo config init          # scaffold both files when absent (never clobbers)
qodo config init --force  # overwrite existing files
```

### Agent-first introspection

| Verb | What it does |
|------|--------------|
| `whoami` | Report this agent's nick, version, backend, and model from `culture.yaml`. |
| `learn` | Print a structured self-teaching prompt (`--json` for a machine map). |
| `explain <path>` | Markdown docs for any noun/verb path (e.g. `explain rules`). |
| `overview` | Read-only descriptive snapshot of the agent (identity, verbs, artifacts). |
| `doctor` | Check agent-identity invariants **and** Qodo setup; guides any fixes. |
| `cli overview` | Describe the CLI surface itself. |

`doctor` reports a rubric-shaped contract — `{healthy, checks: [{id, passed,
severity, message, remediation}]}` — covering agent identity (the
backend → prompt-file mapping, vendored skills) plus Qodo setup (reviewer config
present, client credentials available). Advisory `warning`/`info` checks surface
guidance without failing the command, so it's useful in any repo.

## Conventions (stable contracts)

Every command honors these — tests and the agent-first rubric pin them:

- **`--json` everywhere.** Every command (and argparse-level errors like an
  unknown verb) accepts `--json` and emits machine-readable output.
- **stdout / stderr split.** Command *results* go to stdout; *errors and
  diagnostics* go to stderr. Never mixed, in text or JSON mode.
- **Structured errors.** Every failure is `{code, message, remediation}`. Text
  mode renders `error: <msg>` then `hint: <remediation>`; no Python traceback
  ever leaks.
- **Exit codes:** `0` success · `1` user error (bad flag/arg) · `2` environment
  error (missing `~/.qodo/config.json`, `gh` absent, API error) · `3+` reserved.

## Mesh identity

`qodo-cli` is also an AgentCulture mesh agent. `culture.yaml` declares its `suffix`
(nick), `backend`, and `model`; the backend is **`colleague`**, so the resident
prompt file is **`AGENTS.colleague.md`** (not `CLAUDE.md`). `whoami` and `doctor`
parse `culture.yaml` with a hand-rolled reader — no YAML dependency — to keep the
runtime dependency-free.

The repo also vendors the canonical **guildmaster** skill kit (cite-don't-import)
under [`.claude/skills/`](.claude/skills/); provenance and the re-sync procedure
live in [`docs/skill-sources.md`](docs/skill-sources.md).

## Development

```bash
uv sync                                   # install runtime + dev deps
uv run pytest -n auto                     # full test suite, parallel (xdist)
uv run pytest tests/test_cli.py::test_whoami_text   # a single test
uv run teken cli doctor . --strict        # the agent-first rubric gate CI enforces
```

Lint (CI runs each; all must pass — line length 100):

```bash
uv run black --check qodo tests
uv run isort --check-only qodo tests
uv run flake8 qodo tests
uv run bandit -c pyproject.toml -r qodo
markdownlint-cli2 "**/*.md" "#node_modules" "#.local" "#.claude/skills" "#.teken"
```

**Version-bump on every PR** (CI blocks merge otherwise), including docs/CI-only
changes:

```bash
python3 .claude/skills/version-bump/scripts/bump.py patch   # or minor | major
```

[`CLAUDE.md`](CLAUDE.md) is the authoritative contributor guide — architecture,
the PR lane, SonarCloud gating, and how to add a new command.

## Clone as a template

This runtime began as the agent-first scaffold cited from teken's `afi-cli`
`python-cli` reference, and can be cloned to bootstrap a new agent CLI. The name
`qodo`/`qodo-cli` is hard-coded in ~100 places; list every occurrence first and
follow the rename procedure in [`CLAUDE.md`](CLAUDE.md):

```bash
git grep -nI -e 'qodo-cli' -e 'qodo' -e 'agentculture/qodo-cli'
```

## License

MIT — see [`LICENSE`](LICENSE).
