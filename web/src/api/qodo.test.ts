import { afterEach, describe, expect, it, vi } from "vitest";
import {
  FINDINGS_PAGE,
  configStatus,
  describePrUrl,
  dismissFindings,
  implementFindings,
  listFindings,
  listOpenPrs,
  parsePrs,
  searchRules,
} from "./qodo";
import { HANDWRITTEN_PRS, OPEN_FINDING, PR_URL, findingsPage } from "../fixtures/qodo";
import { stubApi } from "../test/api";

afterEach(() => vi.unstubAllGlobals());

describe("reading a litegit-list-prs reply (never recorded: read defensively)", () => {
  it("reads the hand-written shape", () => {
    const out = parsePrs(HANDWRITTEN_PRS);
    expect(out.recognized).toBe(true);
    expect(out.prs[0]).toEqual({ number: 1, title: "Button codes are used once", url: PR_URL });
  });

  it.each([
    [[{ number: 3, title: "t", url: "https://h/o/r/pull/3" }]],
    [{ prs: [{ number: 3, title: "t", web_url: "https://h/o/r/pull/3" }] }],
    [{ items: [{ pr_number: 3, title: "t", html_url: "https://h/o/r/pull/3" }] }],
  ])("finds the list under a likely key: %j", (reply) => {
    const out = parsePrs(reply);
    expect(out.prs).toEqual([{ number: 3, title: "t", url: "https://h/o/r/pull/3" }]);
  });

  it("invents nothing: missing fields stay null and junk items are counted", () => {
    const out = parsePrs({
      pull_requests: [{ number: 4 }, { title: "no number, no url" }, "junk", { url: "javascript:x" }],
    });
    expect(out.prs).toEqual([{ number: 4, title: null, url: null }]);
    expect(out.unreadable).toBe(3);
  });

  it("says when it does not recognize the reply", () => {
    expect(parsePrs({ ok: true }).recognized).toBe(false);
    expect(parsePrs(null).recognized).toBe(false);
  });
});

describe("a PR URL", () => {
  it.each([
    ["https://github.com/o/r/pull/12", "o/r", 12],
    ["https://gitlab.example/g/sub/r/-/merge_requests/7", "g/sub/r", 7],
    ["not a url", null, null],
  ])("%s is %s #%s", (url, repo, number) => {
    expect(describePrUrl(url)).toEqual({ repo, number });
  });
});

describe("an answer of another shape", () => {
  it.each([
    ["POST /verbs/config/status", () => configStatus()],
    ["POST /verbs/findings/list", () => listFindings(PR_URL)],
    ["POST /verbs/rules/search", () => searchRules("q", null)],
  ])("from %s is an error, never an empty result", async (route, call) => {
    stubApi({ [route]: { body: { status: "ok", unexpected: true } } });
    await expect(call()).rejects.toMatchObject({ code: "bad_reply" });
  });
});

describe("the calls", () => {
  it("reads every page of a PR's findings", async () => {
    const page1 = { ...findingsPage([OPEN_FINDING]), next_offset: 1 };
    const page2 = { ...findingsPage([OPEN_FINDING]), offset: 1, next_offset: null };
    const api = stubApi({
      "POST /verbs/findings/list": (_body, n) => ({ body: n === 1 ? page1 : page2 }),
    });
    const all = await listFindings(PR_URL);
    expect(all.findings).toHaveLength(2);
    expect(all.incomplete).toBe(false);
    expect(api.calls("POST /verbs/findings/list").map((c) => c.body)).toEqual([
      { pr_url: PR_URL, offset: 0, limit: FINDINGS_PAGE },
      { pr_url: PR_URL, offset: 1, limit: FINDINGS_PAGE },
    ]);
  });

  it("sends exactly the write bodies", async () => {
    const api = stubApi({
      "POST /verbs/findings/implement": { body: { ok: true } },
      "POST /verbs/findings/dismiss": { body: { ok: true } },
    });
    await implementFindings(["a"]);
    await dismissFindings(["a", "b"], "intentional");
    expect(api.seen.map((s) => s.body)).toEqual([
      { finding_ids: ["a"] },
      { finding_ids: ["a", "b"], reason: "intentional" },
    ]);
  });

  it("asks a repo for its open pull requests", async () => {
    const api = stubApi({ "POST /verbs/prs/list": { body: HANDWRITTEN_PRS } });
    await listOpenPrs("o/r");
    expect(api.seen[0].body).toEqual({ repo: "o/r", state: "open", max_results: 50 });
  });

  it("searches rules org-wide or in one repo's scope", async () => {
    const api = stubApi({ "POST /verbs/rules/search": { body: { rules: [] } } });
    await searchRules("naming", null);
    await searchRules("naming", "o/r");
    expect(api.seen.map((s) => s.body)).toEqual([
      { query: "naming" },
      { query: "naming", scopes: ["/o/r/"] },
    ]);
  });
});
