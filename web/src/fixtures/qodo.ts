/**
 * Fixture data for the screens' component tests, derived from the recorded Qodo replies in
 * `tests/fixtures/qodo_mcp/` (sanitized replies of one live probe; the only real evidence of what
 * Qodo returns). `fixtures.test.ts` checks every `RECORDED_*` copy below against its source file,
 * so a copy cannot drift. Anything not copied is marked HAND-WRITTEN with what it is derived from.
 */
import type {
  ConfigStatus,
  Finding,
  FindingsPage,
  RepoStatus,
  RuleDetail,
  RuleHit,
} from "../api/types";

/** Copy of `structuredContent` in tests/fixtures/qodo_mcp/get_pr_review_findings.json. */
export const RECORDED_FINDINGS = {
  finding_count: 2,
  findings: [
    {
      action_level: "remediation_recommended",
      attribution_status: "implemented",
      category: "Correctness",
      comment_id: "1000000001",
      created_at: "2026-01-01T00:00:00.000000Z",
      description: "sample description",
      finding_source: "IssueFinding",
      git_sha: "0000000000000000000000000000000000000000",
      id: "00000000-0000-0000-0000-000000000001",
      inline_comment_id: null,
      review_run_id: 1,
      review_source: null,
      title: "sample title",
    },
    {
      action_level: "remediation_recommended",
      attribution_status: "implemented",
      category: "Maintainability",
      comment_id: "1000000001",
      created_at: "2026-01-01T00:00:00.000000Z",
      description: "sample description",
      finding_source: "IssueFinding",
      git_sha: "0000000000000000000000000000000000000000",
      id: "00000000-0000-0000-0000-000000000001",
      inline_comment_id: null,
      review_run_id: 1,
      review_source: null,
      title: "sample title",
    },
  ],
  git_provider: null,
  pr_url: "https://github.com/example-org/example-repo/pull/1",
  review_session: {
    commit_sha: "0000000000000000000000000000000000000000",
    git_provider: "github",
    review_complete: null,
    run_id: 1,
    started_at: "2026-01-01T00:00:00.000000Z",
    status: "completed",
    summary_comment_id: "1000000001",
  },
};

/** Copy of `structuredContent` in tests/fixtures/qodo_mcp/rules_search.json. */
export const RECORDED_RULES_SEARCH = {
  rules: [
    { content: "sample content", id: 1, name: "sample name", severity: "warning" },
    { content: "sample content", id: 1, name: "sample name", severity: "warning" },
  ],
};

/** Copy of `structuredContent` in tests/fixtures/qodo_mcp/rules_get.json. */
export const RECORDED_RULE = {
  badExamples: "sample badExamples",
  category: "Maintainability",
  content: "sample content",
  createdAt: "2026-01-01T00:00:00.000000Z",
  enforcementOverride: "auto",
  goodExamples: "sample goodExamples",
  historyBandPrediction: null,
  insights: null,
  lastMutedAt: null,
  name: "sample name",
  ruleId: 1,
  scopeElements: [
    { kind: "sample kind", org: null, path: null, repo: { fullName: "example/repo", repoId: null } },
  ],
  scopes: ["sample scopes"],
  severity: "warning",
  similaritiesCount: null,
  source: "sample source",
  sourceType: "Repository File",
  sourceUri: "sample sourceUri",
  sourceUris: [],
  state: "active",
  suggestionType: null,
  updatedAt: "2026-01-01T00:00:00.000000Z",
  url: null,
};

export const PR_URL = RECORDED_FINDINGS.pr_url;
export const REPO = "example-org/example-repo";
export const OTHER_REPO = "example-org/other-repo";

/**
 * DERIVED: the recorded findings as the bridge's page form (`findings list --offset --limit`,
 * qodo/registry.py `page_findings`), with two changes so the screen has something to act on:
 * the ids are made distinct (the recorded two share one id), and the second finding's status is
 * `pending` (an open status per the tool's description; the recording holds only `implemented`).
 * Its title and description are hand-written. Counts are recomputed for these findings.
 */
const [recordedA, recordedB] = RECORDED_FINDINGS.findings;
export const OPEN_FINDING: Finding = {
  ...recordedB,
  id: "00000000-0000-0000-0000-000000000002",
  attribution_status: "pending",
  title: "A retried step can use up a code it never checked",
  description: "The compare-and-set runs before the repo and sha are compared.",
};
export const CLOSED_FINDING: Finding = { ...recordedA };

export function findingsPage(findings: Finding[] = [OPEN_FINDING, CLOSED_FINDING]): FindingsPage {
  const byStatus: Record<string, number> = {};
  for (const f of findings) {
    const s = f.attribution_status ?? "unknown";
    byStatus[s] = (byStatus[s] ?? 0) + 1;
  }
  const open = findings.filter((f) => f.attribution_status === "pending").length;
  return {
    pr_url: PR_URL,
    finding_count: findings.length,
    counts: {
      total: findings.length,
      open,
      closed: 0,
      other: findings.length - open,
      by_status: byStatus,
    },
    review_session: {
      commit_sha: RECORDED_FINDINGS.review_session.commit_sha,
      run_id: RECORDED_FINDINGS.review_session.run_id,
      started_at: RECORDED_FINDINGS.review_session.started_at,
      status: RECORDED_FINDINGS.review_session.status,
    },
    offset: 0,
    limit: 50,
    next_offset: null,
    findings,
  };
}

/** DERIVED: Qodo's reply for a PR it has not reviewed (`review_session=null`, per its schema). */
export const NOT_REVIEWED: FindingsPage = {
  ...findingsPage([]),
  review_session: null,
};

/**
 * HAND-WRITTEN from the `litegit-list-prs` input schema only: no reply of that tool was ever
 * recorded, so this shape (a `pull_requests` list of `{number, title, html_url}`) is a guess the
 * UI reads defensively (`parsePrs`), and is UNVERIFIED.
 */
export const HANDWRITTEN_PRS = {
  pull_requests: [
    { number: 1, title: "Button codes are used once", html_url: PR_URL },
    {
      number: 2,
      title: "Keyset list paging",
      html_url: "https://github.com/example-org/example-repo/pull/2",
    },
  ],
};

/** Rules search hits: the recorded reply (`RECORDED_RULES_SEARCH`), as the UI types them. */
export const RULE_HITS: RuleHit[] = RECORDED_RULES_SEARCH.rules;
export const RULE_DETAIL: RuleDetail = RECORDED_RULE;

/**
 * HAND-WRITTEN in the bridge's own `config status` shape (qodo/config_status.py; the bridge
 * produces it, Qodo does not), with values taken from the tests of that verb.
 */
export const REPO_STATUS: RepoStatus = {
  repo: REPO,
  checkout: "found",
  files: [
    {
      path: ".pr_agent.toml",
      kind: "toml",
      present: true,
      bytes: 61,
      valid_toml: true,
      sections: ["pr_reviewer", "review_agent"],
    },
    { path: "REVIEW.md", kind: "markdown", present: false },
    { path: "best_practices.md", kind: "markdown", present: true, bytes: 5 },
  ],
  validation: {
    valid: true,
    checks: [
      {
        id: "config_present",
        ok: true,
        severity: "error",
        message: "a Qodo reviewer config is present",
        remediation: "",
      },
      {
        id: "pr_reviewer_section",
        ok: true,
        severity: "warning",
        message: "[pr_reviewer] section present",
        remediation: "",
      },
    ],
  },
  warnings: [
    {
      code: "legacy_setting",
      path: ".pr_agent.toml",
      detail:
        "[pr_reviewer.extra_instructions] is not in Qodo's current configuration reference " +
        "(whether Qodo still reads it is unverified)",
    },
  ],
  proposals: [{ time: "2026-11-16T09:30:00Z", files: [".pr_agent.toml"], actor: "api" }],
};

export const MISSING_REPO: RepoStatus = {
  repo: OTHER_REPO,
  checkout: "missing",
  files: [],
  validation: null,
  warnings: [],
  proposals: [],
};

export const CONFIG_STATUS: ConfigStatus = {
  configured: true,
  truncated: false,
  repos: [REPO_STATUS, MISSING_REPO],
};

export const NOT_CONFIGURED: ConfigStatus = { configured: false, truncated: false, repos: [] };

/**
 * DERIVED: a second open finding (the open one with another id and a hand-written title), so a
 * test can select more than one finding for a fix request.
 */
export const SECOND_OPEN_FINDING: Finding = {
  ...OPEN_FINDING,
  id: "00000000-0000-0000-0000-000000000003",
  title: "The cursor moves before the saves land",
  description: "A failed save leaves the cursor past the messages it never stored.",
};

/** DERIVED: `n` distinct open findings (ids `...-1000` on), for the 20-finding limit. */
export function manyOpenFindings(n: number): Finding[] {
  return Array.from({ length: n }, (_, i) => ({
    ...OPEN_FINDING,
    id: `00000000-0000-0000-0000-${String(1000 + i).padStart(12, "0")}`,
    title: `Open finding ${i + 1}`,
  }));
}

/**
 * DERIVED from qodo/changelog.py `record_fix_request`: one `fix.requested` line as `GET /changes`
 * returns it (`time` in the log's `%Y-%m-%dT%H:%M:%SZ` form, `actor` as the server records it
 * over HTTP, `api:token:<name>`). Never the note.
 */
export function fixRequestedEntry(
  findingIds: string[],
  { prUrl = PR_URL, ago = 0, actor = "api:token:editor", eventId = "evt_01TEST" } = {},
) {
  const time = new Date(Date.now() - ago * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
  return { time, kind: "fix.requested", actor, pr_url: prUrl, finding_ids: findingIds, event_id: eventId };
}
