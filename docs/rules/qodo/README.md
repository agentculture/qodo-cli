# qodo bundle for culture-rules

Two culture-rules flows that use the qodo-cli bridge. Both ship **disabled**.

- **Findings.** After Qodo reviews a pull request, culture-rules asks the
  qodo-cli bridge for the PR's findings, once per head commit. The counts
  (open, closed, by status) become the run's outputs. This first version only
  collects them. It does not dismiss or implement anything.
- **Improvement.** After a pull request is merged, associate changes the repo's
  Qodo configuration in a dedicated checkout. The qodo-cli bridge then checks
  the change. If the change passes, culture-rules opens it as one pull request
  through its own GitHub App, from a fixed branch.

The bundle is configuration only. culture-rules has no Qodo-specific code; the
bundle uses only its generic parts: `http.call`, `machine.command`,
`github.pr_open`, `keyset.filter` and `keyset.add`, `json.parse`, `text.join`
and `retry_until`.

**It needs culture-rules 0.47.0 or later.** That is the first release with the
`github.pr_open` action. The bundle has no `release.json`, because a
`release.json` would tie it to exactly one culture-rules release.

## What is in it

| File | What it is |
|---|---|
| `actors/qodo-bridge.json` | `service` actor for `http.call` to qodo-cli serve on `127.0.0.1:8792`. It sends `Authorization: Bearer grant:QODO_BRIDGE_SERVICE_TOKEN` |
| `actors/associate-bridge.json` | `service` actor for `http.call` to associate's runs API. It sends `Authorization: Bearer grant:ASSOCIATE_BRIDGE_SERVICE_TOKEN` |
| `actors/qodo-checkout.json` | `runner` actor with three registered commands: `fetch`, `reset` and `pause` (`sleep 30`) |
| `rules/qodo-findings-on-review.json` | fires on `github.review.submitted`; disabled |
| `rules/qodo-findings-on-comment.json` | fires on `github.comment.created`; disabled |
| `rules/qodo-findings-on-review-comment.json` | fires on `github.review_comment.created`; disabled |
| `rules/qodo-improve-on-merge.json` | fires on `github.pr.closed`; disabled |
| `workflows/qodo-findings.json` | the findings flow |
| `workflows/qodo-improve.json` | the improvement flow |

A rule has one trigger type. The findings flow starts from three event types,
so it has three rules, one per type. All three run the same workflow.

The bundle ships no GitHub actor. Both flows use your existing GitHub App
actor, `github-app`; see `docs/actors/github.md` in culture-rules.

## The rules

All three findings rules have the same condition. Each condition is a JSON
predicate tree in the rule file:

- `data.author` is in `["qodo-code-review[bot]"]`. This is the login Qodo's bot
  posts under, written as a literal.
- `data.self_authored == false`. culture-rules' own App never starts a run.
- `data.head_repo == data.base_repo`. Fork PRs are excluded.
- `data.draft == false`.
- `data.repository` is in the allowed repos.

Two more settings control how often a findings run starts:

- The concurrency key is
  `qodo-findings:{trigger.data.repository}#{trigger.data.number}`.
- `max_attempts` is 1. So one Qodo review starts one run, even though it raises
  a review event, a comment event and several inline-comment events. The next
  human push (`github.pr.synchronize`) or green checks open the budget again.

The improvement rule fires on `github.pr.closed` when all of these hold:

- `data.merged == true`;
- `data.self_authored == false`;
- `data.head_repo == data.base_repo`;
- the repository is in the allowed list;
- `data.head_branch` exists and does **not** contain `qodo-config/`.

The last condition is the loop guard. The bundle opens its PR from the fixed
branch `qodo-config/update`, so merging that PR starts nothing. The guard does
not depend on Qodo's `ignore_bot_pr`. GitHub event data carries no labels, so
the guard is the branch name.

The improvement rule's concurrency key is
`qodo-improve:{trigger.data.repository}`, so only one improvement run per repo
runs at a time. A merge that arrives during a run is held. The newest held
merge runs once the current run ends. associate's own `409 busy` on a checkout
that is in use is a second fence.

## The workflows

`qodo-findings`:

1. **`keyset.filter`** on the key `<repo>#<number>@<head sha>`, in the set
   `qodo-findings-heads`, with a 90-day window.
2. **`http.call`** `POST /verbs/findings/list` with `{"pr_url": ..., "summary":
   true}`. The reply holds only counts, ids, statuses and URLs, under the 64 KiB
   `http.call` cap. This step runs only for a head commit not yet collected.
3. **`json.parse`** reads the counts.
4. **`keyset.add`** records the same key. This step comes last, so a run that
   fails records nothing, and the next Qodo event for that head tries again. A
   duplicate after a success is filtered out. The concurrency key keeps runs in
   order, so a filter and its add never race.

Outputs: `new_head` (1 or 0) and `findings`, the counts object.

`qodo-improve`:

1. **`machine.command` `fetch`, then `reset`.** These move the checkout's local
   `main` to `origin/main`. associate merges each run into the checkout's local
   `main` and never pushes, so the reset discards the previous run's merge.
   That change is already in a configuration PR.
2. **`http.call` `POST /runs`.** The body holds the checkout, the model
   (`cortex`), the test commands, `timeout: 900` and the instruction. The
   **instruction text lives only here**, in this step's configuration. It
   tells the agent to read the merged PR's accepted and rejected suggestions
   and to change only `.pr_agent.toml` and `best_practices.md`.
3. **`json.parse`** reads the run id.
4. **`retry_until` loop** (`wait_run`, at most 40 passes). Each pass runs `pause`
   (30 s), then `GET /runs/{id}`, then writes the run's status as one line.
   The loop stops when the status is no longer `running`. If the status is
   still `running` after 40 polls (about 20 minutes), the step fails
   `loop_max_exceeded`, with the last status line in the message.
5. **`run_ok` check.** This is a one-pass `retry_until`. It fails the run
   unless the status is `finished` and `ok` is true. A `lost` run, a
   `failed-to-start` run, failed tests and a refused merge all fail here, with
   associate's own words in the message.
6. **`http.call` `POST /verbs/config/propose`**, only when `merge` is `merged`.
   The body is `{repo, checkout, base}`, where `base` is the run's
   `base_commit`. When `merge` is `nothing_to_merge`, this step and everything
   after it are skipped, and the run ends `succeeded` with no PR.
7. **`json.parse`** reads the proposal.
8. **`refusal` check.** This one-pass `retry_until` fails the run when the
   bridge refuses. The message carries the refusal's `reason: detail`, and no
   PR is opened.
9. **`github.pr_open`** on `github-app`, with the files the bridge returned.
   The branch is `qodo-config/update`, the labels are `qodo-config` and
   `automated`, and the title is fixed. The body names the merged PR and the
   checked base. If a configuration PR is still open, the step completes with
   `skipped: branch_exists`. That is how the at-most-one-open-PR rule works,
   and the run still ends `succeeded`.

Outputs: `run` (the last run reply), `pr_number`, `pr_url` and `pr_skipped`.

## Before enabling

Set each of these values. The shipped values are placeholders, and the rules
are disabled.

1. **The repos.** Replace `example-owner/example-repo` in all four rules'
   `data.repository` lists. No real event comes from the placeholder, so
   nothing fires until you replace it.
2. **Qodo's bot login.** Check `qodo-code-review[bot]` in the three findings
   rules against the login Qodo posts under on your repos.
3. **The bridge base URLs.**
   - qodo-cli serve listens on `http://127.0.0.1:8792` by default. That URL is
     in `qodo-findings` (`list`) and `qodo-improve` (`propose`).
   - associate's runs API is written as `http://127.0.0.1:8794`. **8794 is a
     placeholder**: set the port associate's bridge listens on, in `start`
     and `poll`.
   - The actors run where `machine` says, `core` as shipped. `http.call` runs
     where its actor lives, so loopback works only on that machine.
4. **The `http.call` destination allowlist.** Each service actor allows
   exactly `127.0.0.1`. If a bridge listens elsewhere, change both the URL and
   `params.http.allow`.
5. **The grants that hold the two service tokens.**
   - Mint a qodo-cli service token with the **editor** role (`config propose`
     needs it; `findings list` needs viewer) and store it in grant as
     `QODO_BRIDGE_SERVICE_TOKEN`.
   - Mint an associate token with the **admin** role (`POST /runs` needs it)
     and store it as `ASSOCIATE_BRIDGE_SERVICE_TOKEN`.
   - Inject both into the engine node's unit:
     `--inject CULTURE_RULES_SECRET_<NAME>=<NAME>`.
6. **The GitHub App actor** (`github-app`). It must have:
   - `github.pr_open` in `params.actions`. Without it, the PR step fails
     `action_not_declared`.
   - `self_identity` set to the App's bot login. Without it, no event carries
     `self_authored`, and both flows' `self_authored == false` checks never
     match.
   - `github.review.submitted`, `github.comment.created`,
     `github.review_comment.created`, `github.pr.closed` and
     `github.pr.synchronize` in `params.events`. The last one lets a push reset
     the findings budget.
   - The repos in `connection.repos`, and Contents write and Pull requests
     write on them.
7. **The checkout path.** The improvement rule maps
   `checkout = /srv/qodo-checkouts/{{ trigger.data.repository }}`.
   - Replace the root `/srv/qodo-checkouts`. The path must be exactly
     `<root>/<owner>/<name>`, under a root that qodo-cli `config propose`
     allows (`QODO_PROPOSE_CHECKOUT_ROOTS`) and that associate's bridge
     allows.
   - Each checkout is a dedicated clone, used by nothing else, on the machine
     of `qodo-checkout` and of the two bridges.
   - Add each repo to `QODO_PROPOSE_REPOS`.
8. **The fetch and reset commands.** They name `origin` and `main` literally.
   They run with only `PATH` and `LANG` set, so the remote must be fetchable
   without `HOME`, for example over https for a public repo. Otherwise, prefix
   the `fetch` argv with `env`, `HOME=<the node user's home>`.
9. **The model.** `cortex` is in the `start` step. associate needs a route to
   it and to the decider; without the decider, associate makes no writes.
10. **The test commands.** `["qodo-cli config validate --json"]` is in the
    `start` step. associate runs each command in the checkout.
11. **Branch deletion.** In each repo, turn on GitHub's "Automatically delete
    head branches", or delete `qodo-config/update` by hand after its PR is
    merged or closed. While that branch exists, every improvement run ends
    `skipped: branch_exists`.

Then import the bundle, dry run first:

```bash
culture-rules actors import docs/rules/qodo            # dry run; add --apply
culture-rules workflows import docs/rules/qodo --apply
culture-rules rules import docs/rules/qodo --apply
```

During a culture-rules release rollout, `culture-rules rollout import --bundle
docs/rules/qodo --apply` imports the workflows and rules in one `POST
/import`. It does not import actors, and it refuses until that release's
`seed` step is recorded.

To enable, run `culture-rules rules enable <id> --apply` for each rule you
want. The four ids are `qodo-findings-on-review`, `qodo-findings-on-comment`,
`qodo-findings-on-review-comment` and `qodo-improve-on-merge`.

## Rollback

Disable the four rules: `culture-rules rules disable <id> --apply`. Nothing
else needs undoing.

- No new runs start once the rules are disabled. With the rules disabled, a PR
  open, a Qodo review and a merge start no run.
- The bridges hold no state that culture-rules depends on.
- A configuration PR that is already open stays open, for a person to close.
- The keyset `qodo-findings-heads` expires by itself. To forget it at once:
  `culture-rules keysets clear qodo-findings-heads --apply`.

## Known limits

- **The poll interval is a runner command.** A `retry_until` body cannot hold a
  `wait` step (culture-rules refuses one at run start). The loop has no delay
  of its own. So `pause` (`sleep 30` on `qodo-checkout`) spaces the polls, and
  it holds a runner process for those 30 seconds.
- **`base` is the run's `base_commit`, not the merge commit.** The
  `github.pr.closed` event carries no merge commit SHA, and after a squash
  merge the PR's `head_sha` is not an ancestor of `main`. associate records
  the checkout's HEAD when the run starts: `origin/main` after the reset, so
  the merged commit. `base_commit..HEAD` is exactly the agent's change.
- **A failed findings run waits for the next push or green checks.** The
  budget allows one admitted run per head. A run that fails, for example while
  the bridge is down, is retried by the next human push or green checks, not by
  further comments on the same head.
- **The two checks fail by message.** `run_ok` and `refusal` are one-pass
  `retry_until` loops whose `explain` field carries the reason. culture-rules
  has no step that fails a run with a configured message.
