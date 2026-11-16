import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import Findings from "./Findings";
import {
  CLOSED_FINDING,
  CONFIG_STATUS,
  HANDWRITTEN_PRS,
  NOT_CONFIGURED,
  NOT_REVIEWED,
  OPEN_FINDING,
  PR_URL,
  REPO,
  findingsPage,
} from "../fixtures/qodo";
import { type Answer, deferred, envelope, stubApi } from "../test/api";

afterEach(() => vi.unstubAllGlobals());

const FINDINGS = "POST /verbs/findings/list";
const DISMISS = "POST /verbs/findings/dismiss";
const IMPLEMENT = "POST /verbs/findings/implement";
const SELECTED = `/findings?pr=${encodeURIComponent(PR_URL)}`;

function setup(routes: Record<string, Answer> = {}) {
  return stubApi({
    "POST /verbs/config/status": { body: { ...CONFIG_STATUS, repos: [CONFIG_STATUS.repos[0]] } },
    "POST /verbs/prs/list": { body: HANDWRITTEN_PRS },
    [FINDINGS]: { body: findingsPage() },
    ...routes,
  });
}

function renderAt(path = "/findings") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/findings" element={<Findings />} />
      </Routes>
    </MemoryRouter>,
  );
}

const openCard = () =>
  screen.getByRole("article", { name: OPEN_FINDING.title as string });

/** Tab until `target` has focus (fails after 40 presses). */
async function tabTo(user: ReturnType<typeof userEvent.setup>, target: HTMLElement) {
  for (let i = 0; i < 40 && document.activeElement !== target; i += 1) await user.tab();
  expect(target).toHaveFocus();
}

const result = () => screen.getByRole("status", { name: "Result of the last change" });

describe("the pull request list", () => {
  it("lists each allowlisted repo's open pull requests from prs list", async () => {
    const api = setup();
    renderAt();
    expect(screen.getByRole("heading", { level: 1, name: "Findings" })).toBeInTheDocument();
    const repo = await screen.findByRole("region", { name: REPO });
    expect(
      await within(repo).findByRole("link", { name: "#1 Button codes are used once" }),
    ).toHaveAttribute("href", SELECTED);
    expect(within(repo).getByRole("link", { name: "#2 Keyset list paging" })).toBeInTheDocument();
    expect(api.calls("POST /verbs/prs/list").map((c) => c.body)).toEqual([
      { repo: REPO, state: "open", max_results: 50 },
    ]);
    expect(document.title).toBe("Findings - qodo");
  });

  it("filters the list by repo, number or title", async () => {
    const user = userEvent.setup();
    setup();
    renderAt();
    await screen.findByRole("link", { name: /Keyset list paging/ });
    await user.type(screen.getByRole("searchbox", { name: "Find a pull request" }), "keyset");
    expect(screen.queryByRole("link", { name: /Button codes/ })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Keyset list paging/ })).toBeInTheDocument();
  });

  it("opens a pasted pull request URL", async () => {
    const user = userEvent.setup();
    const url = "https://github.com/someone/else/pull/9";
    const api = setup();
    renderAt();
    await user.type(screen.getByRole("searchbox", { name: "Find a pull request" }), `${url}{Enter}`);
    expect(await screen.findByRole("heading", { level: 1, name: "Pull request #9" })).toBeInTheDocument();
    expect(api.calls(FINDINGS)[0].body).toEqual({ pr_url: url, offset: 0, limit: 50 });
  });

  it("says when no repo is configured, and still takes a pasted URL", async () => {
    setup({ "POST /verbs/config/status": { body: NOT_CONFIGURED } });
    renderAt();
    expect(await screen.findByText(/No repos are configured/)).toBeInTheDocument();
    expect(screen.getByText(/QODO_PROPOSE_REPOS/)).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Find a pull request" })).toBeEnabled();
  });

  it("shows a repo's error with the server's message and a retry", async () => {
    const user = userEvent.setup();
    setup({
      "POST /verbs/prs/list": (_b, n) =>
        n === 1
          ? { status: 503, body: envelope("environment_error", "no Qodo API key found", "export QODO_API_KEY") }
          : { body: HANDWRITTEN_PRS },
    });
    renderAt();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("no Qodo API key found");
    expect(alert).toHaveTextContent("export QODO_API_KEY");
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("link", { name: /Button codes/ })).toBeInTheDocument();
  });

  it("says when Qodo's reply is not a list it can read (the reply shape is unverified)", async () => {
    setup({ "POST /verbs/prs/list": { body: { something: "else" } } });
    renderAt();
    expect(await screen.findByText(/could not be read as a list of pull requests/)).toBeInTheDocument();
  });

  it("says when a repo has no open pull request", async () => {
    setup({ "POST /verbs/prs/list": { body: { pull_requests: [] } } });
    renderAt();
    expect(await screen.findByText("No open pull requests.")).toBeInTheDocument();
  });
});

describe("one pull request's findings", () => {
  it("shows the review, the counts and each finding with its recorded fields", async () => {
    setup();
    renderAt(SELECTED);
    expect(
      await screen.findByRole("heading", { level: 1, name: "Button codes are used once" }),
    ).toBeInTheDocument();
    expect(screen.getByText(`${REPO} · #1`)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open on GitHub" })).toHaveAttribute("href", PR_URL);
    expect(screen.getByText(/Qodo's latest review \(completed\) looked at commit/)).toHaveTextContent(
      "0000000",
    );
    const tiles = screen.getByRole("list", { name: "Findings by status" });
    expect(within(tiles).getByText("Open").previousSibling).toHaveTextContent("1");
    expect(within(tiles).getByText("Implemented").previousSibling).toHaveTextContent("1");
    expect(within(tiles).getByText("Dismissed").previousSibling).toHaveTextContent("0");
    const open = openCard();
    expect(within(open).getByText("Maintainability")).toBeInTheDocument();
    expect(within(open).getByText("Remediation recommended")).toBeInTheDocument();
    expect(within(open).getByText("Status: Open")).toBeInTheDocument();
    expect(within(open).getByText(OPEN_FINDING.description as string)).toBeInTheDocument();
    expect(within(open).getByRole("button", { name: "Mark implemented" })).toBeInTheDocument();
    expect(within(open).getByRole("button", { name: "Dismiss" })).toBeInTheDocument();
  });

  it("offers no change on a closed finding", async () => {
    setup();
    renderAt(SELECTED);
    const closed = await screen.findByRole("article", { name: CLOSED_FINDING.title as string });
    expect(within(closed).getByText("Status: Implemented")).toBeInTheDocument();
    expect(within(closed).queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows a loading state, then the findings", async () => {
    const held = deferred<{ body: unknown }>();
    setup({ [FINDINGS]: () => held.promise });
    renderAt(SELECTED);
    expect(await screen.findByText("Loading Qodo's findings…")).toBeInTheDocument();
    held.resolve({ body: findingsPage() });
    expect(await screen.findByRole("article", { name: OPEN_FINDING.title as string })).toBeInTheDocument();
  });

  it("says when Qodo has not reviewed the pull request yet", async () => {
    setup({ [FINDINGS]: { body: NOT_REVIEWED } });
    renderAt(SELECTED);
    expect(await screen.findByText("Qodo has not reviewed this pull request yet.")).toBeInTheDocument();
  });

  it("shows a failed read with the message and a retry", async () => {
    const user = userEvent.setup();
    setup({
      [FINDINGS]: (_b, n) =>
        n === 1
          ? { status: 400, body: envelope("user_error", "get-pr-review-findings failed: bad url", "check the URL") }
          : { body: findingsPage() },
    });
    renderAt(SELECTED);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("get-pr-review-findings failed: bad url");
    expect(alert).toHaveTextContent("check the URL");
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("article", { name: OPEN_FINDING.title as string })).toBeInTheDocument();
  });
});

describe("changing a finding", () => {
  it("works by keyboard alone: tab to a PR, open it, dismiss with a reason, hear the result", async () => {
    const user = userEvent.setup();
    const api = setup({
      [DISMISS]: { body: { ok: true } },
      [FINDINGS]: (_b, n) =>
        ({
          body:
            n === 1
              ? findingsPage()
              : findingsPage([{ ...OPEN_FINDING, attribution_status: "dismissed" }, CLOSED_FINDING]),
        }),
    });
    renderAt();
    await tabTo(user, await screen.findByRole("link", { name: "#1 Button codes are used once" }));
    await user.keyboard("{Enter}");
    const card = await screen.findByRole("article", { name: OPEN_FINDING.title as string });
    await tabTo(user, within(card).getByRole("button", { name: "Dismiss" }));
    await user.keyboard("{Enter}");

    const dialog = screen.getByRole("dialog", { name: "Dismiss this finding?" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveTextContent(OPEN_FINDING.title as string);
    expect(dialog).toHaveTextContent(`${REPO} #1`);
    const confirm = within(dialog).getByRole("button", { name: "Dismiss finding" });
    expect(confirm).toBeDisabled(); // no reason chosen yet
    // focus starts on the first reason; the reasons are one radio group, walked with the arrows
    expect(within(dialog).getByRole("radio", { name: /False positive/ })).toHaveFocus();
    await user.keyboard("{ArrowDown}{ArrowDown}");
    expect(within(dialog).getByRole("radio", { name: /Deferred/ })).toBeChecked();
    expect(within(dialog).getByRole("radio", { name: /Deferred/ })).toHaveFocus();
    await tabTo(user, confirm);
    await user.keyboard("{Enter}");

    await waitFor(() => expect(result()).toHaveTextContent(/Dismissed .* as deferred/));
    expect(api.calls(DISMISS).map((c) => c.body)).toEqual([
      { finding_ids: [OPEN_FINDING.id], reason: "deferred" },
    ]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await waitFor(() => expect(api.calls(FINDINGS)).toHaveLength(2)); // refetched, never assumed
    const after = await screen.findByRole("article", { name: OPEN_FINDING.title as string });
    expect(await within(after).findByText("Status: Dismissed")).toBeInTheDocument();
  });

  it("marks a finding implemented with exactly its id, after a confirmation naming it", async () => {
    const user = userEvent.setup();
    const api = setup({ [IMPLEMENT]: { body: { anything: "unread" } } });
    renderAt(SELECTED);
    await user.click(await within(await screen.findByRole("article", { name: OPEN_FINDING.title as string })).findByRole("button", { name: "Mark implemented" }));
    const dialog = screen.getByRole("dialog", { name: "Mark this finding implemented?" });
    expect(dialog).toHaveTextContent(OPEN_FINDING.title as string);
    expect(dialog).toHaveTextContent(/merge-policy block/);
    expect(api.calls(IMPLEMENT)).toHaveLength(0); // nothing is sent before the confirmation
    await user.click(within(dialog).getByRole("button", { name: "Mark implemented" }));
    await waitFor(() => expect(result()).toHaveTextContent(/Marked .* implemented/));
    expect(api.calls(IMPLEMENT).map((c) => c.body)).toEqual([{ finding_ids: [OPEN_FINDING.id] }]);
  });

  it("disables the dialog while the write runs and does not close on Escape", async () => {
    const user = userEvent.setup();
    const held = deferred<{ body: unknown }>();
    setup({ [IMPLEMENT]: () => held.promise });
    renderAt(SELECTED);
    await user.click(await within(await screen.findByRole("article", { name: OPEN_FINDING.title as string })).findByRole("button", { name: "Mark implemented" }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Mark implemented" }));
    expect(within(dialog).getByRole("button", { name: "Marking…" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(result()).toHaveTextContent(""); // no success shown before the server answers
    held.resolve({ body: {} });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("shows a failed write's message and remediation and changes nothing", async () => {
    const user = userEvent.setup();
    const api = setup({
      [DISMISS]: {
        status: 503,
        body: envelope("environment_error", "Qodo MCP server answered HTTP 500", "try again, or check Qodo's status"),
      },
    });
    renderAt(SELECTED);
    await user.click(await within(await screen.findByRole("article", { name: OPEN_FINDING.title as string })).findByRole("button", { name: "Dismiss" }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("radio", { name: /False positive/ }));
    await user.click(within(dialog).getByRole("button", { name: "Dismiss finding" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("Qodo MCP server answered HTTP 500");
    expect(alert).toHaveTextContent("try again, or check Qodo's status");
    expect(api.calls(FINDINGS)).toHaveLength(1); // no refetch, nothing assumed
    expect(within(openCard()).getByText("Status: Open")).toBeInTheDocument();
    expect(result()).toHaveTextContent("");
    expect(within(dialog).getByRole("button", { name: "Dismiss finding" })).toBeEnabled();
  });

  it("says a viewer needs the editor role on a 403", async () => {
    const user = userEvent.setup();
    setup({
      [IMPLEMENT]: { status: 403, body: envelope("forbidden", "role viewer may not POST /verbs/findings/implement") },
    });
    renderAt(SELECTED);
    await user.click(await within(await screen.findByRole("article", { name: OPEN_FINDING.title as string })).findByRole("button", { name: "Mark implemented" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Mark implemented" }));
    const alert = await within(screen.getByRole("dialog")).findByRole("alert");
    expect(alert).toHaveTextContent("needs the editor role");
  });

  it("closes on Escape and returns focus to the button that opened it; Tab stays inside", async () => {
    const user = userEvent.setup();
    setup();
    renderAt(SELECTED);
    const card = await screen.findByRole("article", { name: OPEN_FINDING.title as string });
    const dismiss = within(card).getByRole("button", { name: "Dismiss" });
    await user.click(dismiss);
    const dialog = screen.getByRole("dialog");
    expect(dialog.contains(document.activeElement)).toBe(true);
    for (let i = 0; i < 12; i += 1) {
      await user.tab();
      expect(dialog.contains(document.activeElement)).toBe(true);
    }
    await user.tab({ shift: true });
    expect(dialog.contains(document.activeElement)).toBe(true);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(dismiss).toHaveFocus();
  });
});
