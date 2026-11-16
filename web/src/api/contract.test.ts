// The client cannot drift from the pinned contract: every route it can call must be in
// api/openapi.json with the method it uses, and the error envelope it parses must be the
// schema's `Error`.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { ROUTES, verbPath } from "./client";
import { CHANGES_READ, MAX_FIX_FINDINGS, MAX_FIX_NOTE, VERBS, VERB_PARAMS } from "./qodo";
import type { ErrorEnvelope } from "./types";

interface Operation {
  parameters?: { name: string; in: string; schema: { maximum?: number } }[];
  responses?: Record<string, unknown>;
  security?: unknown[];
  requestBody?: {
    content: {
      "application/json": {
        schema: { properties: Record<string, Record<string, unknown>>; required?: string[] };
      };
    };
  };
  "x-role"?: string;
}
interface OpenApi {
  paths: Record<string, Record<string, Operation>>;
  components: {
    schemas: {
      Error: {
        required: string[];
        properties: { error: { required: string[]; properties: Record<string, unknown> } };
      };
    };
  };
}

const spec = JSON.parse(
  readFileSync(resolve(__dirname, "../../../api/openapi.json"), "utf8"),
) as OpenApi;

describe("api/openapi.json", () => {
  it("holds every route the client names, with the method the client uses", () => {
    for (const [name, route] of Object.entries(ROUTES)) {
      const operation = spec.paths[route.path]?.[route.method.toLowerCase()];
      expect(operation, `${name}: ${route.method} ${route.path}`).toBeDefined();
    }
  });

  it("serves /health without a credential (the header reads it before anything else)", () => {
    expect(spec.paths[ROUTES.health.path].get.security).toEqual([]);
  });

  it("holds the verb routes postVerb builds", () => {
    for (const [noun, name] of [
      ["findings", "list"],
      ["rules", "search"],
      ["config", "propose"],
    ]) {
      expect(spec.paths[verbPath(noun, name)]?.post, `${noun} ${name}`).toBeDefined();
    }
  });

  it("holds every verb the screens call, as a POST taking every param they send", () => {
    for (const [key, [noun, name]] of Object.entries(VERBS)) {
      const operation = spec.paths[verbPath(noun, name)]?.post;
      expect(operation, `${noun} ${name}`).toBeDefined();
      const schema = operation?.requestBody?.content["application/json"].schema;
      for (const param of VERB_PARAMS[key as keyof typeof VERBS]) {
        expect(schema?.properties, `${noun} ${name} takes ${param}`).toHaveProperty(param);
      }
    }
  });

  it("asks a viewer for the reads and an editor for the two writes", () => {
    const role = ([noun, name]: readonly [string, string]) =>
      spec.paths[verbPath(noun, name)].post["x-role"];
    expect(role(VERBS.findingsImplement)).toBe("editor");
    expect(role(VERBS.findingsDismiss)).toBe("editor");
    for (const verb of [VERBS.findingsList, VERBS.prsList, VERBS.configStatus, VERBS.rulesSearch]) {
      expect(role(verb)).toBe("viewer");
    }
  });

  it("asks an editor for findings fix and bounds its params as the screen does", () => {
    const operation = spec.paths[verbPath(...VERBS.findingsFix)].post;
    expect(operation["x-role"]).toBe("editor");
    const schema = operation.requestBody?.content["application/json"].schema;
    expect(schema?.required).toEqual(["pr_url", "finding_ids"]); // note is optional
    expect(schema?.properties.finding_ids.maxItems).toBe(MAX_FIX_FINDINGS);
    expect(schema?.properties.note.maxLength).toBe(MAX_FIX_NOTE);
    expect(Object.keys(schema?.properties ?? {}).sort()).toEqual(
      [...VERB_PARAMS.findingsFix].sort(),
    );
  });

  it("lets /changes return as many entries as the fix markers ask for", () => {
    const operation = spec.paths[ROUTES.changes.path].get;
    const limit = operation.parameters?.find((p) => p.name === "limit" && p.in === "query");
    expect(limit?.schema.maximum).toBeGreaterThanOrEqual(CHANGES_READ);
  });

  it("describes the error envelope the client parses", () => {
    const error = spec.components.schemas.Error;
    expect(error.required).toEqual(["error"]);
    const inner = error.properties.error;
    expect(inner.required).toEqual(["code", "message", "errors"]);
    expect(Object.keys(inner.properties).sort()).toEqual(
      ["code", "errors", "message", "remediation"],
    );
    // compile-time: the TypeScript envelope has exactly the schema's fields
    const sample: ErrorEnvelope = { error: { code: "c", message: "m", errors: [], remediation: "r" } };
    expect(Object.keys(sample.error).sort()).toEqual(Object.keys(inner.properties).sort());
  });
});
