// The client cannot drift from the pinned contract: every route it can call must be in
// api/openapi.json with the method it uses, and the error envelope it parses must be the
// schema's `Error`.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { ROUTES, verbPath } from "./client";
import { VERBS, VERB_PARAMS } from "./qodo";
import type { ErrorEnvelope } from "./types";

interface Operation {
  responses?: Record<string, unknown>;
  security?: unknown[];
  requestBody?: {
    content: { "application/json": { schema: { properties: Record<string, unknown> } } };
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
