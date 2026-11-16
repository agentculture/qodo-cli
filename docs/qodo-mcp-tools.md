# Qodo MCP tools the bridge uses

This records one live probe of Qodo's remote MCP server, made on 2026-11-16
with a personal API key. It is the contract the bridge's Qodo client is built
against. Re-run the probe when Qodo changes its tools; the recorded schemas are
in `tests/fixtures/qodo_mcp/`.

## The server

- Endpoint: `https://sdk.qodo.ai/v1/tools/mcp/` (the trailing slash matters).
- Transport: MCP Streamable HTTP. A JSON-RPC `POST` gets a `text/event-stream`
  reply whose last `data:` line holds the response.
- Auth: `Authorization: Bearer <personal API key>`.
- `initialize` answered protocol `2025-03-26`, server `qodo-managed-tools`
  1.30.0, and returned no session id, so each request stands alone.
- `tools/list` returned 71 tools. Their names are in
  `tests/fixtures/qodo_mcp/tool_names.json`.

## The tools the bridge uses

Required parameters are in bold. The input schemas of all 71 tools, as
returned, are in `tests/fixtures/qodo_mcp/tool_schemas.json`.

| Tool | Parameters |
| --- | --- |
| `get-pr-review-findings` | **pr_url**, extended, git_provider |
| `pr-review-mark-implemented` | **finding_ids**, local_review_id, explanation, idempotency_key |
| `pr-review-dismiss` | **finding_ids**, local_review_id, **reason**, explanation, idempotency_key |
| `rules-search` | **query**, top_k, scopes |
| `rules-list` | state, categories, severity, scopes, name_contains, page, page_size |
| `rules-get` | **rule_id** |
| `rules-metadata` |  |
| `pull-request-review-history` | **repo**, provider, **file_path**, limit, acceptance_status, category, include_code_hunks |
| `pull-request-similar-suggestions` | **repo**, provider, **query**, embed_type, folder_path, category, acceptance_status, top_k, include_code_hunks |
| `pull-request-stats` | **repo**, provider |

Points that differ from what the spec assumed:

- **Findings are addressed by PR URL**, not by repo and number:
  `get-pr-review-findings` takes `pr_url`. `extended: true` adds locations,
  code snippets, dismissal details and review-run history.
- **Changing findings is batched.** `pr-review-mark-implemented` and
  `pr-review-dismiss` take `finding_ids` (1 to 100 per call), and Qodo asks for
  one call per PR because the PR-side update runs once per call.
- **Dismissing needs a reason**: one of `false_positive`, `intentional`,
  `deferred`, `rejected`.
- **Review history is per file or directory, not per repo.**
  `pull-request-review-history` requires `repo` and `file_path`, and searches
  upward from that path.

## What was recorded and what was not

A sanitized reply is recorded in `tests/fixtures/qodo_mcp/` for
`get-pr-review-findings`, `rules-search`, `rules-list`, `rules-get` and
`rules-metadata`, and the error reply for `pull-request-review-history`.

**`pr-review-mark-implemented` and `pr-review-dismiss` were not called.** Qodo
marks both as destructive, and a call changes a real finding on a real pull
request. Only their input schemas are recorded; their reply shape is unknown
until one is called on purpose.

## What a finding looks like

From `get-pr-review-findings` on a merged PR (compact form; fixture
`get_pr_review_findings.json`, with text replaced by sample values):

- The reply has `finding_count`, `findings`, `pr_url` and `review_session`
  (`status`, `commit_sha`, `run_id`, `started_at`, `summary_comment_id`).
- A finding has `id`, `title`, `description`, `category`, `action_level`,
  `attribution_status`, `finding_source`, `git_sha`, `review_run_id`,
  `comment_id`, `inline_comment_id` and `created_at`.
- Values seen: `category` `Correctness` and `Maintainability`; `action_level`
  `remediation_recommended`; `attribution_status` `implemented`;
  `finding_source` `IssueFinding`; session `status` `completed`.
- The compact form carries no file path or line. Those need `extended: true`,
  which this probe did not call.

`rules-metadata` lists the rule vocabulary: 12 categories (Correctness,
Security, Reliability, Performance, Observability, Maintainability, Quality,
Testability, Compliance, Accessibility, Architecture, Documentation),
severities `error`, `warning` and `recommendation`, and source types `User`,
`Qodo Library`, `Code Patterns`, `Compliance Checklist` and `Repository File`.

## Not confirmed: the history tools

`pull-request-review-history`, `pull-request-similar-suggestions` and
`pull-request-stats` each failed twice with
`MT-UPSTREAM-DOWN: context-retriever unreachable`. The failure is on Qodo's
side of the call. Their input schemas are recorded, but **no successful reply
has been seen**, so the shape of an accepted or rejected suggestion is unknown.
The fixture `pull_request_review_history.json` holds the error reply. Anything
built on review history must be re-probed first.

## Tools the bridge does not use

The server offers more than the spec knew about. None of these is used, and
some conflict with the bridge's rules:

- `litegit-post-comment`, `litegit-post-inline-comment(s)`, `litegit-approve`,
  `litegit-set-labels` and `litegit-update-description` write to pull requests.
  The bridge never writes to GitHub repositories.
- `rules-create`, `rules-update`, `rules-set-state`, `rules-set-scope` and
  `rules-bulk` change rules. The first version only reads rules.
- `start-pre-pr-review` with the `*-managed-agent-*` tools starts a review.
  The first version does not trigger reviews.
- `review-skills-*`, `review-notes-*`, `cross-repo-*` and the read-only
  `litegit-*` tools are out of scope.

## Configuration: what Qodo's current reference says

Checked against Qodo's configuration reference, configuration-file page,
legacy-command sunset page and PR-review page on 2026-11-16. This is from the
published docs, not from a call.

- **`best_practices.md` is not mentioned on any of the four pages**, in any
  role. The repo file the current review reads for instructions is
  `REVIEW.md` (`[review_agent] enable_review_md`, on by default on SaaS).
- **`[pr_reviewer] extra_instructions` is not mentioned either.** The current
  keys for custom review instructions are `[review_agent]`
  `issues_user_guidelines`, `compliance_user_guidelines` and
  `smart_router_extra_instructions`.
- The pages do not say that the two older settings stopped working; they are
  simply absent. Whether Qodo still reads them is **unverified**.
- Triggers live under `[github_app]`: `pr_commands` (the review runs on PR
  open when it lists `/agentic_review`), `handle_push_trigger` (default
  false), `push_commands`, `ignore_bot_pr` (default true) and
  `feedback_on_draft_pr` (default false).

`qodo-cli config init` writes both older settings today.
