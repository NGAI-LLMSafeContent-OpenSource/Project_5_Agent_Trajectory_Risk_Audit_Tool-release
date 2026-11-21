import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import fixture from "../../../fixtures/workbench/review_bundle_hintbench.json";
import App from "./App";

afterEach(() => { vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

it("sends legacy selections above twenty without clamping", async () => {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({ ok: true, json: async () => ({
    ok: true, samples: [{ sample_id: "case", ok: true, bundle: fixture }],
  }) }));
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<App />);
  await user.selectOptions(screen.getByTestId("adapter-select"), "hintbench");
  expect(screen.getByTestId("limit-input")).not.toHaveAttribute("max");
  await user.clear(screen.getByTestId("limit-input"));
  await user.type(screen.getByTestId("limit-input"), "30");
  await user.upload(screen.getByLabelText("选择 JSON 文件"), new File(["[]"], "batch.json", { type: "application/json" }));
  await screen.findByTestId("bundle-view");
  const body = fetchMock.mock.calls[0][1]?.body as FormData;
  expect(body.get("limit")).toBe("30");
});

it("defaults to Pujiang first and uploads full JSONL from both entries without demo or range controls", async () => {
  const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({
    ok: true, session_id: "a".repeat(32),
    selection: { offset: 0, limit: 30, selected: 30, total_records: 30 },
    samples: [{ sample_id: "s01", ok: true, bundle: fixture }],
  }) }));
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<App />);
  expect(screen.getByTestId("adapter-select")).toHaveValue("openai-messages");
  expect(screen.getAllByRole("option")[0]).toHaveValue("openai-messages");
  expect(screen.queryByText(/Fixture/)).not.toBeInTheDocument();
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  expect(screen.queryByTestId("limit-input")).not.toBeInTheDocument();
  const file = new File(["{}"], "samples.jsonl", { type: "application/x-ndjson" });
  await user.upload(screen.getByLabelText("选择 JSONL 文件"), file);
  expect(await screen.findByTestId("bundle-view")).toBeInTheDocument();
  const sent = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body as FormData;
  expect(sent.get("adapter")).toBe("openai-messages");
  expect(sent.has("limit")).toBe(false);
  expect(sent.has("offset")).toBe(false);

  await user.click(screen.getByRole("button", { name: "上传轨迹" }));
  const popover = screen.getByRole("dialog", { name: "上传轨迹" });
  expect(within(popover).getByTestId("adapter-select")).toHaveValue("openai-messages");
  expect(within(popover).queryByTestId("limit-input")).not.toBeInTheDocument();
  await user.upload(within(popover).getByLabelText("选择 JSONL 文件"), file);
  const subset = (fetchMock.mock.calls[1] as unknown as [string, RequestInit])[1].body as FormData;
  expect(subset.has("limit")).toBe(false);
  expect(screen.queryByRole("dialog", { name: "上传轨迹" })).not.toBeInTheDocument();
});

it("keeps failed algorithm samples reviewable without saying there are no findings", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({
    ok: true, samples: [{ sample_id: "failed", ok: false, error: { code: "provider_failed", message: "未完成" },
      bundle: { ...fixture, algorithm_status: "failure", verdict: { value: "undetermined" }, detected_findings: [] },
    }],
  }) })));
  const user = userEvent.setup();
  render(<App />);
  await user.upload(screen.getByLabelText("选择 JSONL 文件"), new File(["{}"], "t.jsonl", { type: "application/x-ndjson" }));
  expect(await screen.findByTestId("sample-error")).toHaveTextContent("算法失败");
  expect(screen.getByTestId("bundle-view")).toBeInTheDocument();
  expect(screen.getByTestId("verdict-badge")).toHaveTextContent("未判定");
  await user.click(screen.getByRole("tab", { name: /思维链/ }));
  expect(screen.getByText("算法未完成，检测发现未提供。")).toBeInTheDocument();
  expect(screen.queryByText("本条轨迹没有 Detected Finding。")).not.toBeInTheDocument();
});
