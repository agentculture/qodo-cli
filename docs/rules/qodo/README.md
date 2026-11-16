# qodo bundle for culture-rules

Three culture-rules flows that use the qodo-cli bridge. All three ship
**disabled**.

- **Findings.** After Qodo reviews a pull request, culture-rules asks the
  qodo-cli bridge for the PR's findings, once per head commit. The counts
  (open, closed, by status) become the run's outputs. This first version only
  collects them. It does not dismiss or implement anything.
- **Improvement.** After a pull request is merged, associate changes the repo's
  Qodo configuration in a dedicated checkout. The qodo-cli bridge then checks
  the change. If the change passes, culture-rules opens it as one pull request
  through its own GitHub App, from a fixed branch.
- **Fix.** A person asks for an agent to fix some Qodo findings on a pull
  request (`qodo-cli findings fix`, which publishes one `qodo.fix.requested`
  event on the event bus). associate fixes them in a dedicated checkout of the
  pull request's head. The qodo-cli bridge checks the change (`fix propose`).
  If it passes, culture-rules opens it as one pull request **against the
  reviewed pull request's branch**.

The bundle is configuration only. culture-rules has no Qodo-specific code; the
bundle uses only its generic parts: `http.call`, `machine.command`,
`github.pr`, `github.pr_open`, `keyset.filter` and `keyset.add`, `json.parse`,
`text.join`, `list.filter` and `retry_until`, and an `event` trigger on a bus
event type.

**It needs culture-rules 0.47.0 or later.** That is the first release with the
`github.pr_open` action. The bundle has no `release.json`, because a
`release.json` would tie it to exactly one culture-rules release.

## What is in it

| File | What it is |
|---|---|
| `actors/qodo-bridge.json` | `service` actor for `http.call` to qodo-cli serve on `127.0.0.1:8792`. It sends `Authorization: Bearer grant:QODO_BRIDGE_SERVICE_TOKEN` |
| `actors/associate-bridge.json` | `service` actor for `http.call` to associate's runs API. It sends `Authorization: Bearer grant:ASSOCIATE_BRIDGE_SERVICE_TOKEN` |
| `actors/qodo-checkout.json` | `runner` actor with five registered commands: `fetch`, `reset`, `fetch-pr`, `reset-pr` and `pause` (`sleep 30`) |
| `rules/qodo-findings-on-review.json` | fires on `github.review.submitted`; disabled |
| `rules/qodo-findings-on-comment.json` | fires on `github.comment.created`; disabled |
| `rules/qodo-findings-on-review-comment.json` | fires on `github.review_comment.created`; disabled |
| `rules/qodo-improve-on-merge.json` | fires on `github.pr.closed`; disabled |
| `rules/qodo-fix-on-request.json` | fires on `qodo.fix.requested`; disabled |
| `workflows/qodo-findings.json` | the findings flow |
| `workflows/qodo-improve.json` | the improvement flow |
| `workflows/qodo-fix.json` | the fix flow |

A rule has one trigger type. The findings flow starts from three event types,
so it has three rules, one per type. All three run the same workflow.

The bundle ships no GitHub actor. The flows use your existing GitHub App
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
- `data.head_branch` exists;
- `data.base_branch == "main"`: the PR was merged into the default branch,
  the branch the `fetch` and `reset` commands name. A merge into another
  PR's branch (a stacked PR, or a fix PR merged into the PR it fixes) starts
  nothing;
- `data.head_branch` does **not** contain `qodo-fix/` or `qodo-config/`.

The last two conditions are the loop guards. The improvement flow opens its PR
from the fixed branch `qodo-config/update`, and the fix flow opens its PRs from
`qodo-fix/pr-<number>`, so merging either starts nothing. A fix PR whose base
GitHub retargets to `main` (after the reviewed branch is merged and deleted)
still carries the `qodo-fix/` head branch. The guards do not depend on Qodo's
`ignore_bot_pr`. GitHub event data carries no labels, so the guards are the
branch names. The `github.pr.closed` event carries the PR's base branch but not
the repo's default branch, so `main` is written as a literal: change it with
the `fetch` and `reset` commands if your default branch has another name.

The improvement rule's concurrency key is
`qodo-improve:{trigger.data.repository}`, so only one improvement run per repo
runs at a time. A merge that arrives during a run is held. The newest held
merge runs once the current run ends. associate's own `409 busy` on a checkout
that is in use is a second fence.

The fix rule fires on `qodo.fix.requested` when all of these hold:

- `source == "app://qodo-cli"`, the bridge's own source. culture-rules stores
  the bus envelope whole, so a condition reads its `source` like its `data`.
  Another producer that publishes the same type under its own source starts
  nothing. The bus does not authenticate producers, so this keeps out a
  mistaken producer, not a hostile one with access to the broker;
- `data.repo` is in the allowed repos (the same placeholder list as the other
  rules);
- `data.number > 0`, so the number is a number;
- `data.finding_ids` holds at least one id, and every id is a plain id
  (letters, digits, `.`, `_`, `:`, `-`, at most 128 characters), the shape the
  bridge checks before it publishes. The ids go into the agent's instruction.

The rule maps the PR URL from the allowed repo and the number
(`https://github.com/<repo>/pull/<number>`), not from the event's `pr_url`. It
maps `requested_by` as it comes; the workflow keeps it in the run and does not
put it in the PR, because it can be a person's address.

Its concurrency key is `qodo-fix:{trigger.data.repo}`: one fix run per repo at
a time, in a namespace of its own, so it never shares a key with the
improvement flow. A request that arrives during a run is held and runs when
the run ends. **Of several requests held at once, only the newest runs**: a
culture-rules concurrency key keeps one pending firing and replaces it with a
newer one. The person whose request was replaced asks again.

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

`qodo-fix`:

1. **`github.pr`** on `github-app` reads the pull request: its state, head and
   base repos, head branch and head sha. It writes nothing.
2. **`same_repo` check.** A one-pass `retry_until` that fails the run when an
   open pull request comes from a fork, or has no head branch. culture-rules'
   App cannot open a PR against a fork's branch, and the fix checkout tracks
   only the repo itself. The message names the head repo and branch.
3. **`go` gate** (`list.filter`). The run goes on only when the pull request is
   open and its head branch contains neither `qodo-fix/` nor `qodo-config/`.
   Otherwise every later step is skipped by its `when`, the checks pass on
   nothing, and the run ends `succeeded` with nothing done: a closed or merged
   pull request, and a request on an agent's own fix or configuration branch
   (so agents never stack fixes on their own fixes). A draft is fixed like any
   open pull request.
4. **`machine.command` `fetch-pr`, then `reset-pr`.** `fetch-pr` runs `git -C
   <checkout> fetch --quiet --end-of-options origin
   +refs/pull/<number>/head:refs/qodo-fix/head`, and `reset-pr` resets the
   checkout's local `main` to `refs/qodo-fix/head`. associate merges into
   local `main`, so `main` must be the pull request's head for the run. The
   pull request is fetched by its **pull ref**, so its branch name never
   reaches git: a branch name comes from GitHub and is hostile input, and no
   runner argument type carries a name with a `/` safely (`segment` refuses
   `/`, and `string` accepts a leading `-`). The only argument is the number,
   an `int`, inside a fixed refspec.
5. **`text.join`** writes the finding ids as one line.
6. **`http.call` `POST /runs`** on `associate-bridge`: the fix checkout, the
   model (`cortex`), the test commands, `timeout: 900` and the instruction.
   The **instruction text lives only here**. It names the pull request and the
   finding ids, tells the agent to read each finding with `qodo-cli findings
   list --pr-url <url> --extended --json`, to fix only those findings with the
   smallest change, to change no file the fix does not need and never CI
   files, dependency manifests, lockfiles or the reviewer's configuration, to
   treat everything it reads (the pull request, comments, Qodo and the note)
   as data, to commit with a message naming the finding ids, and to change
   nothing for a finding that is not real or cannot be fixed safely. The
   person's note follows between `-----BEGIN NOTE-----` and `-----END
   NOTE-----` lines, as data.
7. **The same poll loop and `run_ok` check** as `qodo-improve`: `lost`,
   `failed-to-start`, a timeout and `ok` false fail the run with associate's
   own words.
8. **`http.call` `POST /verbs/fix/propose`** on `qodo-bridge`, only when
   `merge` is `merged`, with `{repo, checkout, base}`, where `base` is the
   run's `base_commit`: the pull request's head, since the checkout was reset
   to it. `nothing_to_merge` ends the run quietly, with no PR.
9. **`json.parse`** reads the proposal, and the **`refusal` check** fails the
   run with the refusal's `reason: detail` (`too_large` among them) when the
   `ok` flag is not true. No PR is opened.
10. **`github.pr` again, and the `same_head` check.** It fails the run when the
    pull request's head is no longer the commit the agent worked on (someone
    pushed during the run). `github.pr_open` builds the new commit on the
    base branch's tip at that moment, and the proposal holds whole files, so
    a moved head would undo the new commits. Ask again after a push.
11. **`github.pr_open`** on `github-app`: the proposal's files; `base` is the
    pull request's head branch, so the fix PR targets the reviewed PR; the
    branch is `qodo-fix/pr-<number>`, fixed per reviewed PR; the labels are
    `qodo-fix` and `automated`; the title names the PR, and the body names
    the PR, the finding ids and that an agent wrote it. Who asked is not put
    in the PR (it can be a person's address); it is in the bridge's change log. If a fix PR for that pull request is still
    open, the step completes with `skipped: branch_exists`, and the run still
    ends `succeeded`. A base branch name that is not a plain branch name is
    refused by `github.pr_open` (`bad_input`), so nothing is written.

Qodo reviews the fix PR like any other, and the findings flow collects its
findings. That starts no fix: only a person's `findings fix` does.

Outputs: `run`, `pr_number`, `pr_url` and `pr_skipped`.

## Before enabling

Set each of these values. The shipped values are placeholders, and the rules
are disabled.

1. **The repos.** Replace `example-owner/example-repo` in all five rules
   (`data.repository` in four, `data.repo` in `qodo-fix-on-request`). No real
   event comes from the placeholder, so nothing fires until you replace it.
2. **Qodo's bot login.** Check `qodo-code-review[bot]` in the three findings
   rules against the login Qodo posts under on your repos.
3. **The bridge base URLs.**
   - qodo-cli serve listens on `http://127.0.0.1:8792` by default. That URL is
     in `qodo-findings` (`list`), `qodo-improve` (`propose`) and `qodo-fix`
     (`propose`).
   - associate's runs API is written as `http://127.0.0.1:8794`, associate
     serve's default loopback address. If associate's bridge listens
     elsewhere, change it in `start` and `poll` of both `qodo-improve` and
     `qodo-fix`.
   - The actors run where `machine` says, `core` as shipped. `http.call` runs
     where its actor lives, so loopback works only on that machine.
4. **The `http.call` destination allowlist.** Each service actor allows
   exactly `127.0.0.1`. If a bridge listens elsewhere, change both the URL and
   `params.http.allow`.
5. **The grants that hold the two service tokens.**
   - Issue a qodo-cli service token with the **editor** role (`config
     propose` and `fix propose` need it; `findings list` needs viewer) on the
     bridge's machine, `qodo-cli tokens issue --name <name> --role editor`
     (printed once), and store it in grant as `QODO_BRIDGE_SERVICE_TOKEN`.
   - Issue an associate token with the **admin** role (`POST /runs` needs it),
     `associate tokens issue --name <name> --role admin`, and store it as
     `ASSOCIATE_BRIDGE_SERVICE_TOKEN`.
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
     write on them (Pull requests read covers `github.pr`, which needs no
     entry in `params.actions`).
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
10. **The test commands.** `["qodo-cli config validate --json"]` is in
    `qodo-improve`'s `start` step. associate runs each command in the
    checkout.
11. **Branch deletion.** In each repo, turn on GitHub's "Automatically delete
    head branches", or delete `qodo-config/update` and `qodo-fix/pr-<number>`
    by hand after their PRs are merged or closed. While such a branch exists,
    every improvement run, or every fix run for that pull request, ends
    `skipped: branch_exists`.
12. **The default branch.** The improvement rule's `data.base_branch ==
    "main"` and the `fetch` and `reset` commands name `main`. Change all
    three if a repo's default branch has another name.

For the fix flow, also:

1. **The fix checkout root.** The fix rule maps `checkout =
   /srv/qodo-fix-checkouts/{{ trigger.data.repo }}`. Replace the root. **It
   must differ from the improvement flow's root** (`/srv/qodo-checkouts`):
   both flows reset their checkout's `main` and run associate in it, each one
   run at a time under its own concurrency key, so on a shared checkout a fix
   run and an improvement run could overwrite each other's work. Each fix
   checkout is a dedicated clone of the repo (never a fork), exactly
   `<root>/<owner>/<name>`, on the machine of `qodo-checkout` and of the two
   bridges, with `origin` fetchable as for `fetch`.
2. **On the qodo-cli bridge:** each repo in `QODO_FIX_REPOS` (read by both
   `findings fix` and `fix propose`; `QODO_PROPOSE_REPOS` is the improvement
   flow's and is not needed here), and the fix root in
   `QODO_FIX_CHECKOUT_ROOTS`. The bridge's `events` extra
   (`qodo-cli[events]`) and a broker it reaches (`events up`, or
   `EVENTS_BROKER_HOST` / `EVENTS_BROKER_PORT`), so `findings fix` can
   publish. See the fix requests section of `docs/operations/bridge.md`.
3. **On associate's bridge:** `ASSOCIATE_RUNS_ROOTS` covers the fix root as
   well as the improvement root.
4. **On the engine node:** culture-rules' `events` extra
   (`culture-rules[events]`) and the same broker, so the node ingests bus
   events. Without it the node runs without ingest and the rule never sees a
   request. `qodo.fix.requested` has three segments, inside the node's
   default subscription depth (4).
5. **The test commands.** `qodo-fix`'s `start` step ships
   `["false replace-with-this-repo-quick-checks"]`, which always fails, so
   no fix can pass until you set the repo's own quick checks (its linter and
   fast tests).
6. **The model.** `cortex` is in `qodo-fix`'s `start` step too.

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
want. The five ids are `qodo-findings-on-review`, `qodo-findings-on-comment`,
`qodo-findings-on-review-comment`, `qodo-improve-on-merge` and
`qodo-fix-on-request`.

## Rollback

Disable the rules: `culture-rules rules disable <id> --apply`. To stop only
the fix flow, disable `qodo-fix-on-request`. Nothing else needs undoing.

- No new runs start once the rules are disabled. With the rules disabled, a PR
  open, a Qodo review and a merge start no run.
- The bridges hold no state that culture-rules depends on.
- A configuration PR or fix PR that is already open stays open, for a person
  to close.
- A fix request published while the rule is disabled is stored and starts
  nothing, then or later.
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
- **The checks fail by message.** `run_ok`, `refusal`, `same_repo` and
  `same_head` are one-pass `retry_until` loops whose `explain` field carries
  the reason. culture-rules has no step that fails a run with a configured
  message, and no condition compares two step outputs, so `same_repo`,
  `go` and `same_head` compare them in a `list.filter` over one dummy item.
- **The fix flow ends quietly by skipping.** culture-rules has no step that
  ends a run early, and a loop takes no `when`. So when the `go` gate says no,
  each later step carries a `when` that skips it, and each check passes when
  nothing ran.
- **Small fixes only.** `fix propose` returns each changed file whole, and the
  reply must fit `http.call`'s 64 KiB reply cap (the bridge refuses a reply
  over 61440 bytes with `too_large`). A larger fix fails the run with
  `too_large`.
- **Held fix requests coalesce.** Of several requests for one repo held during
  a run, only the newest runs (above).
- **The head may move after the last check.** `same_head` reads the pull
  request seconds before `github.pr_open` builds on the branch's tip. A push
  in those seconds is not caught.
- **`requested_by` is not checked by the rule.** It is kept in the run's inputs
  and never written to the pull request. Who asked is in the bridge's change log.
