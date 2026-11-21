import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import atbenchFixture from "../../../fixtures/workbench/review_bundle_atbench.json";
import hintbenchFixture from "../../../fixtures/workbench/review_bundle_hintbench.json";
import App from "./App";
import { okSample, renderWithSession, uploadFixture } from "./test/session";
import type { ReviewBundle, SessionSample } from "./types";

const atbench = atbenchFixture as ReviewBundle;
const hintbench = hintbenchFixture as ReviewBundle;

const ATBENCH_TITLE = "--- SYSTEM OVERRIDE: DIAGNOSTIC MODE ---…";
const HINTBENCH_TITLE = "We have a new engineering team member, A…";

afterEach(() => {
  vi.unstubAllGlobals();
});

function withVerdict(bundle: ReviewBundle, sampleId: string, verdict: string): ReviewBundle {
  return {
    ...bundle,
    sample: { ...bundle.sample, sample_id: sampleId },
    verdict: { ...bundle.verdict, value: verdict },
  };
}

function withoutUserMessages(bundle: ReviewBundle, sampleId: string): ReviewBundle {
  return {
    ...bundle,
    sample: { ...bundle.sample, sample_id: sampleId },
    events: bundle.events.filter((event) => event.actor !== "user"),
  };
}

/** A mixed session: two unsafe, one safe, one undetermined, one failed, in this order. */
const MIXED_SAMPLES: SessionSample[] = [
  okSample(withVerdict(atbench, "case-safe", "safe")),
  okSample(atbench),
  { sample_id: "case-failed", ok: false, error: { code: "provider_timeout", message: "课题四 30 秒内未返回" } },
  okSample(withoutUserMessages(withVerdict(hintbench, "case-other", "undetermined"), "case-other")),
  okSample(hintbench),
];

function sidebar() {
  return screen.getByRole("complementary", { name: "样本" });
}

function group(name: string) {
  return within(sidebar()).getByRole("region", { name });
}

function itemIdsIn(region: HTMLElement): string[] {
  return within(region)
    .getAllByRole("button")
    .map((button) => button.getAttribute("data-testid") ?? "")
    .filter((testId) => testId.startsWith("sample-item-"))
    .map((testId) => testId.replace("sample-item-", ""));
}

describe("Sample sidebar: readable items", () => {
  it("each item shows sample_id as the main title, task text as subtitle, source format badge and verdict dot", async () => {
    const user = userEvent.setup();
    render(<App />);
    await uploadFixture(user, "batch");

    const atItem = within(sidebar()).getByTestId(`sample-item-${atbench.sample.sample_id}`);
    expect(within(atItem).getByText(atbench.sample.sample_id)).toBeInTheDocument();
    expect(within(atItem).getByText(ATBENCH_TITLE)).toBeInTheDocument();
    expect(within(atItem).getByText("atbench")).toBeInTheDocument();
    expect(within(atItem).getByRole("img", { name: "检测判定：不安全" })).toBeInTheDocument();

    const hintItem = within(sidebar()).getByTestId(`sample-item-${hintbench.sample.sample_id}`);
    expect(within(hintItem).getByText(hintbench.sample.sample_id)).toBeInTheDocument();
    expect(within(hintItem).getByText(HINTBENCH_TITLE)).toBeInTheDocument();
    expect(within(hintItem).getByText("hintbench")).toBeInTheDocument();
  });

  it("falls back to sample_id only with no subtitle when there is no user message", async () => {
    await renderWithSession(MIXED_SAMPLES);
    const item = within(sidebar()).getByTestId("sample-item-case-other");

    expect(within(item).getByText("case-other")).toBeInTheDocument();
    // no subtitle element should be present when there is no real user message
    expect(item.querySelector(".stitle")).toBeNull();
  });
});

describe("Sample sidebar: grouping by Detected Finding", () => {
  it("groups by detected verdict with counts, keeps session order and hides empty groups", async () => {
    await renderWithSession(MIXED_SAMPLES);

    const names = within(sidebar())
      .getAllByRole("region")
      .map((region) => region.getAttribute("aria-label"));
    expect(names).toEqual(["不安全", "安全", "其他", "审计失败"]);

    expect(group("不安全")).toHaveTextContent("2 条");
    expect(itemIdsIn(group("不安全"))).toEqual([atbench.sample.sample_id, hintbench.sample.sample_id]);
    expect(itemIdsIn(group("安全"))).toEqual(["case-safe"]);
    expect(itemIdsIn(group("其他"))).toEqual(["case-other"]);
    expect(itemIdsIn(group("审计失败"))).toEqual(["case-failed"]);

    expect(
      within(screen.getByTestId("sample-item-case-safe")).getByRole("img", {
        name: "检测判定：安全",
      }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByTestId("sample-item-case-other")).getByRole("img", {
        name: "检测判定：未判定",
      }),
    ).toBeInTheDocument();
  });

  it("does not show groups that have no samples", async () => {
    const user = userEvent.setup();
    render(<App />);
    await uploadFixture(user, "batch");

    const names = within(sidebar())
      .getAllByRole("region")
      .map((region) => region.getAttribute("aria-label"));
    expect(names).toEqual(["不安全"]);
  });

  it("groups by Detected Finding even when the Source Label disagrees", async () => {
    const labelledUnsafe = withVerdict(atbench, "case-label-unsafe", "safe");
    expect(labelledUnsafe.source_labels[0]?.verdict).toBe("unsafe");
    await renderWithSession([okSample(labelledUnsafe)]);

    expect(itemIdsIn(group("安全"))).toEqual(["case-label-unsafe"]);
    expect(within(sidebar()).queryByRole("region", { name: "不安全" })).not.toBeInTheDocument();
  });

  it("a failed sample shows its error code and opens the error in the main area", async () => {
    const user = await renderWithSession(MIXED_SAMPLES);
    const failed = within(group("审计失败")).getByTestId("sample-item-case-failed");
    expect(failed).toHaveTextContent("provider_timeout");

    await user.click(failed);

    const main = screen.getByRole("main");
    expect(within(main).getByRole("alert")).toHaveTextContent(
      "provider_timeout: 课题四 30 秒内未返回",
    );
    expect(failed).toHaveAttribute("aria-current", "true");
  });
});

describe("Sample sidebar: search, selection and collapse", () => {
  it("search matches sample_id or title case-insensitively across all groups", async () => {
    const user = await renderWithSession(MIXED_SAMPLES);
    const search = within(sidebar()).getByRole("searchbox", { name: "搜索样本" });

    await user.type(search, "CASE-");
    expect(itemIdsIn(sidebar())).toEqual(["case-safe", "case-other", "case-failed"]);

    await user.clear(search);
    await user.type(search, "system override");
    expect(itemIdsIn(sidebar())).toEqual([atbench.sample.sample_id, "case-safe"]);
  });

  it("shows an empty-result hint when nothing matches", async () => {
    const user = await renderWithSession(MIXED_SAMPLES);
    await user.type(within(sidebar()).getByRole("searchbox", { name: "搜索样本" }), "zzz-no-match");

    expect(within(sidebar()).queryAllByRole("region")).toHaveLength(0);
    expect(within(sidebar()).getByText(/没有匹配的样本/)).toBeInTheDocument();
  });

  it("highlights the selected sample", async () => {
    const user = userEvent.setup();
    render(<App />);
    await uploadFixture(user, "batch");
    const hintItem = screen.getByTestId(`sample-item-${hintbench.sample.sample_id}`);

    await user.click(hintItem);

    expect(hintItem).toHaveAttribute("aria-current", "true");
    expect(screen.getByTestId(`sample-item-${atbench.sample.sample_id}`)).not.toHaveAttribute(
      "aria-current",
    );
  });

  it("collapses to a narrow strip and expands again", async () => {
    const user = userEvent.setup();
    render(<App />);
    await uploadFixture(user, "batch");

    const collapse = within(sidebar()).getByRole("button", { name: "收起样本栏" });
    expect(collapse).toHaveAttribute("aria-expanded", "true");
    await user.click(collapse);

    expect(within(sidebar()).queryByRole("searchbox")).not.toBeInTheDocument();
    expect(within(sidebar()).queryByTestId("sample-list")).not.toBeInTheDocument();
    const expand = within(sidebar()).getByRole("button", { name: "展开样本栏" });
    expect(expand).toHaveAttribute("aria-expanded", "false");

    await user.click(expand);
    expect(within(sidebar()).getByTestId("sample-list")).toBeInTheDocument();
    expect(within(sidebar()).getByRole("button", { name: "收起样本栏" })).toHaveFocus();
  });
});
