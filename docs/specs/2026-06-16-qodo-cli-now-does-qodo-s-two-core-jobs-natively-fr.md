# qodo-cli now does Qodo's two core jobs natively from your terminal, in zero-dependency Python: 'qodo rules' surfaces your org's coding rules by semantic search (reading your existing ~/.qodo/config.json API key), and 'qodo review' (a.k.a. 'qodo pr') triages and resolves the Qodo bot's PR review comments through your existing provider CLIs (gh/glab/az). Each verb cites the official qodo-ai/qodo-skills as its behavioral source of truth — we point at the skills as the spec; we do not fork, vendor, or npx-install them. A dedicated auth/config verb and repos/agents commands are explicit follow-ups.

> qodo-cli now does Qodo's two core jobs natively from your terminal, in zero-dependency Python: 'qodo rules' surfaces your org's coding rules by semantic search (reading your existing ~/.qodo/config.json API key), and 'qodo review' (a.k.a. 'qodo pr') triages and resolves the Qodo bot's PR review comments through your existing provider CLIs (gh/glab/az). Each verb cites the official qodo-ai/qodo-skills as its behavioral source of truth — we point at the skills as the spec; we do not fork, vendor, or npx-install them. A dedicated auth/config verb and repos/agents commands are explicit follow-ups.

## Audience

- Developers and AI coding agents with a Qodo subscription who want one terminal-native, zero-dependency CLI for Qodo's domain — rules and PR review — instead of juggling per-agent skill installs (npx skills add) and the Qodo web app.

## Before → After

- Before: Qodo's capabilities are reachable today only through (a) the per-agent skills in qodo-ai/qodo-skills, installed ad-hoc via 'npx skills add' / the Claude marketplace, and (b) the Qodo web app. qodo-cli has no Qodo surface yet — it is still the agent-first introspection scaffold (whoami/learn/explain/doctor).
- After: 'qodo' exposes two Qodo verbs as native command groups — 'rules' (get) and 'review'/'pr' (list, resolve) — each documenting which upstream skill it derives from. 'rules' calls the Qodo API using the API key already in ~/.qodo/config.json; 'review' drives the user's existing provider CLIs (gh/glab/az) to read and resolve the Qodo bot's PR comments. There is no meta 'skills' verb; a dedicated auth/config verb is deferred to follow-up.

## Why it matters

- qodo-cli's stated domain is managing Qodo. Reimplementing the skills' behavior as native zero-dep commands — while citing qodo-ai/qodo-skills as the canonical spec — gives one consistent CLI across agents, removes Node/npx + per-agent-install friction, and keeps upstream as the source of truth we point at rather than a fork that drifts.

## Requirements

- qodo-cli reuses the skills' existing credentials as-is — ~/.qodo/config.json (API key + environment) for 'rules' and the user's already-working provider-CLI auth (gh/glab/az) for 'review' — introducing no new config format or auth flow.
  - honesty: On a machine where the skills already work, 'qodo rules' reads the same ~/.qodo/config.json keys with no migration, and 'qodo review' reuses the existing provider-CLI auth — neither prompts for new credentials.
- Each verb maps 1:1 to a cited upstream skill: 'qodo rules' <- qodo-get-rules (POST /rules/search, severity ERROR/WARNING/RECOMMENDATION); 'qodo review'/'pr' <- qodo-pr-resolver (provider-CLI fetch of Qodo bot comments, dedup, fix, reply, resolve threads, push).
  - honesty: The 1:1 verb<-skill mapping is checkable against the upstream SKILL.md scripts: the endpoints, severity labels, and provider-CLI calls match what those scripts do.

## Honesty conditions

- Behavioral parity is verifiable: for a given input, 'qodo rules'/'qodo review' make equivalent API/provider-CLI calls and apply the same severity mapping as the cited upstream skill.
- Audience is reachable: a Qodo subscriber can run 'qodo rules' / 'qodo review' on a machine that already has ~/.qodo/config.json (rules) and gh/glab/az (review), with no Qodo-side setup beyond what the skills already require.
- Before-state is checkable: 'git grep' in qodo-cli finds no rules/review command surface today, and the only ways to reach these Qodo behaviors are the upstream skills (npx skills add) and the Qodo web app.
- Benefit is observable: a user gets identical Qodo behavior from 'qodo ...' with no Node/npx or per-agent skill install, and upstream qodo-ai/qodo-skills stays the single cited source (no forked copy lives in this repo).
- Every native verb's --help/docs names the upstream skill it cites (qodo-get-rules / qodo-pr-resolver) and the API endpoint or provider CLI it uses.
- Non-goals are enforceable: the shipped CLI has no 'skills' verb and no npx invocation, 'qodo rules' errors (not prompts) when ~/.qodo/config.json is absent, and nothing in the repo vendors or forks upstream skill content.
- Parity is demonstrable on a fixture: for a known task input 'qodo rules get' returns the same severity-ranked rules qodo-get-rules yields from the same API response; for a PR with known Qodo comments 'qodo review list' enumerates the same comments qodo-pr-resolver would.

## Success signals

- With a valid ~/.qodo/config.json, 'qodo rules get "<task>"' returns the same severity-ranked rules qodo-get-rules would; and on a repo with open Qodo PR comments, 'qodo review' lists and resolves the same comments qodo-pr-resolver would — behavioral parity with both cited skills from one zero-dep CLI.

## Scope / boundaries

- Non-goals: no 'qodo skills' verb and no npx-skills wrapper (skills are cited as spec, not shipped/installed/forked); no dedicated auth/config or repos/agents verbs in this slice (follow-ups); no reimplementation of Qodo's server-side analysis; not a general Agent-Skills package manager. 'qodo rules' requires a pre-existing ~/.qodo/config.json and does not implement an interactive login.

## Open / follow-up

- Dedicated 'qodo auth'/'qodo config' verb, repos-level commands, and Qodo agents management are future scope ('and more'), not built in this first slice.
