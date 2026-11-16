import { useId } from "react";
import { configStatus } from "../api/qodo";
import type { ConfigFile, ConfigWarning, RepoStatus, ValidationCheck } from "../api/types";
import Problem from "../components/Problem";
import { useLoad } from "../hooks/useLoad";
import { useTitle } from "../hooks/useTitle";
import { formatTime, plural } from "./words";

/**
 * Configuration (canvas artboard "Configuration"): for each repo of the bridge's allowlist, the
 * Qodo configuration files its checkout holds, whether they validate, and the changes the bridge
 * proposed for it. One read: `config status` (names, sizes, validation, warnings and the change
 * log's proposals; never a file's contents, so there is no "View file" and no diff).
 */
export default function Configuration() {
  useTitle("Configuration");
  const { state, reload } = useLoad(configStatus, "status");
  return (
    <>
      <div className="intro">
        <h1 className="page__title">Configuration</h1>
        <p className="page__lede">
          What Qodo reads in each repo, whether it validates, and the changes the bridge proposed
          for it. A person merges every change.
        </p>
      </div>
      {state.status === "loading" ? (
        <p className="muted" role="status">
          Loading the configuration…
        </p>
      ) : state.status === "error" ? (
        <Problem error={state.error} onRetry={reload} />
      ) : !state.data.configured ? (
        <div className="panel empty">
          <p>No repos are configured on this bridge.</p>
          <p>
            Set <code>QODO_PROPOSE_REPOS</code> (owner/name, comma separated) and{" "}
            <code>QODO_PROPOSE_CHECKOUT_ROOTS</code> (where their checkouts are) where{" "}
            <code>qodo-cli serve</code> runs, then reload.
          </p>
        </div>
      ) : (
        <>
          {state.data.truncated ? (
            <p className="muted">
              Showing the first 20 repos of the allowlist; the API answers for one repo by name.
            </p>
          ) : null}
          {state.data.repos.map((repo) => (
            <RepoCard key={repo.repo} status={repo} />
          ))}
        </>
      )}
    </>
  );
}

function pill(status: RepoStatus): { text: string; kind: string } {
  if (status.checkout === "missing" || !status.validation) return { text: "No checkout", kind: "none" };
  const failed = status.validation.checks.filter((c) => !c.ok);
  const warnings = status.warnings.length;
  if (!status.validation.valid) {
    return { text: `Invalid, ${plural(failed.length + warnings, "problem")}`, kind: "bad" };
  }
  const notes = failed.length + warnings;
  return notes > 0
    ? { text: `Valid, ${plural(notes, "warning")}`, kind: "warn" }
    : { text: "Valid", kind: "ok" };
}

function RepoCard({ status }: { status: RepoStatus }) {
  const headingId = useId();
  const { text, kind } = pill(status);
  return (
    <section className="panel repo-card" aria-labelledby={headingId}>
      <div className="repo__head">
        <h2 id={headingId} className="repo__name">
          {status.repo}
        </h2>
        <span className={`pill pill--${kind}`}>{text}</span>
      </div>
      {status.checkout === "missing" ? (
        <p className="muted">
          The bridge has no checkout of this repo under its checkout roots, so it cannot read its
          files.
        </p>
      ) : (
        <>
          <h3 className="eyebrow-heading">Files Qodo reads</h3>
          <ul className="file-grid" aria-label="Files Qodo reads">
            {status.files.map((file) => (
              <FileTile
                key={file.path}
                file={file}
                warnings={status.warnings.filter((w) => w.path === file.path)}
              />
            ))}
          </ul>
          {status.validation ? <Checks checks={status.validation.checks} /> : null}
        </>
      )}
      <Proposals status={status} />
    </section>
  );
}

function describeFile(file: ConfigFile): string {
  if (!file.present) return "Not present.";
  if (file.problem) return `Not read: ${file.problem.detail}.`;
  const size = typeof file.bytes === "number" ? plural(file.bytes, "byte") : null;
  if (file.kind === "toml") {
    if (file.valid_toml === false) return `Not valid TOML: ${file.error ?? "unknown error"}`;
    return ["Valid TOML", size].filter(Boolean).join(" · ");
  }
  return ["Present", size].filter(Boolean).join(" · ");
}

function FileTile({ file, warnings }: { file: ConfigFile; warnings: ConfigWarning[] }) {
  return (
    <li className="file-tile">
      <code className="file-tile__path">{file.path}</code>
      <span className="file-tile__text">{describeFile(file)}</span>
      {file.sections && file.sections.length > 0 ? (
        <span className="file-tile__text">Sections: {file.sections.join(", ")}</span>
      ) : null}
      {warnings.map((w, i) => (
        <span key={`${w.code}-${i}`} className="file-tile__warn">
          Warning: {w.detail}
        </span>
      ))}
    </li>
  );
}

function checkState(check: ValidationCheck): string {
  if (check.ok) return "Passed";
  return check.severity === "error" ? "Failed" : "Warning";
}

function Checks({ checks }: { checks: ValidationCheck[] }) {
  return (
    <>
      <h3 className="eyebrow-heading">Checks</h3>
      <ul className="checks" aria-label="Checks">
        {checks.map((check) => (
          <li key={check.id} className={`check check--${check.ok ? "ok" : check.severity}`}>
            <span className="check__state">{checkState(check)}</span>
            <span className="check__message">{check.message}</span>
            {!check.ok && check.remediation ? (
              <span className="check__hint">Hint: {check.remediation}</span>
            ) : null}
          </li>
        ))}
      </ul>
    </>
  );
}

const ACTORS: Record<string, string> = {
  api: "through the API",
  cli: "from the CLI",
  mcp: "through MCP",
};

function Proposals({ status }: { status: RepoStatus }) {
  return (
    <div className="proposals">
      <h3 className="eyebrow-heading">Proposed changes</h3>
      {status.proposals.length === 0 ? (
        <p className="muted">No change proposed through this bridge.</p>
      ) : (
        <>
          <ul className="proposal-list" aria-label="Proposed changes">
            {status.proposals.map((p, i) => (
              <li key={`${p.time}-${i}`}>
                Proposed {formatTime(p.time) ?? "at an unknown time"}
                {p.actor ? ` ${ACTORS[p.actor] ?? `by ${p.actor}`}` : ""}: changes{" "}
                {p.files.length ? (
                  p.files.map((f, j) => (
                    <span key={f}>
                      {j > 0 ? ", " : ""}
                      <code>{f}</code>
                    </span>
                  ))
                ) : (
                  <span>no file the log names</span>
                )}
                .
              </li>
            ))}
          </ul>
          <p className="muted small">
            The bridge records that a change was proposed, not the pull request opened for it or
            its status.
          </p>
        </>
      )}
    </div>
  );
}
