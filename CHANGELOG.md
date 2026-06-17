# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). This project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.8.1] - 2026-06-17

### Added

- `tests/test_contracts.py` + `tests/fixtures/rules_search_response.json` — an
  **offline contract test** that pins the Qodo `/rules/search` response shape
  (relevance order, the `{id, name, content, severity}` fields, severities within
  the known set, unknown extra fields passing through), so the parser is verified
  without a Qodo subscription in CI. (#8)
- **Opt-in live smokes** (skipped by default): `test_live_rules_search_smoke`
  (runs when `QODO_API_KEY` is set) and `test_live_ghe_resolves_to_github` (runs
  when `QODO_CLI_GHE_REMOTE` points at a real GitHub Enterprise origin). (#8)
- `docs/manual-verification.md` — a manual checklist for the paths that need a
  real system to exercise (live `qodo rules`, GitHub Enterprise resolution, the
  non-GitHub provider gate), cross-referenced from the citation ledger. (#8)

### Changed

### Fixed

## [0.8.0] - 2026-06-17

### Added

- `qodo config` — a new noun group to manage the **repo-level** Qodo reviewer
  config (`.pr_agent.toml` + `best_practices.md`), distinct from the *client*
  `~/.qodo/config.json` that `qodo rules` reads: (#7)
  - `config show` — report presence, the parsed `.pr_agent.toml` sections, and
    `best_practices.md` status (read-only).
  - `config validate` — validate the config (valid TOML, a config present) and
    exit 1 when invalid; warns (without failing) on a missing `[pr_reviewer]`
    section or an empty `best_practices.md`. Emits the rubric-shaped
    `{valid, checks: [...]}` in `--json`.
  - `config init [--force]` — scaffold a minimal `.pr_agent.toml` +
    `best_practices.md` when absent; never overwrites without `--force`.
  - `config overview` — describe the noun (rubric-required).

### Changed

### Fixed

## [0.7.0] - 2026-06-17

### Added

- `qodo rules get` now **auto-detects the rules scope** when `--scope` is
  omitted, mirroring `qodo-get-rules`: the `org/repo` slug from the git `origin`
  (SSH `git@host:org/repo(.git)`, HTTPS, and `ssh://` forms; multi-level
  namespaces such as GitLab subgroups preserved) plus the module name from a
  `modules/<name>/` path. Detection is non-raising — no git / no origin yields no
  scope, and `scopes` is omitted entirely (never sent empty). The detected scope
  is surfaced in `--json` (`scopes`) and the text header. (#9)
- `qodo rules get --no-scope` forces scope omission (skips auto-detection);
  `--scope` continues to override detection. `--scope` and `--no-scope` are
  mutually exclusive. (#9)

### Changed

### Fixed

## [0.6.0] - 2026-06-17

### Added

- `qodo review list` now parses each Qodo comment body into structured triage
  fields surfaced in `--json` and the text table: `severity` (from the badge —
  `Action required → HIGH`, `Review recommended → MEDIUM`, other → `LOW`, none →
  `null`), `type` and `categories` (the `<code>` chips), `description` (the
  `<pre>` block, HTML-entity-decoded), and `agent_prompt` (the remediation
  block). Parsing is best-effort — an unrecognised body degrades to title-only.
  (#3)
- `qodo review list --kind {inline,summary,all}` filters by comment kind so the
  non-actionable summary rollups can be excluded. (#3)
- `qodo review resolve` now resolves the GitHub review **thread** via the GraphQL
  `resolveReviewThread` mutation by default (mapping the REST comment id to its
  thread node id), with `--no-resolve-thread` to skip and a graceful fallback to
  the `+1` reaction when no thread maps to the comment. (#4)
- `qodo review resolve --all` / `--severity <S>` and multiple positional ids
  resolve several inline comments in one call. (#5)
- `qodo review resolve --reply "..." --sign` appends the `culture.yaml` nick
  signature (`- <nick> (Claude)`) to the reply, at most once (duplicate-guarded);
  default stays unsigned. (#6)

### Changed

- `qodo review resolve` is now **best-effort and per-action**: it reports each of
  reply / acknowledge / resolve-thread per comment, so a posted reply whose
  acknowledgement failed reads as partial success (exit 1) rather than a blanket
  failure. The `resolve_comment()` return type changed from `list[str]` to
  `list[{action, ok, detail}]`, and the `resolve --json` payload now carries a
  `resolved` list with per-action results. (#5)

### Fixed

## [0.5.0] - 2026-06-17

### Added

- `.pr_agent.toml` + `best_practices.md` at the repo root — the Qodo Merge
  reviewer config (cite, don't fork). They codify this repo's intentional
  patterns (bare `return 0` handlers, `parser_class=type(p)` subparser nesting,
  the mechanics-only `review resolve --reply`) so Qodo reviews accurately and
  stops raising them as violations.
- `qodo doctor` now also checks **Qodo setup**, against the current git repo:
  `.pr_agent.toml` present, `best_practices.md` present, and whether a usable
  Qodo API key is resolvable for `qodo rules` — `QODO_API_KEY`, else a non-empty
  `API_KEY` in `~/.qodo/config.json` (a present-but-keyless or malformed file
  fails the check with guidance, and never throws). These are advisory and each
  carries a `remediation` that guides an agent through setup. Runs in any repo
  (not just a source checkout). (Addresses #7.)

### Changed

- `qodo doctor` `healthy` now depends on **error**-severity checks only;
  `warning`/`info` checks surface guidance without flipping `healthy` or the
  exit code. Fixes the `claude → CLAUDE.md` drift in the `doctor` explain entry
  (this repo's backend is `colleague` → `AGENTS.colleague.md`).
- `learn` now describes `doctor` as "agent-identity invariants + Qodo setup" in
  both its text and JSON payload, matching `overview` and the explain catalog
  (self-description stays consistent across the introspection surfaces).

## [0.4.0] - 2026-06-17

### Added

- First real Qodo-management surface — two native, zero-dependency noun groups,
  each citing `qodo-ai/qodo-skills` as its behavioral source of truth (cite,
  don't fork/vendor/npx):
  - `qodo rules get "<query>"` — semantic-search your org's Qodo coding rules.
    Reimplements `qodo-get-rules` over the stdlib (`urllib`): reads the API key
    already in `~/.qodo/config.json` (env `QODO_API_KEY`/`QODO_ENVIRONMENT_NAME`/
    `QODO_API_URL` win), POSTs `{base}/rules/search`, and prints the
    relevance-ranked rules with `ERROR`/`WARNING`/`RECOMMENDATION` severity.
    Errors (never prompts) when no credentials are present.
  - `qodo review` (alias `qodo pr`) — `list` and `resolve` the Qodo bot's PR
    review comments. Reimplements `qodo-pr-resolver` by driving the user's
    existing provider CLI (`gh`): detect provider, find the open PR for the
    branch, fetch and filter the Qodo bot's comments (`qodo-code-review`,
    `qodo-merge`, `qodo-ai`, `pr-agent-pro(-staging)` — matched as base names so
    both gh `[bot]`-suffix spellings hit), dedup by stable comment identity
    (id/url) so distinct same-badge inline comments never collapse, reply, and
    acknowledge (`+1`). GitHub is wired — including GitHub Enterprise, recognised
    via your `gh` host config (`gh auth status --hostname`) rather than hostname
    guessing (implemented but not live-tested against a real GHE instance);
    GitLab/Azure/Bitbucket/Gerrit are recognised but raise a clear "not wired
    yet" error.
- `qodo/cli/_qodo_api.py` and `qodo/cli/_providers.py` — the zero-dep mechanics
  behind the two verbs (stdlib `urllib` / `subprocess` only).
- `docs/qodo-skills-sources.md` — the Qodo-skills citation ledger: verb↔skill
  map, the resolved API/provider contract, follow-up providers, and a re-sync
  procedure.
- `docs/specs/…-qodo-cli-now-does-qodo-s-two-core-jobs-natively-fr.md` — the
  converged `/think` spec this slice was built from.

### Changed

- `learn`, `overview`, the explain catalog root, and the parser description now
  describe the real Qodo surface instead of self-describing as "a clonable
  template" (the drift CLAUDE.md flagged); `overview`'s artifact list now names
  `AGENTS.colleague.md` (the colleague-backend prompt file) rather than
  `CLAUDE.md`.
- `.markdownlint-cli2.yaml` ignores `docs/specs/**` — devague-exported specs are
  generated artifacts (verbatim-announcement H1, literal `<placeholders>`) and
  are not reformatted, mirroring the vendored-skills exclusion.

## [0.3.2] - 2026-06-16

### Fixed

- `explain` now resolves the console-script name `qodo` (not only the dist name
  `qodo-cli`), so the agent-first rubric's `explain <self>` check passes — the
  rubric derives the self-token from `[project.scripts]` (`qodo`). Pinned by a
  regression test.

### Changed

- CLAUDE.md: re-initialized from the seed placeholder into a full runtime prompt via /init — documents the agent-first CLI dispatch/contracts, the rubric gate, colleague-backend mesh identity (AGENTS.colleague.md), vendored-skill provenance, version-bump-every-PR + cicd PR lane, and the add-a-command/rename procedures. Flags known drift where stale text still says backend: claude and that the console script is qodo (not qodo-cli).

## [0.3.1] - 2026-06-13

### Changed

- CLAUDE.md: add a convention to reach for the `ask-colleague` skill reflexively
  for explore/review/write/grade — read-only `review`/`explore` are always safe;
  side-effecting `write` needs the user's go-ahead.

## [0.3.0] - 2026-06-13

### Added

- AGENTS.colleague.md resident prompt file (backend colleague <-> AGENTS.colleague.md)

### Changed

- Promote agent identity to a colleague resident: culture.yaml backend
  claude -> colleague with a pinned model. The `doctor` backend-consistency
  map gains `colleague` -> AGENTS.colleague.md.

## [0.2.1] - 2026-06-12

### Changed

- **Re-vendored the `ask-colleague` skill from colleague (now 1.7.0, up from the
  0.39.2 sync)** — the wrapper had drifted multiple releases behind origin. Picks
  up the `clean` verb (reap stale/corrupt `colleague/*` branches + orphaned
  `.colleague/` artifacts a crashed run left behind), the `--json` flag on every
  verb (result JSON on stdout, diagnostics/digest on stderr), the
  `_colleague_via_uv` local-dev resolution that honors `--repo`, and the
  tri-state (0/1/2) exit-code contract. `scripts/ask-colleague.sh` + `prompts/`
  are byte-identical to the origin; `SKILL.md` diverges only in the one
  consumer-identifying Provenance clause (`qodo-cli vendors from
  guildmaster`). `docs/skill-sources.md` sync row updated to
  `2026-06-12 (colleague 1.7.0, direct)`. Refs: colleague#183, #186.

## [0.2.0] - 2026-06-06

### Added

- **`ask-colleague` skill** (`.claude/skills/ask-colleague/`) — the first-party front door to the `colleague` CLI (the renamed `convertible`). On top of `explore` / `review` / `write` it adds a `feedback` verb (grade a finished work item — the ROI loop), and `write` now **previews by default** in a throwaway worktree (no side effects) unless `--apply` / `--pr` is given. Reach for it reflexively — `review` for a diverse second opinion on a committed diff before opening a PR, `explore` for a fresh read of an unfamiliar area.

### Changed

- **Replaced the `outsource` skill with `ask-colleague`.** `outsource` was renamed to `ask-colleague` upstream ([colleague#148](https://github.com/agentculture/colleague/pull/148)). Because guildmaster has not re-broadcast the rename yet (its kit still ships the old `outsource`), `ask-colleague` is vendored **directly from the sibling `colleague` checkout** rather than from guildmaster — a tracked local divergence recorded in `docs/skill-sources.md`, parallel to the `agex` → `devex` one. Vendored verbatim except one consumer-identifying clause in the Provenance paragraph.
- **Ledger + CLAUDE.md + `.gitignore`:** point `docs/skill-sources.md` and the CLAUDE.md Skills section at `colleague` / `ask-colleague`, swap the *optional* runtime prerequisite `convertible` → `colleague` (env prefix `CONVERTIBLE_*` → `COLLEAGUE_*`, with the legacy names kept as a deprecated fallback), and gitignore the `.colleague/` run-artifact dir the skill writes (plus the stale `.agex/`).

## [0.1.4] - 2026-05-31

### Added

- **Vendor the `outsource` skill** (`.claude/skills/outsource/`) from
  guildmaster's canonical copy (origin
  [`agentculture/convertible`](https://github.com/agentculture/convertible),
  re-broadcast via guildmaster — guildmaster
  [#51](https://github.com/agentculture/guildmaster/pull/51)). Every agent
  cloned from this template now inherits the ability to hand a scoped task to a
  *different* engine/mind: `explore` (read-only investigation), `review` (a
  diverse second opinion on the committed diff), and `write` (delegate a small
  implementation). `explore`/`review` run isolated in a throwaway `git worktree`;
  `write` refuses a dirty tree. Fulfils
  [#8](https://github.com/agentculture/qodo-cli/issues/8).
- **Ledger + CLAUDE.md:** record `outsource` in `docs/skill-sources.md`
  (origin = convertible, re-broadcast via guildmaster; vendored verbatim — it
  already carries `type: command`) and document its *optional* runtime
  dependency on the `convertible` CLI (the skill exits with an install hint if
  absent, so a clone that never uses it is unaffected).

### Changed

### Fixed

## [0.1.3] - 2026-05-31

### Changed

- Expanded the clone-and-rename instructions in `CLAUDE.md`: added `README.md` to
  the rename targets and a portable `git grep` discovery command so a cloner can
  find every occurrence of the template name (hard-coded in ~100 places across the
  package, including the CLI command files and `_ISSUES_URL` in
  `qodo/cli/__init__.py`) rather than renaming by hand.
- Synced `README.md`'s "Make it your own" checklist with `CLAUDE.md`: it now lists
  `README.md` itself as a rename target and points to `CLAUDE.md`'s discovery
  command as the authoritative procedure, so the two onboarding checklists no
  longer drift.

## [0.1.2] - 2026-05-30

### Changed

- Renamed the PR-lifecycle CLI references `agex` / `agex-cli` to `devex` (same
  tool, new name) across `CLAUDE.md`, `docs/skill-sources.md`, `.gitignore`, and
  the vendored `cicd`, `assign-to-workforce`, and `communicate` skills — the
  `cicd` scripts now invoke `devex pr`.
- Logged the vendored-skill in-place patch as a local divergence in
  `docs/skill-sources.md`; the matching canonical rename is tracked upstream for
  guildmaster in
  [agentculture/guildmaster#48](https://github.com/agentculture/guildmaster/issues/48)
  so a future re-sync reconciles cleanly.
- Aligned the documented `devex` version floor to `>=0.21` across the vendored
  `cicd` `SKILL.md` and `workflow.sh` install hint (were `>=0.1`), matching
  `docs/skill-sources.md` and the `await`-era feature set; flagged upstream on
  guildmaster#48.

### Fixed

- SonarCloud now reports code coverage — added `relative_files = true` to
  `[tool.coverage.run]` so `coverage.xml` emits repo-relative paths that map to
  `sonar.sources=qodo` (absolute / `.venv` paths were dropped
  as unmappable). Mirrors the sibling `convertible` setup.

## [0.1.1] - 2026-05-26

### Changed

- **CI gates on the SonarCloud quality gate**
  ([issue #3](https://github.com/agentculture/qodo-cli/issues/3)) —
  added `sonar.qualitygate.wait=true` to `sonar-project.properties` so a failing
  gate fails the `test` job when `SONAR_TOKEN` is set. Token-less repos and fork
  PRs remain green (the scan step is guarded by `if: env.SONAR_TOKEN != ''`).

## [0.1.0] - 2026-05-26

### Added

- **Onboarded into the AgentCulture mesh** ([issue #1](https://github.com/agentculture/qodo-cli/issues/1)).
- **Agent-first CLI** cited from teken's (`afi-cli`) `python-cli` reference
  (`teken cli cite`) — verbs `whoami`, `learn`, `explain`, `overview`, `doctor`,
  and the `cli` noun group. Runtime is self-contained (`dependencies = []`);
  `teken>=0.8` is a dev dependency only. Passes the seven-bundle agent-first
  rubric (`teken cli doctor . --strict`). `doctor` checks the agent-identity
  invariants (prompt-file-present, backend-consistency, skills-present).
- **Mesh identity**: `culture.yaml` (`suffix: qodo-cli`,
  `backend: claude`) and the matching `CLAUDE.md` prompt file.
- **Canonical guildmaster skill kit** (11 skills) vendored under
  `.claude/skills/` (cite-don't-import): `agent-config`, `assign-to-workforce`,
  `cicd`, `communicate`, `doc-test-alignment`, `pypi-maintainer`, `run-tests`,
  `sonarclaude`, `spec-to-plan`, `think`, `version-bump`. Every `SKILL.md`
  carries `type: command` (load-bearing for the culture/claude backend);
  `cicd` / `communicate` consumer-identifying prose adapted, all script bodies
  verbatim. Provenance in `docs/skill-sources.md`. Three skills (`think`,
  `spec-to-plan`, `assign-to-workforce`) originate in `devague`, re-broadcast
  via guildmaster.
- **Build + deploy baseline**: `pyproject.toml` (hatchling), `tests/` (pytest,
  xdist, coverage), `.github/workflows/{tests,publish}.yml` (CI rubric/lint gate,
  PyPI Trusted Publishing), `.flake8`, `.markdownlint-cli2.yaml`,
  `sonar-project.properties`, and `.claude/skills.local.yaml.example`.

### Changed

### Fixed
