/**
 * Types for the qodo-cli HTTP API (`qodo-cli serve`), checked against the committed
 * `api/openapi.json` by `api/contract.test.ts`.
 *
 * Shape cited from culture-rules web/src/api/types.ts @ b7f861f690b57c4355f81371cc02ad1dd2ae9cac (cite-don't-import: this repo
 * owns its copy). Like there the types are hand-maintained and only the fields this UI reads
 * are typed; the contract test fails when a route, a method or the error schema moves.
 * Adapted: qodo's error envelope also carries an optional `remediation`; `GET /health` is
 * qodo's (the OpenAPI document leaves its body untyped, so `qodo/server/app.py` is the source).
 */

/** The error envelope every failure carries (the `Error` schema). */
export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    errors: unknown[];
    remediation?: string;
  };
}

/** `key.state` of `GET /health` (`qodo/health.py` `key_status`). */
export type KeyState = "missing" | "unknown" | "invalid" | "expired" | "expiring" | "ok";

/** `GET /health`: public; never carries the key itself. */
export interface Health {
  status: "ok" | "degraded";
  service: string;
  version: string;
  key: {
    present: boolean;
    state: KeyState;
    /** Whole UTC calendar days (negative once expired); null when not known. */
    days_left: number | null;
  };
}

// --- the three screens ------------------------------------------------------------------------
// Fields below come from the recorded Qodo replies in tests/fixtures/qodo_mcp/ (findings, rules)
// and from qodo/config_status.py and qodo/registry.py (the bridge's own shapes). Everything is
// optional where Qodo may leave it out: the UI never assumes a field it was not given.

/** One finding (`get-pr-review-findings`, compact form; fixture get_pr_review_findings.json). */
export interface Finding {
  id: string;
  title?: string | null;
  description?: string | null;
  category?: string | null;
  /** Severity, not open-vs-closed (the tool's description says so). */
  action_level?: string | null;
  attribution_status?: string | null;
  created_at?: string | null;
  git_sha?: string | null;
  /** Fields the bridge shortened to keep a page under 64 KiB (`findings list --limit`). */
  clipped?: string[];
  /** Large nested fields the bridge left out of the page. */
  omitted?: string[];
}

/** `review_session` of the findings reply; `null` when Qodo has not reviewed the PR. */
export interface ReviewSession {
  status?: string | null;
  commit_sha?: string | null;
  run_id?: number | null;
  started_at?: string | null;
}

export interface FindingCounts {
  total: number;
  open: number;
  closed: number;
  other: number;
  by_status: Record<string, number>;
}

/** `findings list --offset --limit` (qodo/registry.py `page_findings`). */
export interface FindingsPage {
  pr_url: string | null;
  finding_count: number;
  counts: FindingCounts;
  review_session: ReviewSession | null;
  offset: number;
  limit: number;
  next_offset: number | null;
  findings: Finding[];
}

/** One allowlisted file of a repo (`config status`; qodo/config_status.py). Never contents. */
export interface ConfigFile {
  path: string;
  kind: string;
  present: boolean;
  bytes?: number;
  valid_toml?: boolean;
  sections?: string[];
  error?: string;
  problem?: { code: string; detail: string };
}

/** One check of `config validate` (qodo/api.py `_validation_checks`). */
export interface ValidationCheck {
  id: string;
  ok: boolean;
  severity: string;
  message: string;
  remediation: string;
}

export interface ConfigWarning {
  code: string;
  path: string;
  detail: string;
}

/** A `config.proposed` change-log entry: when, which files, through which surface. */
export interface Proposal {
  time: string;
  files: string[];
  actor: string;
}

export interface RepoStatus {
  repo: string;
  checkout: "found" | "missing";
  files: ConfigFile[];
  validation: { valid: boolean; checks: ValidationCheck[] } | null;
  warnings: ConfigWarning[];
  proposals: Proposal[];
}

/** `config status`: every allowlisted repo (at most 20; `truncated` when there are more). */
export interface ConfigStatus {
  configured: boolean;
  truncated: boolean;
  repos: RepoStatus[];
}

/** One rule of `rules-search` (fixture rules_search.json): id, name, severity, content. */
export interface RuleHit {
  id?: number;
  name?: string | null;
  severity?: string | null;
  content?: string | null;
}

/** `rules-get` (fixture rules_get.json); only the fields this UI shows are typed. */
export interface RuleDetail {
  ruleId?: number;
  name?: string | null;
  category?: string | null;
  severity?: string | null;
  state?: string | null;
  content?: string | null;
  sourceType?: string | null;
  source?: string | null;
  scopes?: string[] | null;
  goodExamples?: string | null;
  badExamples?: string | null;
}

/** One pull request read defensively from `litegit-list-prs` (its reply was never recorded). */
export interface PullRequest {
  number: number | null;
  title: string | null;
  url: string | null;
}
