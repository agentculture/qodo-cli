# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**qodo-cli** — an unofficial, community CLI and agent to manage **Qodo** (the AI
code reviewer and Qodo's other agents; requires a Qodo subscription). Not
affiliated with, authorized, or endorsed by Qodo; the Qodo name and trademark
belong to Qodo Ltd.

**The bridge.** In these docs the **bridge** is this one app: a CLI, an HTTP API,
an MCP server and a web UI that all run the same verbs from one registry
(`qodo/registry.py`), between callers and Qodo's remote MCP server. It calls Qodo
with a personal API key, and **no code path pushes, merges, commits or opens a
pull request** (`tests/test_no_writes.py` enforces it). The runtime was cited from
teken's `afi-cli` `python-cli` reference (the agent-first scaffold: `whoami`,
`learn`, `explain`, `overview`, `doctor`, `cli`); the Qodo surface is built on top
of it.

**Docs for the three audiences** (each page starts from what the reader can do):

| Audience | Page |
|----------|------|
| Operator: run `serve`, tokens, key expiry, exposure policy, `config propose`, change log, the culture-rules bundle, release order, Known limits | `docs/operations/bridge.md` |
| Agents: the verbs by noun, the contract, which verbs write, the MCP tools | `docs/agents.md` |
| Integrators: `qodo.api`, the HTTP routes, the error envelope, the pinned contract | `docs/integrators.md` |

Also `docs/qodo-mcp-tools.md` (the live probe of Qodo's MCP server: what was and
was not recorded), `docs/rules/qodo/README.md` (the culture-rules bundle) and
`docs/manual-verification.md`. **Docs describe what is on disk today**: check a
command, flag, variable, route or default against the code before you write it,
and mark anything ahead of reality *planned*.

The runtime has **zero third-party dependencies** (`dependencies = []` in
`pyproject.toml`); `teken` is a dev-only dependency used by the rubric gate.
Requires Python ≥ 3.12.

## Commands

```bash
uv sync                                  # install runtime + dev deps into .venv
uv run qodo-cli whoami                       # run the CLI (console script is `qodo-cli`)
uv run pytest -n auto                    # full test suite, parallel (xdist)
uv run pytest tests/test_cli.py::test_whoami_text   # a single test
uv run teken cli doctor . --strict       # the agent-first rubric gate CI enforces
```

> The installed console script is **`qodo-cli`**, matching the distribution name.
> The CLI's argparse `prog` is also `qodo-cli`, so usage, `--version`, error
> remediation, and every example command invoke `qodo-cli`. Only the Python
> import package keeps the short name `qodo` (`import qodo`, `python -m qodo`).
> Run `uv run qodo-cli …`.

Lint (CI runs each of these; all must pass):

```bash
uv run black --check qodo tests
uv run isort --check-only qodo tests
uv run flake8 qodo tests
uv run bandit -c pyproject.toml -r qodo
markdownlint-cli2 "**/*.md" "#node_modules" "#.local" "#.claude/skills" "#.teken"
```

Black/isort/flake8 all use line length 100. Bandit skips B101/B404/B603.

## Architecture

### CLI dispatch (`qodo/cli/`)

`main(argv)` in `qodo/cli/__init__.py` is the single entry point. Flow:

1. `_build_parser()` constructs the argparse tree and calls each command
   module's `register(sub)` to attach its subparser + handler.
2. `parse_args` resolves `args.func`; `_dispatch(args)` invokes it.
3. A handler returns `None`/`int` on success and **raises `CliError` on
   failure**. `_dispatch` catches `CliError` and routes it through `_output`;
   any *other* exception is wrapped into a `CliError` so **no Python traceback
   ever leaks to stderr**.

Each verb/noun lives in its own module under `qodo/cli/_commands/` exposing a
`register(sub)` function and a handler. Noun groups (like `cli`) add their own
nested subparsers inside `register`.

### The bridge's modules

One verb registry feeds every surface; nothing is written by hand per surface.

- **`qodo/registry.py`**: a `Verb` (noun, name, params JSON Schema, `writes`,
  `surfaces`, handler) for each verb. Two kinds: `upstream` (one per Qodo tool the
  exposure policy names; params come from the packaged `qodo/tool_schemas.json`,
  rewritten by `scripts/export_tool_schemas.py`) and `local` (`rules get`,
  `review`, `config`, which do not go through the gateway). A handler takes a
  params dict and `surface=` and raises `CliError`.
- **`qodo/exposure.py` + `exposure_default.json`**: which Qodo tools are exposed, as
  which verb, on which of `cli`, `api`, `mcp`. Default deny: an unnamed tool is
  refused, a tool whose annotation is not read-only needs `allow_write`, and a
  surface the entry does not list is refused. There is no flag or variable to load
  another file; `load_policy(path)` is for code.
- **`qodo/gateway.py`**: the client of Qodo's MCP server (urllib, HTTPS only, no
  redirects, key redacted from errors); enforces the policy again at call time.
- **`qodo/api.py`**: the public Python API; the CLI's `rules`/`review`/`config`
  handlers and the registry's handlers call it. `docs/integrators.md` lists it.
- **`qodo/propose.py` + `propose_default.json`**: `config propose`, a read-only gate;
  repo, checkout-root (`<root>/<owner>/<name>`) and path allowlists; settings layer
  as packaged defaults, `$QODO_PROPOSE_CONFIG` or `~/.qodo/propose.json`, then
  `QODO_PROPOSE_REPOS` and `QODO_PROPOSE_CHECKOUT_ROOTS`.
- **`qodo/health.py`, `qodo/changelog.py`**: the key's expiry (from
  `QODO_API_KEY_EXPIRES` or `API_KEY_EXPIRES`, never from the key) and the append-only
  change log (`$QODO_CHANGE_LOG`, JSON Lines, no key and no finding text).
- **`qodo/server/`**: `serve.py` plans and runs the two listeners (loopback, always,
  loopback addresses only, Cloudflare Access when `QODO_ACCESS_*` are all set; LAN,
  service tokens only, only when a LAN host is set), `app.py` builds the FastAPI app
  (`POST /verbs/<noun>/<name>`, `GET /health`, `/verbs`, `/changes`, `/openapi.json`;
  roles in `build_policy`), `tokens.py` locates the token file (`QODO_SERVER_TOKENS`).
- **`qodo/mcp_tools.py`**: one MCP tool per verb on the `mcp` surface, named
  `<noun>_<name>`; the generic stdio server is `qodo/bridge/mcpserver.py`.
- **`qodo/bridge/`**: the generic auth, policy, token, Access-JWT, caching and static
  pieces, **cited** from culture-rules (provenance headers in each file; this repo
  owns its copy). `qodo/cli/_build.py` builds the argparse commands for `findings`
  and `history` from the registry.
- **`api/openapi.json`** is the pinned HTTP contract. After a verb, params schema,
  role or route changes: `uv run python scripts/export_openapi.py --write`.
- **`docs/rules/qodo/`**: the culture-rules bundle (configuration only, all four
  rules disabled). `tests/test_culture_rules_bundle.py` pins it.

### Stable contracts (don't break these — tests and the rubric pin them)

- **stdout/stderr split** (`_output.py`): command *results* go to stdout,
  *errors and diagnostics* go to stderr. Never mixed, in text or JSON mode.
- **Structured errors** (`_errors.py`): every failure is a
  `CliError{code, message, remediation}`. Text mode renders `error: <msg>` then
  `hint: <remediation>`; the `hint:` prefix is required by the rubric.
- **Exit codes**: `0` success, `1` user error, `2` environment error, `3+`
  reserved (`EXIT_*` constants in `_errors.py`).
- **`--json` everywhere**: every command accepts `--json`. Argparse-level errors
  (unknown verb/flag) also honor it — `_CliArgumentParser.error()` routes through
  `emit_error`, and `main` pre-scans raw argv into the class-level `_json_hint`
  *before* `parse_args` so JSON mode is known even at parse time. New subparsers
  must be built with `parser_class=_CliArgumentParser` to inherit this.
- **Explain catalog** (`qodo/explain/catalog.py`): markdown keyed by command-path
  tuples; `resolve()` raises `CliError` on an unknown path. The test
  `test_every_catalog_path_resolves` asserts every catalog entry resolves, so a
  new command needs a matching catalog entry.

### The agent-first rubric (`teken cli doctor . --strict`)

This gate is **load-bearing and CI-enforced** — it is why the scaffold verbs
exist and constrains how you add new ones:

- `learn` must be ≥ 200 chars and mention purpose, command map, exit codes,
  `--json`, and `explain`.
- Any noun with action-verbs must also expose `overview` (the `cli` noun exists
  solely to satisfy this — `cli overview` describes the CLI surface, distinct
  from the global `overview` that describes the agent).
- Descriptive verbs (`overview`) must not hard-fail on a bad/missing target path
  (hence `overview` accepts and ignores a positional `target`).
- `doctor` must emit the rubric-shaped contract
  `{healthy, checks: [{id, passed, severity, message, remediation}]}`.

When you add real commands, keep `learn`, `overview`, and the explain catalog in
sync or this gate fails.

### Mesh identity (`culture.yaml` + resident prompt file)

`culture.yaml` declares the agent: `suffix` (nick), `backend`, `model`. The
**backend is `colleague`**, so the resident prompt file is **`AGENTS.colleague.md`**,
*not* `CLAUDE.md`. `whoami`/`doctor` parse `culture.yaml` **without a YAML
dependency** (hand-rolled parser in `whoami.py`, reading the first agent block)
to keep runtime deps empty.

`doctor` enforces the backend→prompt-file map (mirrors `steward doctor`):
`claude → CLAUDE.md`, `colleague → AGENTS.colleague.md`, `acp → AGENTS.md`,
`gemini → GEMINI.md`, plus a `.claude/skills/`-present check. If you change the
backend in `culture.yaml`, teach `doctor._PROMPT_FILE` the new mapping or the
backend-consistency invariant breaks (`test_doctor_recognizes_declared_backend`
guards this).

> **Known drift:** several artifacts still say `backend: claude` / `CLAUDE.md`
> (README, `overview`'s `_ARTIFACTS`, the `_ROOT`/`_DOCTOR` explain entries,
> `docs/skill-sources.md`). The live source of truth is `culture.yaml` + the
> tests, which assert `backend: colleague`. Fix the stale text when you touch
> those files; don't trust them over `culture.yaml`.

### Vendored skills (`.claude/skills/`)

The skill kit is **cite-don't-import**: vendored verbatim from **guildmaster**
(the AgentCulture skills supplier), provenance + re-sync procedure + tracked
local divergences in `docs/skill-sources.md`. **Do not reformat vendored
skills** — markdownlint and SonarCloud both exclude `.claude/skills/**`. Every
`SKILL.md` must carry `type: command` (load-bearing for the culture backend's
`core.skill_loader`, which silently skips files lacking it). To update a skill,
follow the re-sync steps in `docs/skill-sources.md` rather than hand-editing.

## Workflow conventions

- **Version-bump on every PR** — even docs/config/CI-only changes. The CI
  `version-check` job (PR events only) blocks merge if `pyproject.toml`'s
  `version` matches `main`. Use the `version-bump` skill
  (`python3 .claude/skills/version-bump/scripts/bump.py patch|minor|major`),
  which also prepends a Keep-a-Changelog entry to `CHANGELOG.md`. The version is
  single-sourced in `pyproject.toml`; `qodo/__init__.py` reads it via
  `importlib.metadata` (no separate `__version__` literal to sync).
- **PR lane** — use the `cicd` skill (`.claude/skills/cicd/scripts/workflow.sh`,
  a thin layer over `devex pr`). Branch naming: `fix/`, `feat/`, `docs/`,
  `skill/`. The signature `- <nick> (Claude)` is auto-appended by `devex` from
  `culture.yaml`. When implementation is done and tests pass, the standing
  AgentCulture default is **push and open a PR** — don't pause on a
  merge/keep/discard menu.
- **SonarCloud** gates CI when `SONAR_TOKEN` is set (project key
  `agentculture_qodo-cli`); token-less repos and fork PRs stay green.
  `relative_files = true` in `[tool.coverage.run]` is required so `coverage.xml`
  emits repo-relative paths Sonar can map to `sonar.sources=qodo`. Coverage
  `fail_under = 60`.

## Adding a command

For a Qodo tool, name it in `qodo/exposure_default.json` instead (and refresh
`qodo/tool_schemas.json` and `api/openapi.json`): the CLI verb, the HTTP route and
the MCP tool follow from the registry, and `tests/test_surface_parity.py` checks them.
For a local verb, add it to `_LOCAL_VERBS` in `qodo/registry.py`. A hand-written
noun group follows the steps below.

1. Create `qodo/cli/_commands/<name>.py` with a handler and a `register(sub)`
   (mirror `whoami.py`). Add a `--json` flag; raise `CliError` on failure.
2. Wire it into `_build_parser()` in `qodo/cli/__init__.py`.
3. Add a catalog entry in `qodo/explain/catalog.py` (and to `ENTRIES`).
4. Add it to the verb lists in `learn.py` and `overview.py` so the rubric and
   self-description stay consistent.
5. Add tests under `tests/` and bump the version.

## Renaming the package (when cloning as a template)

The scaffold name is hard-coded in ~100 places (package `qodo/`, dist `qodo-cli`,
the `_ISSUES_URL` in `qodo/cli/__init__.py`, the CLI command files, `tests/`,
`sonar-project.properties`, README). List every occurrence first:

```bash
git grep -nI -e 'qodo-cli' -e 'qodo' -e 'agentculture/qodo-cli'
```

Then rename the package dir, update `[project.scripts]`/`name` in
`pyproject.toml`, edit `culture.yaml` (`suffix`/`backend`), rewrite the resident
prompt file, and re-vendor only the skills you need (`docs/skill-sources.md`).

## Web UI (`web/`)

A Vite + React + TypeScript shell (Findings | Configuration | Rules; the screens are built
later) cited from culture-rules' `web/`; every cited file carries a provenance header with the
source path and commit. Commands, from `web/`: `npm ci`, `npm run typecheck`, `npm test`,
`npm run check` (the design tokens), `npm run build`. The browser calls the same-origin `/api`
prefix, which `qodo-cli serve` strips; in dev `npm run dev` proxies it to `QODO_API_URL`
(default `http://127.0.0.1:8792`). `api/openapi.json` is checked by `web/src/api/contract.test.ts`.

`hatch_build.py` packages an existing `web/dist` as `qodo/web_dist` (git-ignored build output) and
never runs Node, so a wheel built without Node is API-only. Release order: `npm ci && npm run
build` in `web/`, then `uv build` with `QODO_REQUIRE_WEB=1` (a missing build then fails the
build); `.github/workflows/publish.yml` does exactly this.
