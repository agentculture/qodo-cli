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
