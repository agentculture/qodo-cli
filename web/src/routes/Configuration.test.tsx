import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Configuration from "./Configuration";
import { CONFIG_STATUS, MISSING_REPO, NOT_CONFIGURED, OTHER_REPO, REPO } from "../fixtures/qodo";
import { type Answer, deferred, envelope, stubApi } from "../test/api";

afterEach(() => vi.unstubAllGlobals());

const STATUS = "POST /verbs/config/status";
const setup = (answer: Answer = { body: CONFIG_STATUS }) => stubApi({ [STATUS]: answer });

describe("the configuration screen", () => {
  it("asks config status once, with no params", async () => {
    const api = setup();
    render(<Configuration />);
    await screen.findByRole("region", { name: REPO });
    expect(api.calls(STATUS).map((c) => c.body)).toEqual([{}]);
    expect(screen.getByRole("heading", { level: 1, name: "Configuration" })).toBeInTheDocument();
    expect(document.title).toBe("Configuration - qodo");
  });

  it("shows each repo's files, validation, warnings and proposals, and no contents", async () => {
    setup();
    render(<Configuration />);
    const repo = await screen.findByRole("region", { name: REPO });
    expect(within(repo).getByText("Valid, 1 warning")).toBeInTheDocument();
    const files = within(repo).getByRole("list", { name: "Files Qodo reads" });
    const toml = within(files).getByText(".pr_agent.toml").closest("li") as HTMLElement;
    expect(toml).toHaveTextContent("Valid TOML");
    expect(toml).toHaveTextContent("61 bytes");
    expect(toml).toHaveTextContent("Sections: pr_reviewer, review_agent");
    expect(toml).toHaveTextContent("[pr_reviewer.extra_instructions] is not in Qodo's current configuration reference");
    const review = within(files).getByText("REVIEW.md").closest("li") as HTMLElement;
    expect(review).toHaveTextContent("Not present");
    const checks = within(repo).getByRole("list", { name: "Checks" });
    expect(within(checks).getByText("a Qodo reviewer config is present").closest("li")).toHaveTextContent(
      "Passed",
    );
    const proposals = within(repo).getByRole("list", { name: "Proposed changes" });
    expect(proposals).toHaveTextContent(".pr_agent.toml");
    expect(proposals).toHaveTextContent("through the API");
    // the canvas's "View file" link and its diff are not served: no file contents reach a viewer
    expect(screen.queryByRole("link", { name: /View file/ })).not.toBeInTheDocument();
  });

  it("says when the bridge has no checkout of a repo", async () => {
    setup();
    render(<Configuration />);
    const repo = await screen.findByRole("region", { name: OTHER_REPO });
    expect(within(repo).getByText("No checkout")).toBeInTheDocument();
    expect(repo).toHaveTextContent(/has no checkout of this repo/);
    expect(repo).toHaveTextContent("No change proposed through this bridge.");
  });

  it("names the failing checks with their remediation", async () => {
    setup({
      body: {
        configured: true,
        truncated: false,
        repos: [
          {
            ...MISSING_REPO,
            repo: REPO,
            checkout: "found",
            files: [{ path: ".pr_agent.toml", kind: "toml", present: true, bytes: 9, valid_toml: false, error: "Expected '='" }],
            validation: {
              valid: false,
              checks: [
                {
                  id: "pr_agent_valid_toml",
                  ok: false,
                  severity: "error",
                  message: ".pr_agent.toml is not valid TOML: Expected '='",
                  remediation: "fix the TOML syntax in .pr_agent.toml",
                },
              ],
            },
          },
        ],
      },
    });
    render(<Configuration />);
    const repo = await screen.findByRole("region", { name: REPO });
    expect(within(repo).getByText("Invalid, 1 problem")).toBeInTheDocument();
    const failed = within(repo).getByText(".pr_agent.toml is not valid TOML: Expected '='").closest("li");
    expect(failed).toHaveTextContent("Failed");
    expect(failed).toHaveTextContent("fix the TOML syntax in .pr_agent.toml");
    expect(within(repo).getByText(/Not valid TOML/)).toBeInTheDocument();
  });

  it("says how to configure the repos when there are none", async () => {
    setup({ body: NOT_CONFIGURED });
    render(<Configuration />);
    expect(await screen.findByText(/No repos are configured/)).toBeInTheDocument();
    expect(screen.getByText(/QODO_PROPOSE_REPOS/)).toBeInTheDocument();
  });

  it("says when more repos are allowlisted than one answer holds", async () => {
    setup({ body: { ...CONFIG_STATUS, truncated: true } });
    render(<Configuration />);
    expect(await screen.findByText(/first 20 repos/)).toBeInTheDocument();
  });

  it("shows loading, then an error with the message and a retry that works", async () => {
    const user = userEvent.setup();
    const held = deferred<{ status?: number; body: unknown }>();
    let n = 0;
    setup(() => {
      n += 1;
      return n === 1 ? held.promise : { body: CONFIG_STATUS };
    });
    render(<Configuration />);
    expect(screen.getByText("Loading the configuration…")).toBeInTheDocument();
    held.resolve({ status: 503, body: envelope("environment_error", "invalid config propose settings", "fix the settings file") });
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("invalid config propose settings");
    expect(alert).toHaveTextContent("fix the settings file");
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("region", { name: REPO })).toBeInTheDocument();
  });

  it("says a 403 needs another role", async () => {
    setup({ status: 403, body: envelope("forbidden", "role too low") });
    render(<Configuration />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/viewer role/);
  });
});
