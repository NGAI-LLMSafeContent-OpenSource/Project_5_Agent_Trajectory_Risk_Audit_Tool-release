import * as Popover from "@radix-ui/react-popover";
import { useEffect, useRef, useState } from "react";

type Issue = { sample_id: string; item: string; reason: string };

/** Download only the current immutable session. No client-side assembly. */
export function ExportSubmission({ sessionId }: { sessionId: string }) {
  const request = useRef<AbortController | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [issues, setIssues] = useState<Issue[]>([]);
  const [open, setOpen] = useState(false);

  useEffect(() => () => request.current?.abort(), []);

  async function download() {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setMessage("正在校验当前批次归档…");
    setIssues([]);
    setOpen(false);
    try {
      const response = await fetch(`/api/audit-sessions/${encodeURIComponent(sessionId)}/submission.jsonl`, {
        signal: controller.signal,
      });
      if (!response.ok) {
        const result = await response.json();
        if (controller.signal.aborted) return;
        setMessage(result.error?.message || "导出失败，请重试。");
        setIssues(result.error?.details?.issues || []);
        setOpen(true);
        return;
      }
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "submission.jsonl";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setMessage("已开始下载当前批次 submission.jsonl。");
    } catch {
      if (controller.signal.aborted) return;
      setMessage("导出失败，无法连接工作台。请检查连接后重试。");
      setOpen(true);
    } finally {
      if (!controller.signal.aborted) {
        request.current = null;
        setBusy(false);
      }
    }
  }

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Anchor asChild>
        <button ref={trigger} className="btn btn-ghost" type="button" disabled={busy} aria-busy={busy}
          onClick={() => void download()}>
          导出 submission.jsonl
        </button>
      </Popover.Anchor>
      <span className="visually-hidden" role="status">{message}</span>
      <Popover.Portal>
        <Popover.Content className="card submission-feedback" align="end" sideOffset={8}
          aria-label="提交文件导出失败" onCloseAutoFocus={(event) => { event.preventDefault(); trigger.current?.focus(); }}>
          <h3>未生成提交文件</h3>
          <p role="alert">{message}</p>
          {issues.length ? <ul>{issues.map((issue, index) => <li key={index}>
            <strong>{issue.sample_id || "当前批次"} · {issue.item}</strong>：{issue.reason}
          </li>)}</ul> : null}
          <p className="hint">导出读取整批归档，不会重新运行算法。补齐结果需要另建审计批次。</p>
          <Popover.Close className="btn btn-ghost">关闭</Popover.Close>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
