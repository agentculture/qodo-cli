# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**qodo-cli** — an unofficial, community CLI and agent to manage **Qodo** (the AI
code reviewer and Qodo's other agents; requires a Qodo subscription). Not
affiliated with, authorized, or endorsed by Qodo; the Qodo name and trademark
belong to Qodo Ltd.

**Current reality vs. intended domain.** The intended domain is managing Qodo,
but no Qodo-management surface exists yet. Today the runtime is still the
**agent-first scaffold** cited from teken's `afi-cli` `python-cli` reference: a
self-describing introspection CLI (`whoami`, `learn`, `explain`, `overview`,
`doctor`, `cli`). Several artifacts still self-describe this repo as "a clonable
template" (the `learn`/`explain`/`overview` text, the README "Make it your own"
section). When you build the actual Qodo surface, add it as new noun groups (see
*Adding a command*) and update that self-describing text to match.

The runtime has **zero third-party dependencies** (`dependencies = []` in
`pyproject.toml`); `teken` is a dev-only dependency used by the rubric gate.
Requires Python ≥ 3.12.

## Commands

```bash
uv sync                                  # install runtime + dev deps into .venv
uv run qodo whoami                       # run the CLI (console script is `qodo`)
uv run pytest -n auto                    # full test suite, parallel (xdist)
uv run pytest tests/test_cli.py::test_whoami_text   # a single test
uv run teken cli doctor . --strict       # the agent-first rubric gate CI enforces
```

> The installed console script is **`qodo`**. The CLI's argparse `prog` is also
> `qodo`, so usage, `--version`, error remediation, and every example command
> invoke `qodo`; `qodo-cli` survives only as the dist/package/brand name (and in
> titles like the `explain`/`overview` headings). Run `uv run qodo …`.

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
