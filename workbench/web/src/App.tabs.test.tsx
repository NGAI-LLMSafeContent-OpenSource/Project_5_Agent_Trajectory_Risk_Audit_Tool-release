import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import atbenchFixture from "../../../fixtures/workbench/review_bundle_atbench.json";
import hintbenchFixture from "../../../fixtures/workbench/review_bundle_hintbench.json";
import App from "./App";
import { okSample, renderWithSession, uploadFixture } from "./test/session";
import type { ReviewBundle } from "./types";

const atbench = atbenchFixture as ReviewBundle;
const hintbench = hintbenchFixture as ReviewBundle;

afterEach(() => {
  vi.unstubAllGlobals();
});

async function loadFixture(fixture: "atbench" | "hintbench") {
  const user = userEvent.setup();
  render(<App />);
  await uploadFixture(user, fixture);
  return user;
}

describe("Review tabs: Radix tab framework", () => {
  it("switches tabs from the keyboard", async () => {
    const user = await loadFixture("atbench");
    const stats = screen.getByRole("tab", { name: /数据统计/ });
    expect(stats).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByText(/这一页回答/)).not.toBeInTheDocument();

    stats.focus();
    await user.keyboard("{ArrowRight}");

    const cot = screen.getByRole("tab", { name: /思维链/ });
    expect(cot).toHaveFocus();
    expect(cot).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toContainElement(screen.getByTestId("tab-cot"));

    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: /工具调用/ })).toHaveAttribute("aria-selected", "true");
  });
});

describe("Missing evidence presentation", () => {
  it.each([
    ["atbench", atbench],
    ["hintbench", hintbench],
  ])("%s: keeps missing values inline without a missing-dimensions bar", async (_name, bundle) => {
    const user = await renderWithSession([okSample(bundle)]);
    for (const id of ["token-input", "token-output", "token-thinking"]) {
      expect(screen.getByTestId(id)).toHaveTextContent("未提供");
    }
    for (const name of [/数据统计/, /思维链/, /工具调用/]) {
      await user.click(screen.getByRole("tab", { name }));
      expect(screen.queryByRole("region", { name: "未提供的维度" })).not.toBeInTheDocument();
    }
  });

  it("keeps available dimensions as cards", async () => {
    await loadFixture("hintbench");
    const rate = screen.getByTestId("kpi-tool_success_rate");
    expect(rate).toHaveTextContent("83%");
    expect(rate).toHaveTextContent("成功 5 / 6");
  });

  it("keeps not-applicable token values inline", async () => {
    await renderWithSession([okSample({
      ...atbench,
      availability: {
        ...atbench.availability,
        token_usage: { status: "not_applicable", reason: "来源格式不含模型调用" },
      },
    })]);
    for (const id of ["token-input", "token-output", "token-thinking"]) {
      expect(screen.getByTestId(id)).toHaveTextContent("不适用");
      expect(screen.getByTestId(id)).toHaveTextContent("来源格式不含模型调用");
    }
  });
});

describe("Stats tab: prototype layout with observed values only", () => {
  it("shows the interaction overview from the bundle metrics", async () => {
    await loadFixture("hintbench");
    const value = (id: string) => within(screen.getByTestId(`kpi-${id}`)).getByRole("strong");

    expect(value("events")).toHaveTextContent(/^22$/);
    expect(value("tool_calls")).toHaveTextContent(/^6$/);
    expect(screen.getByTestId("kpi-tool_calls")).toHaveTextContent("3 种工具");
    expect(value("tool_results")).toHaveTextContent(/^6$/);
    expect(value("failed_results")).toHaveTextContent(/^1$/);
    expect(value("findings")).toHaveTextContent(/^1$/);
  });

  it("does not stand in 0 for a success count the bundle leaves out", async () => {
    const { success: _omitted, ...rate } = hintbench.availability.tool_success_rate;
    await renderWithSession([
      okSample({ ...hintbench, availability: { ...hintbench.availability, tool_success_rate: rate } }),
    ]);
    expect(screen.getByTestId("kpi-tool_success_rate")).toHaveTextContent("成功 未提供 / 6");
  });

  it("counts only declared errors as failures and unknown statuses as undeclared", async () => {
    const [first, ...rest] = hintbench.tool_interactions;
    await renderWithSession([
      okSample({
        ...hintbench,
        tool_interactions: [
          { ...first, result: { ...first.result!, declared_status: "unknown" } },
          ...rest,
        ],
      }),
    ]);

    const row = within(screen.getByRole("table", { name: "工具使用" })).getByRole("row", {
      name: /invite_user_to_slack/,
    });
    expect(within(row).getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
      "invite_user_to_slack",
      "2",
      "0",
      "1",
      "1",
      "0",
    ]);
  });

  it("breaks tool usage down per tool by declared result status", async () => {
    await loadFixture("hintbench");

    const table = screen.getByRole("table", { name: "工具使用" });
    const row = within(table).getByRole("row", { name: /invite_user_to_slack/ });
    expect(within(row).getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
      "invite_user_to_slack",
      "2",
      "1",
      "1",
      "0",
      "0",
    ]);
  });

  it("shows event composition and detected-finding categories", async () => {
    await loadFixture("hintbench");

    const events = screen.getByRole("region", { name: "事件构成" });
    expect(within(events).getByTestId("bar-thought")).toHaveTextContent("思考6");
    expect(within(events).queryByTestId("bar-state_change")).not.toBeInTheDocument();
    expect(within(events).queryByTestId("bar-unsupported_content")).not.toBeInTheDocument();
    const categories = screen.getByRole("region", { name: "检测发现类别" });
    expect(within(categories).getByTestId("bar-unauthorized_action")).toHaveTextContent("1");
  });

  it("shows a state-change row when that event type was counted", async () => {
    const events = hintbench.metrics.events ?? {};
    const byType = events.by_type;
    if (!byType || typeof byType !== "object") throw new Error("hintbench fixture has no by_type");
    await renderWithSession([
      okSample({
        ...hintbench,
        metrics: {
          ...hintbench.metrics,
          events: { ...events, by_type: { ...byType, state_change: 2 } },
        },
      }),
    ]);

    expect(within(screen.getByRole("region", { name: "事件构成" })).getByTestId("bar-state_change")).toHaveTextContent(
      "状态变化2",
    );
  });

  it("still draws a finding category whose count is zero", async () => {
    const audit = hintbench.metrics.audit ?? {};
    const categories = audit.categories;
    if (!categories || typeof categories !== "object") throw new Error("hintbench fixture has no categories");
    await renderWithSession([
      okSample({
        ...hintbench,
        metrics: {
          ...hintbench.metrics,
          audit: { ...audit, categories: { ...categories, fact_conflict: 0 } },
        },
      }),
    ]);

    expect(
      within(screen.getByRole("region", { name: "检测发现类别" })).getByTestId("bar-fact_conflict"),
    ).toHaveTextContent("fact_conflict0");
  });

  it("omits metrics the bundle leaves out instead of counting them itself", async () => {
    await renderWithSession([okSample({ ...atbench, metrics: { ...atbench.metrics, audit: {} } })]);

    expect(screen.queryByTestId("kpi-findings")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "检测发现类别" })).not.toBeInTheDocument();
  });
});
