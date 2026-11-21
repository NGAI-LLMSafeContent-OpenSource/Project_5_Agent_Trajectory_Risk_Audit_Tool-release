import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import session from "../../../fixtures/workbench/pujiang_race_session.json";
import App from "./App";
import { RaceStats } from "./components/RaceStats";
import type { RaceItems } from "./types";

afterEach(() => { vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

it.each([
  { characters: 724, missing: false, saved: true, value: "724", note: "含空格和换行" },
  { characters: 724, missing: true, saved: true, value: "724", note: "仅统计已保存正文，部分正文未提供" },
  { characters: 0, missing: true, saved: false, value: "未提供思考正文", note: "" },
  { characters: 0, missing: false, saved: false, value: "0", note: "含空格和换行" },
])("distinguishes saved, partial, absent and zero thinking characters: $value / $note", ({ characters, missing, saved, value, note }) => {
  const race = structuredClone(session.samples[0].bundle.race) as RaceItems;
  race.A2 = { known_characters: characters, absent_body_step_ids: missing ? [3] : [] };
  if (!saved) race.B1.segments = [];
  render(<RaceStats race={race} />);
  const metric = screen.getByTestId("race-thinking-characters");
  expect(metric.querySelector("strong")).toHaveTextContent(value);
  expect(metric.querySelector("span")?.textContent).toBe(note);
});

async function uploadRaceSession() {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => (String(url).includes("source-trajectory")
    ? { ok: true, json: async () => ({ ok: false }) }
    : { ok: true, json: async () => session })));
  const user = userEvent.setup();
  render(<App />);
  await user.upload(screen.getByLabelText("选择 JSONL 文件"), new File(["{}"], "samples.jsonl", { type: "application/x-ndjson" }));
  await screen.findByTestId("bundle-view");
  return user;
}

it("shows per-trajectory statistics from the backend, keeping a real zero distinct from 未提供", async () => {
  await uploadRaceSession();
  const stats = screen.getByTestId("race-stats");
  expect(within(stats).getByTestId("race-input-tokens")).toHaveTextContent("1200");
  expect(within(stats).getByTestId("race-output-tokens")).toHaveTextContent("0");
  expect(within(stats).getByTestId("race-thinking-characters")).toHaveTextContent("724");
  expect(within(stats).getByTestId("race-thinking-characters")).toHaveTextContent("仅统计已保存正文，部分正文未提供");
  expect(within(stats).queryByTestId("race-thinking-absent")).not.toBeInTheDocument();
  expect(within(stats).queryByTestId("token-thinking")).not.toBeInTheDocument();
  expect(stats.querySelectorAll(".k")).toHaveLength(5);
  expect(within(stats).getByTestId("race-tool-calls")).toHaveTextContent("3");
  expect(within(stats).getByTestId("race-tool-kinds")).toHaveTextContent("2");
  expect(within(stats).queryByRole("table")).not.toBeInTheDocument();
  const tools = within(screen.getByTestId("tab-stats")).getAllByRole("table", { name: "工具使用" });
  expect(tools).toHaveLength(1);
  expect(within(tools[0]).getAllByRole("row").slice(1).map((row) =>
    within(row).getAllByRole("cell").slice(0, 2).map((cell) => cell.textContent),
  )).toEqual([["bash", "2"], ["Read", "1"]]);
});

it("shows saved thinking in source order, in full, with shared steps, absent steps and a jump to the original", async () => {
  const user = await uploadRaceSession();
  await user.click(screen.getByRole("tab", { name: /思维链/ }));
  const thinking = screen.getByTestId("race-thinking");
  const segments = within(thinking).getAllByTestId(/^race-segment-/);
  expect(segments.map((segment) => segment.querySelector("summary")?.textContent)).toEqual([
    expect.stringContaining("步骤 1、2"), expect.stringContaining("步骤 4"),
  ]);
  const full = session.samples[0].bundle.race.B1.segments[0].text;
  await user.click(within(segments[0]).getByText(/步骤 1、2/));
  expect(within(segments[0]).getByTestId("race-thinking-text").textContent).toBe(full);
  expect(within(thinking).getByText("部分正文未提供")).toBeInTheDocument();
  expect(within(thinking).queryByText(/未提供思考正文：步骤/)).not.toBeInTheDocument();

  await user.click(within(segments[0].closest("li")!).getByRole("button", { name: "查看原文" }));
  expect(screen.getByTestId("source-trajectory-event")).toHaveTextContent("evt-0003");
});

it("maps only the Provider verdict and states why first-risk, redundancy and failure items are absent", async () => {
  const user = await uploadRaceSession();
  await user.click(screen.getByRole("tab", { name: /思维链/ }));
  expect(screen.getByTestId("race-item-B2")).toHaveTextContent("有风险");
  expect(screen.getByTestId("race-item-B3")).toHaveTextContent("未提供");
  expect(screen.getByTestId("race-item-B3")).toHaveTextContent("算法尚未输出该项结果");
});

it("shows every return per call, keeps unmatched returns, and indexes integrity issues with jumps to the original", async () => {
  const user = await uploadRaceSession();
  await user.click(screen.getByRole("tab", { name: /工具调用/ }));
  const race = session.samples[0].bundle.race;
  const second = screen.getByTestId(`tool-card-${race.C1.calls[1].event_id}`);
  expect(within(second).getByTestId("tool-step")).toHaveTextContent("步骤 2");
  expect(within(second).getAllByTestId(/^tool-result-body-/).map((node) => node.textContent))
    .toEqual(["part one", "part two ...[truncated 120 chars]"]);

  const unmatched = screen.getByTestId("race-unmatched");
  expect(unmatched).toHaveTextContent("messages[6]");
  expect(unmatched).toHaveTextContent("ghost");
  expect(unmatched).toHaveTextContent("orphan return");

  const integrity = screen.getByTestId("race-integrity");
  expect(within(integrity).getByTestId("race-completion")).toHaveTextContent("false");
  expect(within(integrity).getByTestId("race-truncations")).toHaveTextContent("步骤 2 · messages[5]");
  expect(within(integrity).getByTestId("race-missing-fields")).toHaveTextContent("无");

  const issues = screen.getByTestId("race-issues");
  expect(within(issues).getByTestId("race-pending-C2")).toHaveTextContent("算法尚未输出该项结果");
  const truncation = within(issues).getAllByRole("listitem").find((item) => item.textContent?.includes("[truncated"))!;
  await user.click(within(truncation).getByRole("button", { name: "查看原文" }));
  expect(screen.getByTestId("source-trajectory-event")).toHaveTextContent(race.C4.truncations[0].event_id);
});

it("summarizes every trajectory and the batch tool totals, and switches trajectory from the summary", async () => {
  const user = await uploadRaceSession();
  await user.click(screen.getByRole("button", { name: /^汇总/ }));
  const summary = screen.getByTestId("batch-summary");
  expect(within(summary).getByTestId("aggregate-tool-calls")).toHaveTextContent("4");
  const totals = within(summary).getByRole("region", { name: "全批次各工具调用次数" });
  expect(within(totals).getAllByRole("listitem").map((row) => row.textContent)).toEqual(["bash3", "Read1"]);
  await user.click(within(summary).getByRole("button", { name: "race-b" }));
  expect(within(screen.getByTestId("race-stats")).getByTestId("race-input-tokens")).toHaveTextContent("未提供");
  expect(window.location.search).toContain("sample_id=race-b");
});
