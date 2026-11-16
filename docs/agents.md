# Using qodo-cli as an agent

This page is for an agent (or a script) that drives the bridge through its CLI
or its MCP server. After reading it you can list a pull request's findings,
search Qodo's rules, check review history, and register the MCP server with a
client.

The **bridge** is the one qodo-cli app: a CLI, an HTTP API, an MCP server and a
web UI over the same verbs. The operator who runs it is covered in
[`docs/operations/bridge.md`](operations/bridge.md); code that imports
`qodo.api` or calls the HTTP API is in [`docs/integrators.md`](integrators.md).

You can also ask the CLI itself. These need no credentials:

```bash
qodo-cli learn --json          # the self-teaching prompt
qodo-cli explain findings      # docs for a noun or verb path
qodo-cli overview --json       # identity, verbs, artifacts
qodo-cli cli overview          # the CLI surface
```

## The contract every verb follows

- **`--json` everywhere.** Every verb takes `--json`, and so does an argparse
  error (an unknown verb or flag). With `--json`, a result is one JSON document
  on stdout.
- **Results on stdout, errors on stderr.** Never mixed. In JSON mode an error
  is `{"code", "message", "remediation"}` on stderr.
- **Exit codes:** `0` success, `1` user error (bad flag, missing argument,
  Qodo refused the call, the exposure policy refused the tool), `2` environment
  error (no API key, Qodo unreachable, `gh` missing), `3` and up reserved. No
  traceback is ever printed.
- **Qodo verbs need a key.** Everything that calls Qodo needs `QODO_API_KEY` or
  an `API_KEY` in `~/.qodo/config.json`. Without one the exit is 2 with
  `no Qodo API key found`.

## The verbs, by noun

Verbs marked **write** change something. Every other verb only reads.

### `findings` — Qodo's findings on a pull request

| Verb | What it does |
|------|--------------|
| `findings list --pr-url URL [--extended] [--summary]` | The findings Qodo raised on the pull request at `URL`. `--extended` adds locations, snippets and dismissal details. `--summary` prints only counts, ids, statuses and URLs (a small reply) |
| `findings implement --finding-ids ID [--finding-ids ID ...] [--explanation T] [--idempotency-key K] [--local-review-id R]` | **Write.** Mark findings implemented: the fix has landed |
| `findings dismiss --finding-ids ID [...] --reason REASON [--explanation T] [--idempotency-key K] [--local-review-id R]` | **Write.** Dismiss findings without a code change. `REASON` is `false_positive`, `intentional`, `deferred` or `rejected` |
| `findings fix --pr-url URL --finding-ids ID [...] [--note TEXT]` | **Write.** Ask for an agent to fix the findings: publishes one `qodo.fix.requested` event on the mesh's event bus. It does not call Qodo and opens no pull request (see below) |

`URL` is the pull request's full web URL, for example
`https://github.com/example-owner/example-repo/pull/1`. A URL that does not start with `http://` or
`https://` is refused (exit 1).

`findings list --summary` returns counts. Open statuses are `pending`,
`partial_implementation`, `not_implemented` and `focus_areas_edited`; closed
ones are `full_implementation`, `dismissed`, `detected_after_merge` and
`outdated`. Anything else counts under `other`.

```bash
qodo-cli findings list --pr-url https://github.com/example-owner/example-repo/pull/1 --summary --json
```

`findings fix` takes a `github.com` pull request URL (no query, fragment, user or
port), 1 to 20 finding ids (letters, digits, `.` `_` `:` `-`, at most 128
characters each) and an optional note of at most 500 characters. It refuses
(exit 1) a repo the operator has not allowlisted and the same PR and set of ids
asked for again within the operator's window (5 minutes by default). Without
the `events` extra or a reachable event broker it exits 2. On success it returns
`{event_id, type, pr_url, repo, number, finding_ids}`. culture-rules picks the
event up, an agent fixes the findings in its own checkout, and culture-rules
opens the fix as a new pull request against the reviewed branch after
`fix propose` has checked it.

### `rules` — Qodo's coding rules

| Verb | What it does |
|------|--------------|
| `rules search --query Q [--scopes S ...] [--top-k N]` | Semantic search of the org's rules through Qodo's MCP tool `rules-search` |
| `rules list [--state S] [--categories C ...] [--severity S] [--scopes S ...] [--name-contains T] [--page N] [--page-size N]` | List rules, filtered |
| `rules show --rule-id ID` | One rule by id |
| `rules metadata` | The values the filters accept (categories, severities, states) |
| `rules get QUERY [--top-k N] [--scope S ...] [--no-scope]` | The older search over Qodo's rules HTTP API. Scope comes from your `git origin` unless you pass `--scope` or `--no-scope` |

### `history` — what Qodo's pull request history says

| Verb | What it does |
|------|--------------|
| `history show --repo R --file-path P [--limit N] [--category CAT] [--acceptance-status S] [--include-code-hunks]` | Past review suggestions for a file or directory. History is per file, searched upward from `P`. `CAT` is `bug` or `rule` |
| `history similar --repo R --query Q [--top-k N] [--embed-type TYPE] [--folder-path P]` | Past suggestions similar to a description or snippet. `TYPE` is `description` or `diff_hunk` |
| `history stats --repo R` | What pull request history Qodo has indexed for a repo |

Qodo's history tools failed upstream when they were probed, so the shape of
their replies is unconfirmed (see
[Known limits](operations/bridge.md#known-limits)).

### `review` (alias `pr`) — the Qodo bot's comments on a pull request

| Verb | What it does |
|------|--------------|
| `review list [--pr N] [--kind KIND]` | List the Qodo bot's comments, with severity, type and the parsed agent prompt. `KIND` is `all` (default), `inline` or `summary`. Default PR: the current branch's open PR |
| `review resolve [ID ...] [--all] [--severity S] [--reply T] [--sign] [--no-resolve-thread] [--pr N]` | **Write.** Reply to, acknowledge and resolve inline comments. Name ids, or use `--all` or `--severity`, not both |

These drive your own `gh` (GitHub) or `glab` (GitLab) login and need no Qodo key.
They are CLI only: they have no HTTP route and no MCP tool.

### `config` — the repo's Qodo reviewer configuration

| Verb | What it does |
|------|--------------|
| `config show` | Read-only snapshot of `.pr_agent.toml` and `best_practices.md` |
| `config validate` | Exit 1 when the configuration is invalid or absent |
| `config init [--force]` | **Write.** Scaffold both files when absent; never overwrites without `--force` |
| `config propose --repo OWNER/NAME --checkout PATH --base REF` | Check a checkout's committed change against `REF` and return the changed files only when it is a safe Qodo configuration change. Read-only. Needs the operator to have allowed the repo and checkout root |

`config propose` answers a refusal as a normal result, with exit 0 and
`{"ok": false, "reason", "detail", "paths"}`. Check `ok`, not just the exit
code. `config show`, `config validate` and `config init` are CLI only;
`config propose` is on the CLI and HTTP, not MCP.

### `fix` — an agent's committed code fix

| Verb | What it does |
|------|--------------|
| `fix propose --repo OWNER/NAME --checkout PATH --base REF` | Check the fix an agent committed in its checkout against `REF` and return the changed files only when the change is safe to propose. Read-only. Needs the operator to have allowed the repo and checkout root for fixes |

`fix propose` runs the same checks as `config propose`, with a different path
rule: any file may change except a refused set (CI and repo automation, the
reviewer's and agents' instructions, git internals, dependency manifests and
lockfiles, secret-bearing names; `qodo-cli explain fix propose` lists them). At
most 20 files, and the whole reply must stay under 64 KiB: a larger change is
refused with `too_large`, so keep a fix small. A refusal is a normal result
(exit 0, `ok: false`). It is on the CLI and HTTP, not MCP.

### Identity and server verbs

| Verb | What it does |
|------|--------------|
| `whoami`, `learn`, `explain PATH`, `overview`, `doctor`, `cli overview` | Identity, self-teaching prompt, docs, snapshot, health checks (including the Qodo key's expiry) |
| `serve` | Run the HTTP API. The operator's job: see [`docs/operations/bridge.md`](operations/bridge.md) |
| `mcp` | Serve the MCP tools over stdio (below) |
| `tokens issue`, `tokens list`, `tokens revoke` | Manage HTTP service tokens. The operator's job |

## Which verbs write

| Verb | What a write does | Where it is recorded |
|------|-------------------|----------------------|
| `findings implement` | Marks the named findings implemented on Qodo's side (one call, 1 to 100 ids, one pull request) | One change-log line per finding |
| `findings dismiss` | Dismisses the named findings with a reason | One change-log line per finding, with the reason |
| `review resolve` | Posts a reply, adds a reaction and resolves the review thread on the pull request, through `gh` or `glab` | Not in the bridge's change log |
| `config init` | Writes `.pr_agent.toml` and `best_practices.md` in the repo root | Not in the change log |
| `findings fix` | Publishes one `qodo.fix.requested` event on the mesh's event bus; nothing on Qodo or in a repo changes | One `fix.requested` change-log line (PR URL, ids, event id) |

Both Qodo writes are changes to a real finding on a real pull request, so call
them on purpose. `--idempotency-key` is passed to Qodo: reuse the same key with
identical arguments after an uncertain outcome (a timeout, say) so that the
retry is not applied twice. Qodo asks for one call per pull request, so batch
all the ids of one PR into one call. The reply shape of the two write tools was
never recorded.

None of these verbs pushes, merges, commits or opens a pull request. A
successful `findings` write, `config propose` or `fix propose` adds a line to the
change log; see [the operator guide](operations/bridge.md#the-change-log).

## The MCP server

`qodo-cli mcp` serves the verbs as MCP tools over **stdio**. It needs the `mcp`
extra (`pip install 'qodo-cli[mcp]'`); without it the command exits 2 and names
the extra.

There is one tool per verb on the `mcp` surface, named `<noun>_<name>`. The
tool's input schema is the verb's own parameters schema.

| Tool | Writes |
|------|--------|
| `findings_list` | no |
| `findings_implement` | yes |
| `findings_dismiss` | yes |
| `findings_fix` | yes (publishes one event; not a Qodo write) |
| `rules_search` | no |
| `rules_list` | no |
| `rules_show` | no |
| `rules_metadata` | no |
| `rules_get` | no |
| `history_show` | no |
| `history_similar` | no |
| `history_stats` | no |

Each tool's description ends with `(read-only)`, `(writes to Qodo)` or, for
`findings_fix`, `(a write)`, so a client can tell them apart. `review_*`,
`config_*`, `fix_*`, `serve` and `tokens` are not tools. Failures come back as a tool error with the message and a hint, never a
traceback, and the API key is never part of a result.

### Register it with an MCP client

Point the client at the command `qodo-cli mcp` with the key in its environment.
For a client that reads a JSON `mcpServers` block:

```json
{
  "mcpServers": {
    "qodo-cli": {
      "command": "qodo-cli",
      "args": ["mcp"],
      "env": { "QODO_API_KEY": "<your Qodo API key>" }
    }
  }
}
```

Use the client's own way of holding a secret instead of writing the key into a
file you commit. If `qodo-cli` is not on the client's `PATH`, give the full path
to it.
Stdout carries the protocol and nothing else, so `qodo-cli mcp` prints one
diagnostic line to stderr when it starts (not when you pass `--json`).

A write tool goes through the same exposure policy as the CLI: the operator
decides which Qodo tools exist at all, and a tool the policy does not list for
`mcp` has no MCP tool. See
[the operator guide](operations/bridge.md#the-exposure-policy).

## Notes for agents working inside this repo

The mesh identity of this repo is `qodo-cli` with the `colleague` backend, so its
resident prompt is [`AGENTS.colleague.md`](../AGENTS.colleague.md). The rest of
the contributor guidance is in [`CLAUDE.md`](../CLAUDE.md).
