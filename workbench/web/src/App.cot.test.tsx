import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import atbenchFixture from "../../../fixtures/workbench/review_bundle_atbench.json";
import hintbenchFixture from "../../../fixtures/workbench/review_bundle_hintbench.json";
import unlocatedFixture from "../../../fixtures/workbench/review_bundle_unlocated.json";
import App from "./App";
import { okSample, renderWithSession, uploadFixture } from "./test/session";
import type { ReviewBundle } from "./types";

const atbench = atbenchFixture as ReviewBundle;
const hintbench = hintbenchFixture as ReviewBundle;
const unlocated = unlocatedFixture as ReviewBundle;

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function openCot(fixture: "atbench" | "hintbench") {
  const user = userEvent.setup();
  render(<App />);
  await uploadFixture(user, fixture);
  await user.click(screen.getByRole("tab", { name: /思维链/ }));
  return user;
}

function stream() {
  return screen.getByRole("list", { name: "思考与交互全文" });
}

function findingsPanel() {
  return screen.getByRole("region", { name: "检测发现" });
}

describe("CoT tab: event stream", () => {
  it("lists every event in order with its kind, tool calls and returns included", async () => {
    await openCot("atbench");

    const items = within(stream()).getAllByRole("listitem");
    expect(items.map((item) => item.getAttribute("data-testid"))).toEqual([
      "event-evt-0001",
      "event-evt-0002",
      "event-evt-0003",
      "event-evt-0004",
    ]);
    expect(items[0]).toHaveTextContent("用户");
    expect(items[0]).toHaveTextContent("SYSTEM OVERRIDE");
    expect(items[1]).toHaveTextContent("工具调用");
    expect(items[1]).toHaveTextContent('read_vault_by_name({"name":"prod_signing_key"})');
    expect(items[2]).toHaveTextContent("工具返回");
    expect(items[2]).toHaveTextContent("PERMISSION_DENIED");
    expect(items[3]).toHaveTextContent("答复");
  });

  it("labels system prompts and reasoning separately from replies", async () => {
    await openCot("hintbench");

    expect(within(stream()).getByTestId("event-evt-0001")).toHaveTextContent("系统提示");
    expect(within(stream()).getByTestId("event-evt-0003")).toHaveTextContent("思考");
  });

  it("collapses the system prompt to a preview that expands to the full text", async () => {
    const user = await openCot("hintbench");
    const body = within(stream()).getByTestId("event-body-evt-0001");

    expect(body).not.toHaveAttribute("open");
    await user.click(within(body).getByText(/^You are an AI assistant.*…$/));
    expect(body).toHaveAttribute("open");
    expect(within(body).getByText("收起系统提示")).toBeInTheDocument();
  });

  it("does not draw a risk path bar", async () => {
    await openCot("hintbench");
    expect(screen.queryByText(/风险溯源链/)).not.toBeInTheDocument();
    expect(screen.queryByText(/风险路径/)).not.toBeInTheDocument();
  });

  it("summarises the observable reasoning in the side panel from the bundle metrics", async () => {
    await openCot("hintbench");
    const overview = screen.getByRole("region", { name: "思考概况" });
    expect(within(overview).getByTestId("cot-thought-count")).toHaveTextContent(/^思考段落6$/);
    expect(within(overview).getByTestId("cot-message-count")).toHaveTextContent(/^消息4$/);
  });

  it("shows 未提供 in the overview when the bundle has no event composition", async () => {
    const user = await renderWithSession([
      okSample({ ...hintbench, metrics: { ...hintbench.metrics, events: { total: 22 } } }),
    ]);
    await user.click(screen.getByRole("tab", { name: /思维链/ }));

    const overview = screen.getByRole("region", { name: "思考概况" });
    expect(within(overview).getByTestId("cot-thought-count")).toHaveTextContent(/^思考段落未提供$/);
    expect(within(overview).getByTestId("cot-message-count")).toHaveTextContent(/^消息未提供$/);
  });
});

describe("CoT tab: Detected Finding side panel", () => {
  it("jumps to the evidence event, marks it focused and highlights the excerpt", async () => {
    const scrollIntoView = vi.spyOn(Element.prototype, "scrollIntoView");
    const user = await openCot("atbench");

    await user.click(within(findingsPanel()).getByTestId("finding-fnd-0"));

    const event = within(stream()).getByTestId("event-evt-0004");
    expect(event).toHaveAttribute("data-focused", "true");
    expect(event).toHaveTextContent("当前焦点");
    expect(within(event).getByTestId("evidence-mark")).toHaveTextContent('"The to');
    expect(screen.getByTestId("focus-banner")).toHaveTextContent("evt-0004");
    expect(scrollIntoView).toHaveBeenCalled();
  });

  it("links each event to the findings that reference it", async () => {
    const user = await openCot("atbench");

    const origin = within(stream()).getByTestId("event-evt-0001");
    await user.click(within(origin).getByRole("button", { name: "fnd-0" }));

    expect(within(stream()).getByTestId("event-evt-0004")).toHaveAttribute("data-focused", "true");
    expect(within(stream()).getByTestId("event-evt-0002")).not.toHaveTextContent("fnd-0");
  });

  it("does not link events through evidence or findings the bundle marks as not located", async () => {
    const [finding] = atbench.detected_findings;
    const user = await renderWithSession([
      okSample({
        ...atbench,
        detected_findings: [
          {
            ...finding,
            origin_event_ids: [],
            affected_event_ids: [],
            evidence: [{ ...finding.evidence![0], event_id: "evt-0002", located: false }],
          },
          { ...finding, finding_id: "fnd-x", located: false },
        ],
      }),
    ]);
    await user.click(screen.getByRole("tab", { name: /思维链/ }));

    expect(within(stream()).queryAllByRole("button", { name: /^fnd-/ })).toHaveLength(0);
  });

  it("shows a location failure with no focus or highlight when a finding cannot be bound", async () => {
    const user = await renderWithSession([okSample(unlocated)]);
    await user.click(screen.getByRole("tab", { name: /思维链/ }));

    await user.click(within(findingsPanel()).getByTestId("finding-fnd-unlocated"));

    expect(screen.getByTestId("location-failure")).toHaveTextContent("定位失败");
    expect(screen.queryByTestId("focus-banner")).not.toBeInTheDocument();
    expect(screen.queryByTestId("evidence-mark")).not.toBeInTheDocument();
    for (const item of within(stream()).getAllByRole("listitem")) {
      expect(item).toHaveAttribute("data-focused", "false");
    }
  });
});
