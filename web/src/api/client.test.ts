import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, REQUEST_TIMEOUT_MS, getJson, postVerb, query, request } from "./client";

function reply(status: number, body: unknown, statusText = ""): Response {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return new Response(text, { status, statusText });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("the error envelope", () => {
  it("becomes a typed ApiError with status, code, message, errors and remediation", async () => {
    const envelope = {
      error: {
        code: "user_error",
        message: "pr_url is required",
        errors: [{ path: "pr_url", code: "required", message: "missing" }],
        remediation: "pass --pr-url",
      },
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(reply(400, envelope)));
    const error = await getJson("/verbs").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const api = error as ApiError;
    expect(api.status).toBe(400);
    expect(api.code).toBe("user_error");
    expect(api.message).toBe("pr_url is required");
    expect(api.errors).toEqual(envelope.error.errors);
    expect(api.remediation).toBe("pass --pr-url");
  });

  it("falls back to http_error with the status line when the body is no envelope", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(reply(502, "<html>bad gateway</html>", "Bad Gateway")));
    const api = (await request("GET", "/health").catch((e: unknown) => e)) as ApiError;
    expect(api).toBeInstanceOf(ApiError);
    expect([api.status, api.code, api.message]).toEqual([502, "http_error", "502 Bad Gateway"]);
    expect(api.errors).toEqual([]);
    expect(api.remediation).toBeUndefined();
  });

  it("reports an unreachable API and a cancelled call with their own codes", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network down")));
    const down = (await request("GET", "/health").catch((e: unknown) => e)) as ApiError;
    expect([down.status, down.code]).toEqual([0, "unreachable"]);

    const ctl = new AbortController();
    ctl.abort();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("x", "AbortError")));
    const gone = (await request("GET", "/health", undefined, ctl.signal).catch(
      (e: unknown) => e,
    )) as ApiError;
    expect(gone.code).toBe("aborted");
  });

  it("gives up with code timeout when nothing answers", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener("abort", () => reject(new DOMException("x", "AbortError")));
          }),
      ),
    );
    const pending = request("GET", "/health").catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS + 1);
    expect(((await pending) as ApiError).code).toBe("timeout");
  });

  it("treats a 2xx answer that is not JSON as not_json", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(reply(200, "<html></html>")));
    const api = (await getJson("/health").catch((e: unknown) => e)) as ApiError;
    expect(api.code).toBe("not_json");
  });
});

describe("requests", () => {
  it("go to the same-origin /api prefix and send no credential", async () => {
    const fetchMock = vi.fn().mockResolvedValue(reply(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    await getJson("/health");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/health");
    expect(init.method).toBe("GET");
    expect(init.headers).toEqual({ accept: "application/json" });
    expect(init.credentials).toBeUndefined();
  });

  it("post a verb as one JSON body to /verbs/<noun>/<name>", async () => {
    const fetchMock = vi.fn().mockResolvedValue(reply(200, { findings: [] }));
    vi.stubGlobal("fetch", fetchMock);
    await postVerb("rules", "search", { query: "naming" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/verbs/rules/search");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ query: "naming" }));
    expect(init.headers).toEqual({ accept: "application/json", "content-type": "application/json" });
  });

  it("build a query string from the defined params only", () => {
    expect(query({ limit: 5, nope: undefined })).toBe("?limit=5");
    expect(query({})).toBe("");
  });
});
