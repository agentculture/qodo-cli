# Integrating with qodo-cli

This page is for a program that talks to the bridge: Python code that imports
`qodo.api`, or any client of the HTTP API (culture-rules' `http.call`, a script,
a browser). After reading it you can call each public function, call each route,
read the error envelope, and keep the pinned contract current.

The **bridge** is the one qodo-cli app: a CLI, an HTTP API, an MCP server and a
web UI over the same verbs. The operator who runs it is covered in
[`docs/operations/bridge.md`](operations/bridge.md); agents that use the CLI or
MCP tools are in [`docs/agents.md`](agents.md).

## The Python API: `qodo.api`

`qodo.api` is a set of plain functions that return plain `dict` and `list`
objects, the same shapes the CLI prints under `--json`. It uses only the
standard library, so `import qodo.api` works on the base install. Import from
`qodo.api` and not from `qodo.cli._*`.

```python
from qodo import api
from qodo.cli._errors import CliError

try:
    reply = api.findings_list("https://github.com/example-owner/example-repo/pull/1")
except CliError as err:
    print(err.code, err.message, err.remediation)
```

### Exceptions

Every failure is a `qodo.cli._errors.CliError` with three fields: `code` (the
CLI exit code: `1` for a user error such as a bad argument or a call Qodo or the
exposure policy refused, `2` for an environment error such as a missing API key
or an unreachable Qodo), `message` and `remediation`. `err.to_dict()` returns
them as a dict. `CliError` lives in a private module, but it is the one
exception type to catch. A key is never part of a message.

### Functions that call Qodo

These need an API key (`QODO_API_KEY` or `~/.qodo/config.json`) and go through
the exposure policy. Arguments after `*` are keyword-only.

| Function | Returns |
|----------|---------|
| `findings_list(pr_url, *, extended=False)` | `{"finding_count", "findings", "pr_url", "review_session"}`. `pr_url` must be an `http://` or `https://` URL. `extended=True` adds locations, snippets and dismissal details |
| `findings_implement(ids, *, actor=None)` | **Write.** Qodo's reply, whose shape was never recorded. `ids` is 1 to 100 finding ids; more is a user error. Logs one line per finding |
| `findings_dismiss(ids, reason, *, actor=None)` | **Write.** As above. `reason` must be in `api.DISMISS_REASONS`: `false_positive`, `intentional`, `deferred`, `rejected` |
| `rules_search(query, *, top_k=None, scopes=None)` | `{"rules": [...]}` from the MCP tool `rules-search`. Scopes are only those you pass |
| `rules_list(*, state=None, categories=None, severity=None, scopes=None, name_contains=None, page=None, page_size=None)` | Qodo's reply for `rules-list`; only the filters you give are sent |
| `review_history(repo, path, *, limit=None)` | Qodo's reply for `pull-request-review-history`. Built from the input schema only: the upstream tool failed when probed, so the reply shape is unconfirmed |
| `rules_get(query, *, top_k=api.DEFAULT_TOP_K, scopes=None, auto_scope=True)` | A list of ranked rules (`name`, `severity`, `content`, ...) from the older rules HTTP API. Scopes: explicit `scopes`, else the git origin when `auto_scope` |
| `rules_scopes(*, scopes=None, auto_scope=True)` | The scopes `rules_get` would send, or `None` |

`actor` labels who made the change in the change log (`cli`, `api`, `mcp` or any
short string you choose). The verbs `rules show`, `rules metadata`,
`history similar` and `history stats` have no function in `qodo.api`; reach them
over HTTP or MCP, or through the registry (below).

### Functions that read or write locally

| Function | Returns |
|----------|---------|
| `key_status()` | `{"present", "state", "expires", "days_left", "detail"}`. Never contains the key. `state` is `missing`, `unknown`, `invalid`, `expired`, `expiring` or `ok` |
| `changes(limit=50)` | The newest `limit` change-log entries, oldest first (all when `None`) |
| `review_list(pr=None, kind="all")` | `{"provider", "pr", "kind", "count", "comments"}`. Uses `gh` or `glab` |
| `review_resolve(ids=None, *, pr=None, all=False, severity=None, reply=None, resolve_thread=True)` | **Write.** `{"pr", "count", "ok", "resolved": [{"comment_id", "ok", "actions"}]}`. `ok` is false when any action failed; partial results are kept |
| `config_show(root=None)` | Snapshot of `.pr_agent.toml` and `best_practices.md` under the git root (default: at or above the current directory) |
| `config_validate(root=None)` | `{"valid", "checks": [{"id", "ok", "severity", "message", "remediation"}]}`. `valid` is false only when an `error`-severity check fails |
| `config_init(root=None, *, force=False)` | **Write.** `{"repo_root", "written", "skipped", "forced"}`. Refuses a symlink or a non-regular path before writing anything |
| `config_propose(repo, checkout, base, *, settings=None, actor=None)` | See below |

### `config_propose`

A read: it writes nothing to the checkout or anywhere else except one
change-log line on success. `settings` defaults to
`qodo.propose.load_settings()`, which applies the operator's repo, checkout-root
and path allowlists ([operator guide](operations/bridge.md#config-propose-and-its-allowlists)).

- Success: `{"ok": True, "repo", "base", "base_sha", "head", "files": [{"path",
  "content"}], "warnings": [{"code", "path", "detail"}]}`.
- Refusal: `{"ok": False, "reason", "detail", "paths"}`, never with file
  contents. The reasons are `qodo.propose.REASONS`. A refusal is a return value,
  not an exception.
- A broken environment (git missing or timed out, a checkout that is not a git
  repository, an invalid settings file) raises `CliError` with code `2`.

### Verbs by params dict

Every verb is also callable by name through the registry, with the same params
schema the HTTP API uses.

```python
from qodo.registry import default_registry

verb = default_registry().get("rules", "metadata")
result = verb.handler({}, surface="api")
```

`verb.params` is the JSON Schema of the params, `verb.writes` says whether it
writes, and `verb.surfaces` lists where it is exposed. The handler checks the
params against the schema (a violation is a user error, exit 1) and, for a verb
that calls Qodo, passes `surface` to the gateway, which refuses a surface the
exposure policy does not list for that tool.

## The HTTP API

Start it with `qodo-cli serve` (see the
[operator guide](operations/bridge.md#run-the-server)). It needs the `server`
extra.

### The route scheme

Every verb on the `api` surface is **one operation**:

```text
POST /verbs/<noun>/<name>
```

The body is the verb's params as a JSON object; an empty body means no params.
The reply is the verb's result as JSON. There is no per-verb code: the routes
are generated from the same registry as the CLI. A verb that is not on the `api`
surface, or whose Qodo tool the exposure policy does not name for it, has no
route.

| Route | Role | What it does |
|-------|------|--------------|
| `POST /verbs/findings/list` | viewer | `findings list` |
| `POST /verbs/findings/implement` | editor | `findings implement` (write) |
| `POST /verbs/findings/dismiss` | editor | `findings dismiss` (write) |
| `POST /verbs/rules/get` | viewer | `rules get` |
| `POST /verbs/rules/search` | viewer | `rules search` |
| `POST /verbs/rules/list` | viewer | `rules list` |
| `POST /verbs/rules/show` | viewer | `rules show` |
| `POST /verbs/rules/metadata` | viewer | `rules metadata` |
| `POST /verbs/history/show` | viewer | `history show` |
| `POST /verbs/history/similar` | viewer | `history similar` |
| `POST /verbs/history/stats` | viewer | `history stats` |
| `POST /verbs/config/propose` | editor | `config propose` |
| `GET /health` | public | Service name, version and the Qodo key status, never the key |
| `GET /verbs` | viewer | Every served verb: noun, name, summary, kind, `writes`, `role`, `method`, `path` and the `params` schema |
| `GET /changes?limit=N` | viewer | `{"entries": [...]}`, the newest change-log entries (`N` from 1 to 500, default 50) |
| `GET /openapi.json` | viewer | The schema the server renders |

Anything else needs `admin`, so an unlisted route is closed, not open. When the
web UI is installed, `GET` of its own files and client-side routes needs
`viewer`.

```bash
curl -s -X POST http://127.0.0.1:8792/verbs/findings/list \
  -H "Authorization: Bearer $QODO_BRIDGE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"pr_url": "https://github.com/example-owner/example-repo/pull/1", "summary": true}'
```

Rules for a request:

- Send the body as `application/json` (or a `+json` type). Any other type with a
  body is 415. This keeps a page on another origin from riding a browser's
  Access cookie into a write.
- The body must be a JSON object, at most 64 KiB (65,536 bytes). A larger one is
  413; anything that is not an object is 400.
- Params are checked against the verb's schema. Unknown names, missing required
  ones and wrong types are 400.
- A reply is `Cache-Control: no-store`, except the web UI's own files.
- Keep a reply that a rules engine reads under 64 KiB: `findings list` takes
  `summary: true` for that (counts, ids, statuses and URLs only).

### Authentication

Send `Authorization: Bearer <token>` with a service token the operator issued
(`qcl_<id>.<secret>`; see `qodo-cli tokens issue`). A token works on both
listeners. The loopback listener also accepts a Cloudflare Access assertion
(`Cf-Access-Jwt-Assertion`) when the operator configured Access; the LAN
listener ignores that header. The credential and the role are checked before any
handler runs, and before any body is read.

### The error envelope

Every error is one JSON object, never a traceback:

```json
{
  "error": {
    "code": "user_error",
    "message": "pr_url must be a PR URL, got 'x'",
    "remediation": "pass the pull request's web URL, e.g. https://github.com/org/repo/pull/1",
    "errors": []
  }
}
```

`remediation` is present when there is one. `errors` is an array and is empty
today.

| Status | `error.code` | When |
|--------|--------------|------|
| 400 | `user_error` | The params are not valid for the verb, or Qodo or the policy refused the call |
| 400 | `bad_body` | The body is not a JSON object |
| 401 | `no_credentials` or `bad_token` | No credential, or an unknown or revoked one (with `WWW-Authenticate: Bearer`). A refused Access assertion carries its own reason, such as `expired` |
| 403 | `forbidden` | The role is too low for the route |
| 404 | `not_found` | No such route (with no UI installed, `GET /` too) |
| 405 | `method_not_allowed` | Wrong method for the route |
| 413 | `body_too_large` | The body is over 64 KiB |
| 415 | `unsupported_media_type` | A body that is not `application/json` |
| 500 | `internal_error` | An unexpected failure; nothing more is disclosed |
| 503 | `environment_error` | Qodo, or this server's own configuration, could not complete the call (no key, Qodo unreachable) |

A path no rule lists needs `admin`, so a caller below `admin` gets 401 or 403
for an unknown path or a wrong method before it can get 404 or 405.

A `config propose` refusal is not an error: it is a 200 with `"ok": false` in
the body.

### The pinned contract

The contract is pinned in one file: [`api/openapi.json`](../api/openapi.json)
(OpenAPI 3.1). Its `info.version` is `"1"`, the version of the HTTP contract,
not of the package, so a release that leaves the contract alone does not change
the file. Each operation carries `x-role` and `x-writes`.

Two tests keep it honest: `tests/server/test_openapi_contract.py` diffs the
served schema against the file, and the web client's
`web/src/api/contract.test.ts` checks the UI against it.

After you change a verb, a params schema, a role or a route, regenerate it and
commit the result:

```bash
uv run python scripts/export_openapi.py --write    # rewrite api/openapi.json
uv run python scripts/export_openapi.py            # check only; exit 1 when stale
```

Both need the `server` extra. The check form is what to run in CI.
