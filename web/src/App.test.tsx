import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { CONFIG_STATUS, HANDWRITTEN_PRS } from "./fixtures/qodo";
import { stubApi } from "./test/api";

// Renders, then lets the key status settle so no state update lands outside act().
async function renderAt(path: string) {
  const view = render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText(/^Qodo key:/)).not.toHaveTextContent("checking"));
  return view;
}

beforeEach(() => {
  // the header reads /health; the screens read the allowlist and each repo's open PRs
  stubApi({
    "POST /verbs/config/status": { body: CONFIG_STATUS },
    "POST /verbs/prs/list": { body: HANDWRITTEN_PRS },
  });
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
    ["/findings", "Findings", "Findings"],
    ["/configuration", "Configuration", "Configuration"],
    ["/rules", "Rules", "What does Qodo enforce?"],
  ])("%s is its screen, with its heading and title", async (path, title, heading) => {
    await renderAt(path);
    expect(screen.getByRole("heading", { level: 1, name: heading })).toBeInTheDocument();
    expect(screen.getByRole("main")).not.toHaveTextContent(/not built yet/i);
    expect(document.title).toBe(`${title} - qodo`);
    await waitFor(() => expect(screen.queryByText(/^Loading/)).not.toBeInTheDocument());
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
