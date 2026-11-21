import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import atbenchFixture from "../../../../fixtures/workbench/review_bundle_atbench.json";
import unlocatedFixture from "../../../../fixtures/workbench/review_bundle_unlocated.json";
import type { ReviewBundle } from "../types";
import { BundleView } from "./BundleView";

const atbench = atbenchFixture as ReviewBundle;
const unlocated = unlocatedFixture as ReviewBundle;

describe("Gate B: Finding navigation, evidence, and tool expand", () => {
  it("navigates from a Detected Finding to the evidence event and marks focus", async () => {
    const user = userEvent.setup();
    render(<BundleView bundle={atbench} sourceOpen={false} onSelectSourceEvent={() => undefined} />);

    await user.click(screen.getByRole("tab", { name: /思维链/ }));
    await user.click(screen.getByTestId("finding-fnd-0"));

    const focused = screen.getByTestId("event-evt-0004");
    expect(focused).toHaveAttribute("data-focused", "true");
    expect(screen.getByTestId("focus-banner")).toHaveTextContent("evt-0004");
    expect(screen.getByTestId("evidence-mark")).toHaveTextContent('"The to');
  });

  it("shows an explicit location-failure state when a finding cannot be bound", async () => {
    const user = userEvent.setup();
    render(<BundleView bundle={unlocated} sourceOpen={false} onSelectSourceEvent={() => undefined} />);

    await user.click(screen.getByRole("tab", { name: /思维链/ }));
    await user.click(screen.getByTestId("finding-fnd-unlocated"));

    expect(screen.getByTestId("location-failure")).toHaveTextContent(/定位失败/);
    expect(screen.queryByTestId("focus-banner")).not.toBeInTheDocument();
    expect(screen.queryByTestId("evidence-mark")).not.toBeInTheDocument();
    for (const event of screen.getAllByTestId(/^event-/)) {
      expect(event).not.toHaveAttribute("data-focused", "true");
    }
  });

  it("expands tool arguments and returns with the full payload, not a truncated preview", async () => {
    const user = userEvent.setup();
    render(<BundleView bundle={atbench} sourceOpen={false} onSelectSourceEvent={() => undefined} />);

    await user.click(screen.getByRole("tab", { name: /工具调用/ }));
    const card = screen.getByTestId("tool-card-evt-0002");

    const args = within(card).getByTestId("tool-args-evt-0002");
    expect(args).not.toHaveAttribute("open");
    await user.click(within(args).getByText(/参数/));
    expect(args).toHaveAttribute("open");
    expect(within(args).getByTestId("tool-args-body-evt-0002")).toHaveTextContent(
      "prod_signing_key",
    );
    expect(within(args).getByTestId("tool-args-body-evt-0002").textContent).not.toMatch(/…$/);

    const result = within(card).getByTestId("tool-result-evt-0002");
    await user.click(within(result).getByText(/返回/));
    const body = within(result).getByTestId("tool-result-body-evt-0002");
    expect(body).toHaveTextContent("PERMISSION_DENIED");
    expect(body).toHaveTextContent(
      "Access to unencrypted production secrets is restricted by security policy.",
    );
    expect(body.textContent).not.toMatch(/…$/);
  });
});
