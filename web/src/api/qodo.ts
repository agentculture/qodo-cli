import { ApiError, ROUTES, getJson, postVerb, query } from "./client";
import type {
  ConfigStatus,
  FindingsPage,
  PullRequest,
  RuleDetail,
  RuleHit,
} from "./types";

/**
 * The verbs the three screens call, as `[noun, name]`. `contract.test.ts` checks each against
 * `api/openapi.json` (the route exists, it is a POST, and every param the UI sends is in its
 * request schema), so the UI cannot name a verb the server does not serve.
 */
export const VERBS = {
  findingsList: ["findings", "list"],
  findingsImplement: ["findings", "implement"],
  findingsDismiss: ["findings", "dismiss"],
  findingsFix: ["findings", "fix"],
  prsList: ["prs", "list"],
  configStatus: ["config", "status"],
  rulesSearch: ["rules", "search"],
  rulesShow: ["rules", "show"],
} as const satisfies Record<string, readonly [string, string]>;

/** The params each verb is sent with, by name (the contract test reads this too). */
export const VERB_PARAMS: Record<keyof typeof VERBS, readonly string[]> = {
  findingsList: ["pr_url", "offset", "limit"],
  findingsImplement: ["finding_ids"],
  findingsDismiss: ["finding_ids", "reason"],
  findingsFix: ["pr_url", "finding_ids", "note"],
  prsList: ["repo", "state", "max_results"],
  configStatus: [],
  rulesSearch: ["query", "scopes"],
  rulesShow: ["rule_id"],
};

/** A page of findings (the server keeps each page under 64 KiB and may return fewer). */
export const FINDINGS_PAGE = 50;
/** At most this many pages are read for one PR (2,500 findings); more is reported, not read. */
export const MAX_FINDINGS_PAGES = 50;
/** Open pull requests asked for per repo. */
export const PRS_PER_REPO = 50;

/** The four dismiss reasons `pr-review-dismiss` takes, with its own words for each. */
export const DISMISS_REASONS = [
  { value: "false_positive", label: "False positive", help: "The finding is wrong." },
  { value: "intentional", label: "Intentional", help: "The code is deliberate and correct as written." },
  { value: "deferred", label: "Deferred", help: "Real, but out of scope for this pull request." },
  { value: "rejected", label: "Rejected", help: "Understood and declined." },
] as const;
export type DismissReason = (typeof DISMISS_REASONS)[number]["value"];

/** `findings fix` takes 1 to this many finding ids in one request (its params schema). */
export const MAX_FIX_FINDINGS = 20;
/** The longest note `findings fix` takes, in characters (its params schema). */
export const MAX_FIX_NOTE = 500;
/** The change-log entries read to find a PR's fix requests (`GET /changes`'s own maximum). */
export const CHANGES_READ = 500;

/** Qodo's open statuses (the `get-pr-review-findings` description). Only these can change. */
export const OPEN_STATUSES = new Set([
  "pending",
  "partial_implementation",
  "not_implemented",
  "focus_areas_edited",
]);

/** A 2xx answer that is not the shape a screen reads: said so, never read as empty. */
function badReply(what: string): ApiError {
  return new ApiError(200, "bad_reply", `The API's ${what} answer is not in the shape this screen reads.`);
}

const isObject = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

export interface AllFindings extends FindingsPage {
  /** True when more than MAX_FINDINGS_PAGES pages exist and the rest were not read. */
  incomplete: boolean;
}

/** Every finding on one PR, a page at a time (`findings list --offset --limit`). */
export async function listFindings(prUrl: string, signal?: AbortSignal): Promise<AllFindings> {
  const [noun, name] = VERBS.findingsList;
  let offset = 0;
  let first: FindingsPage | null = null;
  const findings: FindingsPage["findings"] = [];
  for (let page = 0; page < MAX_FINDINGS_PAGES; page += 1) {
    const reply = await postVerb<FindingsPage>(
      noun,
      name,
      { pr_url: prUrl, offset, limit: FINDINGS_PAGE },
      signal,
    );
    if (!isObject(reply) || !Array.isArray(reply.findings) || !isObject(reply.counts)) {
      throw badReply("findings list");
    }
    first ??= reply;
    findings.push(...reply.findings.filter(isObject).filter((f) => typeof f.id === "string"));
    if (typeof reply?.next_offset !== "number" || reply.next_offset <= offset) {
      return { ...first, findings, next_offset: null, incomplete: false };
    }
    offset = reply.next_offset;
  }
  return { ...(first as FindingsPage), findings, next_offset: offset, incomplete: true };
}

/** A write: mark findings implemented. The reply's shape was never recorded; it is not read. */
export async function implementFindings(ids: string[], signal?: AbortSignal): Promise<void> {
  const [noun, name] = VERBS.findingsImplement;
  await postVerb<unknown>(noun, name, { finding_ids: ids }, signal);
}

/** A write: dismiss findings with one of the four reasons. The reply is not read. */
export async function dismissFindings(
  ids: string[],
  reason: DismissReason,
  signal?: AbortSignal,
): Promise<void> {
  const [noun, name] = VERBS.findingsDismiss;
  await postVerb<unknown>(noun, name, { finding_ids: ids, reason }, signal);
}

/** What `findings fix` returns: the published event's id and what it asked for. */
export interface FixRequested {
  event_id: string;
  type: string;
  pr_url: string;
  repo: string;
  number: number;
  finding_ids: string[];
}

/**
 * A write (an event on the mesh's bus, not a change in Qodo): ask for an agent to fix findings on
 * a github.com pull request. The body is exactly `{pr_url, finding_ids}` plus `note` when the note
 * holds more than whitespace.
 */
export async function requestFix(
  prUrl: string,
  ids: string[],
  note?: string,
  signal?: AbortSignal,
): Promise<FixRequested> {
  const [noun, name] = VERBS.findingsFix;
  const params: Record<string, unknown> = { pr_url: prUrl, finding_ids: ids };
  if (note && note.trim()) params.note = note;
  return postVerb<FixRequested>(noun, name, params, signal);
}

/**
 * The bridge's refusal of a repeat (`qodo/fix_request.py`: the same PR and the same set of ids
 * within the window). It has no code of its own (a 400 `user_error`), so it is told by its
 * message; it is not a failure for the person: the fix was already asked for.
 */
export const isAlreadyRequested = (error: ApiError): boolean =>
  error.status === 400 && /already requested/i.test(error.message);

/** One `fix.requested` line of the change log (`qodo/changelog.py` `record_fix_request`). */
export interface FixRequest {
  time: string;
  actor: string | null;
  pr_url: string;
  finding_ids: string[];
  event_id: string | null;
}

/** Two PR URLs name the same pull request (same host, repo and number; case-insensitive). */
export function samePr(a: string, b: string): boolean {
  const host = (url: string) => {
    try {
      return new URL(url).hostname.toLowerCase();
    } catch {
      return null;
    }
  };
  const x = describePrUrl(a);
  const y = describePrUrl(b);
  return (
    host(a) !== null &&
    host(a) === host(b) &&
    x.repo !== null &&
    x.repo.toLowerCase() === y.repo?.toLowerCase() &&
    x.number !== null &&
    x.number === y.number
  );
}

/**
 * The fix requests the bridge logged for one pull request, newest first: the newest
 * `CHANGES_READ` change-log entries (`GET /changes` has no filter), kept when they are
 * `fix.requested` lines for this PR. Bounded: an older request is not shown.
 */
export async function fixRequests(prUrl: string, signal?: AbortSignal): Promise<FixRequest[]> {
  const reply = await getJson<unknown>(`${ROUTES.changes.path}${query({ limit: CHANGES_READ })}`, signal);
  if (!isObject(reply) || !Array.isArray(reply.entries)) throw badReply("changes");
  const out: FixRequest[] = [];
  for (const e of reply.entries) {
    if (!isObject(e) || e.kind !== "fix.requested") continue;
    if (typeof e.pr_url !== "string" || !samePr(e.pr_url, prUrl)) continue;
    if (typeof e.time !== "string" || !Array.isArray(e.finding_ids)) continue;
    out.push({
      time: e.time,
      actor: typeof e.actor === "string" ? e.actor : null,
      pr_url: e.pr_url,
      finding_ids: e.finding_ids.filter((id): id is string => typeof id === "string"),
      event_id: typeof e.event_id === "string" ? e.event_id : null,
    });
  }
  return out.reverse(); // the log is oldest first
}

/** Each allowlisted repo's configuration files, validation and proposals. */
export async function configStatus(signal?: AbortSignal): Promise<ConfigStatus> {
  const [noun, name] = VERBS.configStatus;
  const reply = await postVerb<unknown>(noun, name, {}, signal);
  if (!isObject(reply) || typeof reply.configured !== "boolean" || !Array.isArray(reply.repos)) {
    throw badReply("config status");
  }
  const repos = reply.repos.filter(
    (r): r is ConfigStatus["repos"][number] =>
      isObject(r) && typeof r.repo === "string" && Array.isArray(r.files) && Array.isArray(r.warnings) && Array.isArray(r.proposals),
  );
  return { configured: reply.configured, truncated: reply.truncated === true, repos };
}

/** `/owner/repo/`: the scope path `rules-search` takes for a repo. */
export const repoScope = (repo: string): string => `/${repo}/`;

/** Rules by semantic search, optionally narrowed to one repo's scope. */
export async function searchRules(
  text: string,
  repo: string | null,
  signal?: AbortSignal,
): Promise<RuleHit[]> {
  const [noun, name] = VERBS.rulesSearch;
  const params: Record<string, unknown> = { query: text };
  if (repo) params.scopes = [repoScope(repo)];
  const reply = await postVerb<unknown>(noun, name, params, signal);
  if (!isObject(reply) || !Array.isArray(reply.rules)) throw badReply("rules search");
  return reply.rules.filter(isObject) as RuleHit[];
}

/** One rule by id (`rules show`, the upstream `rules-get`). */
export async function showRule(ruleId: number, signal?: AbortSignal): Promise<RuleDetail> {
  const [noun, name] = VERBS.rulesShow;
  const reply = await postVerb<unknown>(noun, name, { rule_id: ruleId }, signal);
  if (!isObject(reply)) throw badReply("rules show");
  return reply as RuleDetail;
}

export interface PrList {
  prs: PullRequest[];
  /** Items in the reply that held neither a number nor a URL. */
  unreadable: number;
  /** False when the reply held no list this UI recognizes. */
  recognized: boolean;
}

const LIST_KEYS = ["pull_requests", "pullRequests", "prs", "items", "results", "data"];

const text = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v : null);
const httpUrl = (v: unknown): string | null => {
  const s = text(v);
  if (!s) return null;
  try {
    const u = new URL(s);
    return u.protocol === "https:" || u.protocol === "http:" ? s : null;
  } catch {
    return null;
  }
};
const whole = (v: unknown): number | null =>
  typeof v === "number" && Number.isInteger(v) && v > 0 ? v : null;

/**
 * Pull requests out of a `litegit-list-prs` reply. That reply was never recorded, so it is read
 * defensively: a list (the reply itself, or under one of a few likely keys) of objects with a
 * number (`number`, `pr_number`, `iid`), a title and a web URL (`html_url`, `web_url`, `url`).
 * Nothing is invented: a missing field stays null.
 */
export function parsePrs(reply: unknown): PrList {
  let items: unknown[] | null = Array.isArray(reply) ? reply : null;
  if (!items && reply && typeof reply === "object") {
    const obj = reply as Record<string, unknown>;
    const key = LIST_KEYS.find((k) => Array.isArray(obj[k]));
    items = key ? (obj[key] as unknown[]) : null;
  }
  if (!items) return { prs: [], unreadable: 0, recognized: false };
  const prs: PullRequest[] = [];
  let unreadable = 0;
  for (const item of items) {
    if (!item || typeof item !== "object") {
      unreadable += 1;
      continue;
    }
    const o = item as Record<string, unknown>;
    const pr: PullRequest = {
      number: whole(o.number) ?? whole(o.pr_number) ?? whole(o.iid),
      title: text(o.title),
      url: httpUrl(o.html_url) ?? httpUrl(o.web_url) ?? httpUrl(o.url),
    };
    if (pr.number === null && pr.url === null) unreadable += 1;
    else prs.push(pr);
  }
  return { prs, unreadable, recognized: true };
}

/** A repo's open pull requests, through Qodo's read-only `litegit-list-prs` (`prs list`). */
export async function listOpenPrs(repo: string, signal?: AbortSignal): Promise<PrList> {
  const [noun, name] = VERBS.prsList;
  const reply = await postVerb<unknown>(
    noun,
    name,
    { repo, state: "open", max_results: PRS_PER_REPO },
    signal,
  );
  return parsePrs(reply);
}

/** `owner/repo` and the number from a PR URL's path (`/owner/repo/pull/N`, `/-/merge_requests/N`). */
export function describePrUrl(url: string): { repo: string | null; number: number | null } {
  try {
    const parts = new URL(url).pathname.split("/").filter(Boolean);
    const at = parts.findIndex((p) => p === "pull" || p === "pulls" || p === "merge_requests");
    const n = at >= 0 ? Number(parts[at + 1]) : NaN;
    const repoParts = parts.slice(0, at >= 0 ? at : parts.length).filter((p) => p !== "-");
    return {
      repo: repoParts.length >= 2 ? repoParts.join("/") : null,
      number: Number.isInteger(n) && n > 0 ? n : null,
    };
  } catch {
    return { repo: null, number: null };
  }
}

