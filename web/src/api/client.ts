import type { ErrorEnvelope } from "./types";

/**
 * Cited from culture-rules web/src/api/client.ts @ b7f861f690b57c4355f81371cc02ad1dd2ae9cac (cite-don't-import: this repo owns
 * its copy). Adapted: no whoami or list helpers (this API has none); the error carries the
 * envelope's `remediation`; `postVerb` calls the verb routes; the route table below is what
 * `contract.test.ts` checks against `api/openapi.json`.
 *
 * Same-origin API prefix. In dev, vite.config.ts proxies `/api` to `qodo-cli serve`
 * (QODO_API_URL, default http://127.0.0.1:8792) with the prefix stripped; in production
 * `qodo-cli serve` strips it itself and serves this bundle. Never an absolute remote URL.
 *
 * No request from this client attaches a credential: behind Cloudflare Access on the
 * loopback listener the browser carries the Access cookie on every same-origin request.
 */
export const API_ROOT = "/api";

/** How long one API call may take before it fails with code `timeout`. */
export const REQUEST_TIMEOUT_MS = 15_000;

export type Method = "GET" | "POST";

/** The routes the UI calls that are not verbs, as `api/openapi.json` names them. */
export const ROUTES = {
  health: { method: "GET", path: "/health" },
  verbs: { method: "GET", path: "/verbs" },
  changes: { method: "GET", path: "/changes" },
} as const satisfies Record<string, { method: Method; path: string }>;

/** The route of one verb: `POST /verbs/<noun>/<name>`. */
export const verbPath = (noun: string, name: string): string => `/verbs/${noun}/${name}`;

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  /** The envelope's nested `errors[]`; empty when it carried none. */
  readonly errors: unknown[];
  /** The envelope's `remediation`, when it carried one. */
  readonly remediation?: string;

  constructor(
    status: number,
    code: string,
    message: string,
    errors: unknown[] = [],
    remediation?: string,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.errors = errors;
    this.remediation = remediation;
  }
}

/**
 * One call to the API: JSON in, JSON out, the error envelope turned into an `ApiError`.
 * Answers the parsed body, which may be `null` for an empty answer.
 */
export async function request<T>(
  method: Method,
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  // A request that never answers must still settle, or a view waits on it forever: give up
  // after REQUEST_TIMEOUT_MS. The caller's own signal keeps working.
  const ctl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    ctl.abort();
  }, REQUEST_TIMEOUT_MS);
  const forward = () => ctl.abort();
  signal?.addEventListener("abort", forward);
  if (signal?.aborted) ctl.abort();
  let response: Response;
  let text: string;
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      method,
      signal: ctl.signal,
      headers: {
        accept: "application/json",
        ...(body === undefined ? {} : { "content-type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    text = await response.text();
  } catch {
    if (timedOut) {
      throw new ApiError(0, "timeout", `the qodo-cli API did not answer ${method} ${path}`);
    }
    if (signal?.aborted) {
      throw new ApiError(0, "aborted", `${method} ${path} was cancelled`);
    }
    throw new ApiError(0, "unreachable", `cannot reach the qodo-cli API at ${API_ROOT}`);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", forward);
  }
  let parsed: unknown = null;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = null;
  }
  if (!response.ok) {
    const envelope = parsed as Partial<ErrorEnvelope> | null;
    throw new ApiError(
      response.status,
      envelope?.error?.code ?? "http_error",
      envelope?.error?.message ?? `${response.status} ${response.statusText}`.trim(),
      Array.isArray(envelope?.error?.errors) ? envelope.error.errors : [],
      typeof envelope?.error?.remediation === "string" ? envelope.error.remediation : undefined,
    );
  }
  return parsed as T;
}

/** `GET path`; a 2xx answer that is not JSON is an `ApiError` (`not_json`). */
export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const body = await request<T | null>("GET", path, undefined, signal);
  if (body === null) {
    throw new ApiError(200, "not_json", `${path} did not return JSON`);
  }
  return body;
}

/** `POST /verbs/<noun>/<name>` with the verb's params as one JSON object. */
export const postVerb = <T,>(
  noun: string,
  name: string,
  params: Record<string, unknown> = {},
  signal?: AbortSignal,
): Promise<T> => request<T>("POST", verbPath(noun, name), params, signal);

/** A query string from the defined params (`?a=1&b=2`, or "" when none). */
export function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  const q = search.toString();
  return q ? `?${q}` : "";
}
