# Manual verification checklist

Two `qodo-cli` paths are implemented and unit-tested with mocks, but cannot be
exercised against the real systems in CI (we have no GitHub Enterprise instance
and no Qodo subscription in the CI environment). This checklist is how to verify
them by hand when a real system *is* available, plus the offline contract test
that pins the response shape in the meantime.

See `docs/qodo-skills-sources.md` for the resolved contracts these verify.

## 1. Offline contract test (always runs)

`tests/test_contracts.py::test_rules_search_parses_recorded_response` asserts the
`/rules/search` parser against a recorded response fixture
(`tests/fixtures/rules_search_response.json`) — relevance order preserved, the
documented `{id, name, content, severity}` fields present, severities within the
known set, and unknown extra fields (e.g. `score`) passing through untouched.

If the Qodo API response shape ever changes, refresh the fixture from a real
response and update the contract test alongside the parser:

```bash
# with a real key, capture a response shape (redact ids/content as needed):
QODO_API_KEY=… qodo rules get "validate input" --json
# then update tests/fixtures/rules_search_response.json + the contract test.
```

## 2. `qodo rules` against the real Qodo API

Prerequisite: a Qodo subscription and an API key, either in
`~/.qodo/config.json` (`"API_KEY"`) or exported as `QODO_API_KEY`.

```bash
qodo doctor                      # qodo_client_config_present should pass
qodo rules get "validate all user input at trust boundaries"
qodo rules get "sql safety" --json | jq '.rules[].severity'
qodo rules get "anything" --no-scope --json | jq '.scopes'   # -> null
```

Expected: relevance-ranked rules with `ERROR` / `WARNING` / `RECOMMENDATION`
severities; `--json` carries `scopes` (the auto-detected `org/repo`, or `null`
under `--no-scope`); a missing key exits `2` with a `hint:` and never prompts.

Opt-in smoke (runs only when the key is set):

```bash
QODO_API_KEY=… uv run pytest tests/test_contracts.py::test_live_rules_search_smoke -v
```

## 3. `qodo review` against a real GitHub Enterprise host

`resolve_provider()` upgrades an unknown host to `github` when `gh` is
authenticated to it (`gh auth status --hostname <host>`). This is covered by
mocked tests only — verify it against a real GHE instance like so:

```bash
gh auth login --hostname ghe.your-company.com     # one-time
cd /path/to/a/repo/whose/origin/is/that/GHE/host
qodo review list                                   # should detect + list, not error
```

Expected: the GHE remote resolves to `github` (no "not wired yet" / "could not
identify the git provider" error), and the Qodo bot's comments list as on
github.com. Then exercise resolution on a real PR:

```bash
qodo review list --json | jq '.comments[] | {id, severity, type}'
qodo review resolve <comment-id> --reply "Verified." --sign
qodo review resolve --all --severity HIGH          # batch
```

Expected: the reply posts, the `+1` reaction lands, and the GitHub review thread
is marked resolved (via the GraphQL `resolveReviewThread` mutation); a comment
with no mappable thread falls back to reaction-only and is reported, not failed.

Opt-in smoke (runs only when the remote is provided):

```bash
QODO_CLI_GHE_REMOTE=git@ghe.your-company.com:org/repo.git \
  uv run pytest tests/test_contracts.py::test_live_ghe_resolves_to_github -v
```

## 4. `qodo review` against a self-hosted GitLab on a custom domain

`resolve_provider()` upgrades an unknown host to `gitlab` when `glab` is
authenticated to it (`glab auth status --hostname <host>`), mirroring the GHE
path above. `gitlab.com` is detected by hostname directly (no `glab` call);
`gh` is consulted before `glab`, so a host both CLIs know resolves to `github`.
Covered by mocked tests only — verify it against a real self-hosted instance:

```bash
glab auth login --hostname gitlab.your-company.com   # one-time
cd /path/to/a/repo/whose/origin/is/that/GitLab/host
qodo review list                                      # should detect + list, not error
```

Expected: the custom-domain remote resolves to `gitlab` (no "not wired yet" /
"could not identify the git provider" error), and the Qodo bot's notes list as
on gitlab.com. Resolution marks the note's MR *discussion* resolved (GitLab has
no `+1` marker).

## 5. Provider gate (still-unwired providers)

An Azure/Bitbucket/Gerrit remote should fail with a clear, actionable message
rather than misbehaving:

```bash
cd /path/to/an/azure/devops/repo
qodo review list        # exit 2, "provider 'azure' is not wired yet" + hint
```
