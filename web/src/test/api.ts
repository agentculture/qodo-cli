// A stub of the qodo-cli API for component tests: routes `fetch` by method and path, records
// every request (with its parsed JSON body) and answers from a table the test fills. A route the
// table does not name fails the test loudly instead of hanging.
import { vi } from "vitest";

export interface Seen {
  method: string;
  path: string;
  body: unknown;
}

/** One answer: a JSON body with a status, or a function of the request body. */
export interface AnswerBody {
  status?: number;
  body: unknown;
}
export type Answer = AnswerBody | ((body: unknown, n: number) => AnswerBody | Promise<AnswerBody>);

export const HEALTH_OK = {
  status: "ok",
  service: "qodo-cli",
  version: "0.0.0",
  key: { present: true, state: "ok", days_left: 212 },
};

export function envelope(code: string, message: string, remediation?: string) {
  return { error: { code, message, errors: [], ...(remediation ? { remediation } : {}) } };
}

/** `routes["POST /verbs/rules/search"] = {body: ...}`; `GET /health` answers ok by default. */
export function stubApi(routes: Record<string, Answer>) {
  const seen: Seen[] = [];
  const counts: Record<string, number> = {};
  const table: Record<string, Answer> = { "GET /health": { body: HEALTH_OK }, ...routes };
  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = (init.method ?? "GET").toUpperCase();
    const path = String(url).replace(/^\/api/, "").split("?")[0];
    const body = typeof init.body === "string" ? JSON.parse(init.body) : undefined;
    seen.push({ method, path, body });
    const key = `${method} ${path}`;
    counts[key] = (counts[key] ?? 0) + 1;
    const answer = table[key];
    if (!answer) throw new Error(`unexpected request ${key}`);
    const out = typeof answer === "function" ? await answer(body, counts[key]) : answer;
    return new Response(JSON.stringify(out.body), { status: out.status ?? 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return {
    seen,
    table,
    /** The requests to one route, in order. */
    calls: (key: string) => seen.filter((s) => `${s.method} ${s.path}` === key),
  };
}

/** A promise the test resolves by hand, to hold a request open. */
export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
