import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import fixture from "../../../fixtures/workbench/pujiang_race_session.json";
import App from "./App";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

function stubSession(payload: unknown = fixture) {
  const fetchMock = vi.fn(async () => ({ ok: true, json: async () => payload }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function uploadSession(payload: unknown = fixture) {
  stubSession(payload);
  const user = userEvent.setup();
  render(<App />);
  await user.upload(screen.getByLabelText("选择 JSONL 文件"), new File(["{}"], "batch.jsonl"));
  await screen.findByTestId("bundle-view");
  return user;
}

async function openSummary(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /^汇总/ }));
  return screen.getByTestId("batch-summary");
}

it("keeps thinking availability beside character counts without a missing-step column or explanatory footnotes", async () => {
  const payload = structuredClone(fixture);
  const partialRace = payload.samples[0].bundle.race;
  const absentRace = payload.samples[1].bundle.race;
  const user = await uploadSession(payload);
  const summary = await openSummary(user);
  const table = within(summary).getByRole("table", { name: "各轨迹统计" });
  expect(within(table).queryByRole("columnheader", { name: "缺正文步骤" })).not.toBeInTheDocument();
  const rows = within(table).getAllByRole("row").slice(1);
  const partial = within(rows[0]).getAllByRole("cell")[3];
  expect(partial).toHaveTextContent(String(partialRace.A2.known_characters));
  expect(partial).toHaveTextContent("部分正文未提供");
  const absent = within(rows[1]).getAllByRole("cell")[3];
  expect(absent).toHaveTextContent(/^未提供思考正文$/);
  expect(within(summary).getByRole("status")).toHaveTextContent(/^显示 2 \/ 2 条$/);
  expect(within(summary).getByRole("heading", { name: "全批次各工具调用次数" })).toBeInTheDocument();
  const statistics = within(summary).getByRole("region", { name: "各轨迹统计" });
  const toolTotals = within(summary).getByRole("region", { name: "全批次各工具调用次数" });
  expect(statistics.compareDocumentPosition(toolTotals) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(summary).not.toHaveTextContent("接收 2 条 · 本次选中 2 条");
  expect(summary).not.toHaveTextContent("当前批次已接入的轨迹");
  expect(summary).not.toHaveTextContent("统计本批次全部轨迹的调用记录");
  expect(summary).not.toHaveTextContent("按原始工具名称合并");
  expect(summary).not.toHaveTextContent("审计失败的轨迹保留已解析统计");
  expect(summary).not.toHaveTextContent("样本 / 汇总");
  expect(summary).not.toHaveTextContent("输入 / 输出 token 取自来源记录");
  expect(summary).not.toHaveTextContent("汇总范围始终为本批次全部轨迹");
  expect(partialRace.A2.absent_body_step_ids).toEqual(fixture.samples[0].bundle.race.A2.absent_body_step_ids);
  expect(absentRace.A2.absent_body_step_ids).toEqual(fixture.samples[1].bundle.race.A2.absent_body_step_ids);
});

it("filters by tool and verdict without changing batch totals, and recovers from no results", async () => {
  const user = await uploadSession();
  const summary = await openSummary(user);
  const table = within(summary).getByRole("table", { name: "各轨迹统计" });
  const first = within(table).getAllByRole("row")[1];
  expect(first).toHaveTextContent("1,200");
  expect(within(first).getAllByRole("cell")[2]).toHaveTextContent(/^0$/);

  await user.click(within(summary).getByRole("button", { name: /Read\s*1/ }));
  expect(within(table).getAllByRole("row")).toHaveLength(2);
  expect(within(summary).queryByRole("button", { name: "race-b" })).not.toBeInTheDocument();
  expect(new URLSearchParams(window.location.search).get("summary_tool")).toBe("Read");
  expect(within(summary).getByTestId("aggregate-tool-calls")).toHaveTextContent("4");

  await user.click(within(summary).getByRole("button", { name: /无风险\s*0/ }));
  expect(within(summary).getByRole("status")).toHaveTextContent("显示 0 / 2 条");
  await user.click(within(summary).getByRole("button", { name: "清除筛选" }));
  expect(within(table).getAllByRole("row")).toHaveLength(3);
  expect(window.location.search).not.toContain("summary_tool");
});

it("keeps failures and unknown verdicts distinct and includes rows without statistics", async () => {
  const user = await uploadSession({
    ...fixture,
    samples: [
      fixture.samples[0],
      { sample_id: "provider-failed", ok: false, bundle: fixture.samples[1].bundle },
      { sample_id: "missing-bundle", ok: false },
      { ...fixture.samples[1], sample_id: "undetermined", bundle: {
        ...fixture.samples[1].bundle, verdict: { value: "undetermined" },
      } },
      { ...fixture.samples[1], sample_id: "legacy", bundle: {
        ...fixture.samples[1].bundle, race: undefined, verdict: { value: "safe" },
      } },
    ],
  });
  const summary = await openSummary(user);
  const table = within(summary).getByRole("table");
  expect(within(table).getAllByRole("row")).toHaveLength(6);
  expect(within(table).getByRole("row", { name: /missing-bundle/ })).toHaveTextContent("审计失败");
  expect(within(within(table).getByRole("row", { name: /missing-bundle/ })).getAllByText("未提供")).toHaveLength(5);
  expect(within(table).getByRole("row", { name: /provider-failed/ })).toHaveTextContent("审计失败");
  expect(within(table).getByRole("row", { name: /legacy/ })).toHaveTextContent("无风险");
  await user.click(within(summary).getByRole("button", { name: /未判定\s*1/ }));
  expect(within(table).getAllByRole("row")).toHaveLength(2);
  expect(within(table).getByRole("row", { name: /undetermined/ })).toBeInTheDocument();
});

it("restores the summary and filters on reload, then returns to a normal sample URL", async () => {
  window.history.replaceState(null, "", `/?session_id=${fixture.session_id}&sample_id=race-b&view=summary&summary_tool=Read`);
  const fetchMock = stubSession();
  const user = userEvent.setup();
  const first = render(<App />);
  let summary = await screen.findByTestId("batch-summary");
  expect(within(summary).getByRole("table").querySelectorAll("tbody tr")).toHaveLength(1);
  expect(screen.getByRole("button", { name: /^汇总/ })).toHaveAttribute("aria-current", "page");
  expect(screen.getByTestId("sample-item-race-b")).not.toHaveAttribute("aria-current");
  await user.click(within(summary).getByRole("button", { name: /有风险\s*2/ }));
  first.unmount();
  render(<App />);
  summary = await screen.findByTestId("batch-summary");
  expect(within(summary).getByRole("button", { name: /有风险\s*2/ })).toHaveAttribute("aria-pressed", "true");
  expect(within(summary).getByRole("button", { name: /Read\s*1/ })).toHaveAttribute("aria-pressed", "true");
  await user.click(within(summary).getByRole("button", { name: "返回当前样本 →" }));
  expect(screen.getByTestId("sample-item-race-b")).toHaveAttribute("aria-current", "true");
  expect(screen.queryByTestId("batch-summary")).not.toBeInTheDocument();
  expect(window.location.search).not.toMatch(/view=|summary_tool|summary_status/);
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

it("preserves the current sample tab when visiting the summary and returning", async () => {
  const user = await uploadSession();
  await user.click(screen.getByRole("tab", { name: /思维链/ }));
  const summary = await openSummary(user);
  expect(screen.queryByRole("tablist")).not.toBeInTheDocument();
  await user.click(within(summary).getByRole("button", { name: "返回当前样本 →" }));
  expect(screen.getByRole("tab", { name: /思维链/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("main")).toHaveFocus();
});

it("does not offer unavailable aggregate data for an old session", async () => {
  await uploadSession({ ...fixture, aggregate: undefined });
  expect(screen.queryByRole("button", { name: /^汇总/ })).not.toBeInTheDocument();
  expect(screen.getByTestId("race-stats")).toBeInTheDocument();
});

it("clears summary filters after uploading another batch", async () => {
  const user = await uploadSession();
  const summary = await openSummary(user);
  await user.click(within(summary).getByRole("button", { name: /Read\s*1/ }));
  await user.click(screen.getByRole("button", { name: "上传轨迹" }));
  const dialog = screen.getByRole("dialog", { name: "上传轨迹" });
  await user.upload(within(dialog).getByLabelText("选择 JSONL 文件"), new File(["{}"], "next.jsonl"));
  expect(screen.queryByTestId("batch-summary")).not.toBeInTheDocument();
  expect(window.location.search).not.toMatch(/view=|summary_tool/);
  expect(within(await openSummary(user)).getByRole("table").querySelectorAll("tbody tr")).toHaveLength(2);
});
