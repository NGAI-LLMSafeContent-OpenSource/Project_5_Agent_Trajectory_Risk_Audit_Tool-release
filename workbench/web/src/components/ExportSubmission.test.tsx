import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ExportSubmission } from "./ExportSubmission";

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("downloads the archive by session identity without posting another audit", async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, blob: async () => new Blob(['{"trajectory_id":"fixed"}\n']) });
  vi.stubGlobal("fetch", fetch);
  vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:submission"), revokeObjectURL: vi.fn() });
  const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  render(<ExportSubmission sessionId={"a".repeat(32)} />);
  await userEvent.click(screen.getByRole("button", { name: "导出 submission.jsonl" }));
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0][0]).toBe(`/api/audit-sessions/${"a".repeat(32)}/submission.jsonl`);
  expect(click).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("status")).toHaveTextContent("已开始下载当前批次");
});

it("shows exact sample failures, keeps the action retryable and closes with Escape", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, json: async () => ({ error: {
    message: "当前归档不能导出完整 submission.jsonl", details: { issues: [
      { sample_id: "s04", item: "B3", reason: "算法尚未输出该项结果" },
    ] },
  } }) }));
  render(<ExportSubmission sessionId="current" />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "导出 submission.jsonl" }));
  expect(await screen.findByText(/s04 · B3/)).toBeVisible();
  expect(screen.getByText(/算法尚未输出该项结果/)).toBeVisible();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "导出 submission.jsonl" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "导出 submission.jsonl" })).toHaveFocus();
});

it("prevents duplicate requests while busy and recovers from network errors", async () => {
  let reject: (reason?: unknown) => void = () => {};
  const fetch = vi.fn(() => new Promise((_resolve, rejectRequest) => { reject = rejectRequest; }));
  vi.stubGlobal("fetch", fetch);
  render(<ExportSubmission sessionId="current" />);
  const user = userEvent.setup();
  const button = screen.getByRole("button", { name: "导出 submission.jsonl" });
  await user.click(button);
  expect(button).toBeDisabled();
  expect(button).toHaveAttribute("aria-busy", "true");
  await user.click(button);
  expect(fetch).toHaveBeenCalledTimes(1);
  await act(async () => reject(new TypeError("offline")));
  expect(await screen.findByRole("alert")).toHaveTextContent("请检查连接后重试");
  expect(button).toBeEnabled();
});

it("aborts the previous session download on unmount", async () => {
  const fetch = vi.fn(() => new Promise(() => {}));
  vi.stubGlobal("fetch", fetch);
  const view = render(<ExportSubmission sessionId="previous" />);
  await userEvent.click(screen.getByRole("button"));
  const signal = (fetch.mock.calls as unknown as [string, { signal: AbortSignal }][])[0][1].signal;
  view.unmount();
  expect(signal.aborted).toBe(true);
});
