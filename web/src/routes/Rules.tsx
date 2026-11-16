import { type FormEvent, useCallback, useId, useRef, useState } from "react";
import { configStatus, searchRules, showRule } from "../api/qodo";
import type { ApiError } from "../api/client";
import type { RuleDetail, RuleHit } from "../api/types";
import KeyNotice from "../components/KeyNotice";
import Problem from "../components/Problem";
import { asApiError, type LoadState, useLoad } from "../hooks/useLoad";
import { useTitle } from "../hooks/useTitle";
import { humanize, plural } from "./words";

/**
 * Rules (canvas artboard "Rules"): semantic search over the org's Qodo rules (`rules search`),
 * org-wide or narrowed to one allowlisted repo's scope (`/owner/repo/`), with a rule's details
 * read on request (`rules show`). The repos are the bridge's allowlist (`config status`).
 */
export default function Rules() {
  useTitle("Rules");
  const [text, setText] = useState("");
  const [scope, setScope] = useState<string | null>(null);
  const [hint, setHint] = useState("");
  const [last, setLast] = useState<{ query: string; scope: string | null } | null>(null);
  const [result, setResult] = useState<LoadState<RuleHit[]> | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const runId = useRef(0);
  const repos = useLoad(configStatus, "repos");
  const repoNames =
    repos.state.status === "ok" ? repos.state.data.repos.map((r) => r.repo) : [];

  const run = useCallback((query: string, repo: string | null) => {
    const id = (runId.current += 1);
    setLast({ query, scope: repo });
    setResult({ status: "loading" });
    searchRules(query, repo)
      .then((rules) => {
        if (runId.current === id) setResult({ status: "ok", data: rules });
      })
      .catch((err: unknown) => {
        if (runId.current === id) setResult({ status: "error", error: asApiError(err) });
      });
  }, []);

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const query = text.trim();
    if (!query) {
      setHint("Type what the rule is about.");
      inputRef.current?.focus();
      return;
    }
    setHint("");
    run(query, scope);
  }

  function choose(repo: string | null) {
    setScope(repo);
    if (last) run(last.query, repo); // a search on screen follows the scope
  }

  return (
    <>
      <div className="rules-hero">
        <h1 className="page__title">What does Qodo enforce?</h1>
        <form role="search" className="rules-search" onSubmit={onSubmit} noValidate>
          <label className="rules-search__field">
            <span className="sr-only">Search rules</span>
            <input
              ref={inputRef}
              type="search"
              className="rules-search__input"
              value={text}
              onChange={(e) => setText(e.target.value)}
              aria-describedby="rules-hint"
            />
          </label>
          <button type="submit" className="btn btn--primary btn--large">
            Search
          </button>
        </form>
        <p id="rules-hint" className={hint ? "field__hint field__hint--warn" : "field__hint"}>
          {hint || "Describe the rule: a short title and what it checks match best."}
        </p>
        <div className="scope" role="group" aria-labelledby="scope-label">
          <span id="scope-label">Scope</span>
          {repoNames.map((repo) => (
            <ScopeButton key={repo} pressed={scope === repo} onClick={() => choose(repo)}>
              {repo}
            </ScopeButton>
          ))}
          <ScopeButton pressed={scope === null} onClick={() => choose(null)}>
            All repos
          </ScopeButton>
        </div>
        {repos.state.status === "error" ? (
          <p className="muted small">{`Repo scopes are not available: ${repos.state.error.message}`}</p>
        ) : null}
      </div>
      <KeyNotice />
      <Results result={result} onRetry={last ? () => run(last.query, last.scope) : undefined} />
      <p className="muted small">
        Rules are read from Qodo. To change one, edit its source file in the repo or use Qodo's
        portal.
      </p>
    </>
  );
}

function ScopeButton({
  pressed,
  onClick,
  children,
}: {
  pressed: boolean;
  onClick: () => void;
  children: string;
}) {
  return (
    <button type="button" className="scope__button" aria-pressed={pressed} onClick={onClick}>
      {children}
    </button>
  );
}

function Results({
  result,
  onRetry,
}: {
  result: LoadState<RuleHit[]> | null;
  onRetry?: () => void;
}) {
  if (result === null) return null;
  if (result.status === "loading") {
    return (
      <p className="muted" role="status">
        Searching Qodo's rules…
      </p>
    );
  }
  if (result.status === "error") return <Problem error={result.error} onRetry={onRetry} />;
  const rules = result.data;
  return (
    <>
      <p className="muted" role="status">
        {rules.length === 0
          ? "No rules match."
          : `${plural(rules.length, "rule")} ${rules.length === 1 ? "matches" : "match"}.`}
      </p>
      <div className="rule-list">
        {rules.map((rule, i) => (
          <RuleCard key={`${rule.id ?? "rule"}-${i}`} rule={rule} />
        ))}
      </div>
    </>
  );
}

function RuleCard({ rule }: { rule: RuleHit }) {
  const titleId = useId();
  const detailsId = useId();
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<LoadState<RuleDetail> | null>(null);
  const id = typeof rule.id === "number" && Number.isInteger(rule.id) && rule.id > 0 ? rule.id : null;

  function load(ruleId: number) {
    setDetail({ status: "loading" });
    showRule(ruleId)
      .then((data) => setDetail({ status: "ok", data }))
      .catch((err: unknown) => setDetail({ status: "error", error: asApiError(err) as ApiError }));
  }

  function toggle() {
    const next = !open;
    setOpen(next);
    if (next && id !== null && (detail === null || detail.status === "error")) load(id);
  }

  return (
    <article className="rule" aria-labelledby={titleId}>
      {rule.severity ? (
        <div className="chips">
          <span className={`chip chip--severity-${rule.severity}`}>
            Severity: {humanize(rule.severity)}
          </span>
        </div>
      ) : null}
      <h2 id={titleId} className="rule__title">
        {rule.name || "Unnamed rule"}
      </h2>
      {rule.content ? <p className="rule__text">{rule.content}</p> : null}
      {id !== null ? (
        <div className="rule__footer">
          <button
            type="button"
            className="btn btn--quiet"
            aria-expanded={open}
            aria-controls={detailsId}
            onClick={toggle}
          >
            Details
          </button>
        </div>
      ) : null}
      <div id={detailsId} hidden={!open}>
        {open && detail ? (
          detail.status === "loading" ? (
            <p className="muted" role="status">
              Loading the rule…
            </p>
          ) : detail.status === "error" ? (
            <Problem error={detail.error} onRetry={() => id !== null && load(id)} />
          ) : (
            <RuleFacts rule={detail.data} />
          )
        ) : null}
      </div>
    </article>
  );
}

function RuleFacts({ rule }: { rule: RuleDetail }) {
  const facts: [string, string | null | undefined][] = [
    ["Category", rule.category],
    ["State", rule.state],
    ["Source type", rule.sourceType],
    ["Source", rule.source],
    ["Scopes", Array.isArray(rule.scopes) && rule.scopes.length ? rule.scopes.join(", ") : null],
    ["Good example", rule.goodExamples],
    ["Bad example", rule.badExamples],
  ];
  const shown = facts.filter((f): f is [string, string] => typeof f[1] === "string" && f[1] !== "");
  if (shown.length === 0) return <p className="muted">Qodo returned no details for this rule.</p>;
  return (
    <dl className="facts">
      {shown.map(([term, value]) => (
        <div key={term} className="facts__row">
          <dt>{term}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}
