import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

// Renders, then lets the key status settle so no state update lands outside act().
async function renderAt(path: string) {
  const view = render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByRole("status")).not.toHaveTextContent("checking"));
  return view;
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            status: "ok",
            service: "qodo-cli",
            version: "0.10.0",
            key: { present: true, state: "ok", days_left: 212 },
          }),
        ),
      ),
    ),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const TABS = ["Findings", "Configuration", "Rules"];

describe("the header", () => {
  it("has the brand and exactly the three tabs in the primary navigation", async () => {
    await renderAt("/findings");
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual(TABS);
    expect(screen.getByText("qodo")).toBeInTheDocument();
  });

  it.each([
    ["/findings", "Findings"],
    ["/configuration", "Configuration"],
    ["/rules", "Rules"],
  ])("marks only the current tab with aria-current at %s", async (path, current) => {
    await renderAt(path);
    const nav = screen.getByRole("navigation", { name: "Primary" });
    for (const name of TABS) {
      const link = within(nav).getByRole("link", { name });
      if (name === current) expect(link).toHaveAttribute("aria-current", "page");
      else expect(link).not.toHaveAttribute("aria-current");
    }
  });

  it("shows the key status from /health", async () => {
    await renderAt("/findings");
    expect(await screen.findByText("Qodo key: 212 days left")).toBeInTheDocument();
  });
});

describe("the routes", () => {
  it.each([
    ["/findings", "Findings"],
    ["/configuration", "Configuration"],
    ["/rules", "Rules"],
  ])("%s is a placeholder with its heading and a line saying it is not built yet", async (path, title) => {
    await renderAt(path);
    expect(screen.getByRole("heading", { level: 1, name: title })).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveTextContent(/not built yet/i);
    expect(document.title).toBe(`${title} - qodo`);
  });

  it("sends / and an unknown path to Findings", async () => {
    const root = await renderAt("/");
    expect(screen.getByRole("heading", { level: 1, name: "Findings" })).toBeInTheDocument();
    root.unmount();
    await renderAt("/nowhere");
    expect(screen.getByRole("heading", { level: 1, name: "Findings" })).toBeInTheDocument();
  });

  it("moves between tabs from the keyboard", async () => {
    const user = userEvent.setup();
    await renderAt("/findings");
    const nav = screen.getByRole("navigation", { name: "Primary" });
    await user.tab(); // the skip link
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveFocus();
    await user.tab();
    expect(within(nav).getByRole("link", { name: "Findings" })).toHaveFocus();
    await user.tab();
    expect(within(nav).getByRole("link", { name: "Configuration" })).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("heading", { level: 1, name: "Configuration" })).toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: "Configuration" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});
