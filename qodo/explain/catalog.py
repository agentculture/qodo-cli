"""Markdown catalog for ``qodo-cli explain <path>``.

Each entry is verbatim markdown. Keys are command-path tuples. The empty tuple
and ``("qodo-cli",)`` both resolve to the root entry.

Keep bodies self-contained: an agent reading one entry should get enough
context without chaining reads.
"""

from __future__ import annotations

# The distribution, the installed console script and the CLI's argparse `prog`
# are all `qodo-cli` (the Python import package is `qodo`). The agent-first
# rubric runs `explain <script-name>` (i.e. `explain qodo-cli`), which the root
# entry below answers. Command examples in every body invoke `qodo-cli`.
_ROOT = """\
# qodo-cli

An unofficial, community CLI to manage **Qodo** from your terminal, in
zero-dependency Python. It runs Qodo's core jobs natively — `rules` (semantic
rule search) and `review`/`pr` (triage the Qodo bot's PR comments) — each citing
`qodo-ai/qodo-skills` as its behavioral source of truth (we point at the skills,
we do not fork or vendor them). It also carries an agent-first introspection
surface (`whoami`/`learn`/`explain`/`overview`/`doctor`) and a mesh identity
(`culture.yaml` + `AGENTS.colleague.md`). Not affiliated with Qodo Ltd.

The distribution and the installed command are both named `qodo-cli`.

## Verbs

- `qodo-cli rules get "<query>"` — semantic-search your org's Qodo rules.
- `qodo-cli review list` — list the Qodo bot's PR review comments.
- `qodo-cli review resolve <id>` — reply to and acknowledge a comment.
- `qodo-cli pr ...` — alias for `review`.
- `qodo-cli config show|validate|init` — manage the repo Qodo reviewer config.
- `qodo-cli whoami` — identity probe from `culture.yaml`.
- `qodo-cli learn` — structured self-teaching prompt.
- `qodo-cli explain <path>` — markdown docs for any noun/verb.
- `qodo-cli overview` — descriptive snapshot of the agent.
- `qodo-cli doctor` — check the agent-identity invariants.
- `qodo-cli cli overview` — describe the CLI surface.

## Exit-code policy

- `0` success
- `1` user-input error
- `2` environment / setup error
- `3+` reserved

## See also

- `qodo-cli explain rules`
- `qodo-cli explain review`
- `qodo-cli explain doctor`
"""

_RULES = """\
# qodo-cli rules

Surface your org's Qodo coding rules by semantic search. Native reimplementation
of `qodo-ai/qodo-skills` `qodo-get-rules` (cited, not vendored): it reads the API
key already in `~/.qodo/config.json` and POSTs your query to the Qodo rules API,
returning the relevance-ranked rules with their severity
(`ERROR`/`WARNING`/`RECOMMENDATION`).

Reuses existing credentials — it never prompts. Errors (exit 2) when no API key
is available (`~/.qodo/config.json` absent and `QODO_API_KEY` unset).

When `--scope` is omitted, the scope is **auto-detected** like `qodo-get-rules`
does: the `org/repo` slug from the git `origin` (SSH + HTTPS forms) and the
module name from a `modules/<name>/` path. Nothing detectable → `scopes` is
omitted entirely (never sent empty). `--scope` overrides; `--no-scope` forces
omission.

## Usage

    qodo-cli rules get "validate all user input at trust boundaries"
    qodo-cli rules get "<query>" --top-k 10 --scope org/repo
    qodo-cli rules get "<query>" --no-scope
    qodo-cli rules get "<query>" --json
    qodo-cli rules overview

## See also

- `qodo-cli explain review`
- `docs/qodo-skills-sources.md` — the citation ledger
"""

_REVIEW = """\
# qodo-cli review (a.k.a. qodo-cli pr)

Triage the Qodo bot's review comments on the current branch's PR. Native
reimplementation of `qodo-ai/qodo-skills` `qodo-pr-resolver` (cited, not
vendored): it drives your existing provider CLI (`gh` / `glab`) to find the open
PR/MR, list the comments authored by a Qodo bot (`qodo-code-review`,
`qodo-merge`, `qodo-ai`, `pr-agent-pro`), and reply to / acknowledge / resolve
them. Reuses your provider-CLI auth — no new credentials.

GitHub (incl. GitHub Enterprise via your `gh` host config) and GitLab (via
`glab`) are wired; Azure/Bitbucket/Gerrit are recognised but deferred (see the
citation ledger). On GitLab the resolvable unit is the MR *discussion*: `resolve`
replies to and marks the note's discussion resolved (GitLab has no `+1` marker).
The code-fixing loop stays with the calling agent — this surface is the
deterministic detect/list/reply/acknowledge/resolve slice.

`review list` parses each comment body into structured triage fields —
`severity` (from the badge: HIGH/MEDIUM/LOW), `type` and `categories` (the
`<code>` chips), `description` (the `<pre>` block), and `agent_prompt` (the
remediation block) — degrading to title-only when a body isn't recognised.
`--kind {inline,summary,all}` filters out the non-actionable summary rollups.

`review resolve` is best-effort and reports every action (reply / acknowledge /
resolve-thread) per comment, so a posted reply whose acknowledgement failed
reads as partial success, not total failure (exit 1). It posts the `+1` reaction
and, by default, resolves the GitHub review **thread** via the GraphQL
`resolveReviewThread` mutation (`--no-resolve-thread` to skip; falls back to the
reaction when no thread maps to the comment).

## Usage

    qodo-cli review list
    qodo-cli review list --pr 123 --kind inline --json
    qodo-cli review resolve <comment-id> --reply "Fixed in <sha>." --sign
    qodo-cli review resolve --all --severity HIGH
    qodo-cli review resolve <id> --no-resolve-thread
    qodo-cli review overview

`--sign` appends the `culture.yaml` nick signature (`- <nick> (Claude)`) to
`--reply`, at most once. `--all` / `--severity` resolve every matching inline
comment in one call.

## See also

- `qodo-cli explain rules`
- `docs/qodo-skills-sources.md` — the citation ledger
"""

_CONFIG = """\
# qodo-cli config

Manage the **repo-level** Qodo reviewer config — `.pr_agent.toml` (the Qodo Merge
`[pr_reviewer]` section) and `best_practices.md`. These are the levers that make
Qodo's reviews of *this* repo accurate: without them Qodo falls back to inferred
conventions and raises false positives. Distinct from the *client*
`~/.qodo/config.json` that `qodo-cli rules` reads.

`show` and `validate` are read-only; `init` scaffolds the two files when absent
and never overwrites without `--force`. Read from the current git repo root
(where Qodo reads its config). Cite-faithful to Qodo Merge's configuration docs.

## Usage

    qodo-cli config show
    qodo-cli config validate          # exit 1 if invalid
    qodo-cli config init [--force]
    qodo-cli config overview
    qodo-cli config show --json

`propose` is read-only too: it checks a change an agent committed in a checkout and
returns it only when it is a safe Qodo configuration change (`qodo-cli explain config
propose`). It never pushes, merges or opens a pull request.

## See also

- `qodo-cli explain config propose`
- `qodo-cli explain review`
- `qodo-cli explain doctor` — `doctor` also reports whether these files exist
- `docs/qodo-skills-sources.md` — the citation ledger
"""

_CONFIG_PROPOSE = """\
# qodo-cli config propose

Compare a checkout's committed `HEAD` with a base ref and return the changed files with
their contents, but only when the change is a safe Qodo configuration change. It is the
gate between an improvement agent's local commits and a pull request someone else opens
(culture-rules, through its own GitHub App). **Read-only**: it reads the checkout's git
objects and writes nothing; no code path in qodo-cli pushes, merges or opens a PR. On
the `cli` and `api` surfaces.

## Usage

    qodo-cli config propose --repo OWNER/NAME --checkout /abs/path --base main [--json]

## Checks, in order

1. `repo` is in the repo allowlist (before the checkout is touched at all).
2. The checkout's real path is exactly `<root>/<owner>/<name>` for an allowed checkout
   root (the place binds the repo name to its checkout), and is a plain git
   repository (a `.git` directory).
3. `base` is a plain branch, tag or commit name (never `-...`, `..`, `@{`, `^`, `:`),
   names a commit, and is an ancestor of `HEAD`.
4. The change (`git diff-tree -r -z --raw -M`): at least one file, at most `max_files`;
   no rename or copy; every path exactly in the path allowlist; no deletion, type change,
   symlink, submodule or executable file at `HEAD`.
5. Contents, read from the objects at `HEAD` (never the working tree; uncommitted work is
   ignored): UTF-8, no NUL byte, within `max_file_bytes`; `.pr_agent.toml` parses as TOML
   and changes no refused setting (auto-approval, `[github_app]` triggers and the other
   platform trigger tables, `[config]`); the whole reply within `max_reply_bytes`.

Legacy settings (`best_practices.md`, `[pr_reviewer] extra_instructions`) are returned as
**warnings** ("not in Qodo's current configuration reference"), not refusals: whether Qodo
still reads them is unverified.

## Result

- success: `{ok: true, repo, base, base_sha, head, files: [{path, content}], warnings:
  [{code, path, detail}]}`
- refusal (exit 0): `{ok: false, reason, detail, paths}`, never with file contents.
  Reasons: `repo_not_allowed`, `checkout_not_allowed`, `invalid_base`, `unknown_base`,
  `base_not_ancestor`, `no_changes`, `too_many_files`, `rename`, `path_not_allowed`,
  `deletion`, `type_change`, `not_regular_file`, `executable`, `file_too_large`, `binary`,
  `not_utf8`, `invalid_toml`, `refused_setting`, `too_large`.
- exit 2: git missing or timed out, or the checkout is not a readable git repository.

## Configuration

Defaults ship in `qodo/propose_default.json` (path allowlist, refused and legacy
settings, caps; no repos and no roots, so nothing is allowed until configured). A JSON
file at `$QODO_PROPOSE_CONFIG` (else `~/.qodo/propose.json`) overrides it key by key;
`QODO_PROPOSE_REPOS` (comma separated) and `QODO_PROPOSE_CHECKOUT_ROOTS` (path-separator
separated) override the two allowlists.

## See also

- `qodo-cli explain config`
"""

_SERVE = """\
# qodo-cli serve

Run the HTTP API (needs the `server` extra: `pip install 'qodo-cli[server]'`). No Node is
needed; a built web UI inside the package is served too when it exists.

Routes come from the verb registry: every verb on the `api` surface is
`POST /verbs/<noun>/<name>` with the verb's params as a JSON object body, and answers JSON. A
verb the exposure policy does not put on the api surface has no route. Also: `GET /health`
(public; service, version and the key's `present`/`state`/`days_left`, never the key),
`GET /verbs`, `GET /changes?limit=` and `GET /openapi.json` (viewer). The contract is pinned in
`api/openapi.json`.

Roles: a read verb needs `viewer`, a write verb and `config propose` need `editor`, anything
else `admin`. No credential is 401, too low a role is 403. Errors are
`{"error": {"code", "message", "remediation", "errors"}}`: a bad request is 400, a body over
64 KiB is 413, Qodo or the configuration failing is 503.

## Usage

    qodo-cli serve [--host H] [--port P] [--lan-host H] [--lan-port P] [--tokens FILE] [--json]

- Loopback listener (always): `--host`/`--port` or `QODO_HOST`/`QODO_PORT`, default
  127.0.0.1:8792. It must be a loopback address (it trusts Cloudflare Access headers). Access is
  on when `QODO_ACCESS_LISTEN` (loopback `host:port`), `QODO_ACCESS_TEAM_DOMAIN` and
  `QODO_ACCESS_AUD` are all set.
- LAN listener (off unless a host is given): `--lan-host`/`--lan-port` or
  `QODO_LAN_HOST`/`QODO_LAN_PORT` (port default 8793). Service tokens only.
- `QODO_ADMINS` / `QODO_EDITORS`: comma-separated Access identities raised above viewer.
- Tokens: `--tokens` or `QODO_SERVER_TOKENS`, default under `$XDG_STATE_HOME/qodo-cli/`; see
  `qodo-cli explain tokens`.

Exit 1 on a refused configuration (a non-loopback loopback address, a bad port), 2 when the
`server` extra is missing.
"""

_MCP = """\
# qodo-cli mcp

Serve the registry's verbs as MCP tools over stdio (needs the `mcp` extra:
`pip install 'qodo-cli[mcp]'`). Point an MCP client at the command `qodo-cli mcp`.

One tool per verb on the registry's `mcp` surface, named `<noun>_<name>` (`findings_list`,
`findings_implement`, `rules_search`, ...). The tool's input schema is the verb's params schema,
and its handler is the verb's own, called for the `mcp` surface: the gateway refuses an upstream
tool the exposure policy does not list for `mcp`, and a change made through a tool is logged with
actor `mcp` (`GET /changes`). A verb the policy leaves off `mcp`, or a tool it does not name, has
no tool. Verbs that are local or `cli`-only (`config`, `review`) are not tools.

Arguments are checked before the call (unknown names, missing required ones, JSON types); a
refusal, a `CliError` and an upstream failure each come back as a tool error with the message and
the hint, never a traceback. The API key is never part of a result or an error. Stdout carries
the protocol only.

## Usage

    qodo-cli mcp [--json]

Needs `QODO_API_KEY` (or `~/.qodo/config.json`) like every verb that calls Qodo. Exit 2 when the
`mcp` extra is missing (the message names it).
"""

_TOKENS = """\
# qodo-cli tokens

Issue, list and revoke the HTTP API's service tokens (`qcl_<id>.<secret>`). They act on the token
file: `--tokens FILE`, else `QODO_SERVER_TOKENS`, else
`$XDG_STATE_HOME/qodo-cli/service_tokens.json` (mode 0600; only hashes are stored). The server
and these verbs must read the same file.

## Usage

    qodo-cli tokens issue --name NAME --role viewer|editor|admin [--tokens FILE] [--json]
    qodo-cli tokens list [--tokens FILE] [--json]
    qodo-cli tokens revoke ID [--tokens FILE] [--json]
    qodo-cli tokens overview [--json]

`issue` prints the plain token once: it is never stored or logged, so keep it. `list` shows ids,
identities, roles and whether each is revoked, never a token or a hash. Send a token as
`Authorization: Bearer <token>`.

Exit 1 for an unknown role or id, or a token already revoked; 2 for an unusable token file.
"""

_WHOAMI = """\
# qodo-cli whoami

Reports the agent's identity from `culture.yaml`: nick (`suffix`), backend,
served model, and the package version. Read-only.

## Usage

    qodo-cli whoami
    qodo-cli whoami --json
"""

_LEARN = """\
# qodo-cli learn

Prints a structured self-teaching prompt covering purpose, command map,
exit-code policy, `--json` support, and the `explain` pointer.

## Usage

    qodo-cli learn
    qodo-cli learn --json
"""

_EXPLAIN = """\
# qodo-cli explain <path>

Prints markdown documentation for any noun/verb path. Unlike `--help` (terse,
positional), `explain` is global and addressable by path.

## Usage

    qodo-cli explain qodo-cli
    qodo-cli explain whoami
    qodo-cli explain --json <path>
"""

_OVERVIEW = """\
# qodo-cli overview

Read-only descriptive snapshot of the agent: identity (from `culture.yaml`), the
verb surface, and the sibling-pattern artifacts the template carries. Accepts an
ignored `target` so a stray path never hard-fails.

## Usage

    qodo-cli overview
    qodo-cli overview --json
"""

_DOCTOR = """\
# qodo-cli doctor

Two groups of checks, emitted as
`{healthy, checks: [{id, passed, severity, message, remediation}]}`:

- **agent identity** (in a source checkout) — prompt-file-present and
  backend-consistency, mirroring `steward doctor` (this repo's backend is
  `colleague` → `AGENTS.colleague.md`), plus a `.claude/skills/` check.
- **Qodo setup** (any repo, against the current git root) — whether
  `.pr_agent.toml` and `best_practices.md` are present (tune Qodo's PR reviews)
  and whether `~/.qodo/config.json` / `QODO_API_KEY` is available (for
  `qodo-cli rules`). These are advisory; each carries a `remediation` that guides
  setup.

`healthy` is true when every **error**-severity check passes; advisory
(`warning`/`info`) checks surface guidance without flipping it. Exits 1 only on
an error-severity failure.

## Usage

    qodo-cli doctor
    qodo-cli doctor --json
"""

_CLI = """\
# qodo-cli cli

Noun group for CLI-surface introspection. `cli overview` describes the CLI
itself (distinct from the global `overview`, which describes the agent).

## Usage

    qodo-cli cli overview
    qodo-cli cli overview --json
"""


ENTRIES: dict[tuple[str, ...], str] = {
    (): _ROOT,
    ("qodo-cli",): _ROOT,
    ("rules",): _RULES,
    ("rules", "get"): _RULES,
    ("rules", "overview"): _RULES,
    ("review",): _REVIEW,
    ("review", "list"): _REVIEW,
    ("review", "resolve"): _REVIEW,
    ("review", "overview"): _REVIEW,
    ("pr",): _REVIEW,  # `pr` is an alias for `review`
    ("config",): _CONFIG,
    ("config", "show"): _CONFIG,
    ("config", "validate"): _CONFIG,
    ("config", "init"): _CONFIG,
    ("config", "propose"): _CONFIG_PROPOSE,
    ("config", "overview"): _CONFIG,
    ("serve",): _SERVE,
    ("mcp",): _MCP,
    ("tokens",): _TOKENS,
    ("tokens", "issue"): _TOKENS,
    ("tokens", "list"): _TOKENS,
    ("tokens", "revoke"): _TOKENS,
    ("tokens", "overview"): _TOKENS,
    ("whoami",): _WHOAMI,
    ("learn",): _LEARN,
    ("explain",): _EXPLAIN,
    ("overview",): _OVERVIEW,
    ("doctor",): _DOCTOR,
    ("cli",): _CLI,
    ("cli", "overview"): _CLI,
}


# --- verbs from the verb registry (qodo.registry) -------------------------------------------------
# Built from the registry, so an entry cannot drift from the verb's params; an entry written above
# is never replaced.


def _flag_usage(verb, params) -> str:
    required = set(verb.params.get("required", []))
    parts = []
    for name, prop in params:
        flag = "--" + name.replace("_", "-")
        arg = "" if prop.get("type") == "boolean" else f" {name.upper()}"
        parts.append(f"{flag}{arg}" if name in required else f"[{flag}{arg}]")
    return " ".join(parts + ["[--json]"])


def _verb_entry(verb) -> str:
    props = [
        (n, p) for n, p in verb.params.get("properties", {}).items() if not p.get("deprecated")
    ]
    where = f"`{verb.tool}`" if verb.tool else "a local verb"
    out = [f"# qodo-cli {verb.verb}", "", verb.summary, ""]
    if verb.kind == "upstream":
        out += [
            f"Calls Qodo's upstream tool {where} through the exposure policy "
            f"(surfaces: {', '.join(verb.surfaces)}); a tool the policy does not name is refused.",
            "It **writes** (the policy allows it)." if verb.writes else "It is read-only.",
            "Needs `QODO_API_KEY` or `~/.qodo/config.json`; exit 2 when Qodo cannot be reached.",
            "",
        ]
    out += ["## Usage", "", f"    qodo-cli {verb.verb} {_flag_usage(verb, props)}", ""]
    if props:
        out.append("## Parameters")
        out.append("")
        for name, prop in props:
            need = "required" if name in verb.params.get("required", []) else "optional"
            text = " ".join(str(prop.get("description", "")).split())
            out.append(
                f"- `--{name.replace('_', '-')}` ({need}, {prop.get('type', 'json')}): {text}"
            )
        out.append("")
    out += ["## See also", "", f"- `qodo-cli explain {verb.noun}`"]
    return "\n".join(out) + "\n"


def _noun_entry(noun: str, verbs) -> str:
    lines = [f"# qodo-cli {noun}", ""]
    lines += [f"- `qodo-cli {v.verb}` — {v.summary}" for v in verbs]
    lines += [f"- `qodo-cli {noun} overview` — describe this noun", ""]
    lines += ["Built from the exposure policy: a tool it does not name has no verb here.", ""]
    lines += ["Every verb supports `--json`; exit 1 on bad input or a refused call, 2 when"]
    lines += ["Qodo cannot be reached.", ""]
    return "\n".join(lines)


def _add_registry_entries() -> None:
    from qodo import registry
    from qodo.cli._errors import CliError

    try:
        verbs = registry.default_registry().on_surface("cli")
    except CliError:  # a broken policy must not take `explain` down with it
        return
    for noun in dict.fromkeys(v.noun for v in verbs):
        members = [v for v in verbs if v.noun == noun]
        text = _noun_entry(noun, members)
        for key in ((noun,), (noun, "overview")):
            ENTRIES.setdefault(key, text)
        for verb in members:
            ENTRIES.setdefault(verb.path, _verb_entry(verb))


_add_registry_entries()
