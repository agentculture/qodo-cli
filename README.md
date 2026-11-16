# qodo-cli

An unofficial, community CLI and agent to manage **Qodo** — the AI code reviewer
and Qodo's other agents — from your terminal, in zero-dependency Python.

qodo-cli is also a **bridge**: one app with a CLI, an HTTP API, an MCP server and
a web UI, all running the same verbs, between your tools and Qodo's remote MCP
server. This is not the cultureagent agent-session bridge. The bridge calls
Qodo with your personal API key, and it never pushes, merges or opens a pull
request.

> **Unofficial.** Not affiliated with, authorized, or endorsed by Qodo. The Qodo
> name and trademark belong to Qodo Ltd. Using the Qodo surface (`rules`,
> `review`) requires your own Qodo subscription and credentials.

## Start here

Pick the page for what you are doing. Each one stands on its own.

| You are | You want to | Read |
|---------|-------------|------|
| An operator | Run `qodo-cli serve`, issue tokens, set the key and its expiry, set the exposure policy, allow `config propose`, enable the culture-rules bundle | [`docs/operations/bridge.md`](docs/operations/bridge.md) |
| An agent | Call the CLI verbs or the MCP tools: findings, rules, history | [`docs/agents.md`](docs/agents.md) |
| An integrator | Import `qodo.api` or call the HTTP API | [`docs/integrators.md`](docs/integrators.md) |

The culture-rules bundle (two flows, shipped disabled) is in
[`docs/rules/qodo/`](docs/rules/qodo/README.md). What the bridge cannot do yet,
and what was never verified, is in
[Known limits](docs/operations/bridge.md#known-limits).

## What it does

`qodo-cli` runs Qodo's core jobs natively over the Python standard library — no
third-party runtime dependencies. Each Qodo command **cites**
[`qodo-ai/qodo-skills`](https://github.com/qodo-ai/qodo-skills) as its behavioral
source of truth and reimplements the deterministic mechanics; it does not fork,
vendor, or `npx`-install those skills (see
[`docs/qodo-skills-sources.md`](docs/qodo-skills-sources.md)).

| Command | What it does | Needs |
|---------|--------------|-------|
| `findings list` `implement` `dismiss` | Qodo's findings on a pull request, addressed by its URL; mark them implemented or dismiss them (the two writes). | the Qodo API key |
| `rules get "<query>"` | Semantic-search your org's Qodo coding rules, relevance-ranked with severity. | `~/.qodo/config.json` (or `QODO_API_KEY`) |
| `rules search` `list` `show` `metadata` | Search, list and read rules through Qodo's MCP tools. | the Qodo API key |
| `history show` `similar` `stats` | What Qodo's pull request history says about a file, a similar change, or a repo. | the Qodo API key |
| `review` (alias `pr`) | List, reply to, acknowledge, and resolve the Qodo bot's PR review comments. | `gh` / `glab`, authenticated |
| `config` | Manage the repo-level Qodo reviewer config (`.pr_agent.toml` + `best_practices.md`), and `config propose` a checked change for a pull request. | a git repo |
| `serve` | Run the HTTP API (and the web UI, when the wheel has it). | the `server` extra |
| `mcp` | Serve the verbs as MCP tools over stdio. | the `mcp` extra |
| `tokens issue` `list` `revoke` | Manage the HTTP API's service tokens. | a writable token file |
| `whoami` `learn` `explain` `overview` `doctor` `cli` | Agent-first introspection: identity, self-teaching prompt, docs, health checks. | — |

The CLI is **agent-first**: every command is self-describing, machine-readable
(`--json`), and emits structured errors, so a coding agent can discover and drive
it without a human in the loop.

## Requirements

- **Python ≥ 3.12**
- [`uv`](https://docs.astral.sh/uv/) (recommended for the dev workflow)
- For `qodo-cli review`: the [`gh`](https://cli.github.com/) (GitHub, incl.
  Enterprise) or `glab` (GitLab) CLI on your `PATH`, already authenticated.
- For every verb that calls Qodo (`findings`, `rules`, `history`): a Qodo API key
  in `~/.qodo/config.json` (`{"API_KEY": "..."}`) or the `QODO_API_KEY`
  environment variable. Set its expiry too, with `QODO_API_KEY_EXPIRES` or
  `API_KEY_EXPIRES` in the same file, so `doctor` and `/health` can warn you.
- For `qodo-cli serve`: the `server` extra. For `qodo-cli mcp`: the `mcp` extra.

## Install

The package is published to PyPI as **`qodo-cli`**; the installed console script
is **`qodo-cli`** (run commands as `qodo-cli …`). The Python import package is `qodo`.

```bash
uv tool install qodo-cli      # or: pipx install qodo-cli
qodo-cli --help
```

The HTTP API and the MCP server are optional extras; the base install has no
third-party dependencies:

```bash
uv tool install 'qodo-cli[server,mcp]'    # adds `serve` and `mcp`
```

Run it ad-hoc without installing:

```bash
uvx qodo-cli --help
```

From source (for development):

```bash
git clone https://github.com/agentculture/qodo-cli
cd qodo-cli
uv sync                       # install runtime + dev deps into .venv
uv run qodo-cli --help
```

## Quickstart

```bash
uv run qodo-cli whoami                 # who am I? (identity from culture.yaml)
uv run qodo-cli learn                  # structured self-teaching prompt (add --json)
uv run qodo-cli doctor                 # health: agent-identity invariants + Qodo setup

uv run qodo-cli rules get "how should errors be handled in the CLI layer?"
uv run qodo-cli review list            # Qodo's comments on the PR for the current branch
uv run qodo-cli config show            # is this repo's Qodo reviewer config in place?
```

(Outside the dev checkout, drop the `uv run` prefix and just call `qodo-cli …`.)

The bridge surfaces, each in a line (the pages above have the detail):

```bash
uv run qodo-cli findings list --pr-url https://github.com/example-owner/example-repo/pull/1 --summary
uv run qodo-cli serve                  # HTTP API on 127.0.0.1:8792 (server extra)
uv run qodo-cli tokens issue --name my-script --role viewer
uv run qodo-cli mcp                    # MCP tools over stdio (mcp extra)
```

## Commands

### `qodo-cli rules` — surface your org's coding rules

Semantic-searches the Qodo rules API and prints the relevance-ranked rules with
their severity (`ERROR` / `WARNING` / `RECOMMENDATION`). Reuses the API key
already in `~/.qodo/config.json` (or `QODO_API_KEY`) — it never prompts. Scope is
auto-detected from your `git origin`; override or disable it explicitly. Cites
`qodo-get-rules`.

```bash
qodo-cli rules get "auth token storage"                       # auto-detected scope
qodo-cli rules get "logging conventions" --top-k 5            # cap the result count
qodo-cli rules get "naming" --scope agentculture/qodo-cli     # force a scope (repeatable)
qodo-cli rules get "error handling" --no-scope --json         # disable scope; JSON out
```

### `qodo-cli findings`, `rules search`, `history` — Qodo's own tools, through the bridge

These verbs call Qodo's remote MCP server with your API key. They are generated
from the **exposure policy** (`qodo/exposure_default.json`), which names the ten
Qodo tools the bridge exposes; a tool the policy does not name has no verb, no
route and no MCP tool. `findings implement` and `findings dismiss` change a real
finding on a real pull request, so they are the only Qodo writes.

```bash
qodo-cli findings list --pr-url https://github.com/example-owner/example-repo/pull/1 --summary
qodo-cli rules search --query "error handling in the CLI layer" --top-k 5
qodo-cli history show --repo example-owner/example-repo --file-path qodo/api.py
```

Qodo's history tools failed upstream when they were probed, so their replies are
unconfirmed; see [Known limits](docs/operations/bridge.md#known-limits).

### `qodo-cli review` (alias `qodo-cli pr`) — triage the Qodo bot's PR comments

Drives your existing provider CLI (`gh` / `glab`) to find the open PR for the
current branch, list the Qodo reviewer's comments (severity, type, description,
and `agent_prompt` parsed from each body), and reply / acknowledge / resolve
them. It reuses your provider-CLI auth — no new credentials. The *fixing* loop
(read files, write a fix) stays with the calling agent. Cites `qodo-pr-resolver`.

```bash
qodo-cli review list                       # all Qodo comments on the current branch's PR
qodo-cli review list --kind inline         # hide the summary rollups
qodo-cli review list --pr 42 --json        # explicit PR number, machine-readable

qodo-cli review resolve 123456789                          # resolve one inline comment by id
qodo-cli review resolve 123 456 --reply "Fixed in abc1234" --sign
qodo-cli review resolve --all                              # every inline Qodo comment
qodo-cli review resolve --all --severity LOW               # only LOW-severity comments
qodo-cli review resolve 123 --no-resolve-thread            # +1 reaction only, skip GraphQL
```

- `--sign` appends your `culture.yaml` nick signature to `--reply` (at most once).
- Thread resolution uses GitHub's GraphQL `resolveReviewThread`; `--no-resolve-thread`
  posts the acknowledgement reaction only.
- GitHub (incl. Enterprise via your `gh` host config) and GitLab are wired
  end-to-end. Azure / Bitbucket / Gerrit are recognized but raise a clear "not
  wired yet" error rather than misbehaving.

### `qodo-cli config` — manage the repo's Qodo reviewer config

Maintains the two files that make Qodo's reviews of *this* repo accurate:
`.pr_agent.toml` (the Qodo Merge `[pr_reviewer]` config) and `best_practices.md`.
A missing config is why Qodo falls back to inferred conventions and raises false
positives. This is distinct from the *client* `~/.qodo/config.json` that
`qodo-cli rules` reads.

```bash
qodo-cli config show          # read-only snapshot of both files
qodo-cli config validate      # exit 1 if the config is invalid/absent
qodo-cli config init          # scaffold both files when absent (never clobbers)
qodo-cli config init --force  # overwrite existing files
```

`qodo-cli config propose` is different: it reads a checkout's committed change
and returns it only if it is a safe Qodo configuration change, for another tool
to open as a pull request. The operator must first allow the repo and the
checkout root (see [`docs/operations/bridge.md`](docs/operations/bridge.md)).

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
- **Exit codes:** `0` success · `1` user error (bad flag/arg, or a call Qodo or
  the exposure policy refused) · `2` environment error (no API key, `gh` absent,
  Qodo unreachable) · `3+` reserved.

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
