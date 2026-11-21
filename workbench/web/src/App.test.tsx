import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import hintbenchFixture from "../../../fixtures/workbench/review_bundle_hintbench.json";
import App from "./App";
import { uploadFixture } from "./test/session";
import { UNAVAILABLE_LABEL } from "./types";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

function stubAuditSessionResponse(status: number, payload: unknown) {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function trajectoryFile() {
  return new File(["{}"], "trajectory.jsonl", { type: "application/x-ndjson" });
}

describe("Audit history links", () => {
  const sessionId = "a".repeat(32);
  const sampleId = hintbenchFixture.sample.sample_id;

  it("keeps the selected sample in the URL and restores it on a fresh mount", async () => {
    const fetchMock = stubAuditSessionResponse(200, {
      ok: true,
      session_id: sessionId,
      samples: [
        { ok: false, sample_id: "failed-sample" },
        { ok: true, sample_id: sampleId, bundle: hintbenchFixture },
      ],
    });
    const user = userEvent.setup();
    const first = render(<App />);
    await user.upload(screen.getByLabelText(/选择 JSONL? 文件/), trajectoryFile());
    await user.click(await screen.findByTestId(`sample-item-${sampleId}`));
    expect(new URLSearchParams(window.location.search).get("session_id")).toBe(sessionId);
    expect(new URLSearchParams(window.location.search).get("sample_id")).toBe(sampleId);

    first.unmount();
    render(<App />);
    expect(await screen.findByTestId("bundle-view")).toBeInTheDocument();
    expect(screen.getByTestId(`sample-item-${sampleId}`)).toHaveAttribute("aria-current", "true");
    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method ?? "GET"])).toEqual([
      ["/api/audit-sessions", "POST"],
      [`/api/audit-sessions/${sessionId}`, "GET"],
    ]);
  });

  it("shows a missing archive and lets the user upload a new file", async () => {
    window.history.replaceState(null, "", `/?session_id=${sessionId}`);
    stubAuditSessionResponse(404, { ok: false });
    const user = userEvent.setup();
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("找不到这次审计");
    await uploadFixture(user, "hintbench");
    expect(screen.getByTestId("bundle-view")).toBeInTheDocument();
    expect(window.location.search).toBe("");
  });
});

async function assertSharedThreeTabLayout(
  user: ReturnType<typeof userEvent.setup>,
  options: { expectedTool: RegExp; sourceFormat: string },
) {
  expect(screen.getByTestId("bundle-view")).toBeInTheDocument();
  expect(screen.getByTestId("verdict-badge")).toHaveTextContent("不安全");
  expect(screen.queryByTestId("source-label-layer")).not.toBeInTheDocument();
  expect(screen.queryByText("步骤与原文映射")).not.toBeInTheDocument();
  expect(screen.getByText(new RegExp(`来源格式：${options.sourceFormat}`))).toBeInTheDocument();

  expect(screen.getByRole("tab", { name: /数据统计/ })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /思维链/ })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: /工具调用/ })).toBeInTheDocument();

  expect(screen.getByTestId("tab-stats")).toBeInTheDocument();
  expect(screen.getByTestId("token-input")).toHaveTextContent(UNAVAILABLE_LABEL);

  await user.click(screen.getByRole("tab", { name: /思维链/ }));
  expect(screen.getByTestId("tab-cot")).toBeInTheDocument();

  await user.click(screen.getByRole("tab", { name: /工具调用/ }));
  expect(screen.getByTestId("tab-tools")).toBeInTheDocument();
  expect(screen.getAllByText(options.expectedTool).length).toBeGreaterThan(0);
}

describe("Gate B: frozen Review Bundle → shared three-tab UI", () => {
  it("ATBench fixture uses the shared stats / cot / tools layout", async () => {
    const user = userEvent.setup();
    render(<App />);

    await uploadFixture(user, "atbench");
    await assertSharedThreeTabLayout(user, {
      sourceFormat: "atbench",
      expectedTool: /read_vault_by_name/,
    });
  });

  it("HINTBench fixture uses the same layout without a dataset branch", async () => {
    const user = userEvent.setup();
    render(<App />);

    await uploadFixture(user, "hintbench");
    await assertSharedThreeTabLayout(user, {
      sourceFormat: "hintbench",
      expectedTool: /invite_user_to_slack/,
    });
  });

  it("batch fixture switches samples while keeping the shared three-tab layout", async () => {
    const user = userEvent.setup();
    render(<App />);

    await uploadFixture(user, "batch");
    expect(screen.getByTestId("sample-list")).toBeInTheDocument();
    await assertSharedThreeTabLayout(user, {
      sourceFormat: "atbench",
      expectedTool: /read_vault_by_name/,
    });

    const hintSampleId = (hintbenchFixtureSampleId());
    await user.click(screen.getByTestId(`sample-item-${hintSampleId}`));
    await assertSharedThreeTabLayout(user, {
      sourceFormat: "hintbench",
      expectedTool: /invite_user_to_slack/,
    });
  });

  it("upload panel defaults to full Pujiang and exposes legacy range controls on selection", async () => {
    const user = userEvent.setup();
    render(<App />);
    const select = screen.getByTestId("adapter-select");
    expect(select).toHaveValue("openai-messages");
    expect(screen.getByRole("option", { name: "HINTBench" })).toBeInTheDocument();
    await user.selectOptions(select, "atbench");
    expect(screen.getByTestId("limit-input")).toHaveValue(5);
    expect(screen.getByTestId("offset-input")).toHaveValue(0);
    expect(screen.getByRole("heading", { name: "上传轨迹" })).toBeInTheDocument();
  });
});

describe("App shell: topbar, sample sidebar and main area", () => {
  it("topbar shows the brand", () => {
    render(<App />);
    const banner = screen.getByRole("banner");
    expect(within(banner).getByText("Agent 轨迹风险可视化审计")).toBeInTheDocument();
    expect(within(banner).getByText("数据统计 · 思维链 · 工具调用")).toBeInTheDocument();
  });

  it("empty state puts the full upload card in the main area", () => {
    render(<App />);
    const main = screen.getByRole("main");
    const card = within(main).getByTestId("upload-panel");
    expect(within(card).getByRole("heading", { name: "上传轨迹" })).toBeInTheDocument();
    expect(within(card).getByTestId("adapter-select")).toBeEnabled();
    expect(within(card).queryByTestId("limit-input")).not.toBeInTheDocument();
    expect(within(card).queryByTestId("offset-input")).not.toBeInTheDocument();
    expect(within(card).getByLabelText(/选择 JSONL? 文件/)).toBeEnabled();
    expect(within(card).queryByText(/Fixture/)).not.toBeInTheDocument();

    const sidebar = screen.getByRole("complementary", { name: "样本" });
    expect(within(sidebar).getByText(/暂无轨迹/)).toBeInTheDocument();
  });

  it("loaded samples go to the sidebar and the review to the main area", async () => {
    const user = userEvent.setup();
    render(<App />);
    await uploadFixture(user, "batch");

    const sidebar = screen.getByRole("complementary", { name: "样本" });
    expect(within(sidebar).getByTestId("sample-list")).toBeInTheDocument();
    expect(within(sidebar).queryByText(/暂无轨迹/)).not.toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByTestId("bundle-view")).toBeInTheDocument();
  });

  it("a single loaded sample is still listed in the sidebar", async () => {
    const user = userEvent.setup();
    render(<App />);
    await uploadFixture(user, "atbench");

    const sidebar = screen.getByRole("complementary", { name: "样本" });
    expect(within(sidebar).getByTestId("sample-list")).toBeInTheDocument();
  });
});

describe("Upload entry: centered card collapses into a topbar popover", () => {
  async function loadBatchFromCard(user: ReturnType<typeof userEvent.setup>) {
    render(<App />);
    await uploadFixture(user, "batch");
  }

  function topbarUploadButton() {
    return screen.getByRole("button", { name: "上传轨迹" });
  }

  it("hides the centered card after loading and selects the first sample", async () => {
    const user = userEvent.setup();
    await loadBatchFromCard(user);

    expect(screen.queryByTestId("upload-panel")).not.toBeInTheDocument();
    expect(screen.getByTestId(`sample-item-${atbenchFixtureSampleId()}`)).toHaveAttribute(
      "aria-current",
      "true",
    );
    expect(screen.getByTestId(`sample-item-${hintbenchFixtureSampleId()}`)).not.toHaveAttribute(
      "aria-current",
    );
    expect(screen.getByText(/来源格式：atbench/)).toBeInTheDocument();
  });

  it("hides the centered card after a successful upload from it", async () => {
    stubAuditSessionResponse(200, { ok: true, run_id: "run-card", bundle: hintbenchFixture });
    const user = userEvent.setup();
    render(<App />);
    await user.upload(screen.getByLabelText(/选择 JSONL? 文件/), trajectoryFile());

    expect(await screen.findByText(/来源格式：hintbench/)).toBeInTheDocument();
    expect(screen.queryByTestId("upload-panel")).not.toBeInTheDocument();
  });

  it("keeps the popover open while an upload is in flight so its outcome is not lost", async () => {
    let respond: (value: unknown) => void = () => {};
    vi.stubGlobal(
      "fetch",
      vi.fn(() => new Promise((resolve) => (respond = resolve))),
    );
    const user = userEvent.setup();
    await loadBatchFromCard(user);
    await user.click(topbarUploadButton());
    const popover = screen.getByRole("dialog", { name: "上传轨迹" });
    await user.upload(within(popover).getByLabelText(/选择 JSONL? 文件/), trajectoryFile());

    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog", { name: "上传轨迹" })).toBeInTheDocument();

    respond({
      ok: false,
      status: 502,
      json: async () => ({ ok: false, error: { code: "provider_failed", message: "超时" } }),
    });
    expect(await within(popover).findByRole("alert")).toHaveTextContent("provider_failed: 超时");
  });

  it("topbar button opens a popover with the full upload controls at their defaults", async () => {
    const user = userEvent.setup();
    await loadBatchFromCard(user);
    await user.click(topbarUploadButton());

    const popover = screen.getByRole("dialog", { name: "上传轨迹" });
    expect(within(popover).getByTestId("adapter-select")).toHaveValue("openai-messages");
    expect(within(popover).getByRole("option", { name: "HINTBench" })).toBeInTheDocument();
    expect(within(popover).queryByTestId("limit-input")).not.toBeInTheDocument();
    expect(within(popover).queryByTestId("offset-input")).not.toBeInTheDocument();
    expect(within(popover).getByLabelText(/选择 JSONL? 文件/)).toBeEnabled();
    expect(within(popover).queryByText(/Fixture/)).not.toBeInTheDocument();
  });

  it("uploading from the popover sends the chosen controls, closes it and shows the first sample", async () => {
    const fetchMock = stubAuditSessionResponse(200, {
      ok: true,
      mode: "single",
      run_id: "run-upload",
      bundle: hintbenchFixture,
    });
    const user = userEvent.setup();
    await loadBatchFromCard(user);
    await user.click(topbarUploadButton());
    const popover = screen.getByRole("dialog", { name: "上传轨迹" });
    await user.selectOptions(within(popover).getByTestId("adapter-select"), "hintbench");
    await user.clear(within(popover).getByTestId("limit-input"));
    await user.type(within(popover).getByTestId("limit-input"), "3");
    await user.upload(within(popover).getByLabelText(/选择 JSONL? 文件/), new File(["{}"], "t.json", { type: "application/json" }));

    expect(await screen.findByText(/来源格式：hintbench/)).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "上传轨迹" })).not.toBeInTheDocument();
    const body = fetchMock.mock.calls[0][1]?.body as FormData;
    expect(body.get("adapter")).toBe("hintbench");
    expect(body.get("limit")).toBe("3");
    expect(body.get("offset")).toBe("0");
  });

  it("Esc closes the popover and returns focus to the topbar button", async () => {
    const user = userEvent.setup();
    await loadBatchFromCard(user);
    await user.click(topbarUploadButton());
    expect(screen.getByRole("dialog", { name: "上传轨迹" })).toBeInTheDocument();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("dialog", { name: "上传轨迹" })).not.toBeInTheDocument();
    expect(topbarUploadButton()).toHaveFocus();
  });

  it("an audit failure in the popover is shown there and keeps the current review", async () => {
    stubAuditSessionResponse(502, {
      ok: false,
      error: { code: "provider_failed", message: "课题四调用失败" },
    });
    const user = userEvent.setup();
    await loadBatchFromCard(user);
    await user.click(topbarUploadButton());
    const popover = screen.getByRole("dialog", { name: "上传轨迹" });
    await user.upload(within(popover).getByLabelText(/选择 JSONL? 文件/), trajectoryFile());

    expect(await within(popover).findByRole("alert")).toHaveTextContent(
      "provider_failed: 课题四调用失败",
    );
    expect(screen.getByRole("dialog", { name: "上传轨迹" })).toBeInTheDocument();
    expect(screen.getByText(/来源格式：atbench/)).toBeInTheDocument();
  });

  it("a network failure from the empty-state card is shown on the card", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("Failed to fetch");
      }),
    );
    const user = userEvent.setup();
    render(<App />);
    const card = screen.getByTestId("upload-panel");
    await user.upload(within(card).getByLabelText(/选择 JSONL? 文件/), trajectoryFile());

    expect(await within(card).findByRole("alert")).toHaveTextContent("Failed to fetch");
  });
});

function atbenchFixtureSampleId(): string {
  return "atbench-86";
}

function hintbenchFixtureSampleId(): string {
  // Keep the selector aligned with the frozen HINTBench Review Bundle sample_id.
  return "slack_task_0008_risk_v7";
}
