# Running the qodo-cli bridge (operator guide)

This page is for the person who installs and runs the bridge on a host. After
reading it you can start `qodo-cli serve`, issue and revoke tokens, set the
Qodo API key and its expiry, change what the bridge exposes, allow
`config propose` for your repos and, if you use culture-rules, enable the
bundle.

Other readers: agents that call the CLI or MCP tools start at
[`docs/agents.md`](../agents.md); programs that import `qodo.api` or call the
HTTP API start at [`docs/integrators.md`](../integrators.md).

## What the bridge is

In these docs, the **bridge** is the one qodo-cli app. It has a CLI, an HTTP
API, an MCP server and a web UI, and all four run the same verbs from one
registry (`qodo/registry.py`). It sits between your tools and Qodo's remote
MCP server (`https://sdk.qodo.ai/v1/tools/mcp/`) and calls Qodo with a personal
API key.

The bridge never pushes, merges, commits or opens a pull request. A static
test (`tests/test_no_writes.py`) scans the package for any code that could. What
it does write is small and named:

- the status of Qodo findings, through the two write verbs `findings implement`
  and `findings dismiss`;
- replies, reactions and thread resolutions on the Qodo bot's pull request
  comments, through `review resolve` (CLI only, driven through your own `gh` or
  `glab` login);
- the repo's own `.pr_agent.toml` and `best_practices.md`, through `config init`
  (CLI only; it never overwrites without `--force`);
- the service-token file and its own change log.

## Install

The base package has no third-party dependencies. The HTTP API and the MCP
server each need an optional extra.

```bash
uv tool install 'qodo-cli[server,mcp]'     # or: pipx install 'qodo-cli[server,mcp]'
qodo-cli --version
```

Without `server`, `qodo-cli serve` exits 2 and names the extra. Without `mcp`,
`qodo-cli mcp` does the same. The web UI is inside the wheel when the release
was built with it (see [Web UI](#web-ui-and-the-release-order)); it needs no
Node at run time.

## Set the Qodo API key and its expiry

The bridge reads the key the way Qodo's own skills do, from the first of:

1. the environment variable `QODO_API_KEY`;
2. `API_KEY` in `~/.qodo/config.json` (a JSON object).

It reads these in the environment and home directory of the process that runs
`serve` or `mcp`, so a service unit needs one of them set for its own user. The
key is never returned by any route or tool and is replaced by `[redacted]` in
upstream error text.

Qodo does not expose when a key expires, and the key does not carry it. You set
the expiry yourself, when you create the key:

| Where | Name | Example |
|-------|------|---------|
| Environment (wins) | `QODO_API_KEY_EXPIRES` | `2026-12-31` |
| `~/.qodo/config.json` | `API_KEY_EXPIRES` | `"2026-12-31"` |

The value is an ISO date, or an ISO datetime (then the exact UTC time counts).

What reports it:

- `qodo-cli doctor` has a check `qodo_key_expiry`. It fails when there is no
  key, when the expiry is not a valid date, when the key is expired, and when
  fewer than 14 days are left. A key with no configured expiry passes and says
  the expiry is unknown.
- `GET /health` (public) returns `status` (`ok`, or `degraded` when the key
  state is `missing`, `expired` or `invalid`), `service`, `version` and
  `key: {present, state, days_left}`. `state` is one of `missing`, `unknown`,
  `invalid`, `expired`, `expiring` (under 14 days) and `ok`. The web UI header
  shows the same status.

## Run the server

```bash
qodo-cli serve                                   # loopback listener on 127.0.0.1:8792
qodo-cli serve --lan-host 192.0.2.10             # plus a LAN listener on :8793
```

`serve` prints which listeners it bound once every one is ready, and stops on
SIGINT or SIGTERM. It exits 1 on a refused configuration and 2 when the
`server` extra is missing.

### The two listeners

| Listener | Binds | Accepts | Off unless |
|----------|-------|---------|------------|
| `loopback` | a loopback address only (default `127.0.0.1:8792`) | service tokens, and Cloudflare Access assertions when Access is configured | always on |
| `lan` | the address you give (port default `8793`) | service tokens only; an Access header is ignored | a LAN host is set |

The loopback listener trusts the `Cf-Access-Jwt-Assertion` header, so it must be
reachable only by `cloudflared` on the same host. For that reason `serve`
refuses a non-loopback address for it (`0.0.0.0`, `::` and host names are
refused). Put every address other machines reach on the LAN listener.

### Settings

Every setting is a flag or an environment variable; a flag wins.

| Flag | Variable | Default | Meaning |
|------|----------|---------|---------|
| `--host` | `QODO_HOST` | `127.0.0.1` | Loopback listener address |
| `--port` | `QODO_PORT` | `8792` | Loopback listener port |
| `--lan-host` | `QODO_LAN_HOST` | unset (no LAN listener) | LAN listener address |
| `--lan-port` | `QODO_LAN_PORT` | `8793` | LAN listener port |
| `--tokens` | `QODO_SERVER_TOKENS` | `$XDG_STATE_HOME/qodo-cli/service_tokens.json` (`~/.local/state` when unset) | Service-token file |
| `--log-level` | none | `info` | `critical`, `error`, `warning`, `info` or `debug` |
|  | `QODO_ACCESS_LISTEN` | unset | Loopback `host:port` of the Access listener |
|  | `QODO_ACCESS_TEAM_DOMAIN` | unset | Your Cloudflare Access team domain |
|  | `QODO_ACCESS_AUD` | unset | The Access application's AUD tag |
|  | `QODO_EDITORS` | empty | Comma-separated identities raised to `editor` |
|  | `QODO_ADMINS` | empty | Comma-separated identities raised to `admin` |
|  | `QODO_CHANGE_LOG` | `$XDG_STATE_HOME/qodo-cli/changes.jsonl` | Change-log path |
|  | `QODO_EXPOSURE_POLICY` | the packaged policy | Path of your own exposure policy file |
|  | `QODO_PROPOSE_CONFIG` | `~/.qodo/propose.json` | `config propose` settings file |
|  | `QODO_PROPOSE_REPOS` | empty | `config propose` repo allowlist |
|  | `QODO_PROPOSE_CHECKOUT_ROOTS` | empty | `config propose` checkout roots |

The three `QODO_ACCESS_*` variables are all-or-nothing. Set all three and
Access is on; set none and it is off; a partial set is refused (exit 1). When
Access is on and no `--host` or `--port` is given, the loopback listener binds
the address in `QODO_ACCESS_LISTEN`, which must also be a loopback address.

### Identities and roles

A credential resolves to an identity and a role. Roles are ordered, and each
includes the ones before it.

| Role | Can call |
|------|----------|
| `viewer` | every read verb, `GET /verbs`, `GET /changes`, `GET /openapi.json`, and the web UI |
| `editor` | the above, plus the write verbs (`findings implement`, `findings dismiss`) and `config propose` |
| `admin` | the above. No route needs `admin` today; a route nobody listed falls back to it |

- A **service token** carries the role it was issued with.
- A **person** that Access lets through is a `viewer`. Their email in
  `QODO_EDITORS` makes them an `editor`. An Access service token (identified by
  its common name) is a `viewer` unless listed in `QODO_EDITORS`.
- An identity listed in `QODO_ADMINS` is an `admin`, whatever its credential.
- A request with no credential gets 401. A role that is too low gets 403.
  `/health` needs no credential.

### Tokens

Tokens are for machines: culture-rules, agents, scripts. The commands act on
the token file directly, so they need no running server, but the server and
the commands must read the same file (`--tokens` or `QODO_SERVER_TOKENS`).

```bash
qodo-cli tokens issue --name culture-rules --role editor    # prints the token once
qodo-cli tokens list                                         # ids, roles, state; never a token
qodo-cli tokens revoke 0123456789abcdef                      # stops it working
```

- A token reads `qcl_<id>.<secret>`. Send it as `Authorization: Bearer <token>`.
- The file holds only hashes, at mode 0600 in a 0700 directory. The plain token
  is shown once by `issue` and is not stored or logged. Keep it in a secret
  store.
- `--role` is one of `viewer`, `editor`, `admin`. Give each caller the lowest
  role that works: a caller that only reads findings needs `viewer`.
- `revoke` takes the id from `tokens list`. A revoked token fails with 401.
- Exit 1 for an unknown id or role, or a token already revoked; exit 2 for a
  token file that cannot be used.

## The exposure policy

The bridge does not pass every Qodo tool through. The **exposure policy** names
the upstream Qodo tools it exposes, what verb each one becomes, and on which
surfaces (`cli`, `api`, `mcp`). It is a JSON file shipped in the package:
`qodo/exposure_default.json`.

- **A tool the policy does not name is refused.** It has no verb, no route and
  no MCP tool, and the gateway refuses it again at call time.
- **A tool that is not read-only needs `allow_write: true`.** The gateway reads
  Qodo's own annotation (`readOnlyHint`) for each tool. A tool that is not
  marked read-only, or has no annotation, is refused unless its policy entry
  sets `allow_write`.
- **A tool is refused on a surface its entry does not list**, even if some
  other code asked for it there.

The default policy names ten tools:

| Verb | Upstream tool | Surfaces | `allow_write` |
|------|---------------|----------|---------------|
| `findings list` | `get-pr-review-findings` | cli, api, mcp | no |
| `findings implement` | `pr-review-mark-implemented` | cli, api, mcp | yes |
| `findings dismiss` | `pr-review-dismiss` | cli, api, mcp | yes |
| `rules search` | `rules-search` | cli, api, mcp | no |
| `rules list` | `rules-list` | cli, api, mcp | no |
| `rules show` | `rules-get` | cli, api, mcp | no |
| `rules metadata` | `rules-metadata` | cli, api, mcp | no |
| `history show` | `pull-request-review-history` | cli, api, mcp | no |
| `history similar` | `pull-request-similar-suggestions` | cli, api, mcp | no |
| `history stats` | `pull-request-stats` | cli, api, mcp | no |

Qodo's server offered 71 tools when it was probed. The ones the bridge leaves out include
everything that writes to pull requests or changes rules or starts reviews;
[`docs/qodo-mcp-tools.md`](../qodo-mcp-tools.md) lists them and why.

**Local verbs are not governed by the policy.** `rules get`, `review list`,
`review resolve`, `config show`, `config validate`, `config init` and
`config propose` do not go through the gateway. The registry fixes their
surfaces in code: `rules get` is on cli, api and mcp, `config propose` on cli
and api, and the rest on the CLI only. A person or process that can run the
CLI can run them.

### Changing the policy

Set `QODO_EXPOSURE_POLICY` to the path of your own policy file. It replaces the
shipped policy whole: a tool your file does not name is not exposed. The CLI, the
HTTP routes and the MCP tools are all generated from it when the process starts,
so restart `qodo-cli serve` and any MCP client after a change.

```json
{
  "version": 1,
  "tools": [
    {"tool": "rules-search", "verb": "rules search", "surfaces": ["cli", "mcp"]},
    {"tool": "pr-review-dismiss", "verb": "findings dismiss", "surfaces": ["api"], "allow_write": true}
  ]
}
```

- `tool` is Qodo's own tool name, `verb` is `<noun> <name>` as it appears on
  every surface, and `surfaces` is any of `cli`, `api` and `mcp`.
- The file may name any tool whose input schema is packaged in
  `qodo/tool_schemas.json`: every tool Qodo offered when the schemas were
  recorded (71). A tool with no recorded schema stops the bridge from starting
  (exit 2), and so does a file that is missing or not valid.
- A tool Qodo does not mark read-only is refused unless its entry says
  `"allow_write": true`.
- The pinned HTTP contract (`api/openapi.json`) describes the shipped policy.
  With your own policy the served `/openapi.json` is the truth.

To change the shipped default itself (a change to this repo), edit
`qodo/exposure_default.json`, regenerate the pinned contract (see
[`docs/integrators.md`](../integrators.md#the-pinned-contract)) and run
`uv run pytest -n auto -q`. To pick up tools Qodo added later, re-record
`tests/fixtures/qodo_mcp/tool_schemas.json` and run
`python3 scripts/export_tool_schemas.py --write`.

A program that embeds the bridge can build a registry from its own policy file
with `qodo.exposure.load_policy(path)` and `qodo.registry.build_registry(policy)`.
Allowing a tool to write is a decision, not a default: set `allow_write` only
for a tool you mean to let callers use, and know that the tool then changes
Qodo's state on a real pull request.

## config propose and its allowlists

`config propose` is the gate between an improvement agent's local commits and a
pull request that someone else opens. It compares a checkout's committed `HEAD`
with a base ref and returns the changed files with their contents, but only when
the change is a safe Qodo configuration change. It reads git objects and writes
nothing, and it never opens a pull request.

```bash
qodo-cli config propose --repo example-owner/example-repo \
  --checkout /srv/qodo-checkouts/example-owner/example-repo --base main --json
```

Nothing is allowed until you configure it: the packaged defaults list no repos
and no checkout roots, and an empty list refuses everything. The first answer
from a fresh install is `{"ok": false, "reason": "repo_not_allowed", ...}`.

### Configure it

Settings come from three layers; each overrides the one before it, key by key.

1. The packaged `qodo/propose_default.json`.
2. A JSON file at `$QODO_PROPOSE_CONFIG`, else `~/.qodo/propose.json` (skipped
   when absent; an unreadable or invalid file is exit 2).
3. The environment: `QODO_PROPOSE_REPOS` (comma or space separated
   `owner/name` values) and `QODO_PROPOSE_CHECKOUT_ROOTS` (absolute paths,
   separated by the OS path separator, `:` on Linux).

```bash
export QODO_PROPOSE_REPOS="example-owner/example-repo"
export QODO_PROPOSE_CHECKOUT_ROOTS="/srv/qodo-checkouts"
```

### What it allows

| Setting | Default | Meaning |
|---------|---------|---------|
| `repos` | none | Repos (`owner/name`) a proposal may name. Checked first, before the file system or git is touched |
| `checkout_roots` | none | Absolute roots. A checkout's real path must be exactly `<root>/<owner>/<name>` for the repo it names |
| `paths` | `.pr_agent.toml` (toml), `REVIEW.md` (markdown), `best_practices.md` (markdown) | The only files a proposal may change, by exact path (case included) |
| `refused_settings` | see below | Settings in `.pr_agent.toml` a proposal may not change |
| `max_files` | 10 | Most files in one change |
| `max_file_bytes` | 32768 | Largest file, in bytes |
| `max_reply_bytes` | 61440 | Largest whole reply, in bytes |
| `git_timeout_seconds` | 20 | Time limit on each git call |

The `<root>/<owner>/<name>` layout is what binds a repo name to a place on
disk: an allowed repo name cannot open another repo's checkout. Each checkout
must be a plain git repository (a `.git` directory).

Refused settings in `.pr_agent.toml`, by default: `pr_reviewer.enable_auto_approval`
(only `false` allowed), `pr_reviewer.auto_approve_for_low_review_effort` (any
change refused), `pr_reviewer.auto_approve_for_no_suggestions` (only `false`
allowed), and the whole tables `github_app`, `gitlab`, `bitbucket_app`,
`azure_devops_server` and `config`. See [Known limits](#known-limits): the
auto-approval key names are not verified.

The checks run in this order: repo, checkout, base ref, the change set (no
renames, deletions, type changes, symlinks, submodules or executable files; every
path allowed), then contents (UTF-8, no NUL byte, within the size limits;
`.pr_agent.toml` must parse as TOML and change no refused setting). Only
committed objects are read: uncommitted work in the checkout is ignored.

A refusal is a normal answer, exit 0, `{"ok": false, "reason", "detail",
"paths"}`, and never carries file contents. A broken environment (git missing or
timed out, a checkout that is not a git repository) is exit 2. The full list of
reasons is in `qodo-cli explain config propose`.

Two files, `best_practices.md` and the `[pr_reviewer] extra_instructions`
setting, come back as warnings (`code`, `path`, `detail`) instead of refusals,
because Qodo's current reference does not mention them.

## The change log

Two kinds of change add a line to an append-only log, so a person or the web UI
can see what happened: a finding marked implemented or dismissed (one line per
finding), and a configuration change that `config propose` accepted. Only
successful changes are logged. It is not a complete audit trail: `review resolve`
and `config init` write nothing to it, and if the log cannot be written the
change still succeeds with a warning.

- **Where:** `$QODO_CHANGE_LOG`, else `$XDG_STATE_HOME/qodo-cli/changes.jsonl`,
  else `~/.local/state/qodo-cli/changes.jsonl`. The directory is mode 0700 and
  the file 0600.
- **Format:** JSON Lines, one object per line. Every line has `time` (UTC),
  `kind` and, when known, `actor`, which is the surface that made the change
  (`cli`, `api` or `mcp`).
- **What a line holds:**
  - `finding.status_changed`: `id` (one line per finding), `status`
    (`implemented` or `dismissed`) and, for a dismissal, `reason`;
  - `config.proposed`: `repo` and `files` (the paths only).
- **What it never holds:** the API key, finding text or file contents.
- **Size:** at about 1,000,000 bytes the next write renames the file to
  `changes.jsonl.1`, replacing the previous one, so about twice that is kept.
- **Reading it:** `GET /changes?limit=N` (viewer; 1 to 500, default 50) or
  `qodo.api.changes()`. Entries come back oldest first.

If the log cannot be written, the change itself still succeeds and a warning
goes to stderr.

## The culture-rules bundle

The bundle in [`docs/rules/qodo/`](../rules/qodo/README.md) wires the bridge
into culture-rules. Read its README before enabling anything; this section only
summarises it. It needs culture-rules 0.47.0 or later.

Two flows, both configuration only:

- **Findings.** After Qodo reviews a pull request, culture-rules calls
  `POST /verbs/findings/list` (with `summary: true`) once per head commit and
  keeps the counts as the run's outputs. It dismisses and implements nothing.
- **Improvement.** After a pull request merges, associate changes the repo's
  Qodo configuration in a dedicated checkout, `config propose` checks the change,
  and culture-rules opens it as one pull request through its own GitHub App from
  the fixed branch `qodo-config/update`.

**The bundle ships with all four rules disabled.** Before you enable it, set at
least these (the README has the full list of eleven):

- the repos, replacing `example-owner/example-repo` in all four rules;
- a qodo-cli service token with the `editor` role (`config propose` needs it;
  `findings list` needs `viewer`), stored as the culture-rules grant
  `QODO_BRIDGE_SERVICE_TOKEN`;
- the checkout root (`/srv/qodo-checkouts` as shipped), which must match
  `QODO_PROPOSE_CHECKOUT_ROOTS` here, with each repo in `QODO_PROPOSE_REPOS`;
- the bridge URL, `http://127.0.0.1:8792` as shipped, reachable from the machine
  the culture-rules actor runs on;
- associate's bridge port (the bundle writes `8794` as a placeholder), a token
  for it, and the GitHub App actor settings.

Then enable each rule with `culture-rules rules enable <id> --apply`.

## Web UI and the release order

The web UI is a Vite + React app in `web/`. It is built into the wheel as
`qodo/web_dist` and `qodo-cli serve` serves it with the API on the same
listener, behind the same authentication; the browser calls the API through the
same-origin `/api` prefix, which the server strips. It has a header (with the
Qodo key status) and three tabs, Findings, Configuration and Rules. The screens
inside the tabs are built separately.

Without a built UI, `serve` still works and `GET /` answers a JSON 404.

To release a wheel with the UI:

1. `npm ci && npm run build` in `web/`.
2. `uv build` with `QODO_REQUIRE_WEB=1`, so that a missing build fails the build
   instead of shipping an API-only wheel.

`.github/workflows/publish.yml` does exactly this. `hatch_build.py` only
packages an existing `web/dist`; it never runs Node. `QODO_SKIP_WEB=1` leaves
the UI out on purpose.

## What the bridge never does

- It never pushes, merges, commits or opens a pull request. It reads a
  checkout, returns files, and leaves the pull request to culture-rules.
- It never calls Qodo as a service account. It calls Qodo with one personal API
  key, so every call is made, and rate-limited, as that person.
- It never returns the API key, in a route, a tool or an error.
- It never follows a redirect from Qodo's endpoint, and refuses a non-HTTPS
  endpoint, so the key cannot be sent to another host.

## Known limits

Read these before you rely on the bridge:

- **The replies of the two write tools were never recorded.** The probe in
  [`docs/qodo-mcp-tools.md`](../qodo-mcp-tools.md) did not call
  `pr-review-mark-implemented` or `pr-review-dismiss`, because each changes a
  real finding on a real pull request. Their input schemas are recorded; their
  reply shape is unknown until one is called on purpose.
- **Qodo's history tools failed upstream when probed.**
  `pull-request-review-history`, `pull-request-similar-suggestions` and
  `pull-request-stats` each failed twice with `MT-UPSTREAM-DOWN:
  context-retriever unreachable`. The `history` verbs are built from the
  recorded input schemas only; no successful reply has been seen, and the shape
  of an accepted or rejected suggestion is unknown. Re-probe before building on
  them.
- **Whether Qodo still reads `best_practices.md` and `[pr_reviewer]
  extra_instructions` is unverified.** Neither is mentioned in Qodo's current
  configuration reference (checked 2026-11-16). The file the current review
  reads for instructions is `REVIEW.md`. `config init` still writes both older
  files today, and `config propose` returns changes to them as warnings.
- **The refused-settings key names for auto-approval are unverified** against
  Qodo's reference. The default list refuses `pr_reviewer.enable_auto_approval`,
  `pr_reviewer.auto_approve_for_low_review_effort` and
  `pr_reviewer.auto_approve_for_no_suggestions`; if Qodo uses other key names,
  a change to them is not caught. Check the names and extend `refused_settings`
  in your own `propose.json`.
- **The bundle's own limits** are in
  [its README](../rules/qodo/README.md#known-limits): the poll interval is a
  runner command; `base` is the run's `base_commit`, not the merge commit; a
  failed findings run waits for the next push or green checks; and the two
  checks fail by message.
- **A local verb can be reached by anyone who can run the CLI.** The policy
  governs Qodo's tools only (see [above](#the-exposure-policy)).
