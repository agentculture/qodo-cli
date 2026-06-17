# Best practices for qodo-cli

Repository-specific coding standards for the Qodo reviewer — and for any agent
working in this repo. `qodo-cli` is an unofficial, community CLI to manage Qodo,
built as a **zero-runtime-dependency, stdlib-only** Python package.

These conventions are pinned by the test suite and the agent-first rubric
(`teken cli doctor . --strict`); please review against them rather than against
generic defaults.

## Dependencies

- Runtime dependencies must stay empty (`dependencies = []` in `pyproject.toml`).
  Use only the Python standard library at runtime, and flag any new third-party
  runtime import. `teken` and the lint/test tools are dev-only.

## Exit codes

- Command handlers return a bare `0` / `1` / `None` for their exit code (`0` is
  success). The `EXIT_*` constants in `qodo/cli/_errors.py` are for `CliError`
  codes on the failure path, **not** for handler return values. A bare
  `return 0` in a handler is intentional and consistent across every command —
  do not flag it as a magic number.

## Errors and output

- Every failure raises `CliError(code, message, remediation)`; no Python
  traceback may leak to stderr. Text-mode errors render `error: <msg>` then
  `hint: <remediation>` (the `hint:` prefix is required).
- Results go to stdout; errors and diagnostics go to stderr. Never mix the two,
  in text or `--json` mode. Every command supports `--json`.

## argparse

- Build subparsers with the structured-error parser class. Nested subparsers
  inherit it via `add_subparsers(parser_class=type(p))` (see
  `qodo/cli/_commands/cli.py`); passing `type(p)` is the established idiom and is
  equivalent to naming `_CliArgumentParser` directly — it is not a missing
  `parser_class`.
- Add the standard `--json` flag with `add_json_flag()` from `qodo.cli._output`
  rather than re-declaring the literal.

## The CLI is mechanics-only

- `qodo review resolve --reply` drives the user's own `gh` with their own auth;
  the reply text belongs to the caller. The tool must not auto-append an agent
  signature — that would mis-attribute human-authored replies. Signing is the
  caller's responsibility (an opt-in flag may be added later).

## Cite, don't import

- Behavior derived from `qodo-ai/qodo-skills` and the vendored `.claude/skills/`
  kit is cited as the source of truth, not forked or vendored into runtime code.
  Keep `docs/qodo-skills-sources.md` in sync when the upstream contract changes.

## Keep the self-description in sync

- When adding a command, update `learn`, `overview`, and the explain catalog so
  the rubric and the self-describing text stay consistent.
