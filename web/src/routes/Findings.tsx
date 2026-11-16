import { type FormEvent, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import type { ApiError } from "../api/client";
import {
  DISMISS_REASONS,
  type DismissReason,
  type FixRequest,
  MAX_FIX_FINDINGS,
  MAX_FIX_NOTE,
  OPEN_STATUSES,
  type PrList,
  configStatus,
  describePrUrl,
  dismissFindings,
  fixRequests,
  implementFindings,
  isAlreadyRequested,
  listFindings,
  listOpenPrs,
  requestFix,
} from "../api/qodo";
import type { Finding, PullRequest } from "../api/types";
import Dialog from "../components/Dialog";
import KeyNotice from "../components/KeyNotice";
import Problem from "../components/Problem";
import { asApiError, useLoad } from "../hooks/useLoad";
import { useTitle } from "../hooks/useTitle";
import { formatTime, humanize, plural, shortSha, statusWord, whenAsked, whoAsked } from "./words";

/**
 * Findings (canvas artboard "Main"): the allowlisted repos' open pull requests on the left, one
 * pull request's findings on the right, with Mark implemented and Dismiss behind a confirmation,
 * and Send an agent to fix (one finding, or several selected) behind its own.
 *
 * Data: the repos are the bridge's allowlist (`config status`); each repo's open pull requests
 * come from Qodo's read-only `litegit-list-prs` (`prs list`, reply never recorded: read
 * defensively); a PR's findings from `findings list` a page at a time; the writes are
 * `findings implement` / `findings dismiss` (editor). The selected PR is the `pr` query param, so
 * a pasted URL or a link opens the same view.
 *
 * A fix request is `findings fix` (editor): one event on the mesh's bus asking for an agent to fix
 * the findings on the PR's branch; it changes nothing in Qodo, so the finding stays open. The
 * "Fix requested" marker on a finding comes from the bridge's change log (`GET /changes`, the
 * newest `CHANGES_READ` entries, filtered to this PR), never from the click alone. Only a
 * github.com PR offers it: the bridge refuses any other.
 */

const isHttpUrl = (value: string) => /^https?:\/\/\S+$/i.test(value.trim());
const findingsPath = (url: string) => `/findings?pr=${encodeURIComponent(url)}`;

interface Known {
  repo: string;
  pr: PullRequest;
}

export default function Findings() {
  useTitle("Findings");
  const [params, setParams] = useSearchParams();
  const selected = params.get("pr");
  const [filter, setFilter] = useState("");
  const [known, setKnown] = useState<Record<string, Known>>({});
  const repos = useLoad(configStatus, "repos");

  const remember = useCallback((repo: string, list: PrList) => {
    setKnown((prev) => {
      const next = { ...prev };
      for (const pr of list.prs) if (pr.url) next[pr.url] = { repo, pr };
      return next;
    });
  }, []);

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (isHttpUrl(filter)) {
      setParams({ pr: filter.trim() });
      setFilter("");
    }
  }

  const query = isHttpUrl(filter) ? "" : filter.trim().toLowerCase();

  return (
    <div className="findings">
      <aside className="findings__aside" aria-label="Pull requests">
        <form className="pr-search" onSubmit={onSubmit} role="search">
          <label className="field">
            <span className="field__label">Find a pull request</span>
            <input
              type="search"
              className="field__input"
              placeholder="Repo, number, title or a PR URL"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              aria-describedby="pr-search-hint"
            />
          </label>
          <p id="pr-search-hint" className="field__hint">
            Filter the list, or paste a pull request's URL and press Enter.
          </p>
        </form>
        {repos.state.status === "loading" ? (
          <p className="muted" role="status">
            Loading the repos…
          </p>
        ) : repos.state.status === "error" ? (
          <Problem error={repos.state.error} onRetry={repos.reload} />
        ) : !repos.state.data.configured ? (
          <div className="empty">
            <p>No repos are configured on this bridge, so no pull requests are listed.</p>
            <p>
              Set <code>QODO_PROPOSE_REPOS</code> where <code>qodo-cli serve</code> runs, or paste
              a pull request URL above.
            </p>
          </div>
        ) : (
          repos.state.data.repos.map(({ repo }) => (
            <RepoPrs
              key={repo}
              repo={repo}
              query={query}
              selected={selected}
              onLoaded={remember}
            />
          ))
        )}
      </aside>
      <div className="findings__main">
        <KeyNotice />
        {selected ? (
          <PrFindings key={selected} url={selected} known={known[selected]} />
        ) : (
          <section className="panel">
            <h1 className="page__title">Findings</h1>
            <p className="page__lede">
              Choose a pull request, or paste its URL, to see Qodo's findings on it.
            </p>
          </section>
        )}
      </div>
    </div>
  );
}

// --- the list ---------------------------------------------------------------------------------

function RepoPrs({
  repo,
  query,
  selected,
  onLoaded,
}: {
  repo: string;
  query: string;
  selected: string | null;
  onLoaded: (repo: string, list: PrList) => void;
}) {
  const headingId = useId();
  const load = useCallback(
    async (signal: AbortSignal) => {
      const list = await listOpenPrs(repo, signal);
      onLoaded(repo, list);
      return list;
    },
    [repo, onLoaded],
  );
  const { state, reload } = useLoad(load, repo);
  const shown =
    state.status === "ok"
      ? state.data.prs.filter((pr) =>
          `${repo} #${pr.number ?? ""} ${pr.title ?? ""}`.toLowerCase().includes(query),
        )
      : [];
  if (query && state.status === "ok" && shown.length === 0 && !repo.toLowerCase().includes(query)) {
    return null; // a filter that matches nothing here hides the repo
  }
  return (
    <section className="repo-prs" aria-labelledby={headingId}>
      <h2 id={headingId} className="eyebrow-heading">
        {repo}
      </h2>
      {state.status === "loading" ? (
        <p className="muted" role="status">
          Loading open pull requests…
        </p>
      ) : state.status === "error" ? (
        <Problem error={state.error} onRetry={reload} />
      ) : !state.data.recognized ? (
        <p className="muted">
          Qodo's reply for this repo could not be read as a list of pull requests.
        </p>
      ) : state.data.prs.length === 0 ? (
        <p className="muted">No open pull requests.</p>
      ) : (
        <ul className="pr-list">
          {shown.map((pr, i) => (
            <li key={`${pr.url ?? pr.number}-${i}`}>
              <PrLink pr={pr} current={pr.url !== null && pr.url === selected} />
            </li>
          ))}
        </ul>
      )}
      {state.status === "ok" && state.data.unreadable > 0 ? (
        <p className="muted small">
          {plural(state.data.unreadable, "item")} in Qodo's reply had neither a number nor a URL.
        </p>
      ) : null}
    </section>
  );
}

function prLabel(pr: PullRequest): string {
  const number = pr.number !== null ? `#${pr.number}` : "";
  return [number, pr.title ?? "(no title in Qodo's reply)"].filter(Boolean).join(" ");
}

function PrLink({ pr, current }: { pr: PullRequest; current: boolean }) {
  if (!pr.url) {
    return (
      <div className="pr-link pr-link--inert">
        <span className="pr-link__title">{prLabel(pr)}</span>
        <span className="pr-link__meta">Qodo's reply gave no URL for it.</span>
      </div>
    );
  }
  return (
    <Link
      to={findingsPath(pr.url)}
      className="pr-link"
      aria-current={current ? "true" : undefined}
    >
      <span className="pr-link__title">{prLabel(pr)}</span>
    </Link>
  );
}

// --- one pull request -------------------------------------------------------------------------

type Action = { kind: "implement" | "dismiss"; finding: Finding; opener: HTMLElement | null };
type FixAsk = { findings: Finding[]; opener: HTMLElement | null };

const isOpen = (f: Finding) => OPEN_STATUSES.has(f.attribution_status ?? "");

/** The newest fix request per finding id (the requests come newest first). */
function newestPerFinding(requests: FixRequest[]): Map<string, FixRequest> {
  const out = new Map<string, FixRequest>();
  for (const request of requests) {
    for (const id of request.finding_ids) if (!out.has(id)) out.set(id, request);
  }
  return out;
}

function PrFindings({ url, known }: { url: string; known?: Known }) {
  const load = useCallback((signal: AbortSignal) => listFindings(url, signal), [url]);
  const { state, reload, refresh } = useLoad(load, url);
  const [action, setAction] = useState<Action | null>(null);
  const [fixAsk, setFixAsk] = useState<FixAsk | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set());
  const [announcement, setAnnouncement] = useState("");
  const headingRef = useRef<HTMLHeadingElement>(null);
  const parsed = useMemo(() => describePrUrl(url), [url]);
  // the URL is the ground truth; the list only adds a title
  const repo = parsed.repo ?? known?.repo ?? null;
  const number = parsed.number ?? known?.pr.number ?? null;
  const title = known?.pr.title ?? (number !== null ? `Pull request #${number}` : "Pull request");
  const where = repo && number !== null ? `${repo} #${number}` : url;
  const onGitHub = (() => {
    try {
      return new URL(url).hostname === "github.com";
    } catch {
      return false;
    }
  })();
  // a fix request is for a github.com pull request only (the bridge refuses any other)
  const canFix = onGitHub;
  const loadFixes = useCallback(
    (signal: AbortSignal) => (canFix ? fixRequests(url, signal) : Promise.resolve([])),
    [url, canFix],
  );
  const fixes = useLoad(loadFixes, url);
  const markers = useMemo(
    () => (fixes.state.status === "ok" ? newestPerFinding(fixes.state.data) : new Map<string, FixRequest>()),
    [fixes.state],
  );
  const openFindings = state.status === "ok" ? state.data.findings.filter(isOpen) : [];
  // the selection, in the list's order; an id that is no longer an open finding drops out
  const chosen = openFindings.filter(
    (f, i) => selected.has(f.id) && openFindings.findIndex((g) => g.id === f.id) === i,
  );

  function act(kind: Action["kind"], finding: Finding, opener: HTMLElement) {
    setAnnouncement("");
    setAction({ kind, finding, opener });
  }

  function askFix(findings: Finding[], opener: HTMLElement) {
    setAnnouncement("");
    setFixAsk({ findings, opener });
  }

  function toggle(finding: Finding) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(finding.id)) next.delete(finding.id);
      else if (chosen.length < MAX_FIX_FINDINGS) next.add(finding.id);
      return next;
    });
  }

  function closeDialog() {
    const opener = action?.opener ?? fixAsk?.opener;
    setAction(null);
    setFixAsk(null);
    if (opener?.isConnected) opener.focus();
  }

  function fixDone(message: string) {
    setFixAsk(null);
    setSelected(new Set());
    setAnnouncement(message);
    headingRef.current?.focus();
    fixes.refresh(); // the marker is read back from the bridge's change log, never assumed
  }

  function done(message: string) {
    setAction(null);
    setAnnouncement(message);
    headingRef.current?.focus();
    refresh(); // the finding's new state is read back from Qodo, never assumed
  }

  return (
    <>
      <p className="notice" role="status" aria-label="Result of the last change">
        {announcement}
      </p>
      <section className="panel pr-head">
        <div className="pr-head__top">
          <div className="pr-head__title">
            <span className="muted">{repo && number !== null ? `${repo} · #${number}` : url}</span>
            <h1 className="pr-head__h1" ref={headingRef} tabIndex={-1}>
              {title}
            </h1>
          </div>
          <a className="btn btn--outline" href={url} target="_blank" rel="noreferrer">
            {onGitHub ? "Open on GitHub" : "Open the pull request"}
          </a>
        </div>
        {state.status === "ok" ? <ReviewSummary data={state.data} /> : null}
      </section>

      {state.status === "loading" ? (
        <p className="muted" role="status">
          Loading Qodo's findings…
        </p>
      ) : state.status === "error" ? (
        <Problem error={state.error} onRetry={reload} />
      ) : (
        <FindingList
          findings={state.data.findings}
          reviewed={state.data.review_session !== null}
          onAct={act}
          fix={
            canFix
              ? {
                  selected,
                  full: chosen.length >= MAX_FIX_FINDINGS,
                  markers,
                  onToggle: toggle,
                  onAsk: (finding, opener) => askFix([finding], opener),
                }
              : null
          }
        />
      )}

      {canFix && fixes.state.status === "error" ? (
        <p className="muted small">
          Earlier fix requests could not be read from the bridge's change log (
          {fixes.state.error.message}), so none are marked.
        </p>
      ) : null}

      {canFix && state.status === "ok" ? (
        <SelectionBar
          count={chosen.length}
          onSend={(opener) => askFix(chosen, opener)}
          onClear={() => setSelected(new Set())}
        />
      ) : null}

      {action ? (
        <ActionDialog
          action={action}
          where={where}
          onCancel={closeDialog}
          onDone={done}
        />
      ) : null}

      {fixAsk ? (
        <FixDialog
          url={url}
          findings={fixAsk.findings}
          where={where}
          onCancel={closeDialog}
          onDone={fixDone}
        />
      ) : null}
    </>
  );
}

function ReviewSummary({ data }: { data: Awaited<ReturnType<typeof listFindings>> }) {
  const session = data.review_session;
  const by = data.counts?.by_status ?? {};
  const implemented = (by.full_implementation ?? 0) + (by.implemented ?? 0);
  const dismissed = by.dismissed ?? 0;
  const open = data.counts?.open ?? 0;
  const total = data.counts?.total ?? data.findings.length;
  const other = Math.max(0, total - open - implemented - dismissed);
  const started = formatTime(session?.started_at);
  const sha = shortSha(session?.commit_sha);
  return (
    <>
      {session ? (
        <p className="muted">
          Qodo's latest review ({session.status ?? "status unknown"}) looked at commit{" "}
          <code className="sha">{sha ?? "unknown"}</code>
          {started ? `, started ${started}` : ""}.
        </p>
      ) : (
        <p className="muted">Qodo has not reviewed this pull request yet.</p>
      )}
      {data.incomplete ? (
        <p className="muted">
          This pull request has more findings than the screen reads at once; the counts cover all
          of them, the list below does not.
        </p>
      ) : null}
      {session || total > 0 ? (
        <ul className="tiles" aria-label="Findings by status">
          <Tile n={open} label="Open" />
          <Tile n={implemented} label="Implemented" />
          <Tile n={dismissed} label="Dismissed" />
          {other > 0 ? <Tile n={other} label="Other" /> : null}
        </ul>
      ) : null}
    </>
  );
}

function Tile({ n, label }: { n: number; label: string }) {
  return (
    <li className="tile">
      <span className="tile__n">{n}</span>
      <span className="tile__label">{label}</span>
    </li>
  );
}

/** What a card needs for fix requests; `null` when the PR cannot take one. */
interface FixControls {
  selected: ReadonlySet<string>;
  /** The selection holds as many findings as one request carries. */
  full: boolean;
  markers: Map<string, FixRequest>;
  onToggle: (finding: Finding) => void;
  onAsk: (finding: Finding, opener: HTMLElement) => void;
}

function FindingList({
  findings,
  reviewed,
  onAct,
  fix,
}: {
  findings: Finding[];
  reviewed: boolean;
  onAct: (kind: Action["kind"], finding: Finding, opener: HTMLElement) => void;
  fix: FixControls | null;
}) {
  if (findings.length === 0) {
    return reviewed ? <p className="muted">Qodo raised no findings on this pull request.</p> : null;
  }
  // open findings first, each group in Qodo's order
  const ordered = [...findings.filter(isOpen), ...findings.filter((f) => !isOpen(f))];
  return (
    <div className="finding-list">
      {ordered.map((finding, i) => (
        <FindingCard
          key={`${finding.id}-${i}`}
          finding={finding}
          open={isOpen(finding)}
          onAct={onAct}
          fix={fix}
        />
      ))}
    </div>
  );
}

function FixMarker({ request }: { request: FixRequest }) {
  const who = whoAsked(request.actor);
  return (
    <p className="fix-marker">
      {`Fix requested ${whenAsked(request.time)}${who ? ` by ${who}` : ""}.`}
    </p>
  );
}

function FindingCard({
  finding,
  open,
  onAct,
  fix,
}: {
  finding: Finding;
  open: boolean;
  onAct: (kind: Action["kind"], finding: Finding, opener: HTMLElement) => void;
  fix: FixControls | null;
}) {
  const titleId = useId();
  const raised = formatTime(finding.created_at);
  const sha = shortSha(finding.git_sha);
  const checked = fix?.selected.has(finding.id) ?? false;
  const marker = fix?.markers.get(finding.id);
  return (
    <article className={open ? "finding" : "finding finding--closed"} aria-labelledby={titleId}>
      {open && fix ? (
        <label className="select-fix">
          <input
            type="checkbox"
            checked={checked}
            disabled={!checked && fix.full}
            aria-describedby={titleId}
            onChange={() => fix.onToggle(finding)}
          />
          <span>Select for a fix</span>
        </label>
      ) : null}
      <div className="chips">
        {finding.category ? <span className="chip chip--category">{finding.category}</span> : null}
        {finding.action_level ? (
          <span className="chip chip--level">{humanize(finding.action_level)}</span>
        ) : null}
        <span className={open ? "chip chip--open" : "chip chip--closed"}>
          Status: {statusWord(finding.attribution_status)}
        </span>
      </div>
      <h2 id={titleId} className="finding__title">
        {finding.title || "Untitled finding"}
      </h2>
      {finding.description ? <p className="finding__text">{finding.description}</p> : null}
      {finding.clipped?.length ? (
        <p className="muted small">The bridge shortened the {finding.clipped.join(" and ")} of this finding.</p>
      ) : null}
      {raised || sha ? (
        <p className="muted small">
          Raised{raised ? ` ${raised}` : ""}
          {sha ? (
            <>
              {" "}
              at commit <code className="sha">{sha}</code>
            </>
          ) : null}
          .
        </p>
      ) : null}
      {marker ? <FixMarker request={marker} /> : null}
      {open ? (
        <div className="finding__actions">
          <button
            type="button"
            className="btn btn--primary"
            aria-describedby={titleId}
            onClick={(e) => onAct("implement", finding, e.currentTarget)}
          >
            Mark implemented
          </button>
          <button
            type="button"
            className="btn"
            aria-describedby={titleId}
            onClick={(e) => onAct("dismiss", finding, e.currentTarget)}
          >
            Dismiss
          </button>
          {fix ? (
            <button
              type="button"
              className="btn"
              aria-describedby={titleId}
              onClick={(e) => fix.onAsk(finding, e.currentTarget)}
            >
              Send an agent to fix
            </button>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

// --- several findings at once -----------------------------------------------------------------

function SelectionBar({
  count,
  onSend,
  onClear,
}: {
  count: number;
  onSend: (opener: HTMLElement) => void;
  onClear: () => void;
}) {
  const words = count > 0 ? `${plural(count, "finding")} selected` : "";
  return (
    <>
      {/* always in the page, so a change of the count is announced */}
      <p className="sr-only" role="status" aria-label="Selection">
        {words}
      </p>
      {count > 0 ? (
        <section className="select-bar" aria-label="Selected findings">
          <div className="select-bar__text">
            <p className="select-bar__count">{words}</p>
            {count >= MAX_FIX_FINDINGS ? (
              <p className="field__hint">
                That is the limit: one request carries at most {MAX_FIX_FINDINGS} findings.
              </p>
            ) : null}
          </div>
          <div className="select-bar__actions">
            <button type="button" className="btn btn--primary" onClick={(e) => onSend(e.currentTarget)}>
              Send an agent to fix {plural(count, "finding")}
            </button>
            <button type="button" className="btn btn--quiet" onClick={onClear}>
              Clear selection
            </button>
          </div>
        </section>
      ) : null}
    </>
  );
}

// --- the confirmation -------------------------------------------------------------------------

function writeMessage(kind: Action["kind"], error: ApiError): string {
  if (error.status === 403) {
    return kind === "implement"
      ? "Marking a finding implemented needs the editor role, and this credential does not have it."
      : "Dismissing a finding needs the editor role, and this credential does not have it.";
  }
  if (error.status === 401) return `The API did not accept the credential: ${error.message}`;
  if (error.code === "timeout") {
    return "The API did not answer in time, so whether Qodo made the change is unknown. Close this and reload the findings before trying again.";
  }
  return error.message;
}

function ActionDialog({
  action,
  where,
  onCancel,
  onDone,
}: {
  action: Action;
  where: string;
  onCancel: () => void;
  onDone: (message: string) => void;
}) {
  const { kind, finding } = action;
  const [reason, setReason] = useState<DismissReason | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const alertRef = useRef<HTMLDivElement>(null);
  const title = finding.title || "Untitled finding";

  // a phone-height dialog scrolls: bring the failure into view (it is also a live alert)
  useEffect(() => {
    if (error) alertRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [error]);

  async function confirm() {
    if (pending || (kind === "dismiss" && !reason)) return;
    setPending(true);
    setError(null);
    try {
      if (kind === "implement") {
        await implementFindings([finding.id]);
        onDone(`Marked “${title}” implemented in Qodo.`);
      } else {
        const chosen = reason as DismissReason;
        await dismissFindings([finding.id], chosen);
        const label = DISMISS_REASONS.find((r) => r.value === chosen)?.label ?? chosen;
        onDone(`Dismissed “${title}” as ${label.toLowerCase()} in Qodo.`);
      }
    } catch (err) {
      setError(asApiError(err));
      setPending(false);
    }
  }

  return (
    <Dialog
      title={kind === "implement" ? "Mark this finding implemented?" : "Dismiss this finding?"}
      onClose={onCancel}
      canClose={!pending}
    >
      {kind === "implement" ? (
        <p>
          This tells Qodo that the fix for <strong>“{title}”</strong> on {where} has landed. Qodo
          marks its pull request comment resolved, re-renders the review summary and clears the
          merge-policy block the finding holds. Only do this for a fix that is in the code.
        </p>
      ) : (
        <>
          <p>
            This closes <strong>“{title}”</strong> on {where} in Qodo without a code change, and
            strikes through its pull request comment.
          </p>
          <fieldset className="reasons" disabled={pending}>
            <legend>Why is it dismissed?</legend>
            {DISMISS_REASONS.map((r) => (
              <label key={r.value} className="reason">
                <input
                  type="radio"
                  name="dismiss-reason"
                  value={r.value}
                  checked={reason === r.value}
                  onChange={() => setReason(r.value)}
                />
                <span className="reason__label">{r.label}</span>
                <span className="reason__help">{r.help}</span>
              </label>
            ))}
          </fieldset>
        </>
      )}
      {error ? (
        <div role="alert" className="problem" ref={alertRef}>
          <p className="problem__message">{writeMessage(kind, error)}</p>
          {error.remediation ? <p className="problem__hint">Hint: {error.remediation}</p> : null}
        </div>
      ) : null}
      <div className="dialog__actions">
        <button type="button" className="btn" onClick={onCancel} disabled={pending}>
          Cancel
        </button>
        <button
          type="button"
          className="btn btn--primary"
          onClick={confirm}
          disabled={pending || (kind === "dismiss" && !reason)}
        >
          {kind === "implement"
            ? pending
              ? "Marking…"
              : "Mark implemented"
            : pending
              ? "Dismissing…"
              : "Dismiss finding"}
        </button>
      </div>
    </Dialog>
  );
}

// --- asking for an agent fix ------------------------------------------------------------------

/** A failed fix request in words: a headline, and whether the server's own message follows. */
function fixFailure(error: ApiError): { headline: string; showMessage: boolean } {
  if (error.status === 403) {
    return {
      headline:
        "Asking an agent for a fix needs the editor role, and this credential does not have it.",
      showMessage: false,
    };
  }
  if (error.status === 401) {
    return { headline: `The API did not accept the credential: ${error.message}`, showMessage: false };
  }
  if (error.status === 503) {
    // the events extra is missing, or no broker took the event: either way nothing was asked
    return {
      headline: "The bridge cannot reach the event bus, so no agent was asked.",
      showMessage: true,
    };
  }
  if (error.code === "timeout") {
    return {
      headline:
        "The API did not answer in time, so whether an agent was asked is unknown. Close this and reload the page before asking again.",
      showMessage: false,
    };
  }
  return { headline: error.message, showMessage: false };
}

function FixDialog({
  url,
  findings,
  where,
  onCancel,
  onDone,
}: {
  url: string;
  findings: Finding[];
  where: string;
  onCancel: () => void;
  onDone: (message: string) => void;
}) {
  const [note, setNote] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const alertRef = useRef<HTMLDivElement>(null);
  const noteId = useId();
  const countId = useId();
  const one = findings.length === 1;
  const titleOf = (f: Finding) => f.title || "Untitled finding";
  const what = one ? `“${titleOf(findings[0])}”` : plural(findings.length, "finding");

  useEffect(() => {
    if (error) alertRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [error]);

  async function confirm() {
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      await requestFix(
        url,
        findings.map((f) => f.id),
        note,
      );
      onDone(`Fix requested: an agent was asked to fix ${what} on ${where}.`);
    } catch (err) {
      const failure = asApiError(err);
      if (isAlreadyRequested(failure)) {
        onDone(`A fix was already requested for ${what}. The bridge says: ${failure.message}.`);
        return;
      }
      setError(failure);
      setPending(false);
    }
  }

  const failure = error ? fixFailure(error) : null;
  return (
    <Dialog
      title={one ? "Send an agent to fix this finding?" : `Send an agent to fix ${what}?`}
      onClose={onCancel}
      canClose={!pending}
    >
      {one ? (
        <p>
          An agent will be asked to fix <strong>{what}</strong> on {where}, working on this pull
          request's branch.
        </p>
      ) : (
        <>
          <p>
            An agent will be asked to fix these {what} on {where}, working on this pull request's
            branch:
          </p>
          <ul className="fix-list">
            {findings.map((f) => (
              <li key={f.id}>{titleOf(f)}</li>
            ))}
          </ul>
        </>
      )}
      <p>
        If it produces a fix, a new pull request against that branch is opened for a person to
        review. Nothing is merged automatically, and Qodo keeps the{" "}
        {one ? "finding" : "findings"} open until someone marks {one ? "it" : "them"}.
      </p>
      <div className="field">
        <label className="field__label" htmlFor={noteId}>
          Anything the agent should know (optional)
        </label>
        <textarea
          id={noteId}
          className="field__input field__textarea"
          value={note}
          maxLength={MAX_FIX_NOTE}
          rows={3}
          disabled={pending}
          aria-describedby={countId}
          onChange={(e) => setNote(e.target.value.slice(0, MAX_FIX_NOTE))}
        />
        <p
          id={countId}
          className={note.length >= MAX_FIX_NOTE ? "field__hint field__hint--warn" : "field__hint"}
        >
          {note.length} of {MAX_FIX_NOTE} characters
        </p>
      </div>
      {error && failure ? (
        <div role="alert" className="problem" ref={alertRef}>
          <p className="problem__message">{failure.headline}</p>
          {failure.showMessage ? <p>The bridge said: {error.message}</p> : null}
          {error.remediation ? <p className="problem__hint">Hint: {error.remediation}</p> : null}
        </div>
      ) : null}
      <div className="dialog__actions">
        <button type="button" className="btn" onClick={onCancel} disabled={pending}>
          Cancel
        </button>
        <button type="button" className="btn btn--primary" onClick={confirm} disabled={pending}>
          {pending ? "Sending…" : "Send an agent"}
        </button>
      </div>
    </Dialog>
  );
}
