// The fixtures the screens are tested against are copies of the recorded Qodo replies: this keeps
// each copy byte-for-byte equal to the file it names, so a re-recorded fixture shows up here.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { RECORDED_FINDINGS, RECORDED_RULE, RECORDED_RULES_SEARCH } from "./qodo";

const recorded = (name: string) =>
  JSON.parse(
    readFileSync(resolve(__dirname, "../../../tests/fixtures/qodo_mcp", name), "utf8"),
  ) as { isError: boolean; structuredContent: unknown };

describe("the recorded fixtures", () => {
  it.each([
    ["get_pr_review_findings.json", RECORDED_FINDINGS],
    ["rules_search.json", RECORDED_RULES_SEARCH],
    ["rules_get.json", RECORDED_RULE],
  ])("%s is copied unchanged", (file, copy) => {
    const source = recorded(file);
    expect(source.isError).toBe(false);
    expect(copy).toEqual(source.structuredContent);
  });
});
