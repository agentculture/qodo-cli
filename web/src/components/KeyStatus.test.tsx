import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import KeyStatus from "./KeyStatus";

function stubHealth(response: Response | Error) {
  const fetchMock = vi.fn(() =>
    response instanceof Error ? Promise.reject(response) : Promise.resolve(response),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function health(key: { present: boolean; state: string; days_left: number | null }, status = "ok") {
  return new Response(
    JSON.stringify({ status, service: "qodo-cli", version: "0.10.0", key }),
    { status: 200 },
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the key status in the header", () => {
  it("asks GET /api/health, with a checking state first", async () => {
    const fetchMock = stubHealth(health({ present: true, state: "ok", days_left: 212 }));
    render(<KeyStatus />);
    expect(screen.getByRole("status")).toHaveTextContent("Qodo key: checking");
    expect(await screen.findByText("Qodo key: 212 days left")).toBeInTheDocument();
    expect((fetchMock.mock.calls as unknown[][])[0][0]).toBe("/api/health");
  });

  it.each([
    [{ present: true, state: "ok", days_left: 212 }, "Qodo key: 212 days left", "ok"],
    [{ present: true, state: "ok", days_left: 1 }, "Qodo key: 1 day left", "ok"],
    [{ present: true, state: "expiring", days_left: 9 }, "Qodo key: 9 days left", "expiring"],
    [{ present: true, state: "expiring", days_left: 0 }, "Qodo key: expires today", "expiring"],
    [{ present: true, state: "expired", days_left: -3 }, "Qodo key: expired 3 days ago", "expired"],
    [{ present: true, state: "expired", days_left: -1 }, "Qodo key: expired 1 day ago", "expired"],
    [{ present: false, state: "missing", days_left: null }, "Qodo key: not set", "missing"],
    [{ present: true, state: "unknown", days_left: null }, "Qodo key: expiry not set", "unknown"],
    [{ present: true, state: "invalid", days_left: null }, "Qodo key: expiry unreadable", "invalid"],
  ])("shows %j as %s", async (key, text, state) => {
    stubHealth(health(key, state === "ok" ? "ok" : "degraded"));
    render(<KeyStatus />);
    const status = await screen.findByText(text);
    expect(status).toHaveAttribute("data-key-state", state);
  });

  it("never invents a number: an ok key with no days_left is unavailable", async () => {
    stubHealth(health({ present: true, state: "ok", days_left: null }));
    render(<KeyStatus />);
    expect(await screen.findByText("Qodo key: status unavailable")).toHaveAttribute(
      "data-key-state",
      "unavailable",
    );
  });

  it("says so when the request fails, whatever the failure", async () => {
    stubHealth(new TypeError("down"));
    render(<KeyStatus />);
    const status = await screen.findByText("Qodo key: status unavailable");
    expect(status).toHaveAttribute("data-key-state", "unavailable");
  });

  it("says so when the API answers with an error envelope", async () => {
    stubHealth(
      new Response(JSON.stringify({ error: { code: "internal_error", message: "x", errors: [] } }), {
        status: 500,
      }),
    );
    render(<KeyStatus />);
    expect(await screen.findByText("Qodo key: status unavailable")).toBeInTheDocument();
  });

  it("does not read a health body of another shape as a key", async () => {
    stubHealth(new Response(JSON.stringify({ status: "ok" }), { status: 200 }));
    render(<KeyStatus />);
    expect(await screen.findByText("Qodo key: status unavailable")).toBeInTheDocument();
  });
});
