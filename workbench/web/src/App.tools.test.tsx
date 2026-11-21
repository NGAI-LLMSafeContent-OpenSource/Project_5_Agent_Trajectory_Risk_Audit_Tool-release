import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import atbenchFixture from "../../../fixtures/workbench/review_bundle_atbench.json";
import App from "./App";
import { okSample, renderWithSession, uploadFixture } from "./test/session";
import type { ReviewBundle } from "./types";

const atbench = atbenchFixture as ReviewBundle;

afterEach(() => {
  vi.unstubAllGlobals();
});

async function openTools(fixture: "atbench" | "hintbench") {
  const user = userEvent.setup();
  render(<App />);
  await uploadFixture(user, fixture);
  await user.click(screen.getByRole("tab", { name: /工具调用/ }));
  return user;
}

function calls() {
  return screen.getByRole("list", { name: "工具调用明细" });
}

describe("Tools tab: call cards", () => {
  it("shows each call with its sequence number, tool, arguments preview and declared status", async () => {
    await openTools("atbench");

    const card = within(calls()).getByTestId("tool-card-evt-0002");
    expect(within(card).getByTestId("tool-seq")).toHaveTextContent(/^1evt-0002$/);
    expect(within(card).getByTestId("tool-name")).toHaveTextContent("read_vault_by_name");
    expect(within(card).getByTestId("tool-args-preview")).toHaveTextContent('{ "name": "prod_signing_key" }');
    expect(within(card).getByTestId("tool-status")).toHaveTextContent("失败");
    expect(within(card).getByTestId("tool-findings")).toHaveTextContent("无关联发现");
  });

  it("lists every HINTBench call in order with its own tool name and status", async () => {
    await openTools("hintbench");

    const cards = within(calls()).getAllByRole("listitem");
    expect(cards.map((card) => within(card).getByTestId("tool-name").textContent)).toEqual([
      "invite_user_to_slack",
      "invite_user_to_slack",
      "add_user_to_channel",
      "add_user_to_channel",
      "add_user_to_channel",
      "send_channel_message",
    ]);
    expect(within(cards[0]).getByTestId("tool-status")).toHaveTextContent("成功");
    expect(within(cards[1]).getByTestId("tool-status")).toHaveTextContent("失败");
  });

  it("marks a call whose return never arrived as unpaired", async () => {
    const [call] = atbench.tool_interactions;
    const user = await renderWithSession([okSample({ ...atbench, tool_interactions: [{ ...call, result: null }] })]);
    await user.click(screen.getByRole("tab", { name: /工具调用/ }));

    const card = within(calls()).getByTestId("tool-card-evt-0002");
    expect(within(card).getByTestId("tool-status")).toHaveTextContent("未配对");
  });

  it("shows 未提供 instead of an empty payload when arguments or output are missing", async () => {
    const [call] = atbench.tool_interactions;
    const user = await renderWithSession([
      okSample({
        ...atbench,
        tool_interactions: [
          {
            ...call,
            arguments: undefined,
            arguments_raw: undefined,
            arguments_status: undefined,
            result: { ...call.result!, output: undefined },
          },
        ],
      }),
    ]);
    await user.click(screen.getByRole("tab", { name: /工具调用/ }));
    const card = within(calls()).getByTestId("tool-card-evt-0002");

    expect(within(card).getByTestId("tool-args-preview")).toHaveTextContent(/^未提供$/);
    expect(within(card).getByText("参数 · 未提供")).toBeInTheDocument();
    expect(within(card).getByTestId("tool-args-body-evt-0002")).toHaveTextContent(/^未提供$/);
    expect(within(card).getByTestId("tool-result-body-evt-0002")).toHaveTextContent(/^未提供$/);
  });

  it("previews raw string arguments as written and names how the return was paired", async () => {
    const [call] = atbench.tool_interactions;
    const user = await renderWithSession([
      okSample({
        ...atbench,
        tool_interactions: [{ ...call, arguments: undefined, arguments_raw: '{"name": "prod"' }],
      }),
    ]);
    await user.click(screen.getByRole("tab", { name: /工具调用/ }));
    const card = within(calls()).getByTestId("tool-card-evt-0002");

    expect(within(card).getByTestId("tool-args-preview")).toHaveTextContent(/^\{"name": "prod"$/);
    expect(within(card).getByTestId("tool-result-evt-0002")).toHaveTextContent(
      "返回 · evt-0003 · 配对：positional",
    );
  });

  it("expands arguments and returns to the full payload", async () => {
    const user = await openTools("hintbench");
    const card = within(calls()).getByTestId("tool-card-evt-0004");

    await user.click(within(card).getByText(/参数/));
    const args = within(card).getByTestId("tool-args-body-evt-0004");
    expect(args).toHaveTextContent('"user_email": "alex.chen@example.com"');
    await user.click(within(card).getByText(/返回/));
    const result = within(card).getByTestId("tool-result-body-evt-0004");
    expect(result).toHaveTextContent("Invitation sent to alex.chen@example.com");
    expect(result.textContent).not.toMatch(/…$/);
  });

  it("omits unavailable visualizations and permission comparison", async () => {
    await openTools("hintbench");

    expect(screen.queryByText(/泳道图|耗时瀑布/)).not.toBeInTheDocument();
    expect(within(screen.getByTestId("tab-tools")).queryByText(/所需权限|已授予|权限对照/)).not.toBeInTheDocument();

  });
});

describe("Tools tab: findings on calls", () => {
  const onResult: ReviewBundle = {
    ...atbench,
    detected_findings: [
      {
        ...atbench.detected_findings[0],
        origin_event_ids: [],
        affected_event_ids: [],
        evidence: [
          {
            kind: "event",
            event_id: "evt-0003",
            field_path: "content.output",
            excerpt: "PERMISSION_DENIED",
            located: true,
          },
        ],
      },
    ],
  };

  it("links a call card to the findings on its call or return", async () => {
    const user = await renderWithSession([okSample(onResult)]);
    await user.click(screen.getByRole("tab", { name: /工具调用/ }));

    const card = within(calls()).getByTestId("tool-card-evt-0002");
    expect(within(card).getByTestId("tool-findings")).toHaveTextContent("unauthorized_action");
    await user.click(within(card).getByRole("button", { name: /fnd-0/ }));

    expect(card).toHaveAttribute("data-focused", "true");
    expect(card).toHaveTextContent("当前焦点");
  });

  it("jumps from a finding to its call, opening the payload with the evidence highlighted", async () => {
    const user = await renderWithSession([okSample(onResult)]);
    await user.click(screen.getByRole("tab", { name: /思维链/ }));
    await user.click(screen.getByTestId("finding-fnd-0"));

    expect(screen.getByRole("tab", { name: /工具调用/ })).toHaveAttribute("aria-selected", "true");
    const card = within(calls()).getByTestId("tool-card-evt-0002");
    expect(card).toHaveAttribute("data-focused", "true");
    expect(within(card).getByTestId("tool-result-evt-0002")).toHaveAttribute("open");
    expect(within(card).getByTestId("evidence-mark")).toHaveTextContent("PERMISSION_DENIED");
  });
});
