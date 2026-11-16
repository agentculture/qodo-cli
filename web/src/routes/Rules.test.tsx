import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Rules from "./Rules";
import { CONFIG_STATUS, RECORDED_RULE, RECORDED_RULES_SEARCH, REPO } from "../fixtures/qodo";
import { type Answer, envelope, stubApi } from "../test/api";

afterEach(() => vi.unstubAllGlobals());

const SEARCH = "POST /verbs/rules/search";
const SHOW = "POST /verbs/rules/show";

function setup(routes: Record<string, Answer> = {}) {
  return stubApi({
    "POST /verbs/config/status": { body: CONFIG_STATUS },
    [SEARCH]: { body: RECORDED_RULES_SEARCH },
    [SHOW]: { body: RECORDED_RULE },
    ...routes,
  });
}

const box = () => screen.getByRole("searchbox", { name: "Search rules" });

describe("the rules screen", () => {
  it("searches org-wide by default and shows the recorded fields", async () => {
    const user = userEvent.setup();
    const api = setup();
    render(<Rules />);
    expect(screen.getByRole("heading", { level: 1, name: "What does Qodo enforce?" })).toBeInTheDocument();
    expect(document.title).toBe("Rules - qodo");
    expect(screen.getByRole("button", { name: "All repos" })).toHaveAttribute("aria-pressed", "true");
    await user.type(box(), "imports between extensions{Enter}");
    expect(await screen.findByText("2 rules match.")).toBeInTheDocument();
    expect(api.calls(SEARCH).map((c) => c.body)).toEqual([{ query: "imports between extensions" }]);
    const cards = screen.getAllByRole("article");
    expect(cards).toHaveLength(2);
    expect(within(cards[0]).getByRole("heading", { name: "sample name" })).toBeInTheDocument();
    expect(within(cards[0]).getByText("Severity: Warning")).toBeInTheDocument();
    expect(within(cards[0]).getByText("sample content")).toBeInTheDocument();
    // not in any recorded reply: the canvas's "Raised on N open pull requests"
    expect(screen.queryByText(/Raised on/)).not.toBeInTheDocument();
  });

  it("sends the chosen repo as a scope, by keyboard alone", async () => {
    const user = userEvent.setup();
    const api = setup();
    render(<Rules />);
    const repo = await screen.findByRole("button", { name: REPO });
    expect(repo).toHaveAttribute("aria-pressed", "false");
    await user.tab();
    expect(box()).toHaveFocus();
    await user.keyboard("naming");
    for (let i = 0; i < 10 && document.activeElement !== repo; i += 1) await user.tab();
    expect(repo).toHaveFocus();
    await user.keyboard(" ");
    expect(repo).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "All repos" })).toHaveAttribute("aria-pressed", "false");
    await user.click(box());
    await user.keyboard("{Enter}");
    await screen.findByText("2 rules match.");
    expect(api.calls(SEARCH).map((c) => c.body)).toEqual([
      { query: "naming", scopes: [`/${REPO}/`] },
    ]);
  });

  it("asks for words before searching", async () => {
    const user = userEvent.setup();
    const api = setup();
    render(<Rules />);
    await user.click(screen.getByRole("button", { name: "Search" }));
    expect(screen.getByText("Type what the rule is about.")).toBeInTheDocument();
    expect(box()).toHaveFocus();
    expect(api.calls(SEARCH)).toHaveLength(0);
  });

  it("says when no rule matches", async () => {
    const user = userEvent.setup();
    setup({ [SEARCH]: { body: { rules: [] } } });
    render(<Rules />);
    await user.type(box(), "nothing{Enter}");
    expect(await screen.findByText("No rules match.")).toBeInTheDocument();
  });

  it("shows a failed search with the message and a retry", async () => {
    const user = userEvent.setup();
    setup({
      [SEARCH]: (_b, n) =>
        n === 1
          ? { status: 503, body: envelope("environment_error", "no Qodo API key found", "export QODO_API_KEY") }
          : { body: RECORDED_RULES_SEARCH },
    });
    render(<Rules />);
    await user.type(box(), "naming{Enter}");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("no Qodo API key found");
    expect(alert).toHaveTextContent("export QODO_API_KEY");
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("2 rules match.")).toBeInTheDocument();
  });

  it("opens a rule's details from rules show", async () => {
    const user = userEvent.setup();
    const api = setup();
    render(<Rules />);
    await user.type(box(), "naming{Enter}");
    const [first] = await screen.findAllByRole("article");
    const details = within(first).getByRole("button", { name: "Details" });
    expect(details).toHaveAttribute("aria-expanded", "false");
    await user.click(details);
    expect(details).toHaveAttribute("aria-expanded", "true");
    expect(await within(first).findByText("Maintainability")).toBeInTheDocument();
    expect(within(first).getByText("Repository File")).toBeInTheDocument();
    expect(within(first).getByText("sample source")).toBeInTheDocument();
    expect(within(first).getByText("active")).toBeInTheDocument();
    expect(within(first).getByText("sample goodExamples")).toBeInTheDocument();
    expect(api.calls(SHOW).map((c) => c.body)).toEqual([{ rule_id: 1 }]);
  });

  it("still searches org-wide when the repo list cannot be read", async () => {
    setup({ "POST /verbs/config/status": { status: 503, body: envelope("environment_error", "bad settings") } });
    render(<Rules />);
    expect(await screen.findByText(/Repo scopes are not available: bad settings/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "All repos" })).toHaveAttribute("aria-pressed", "true");
  });
});
