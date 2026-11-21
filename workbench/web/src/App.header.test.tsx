import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import atbenchFixture from "../../../fixtures/workbench/review_bundle_atbench.json";
import App from "./App";
import { okSample, renderWithSession, uploadFixture } from "./test/session";
import type { ReviewBundle } from "./types";

const atbench = atbenchFixture as ReviewBundle;

const DETECTED_LAYER = "检测判定";

afterEach(() => {
  vi.unstubAllGlobals();
});

async function loadFixture(fixture: "atbench" | "hintbench") {
  const user = userEvent.setup();
  render(<App />);
  await uploadFixture(user, fixture);
  return screen.getByTestId("sample-header");
}

describe("Sample header: detected verdict", () => {
  it("shows the detected verdict as the primary layer, named as such", async () => {
    const header = await loadFixture("atbench");
    const detected = within(header).getByRole("region", { name: DETECTED_LAYER });

    expect(detected).toHaveTextContent(DETECTED_LAYER);
    expect(within(detected).getByText("不安全")).toBeInTheDocument();
    expect(detected).not.toHaveTextContent("malicious_user_instruction_or_jailbreak");
    expect(detected).not.toHaveTextContent("算法原始判定，详见算法结果与原始归档。");
  });

  it("shows a reviewer-facing verdict reason when the bundle has one", async () => {
    await renderWithSession([
      okSample({
        ...atbench,
        verdict: { value: "unsafe", reason: "助手执行了未授权的保险库读取。" },
      }),
    ]);
    const detected = within(screen.getByTestId("sample-header")).getByRole("region", {
      name: DETECTED_LAYER,
    });

    expect(detected).toHaveTextContent("助手执行了未授权的保险库读取。");
  });

  it("says the verdict basis is not provided when the bundle gives no reason or basis", async () => {
    await renderWithSession([okSample({ ...atbench, verdict: { value: "unsafe" } })]);
    const detected = within(screen.getByTestId("sample-header")).getByRole("region", {
      name: DETECTED_LAYER,
    });

    expect(detected).toHaveTextContent("判定依据：未提供");
  });

  it("keeps source labels and header counts off the sample page", async () => {
    const header = await loadFixture("atbench");

    expect(within(header).queryByRole("region", { name: "来源标签" })).not.toBeInTheDocument();
    expect(header).not.toHaveTextContent("malicious_user_instruction_or_jailbreak");
    expect(screen.queryByTestId("header-finding-count")).not.toBeInTheDocument();
    expect(screen.queryByTestId("header-tool-calls")).not.toBeInTheDocument();
    expect(screen.queryByText("步骤与原文映射")).not.toBeInTheDocument();
  });
});

describe("Sample header: lineage", () => {
  it("shows source format, dataset and run_id", async () => {
    const header = await loadFixture("atbench");

    expect(header).toHaveTextContent("来源格式：atbench");
    expect(header).toHaveTextContent("数据集：atbench@0.1.0");
    expect(header).toHaveTextContent("run_id：run-fixture-atbench-86");
  });
});

describe("Sample header: one layout for every source", () => {
  it("HINTBench uses the same header", async () => {
    const header = await loadFixture("hintbench");

    expect(within(header).getByRole("region", { name: DETECTED_LAYER })).toHaveTextContent(
      "不安全",
    );
    expect(within(header).queryByRole("region", { name: "来源标签" })).not.toBeInTheDocument();
    expect(header).not.toHaveTextContent("goal_boundary_deviation");
    expect(header).toHaveTextContent("来源格式：hintbench");
    expect(header).toHaveTextContent("数据集：hintbench@0.1.0");
  });
});
