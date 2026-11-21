import { useState } from "react";
import type { AuditSessionResponse } from "../types";

export type UploadHandlers = {
  onSession: (session: AuditSessionResponse) => void;
};

type Props = UploadHandlers & { onBusyChange?: (busy: boolean) => void };

const SOURCES = [
  { id: "openai-messages", label: "OpenAI Messages（浦江）" },
  { id: "atbench", label: "ATBench" },
  { id: "hintbench", label: "HINTBench" },
];

export function UploadPanel({ onSession, onBusyChange }: Props) {
  const [busy, setBusyState] = useState(false);
  const [error, setError] = useState("");
  const [adapter, setAdapter] = useState("openai-messages");
  const [limit, setLimit] = useState("5");
  const [offset, setOffset] = useState("0");
  const messages = adapter === "openai-messages";

  function setBusy(next: boolean) {
    setBusyState(next);
    onBusyChange?.(next);
  }

  async function onFileChange(file: File | null) {
    if (!file) return;
    setError("");
    if (!messages && (!/^\d+$/.test(limit) || Number(limit) < 1 || !/^\d+$/.test(offset))) {
      setError("处理条数必须是正整数，跳过条数必须是非负整数");
      return;
    }
    setBusy(true);
    try {
      const body = new FormData();
      body.append("trajectory", file);
      body.append("adapter", adapter);
      if (!messages) {
        body.append("limit", limit);
        body.append("offset", offset);
      }
      const response = await fetch("/api/audit-sessions", { method: "POST", body });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        setError(`${payload?.error?.code || `http_${response.status}`}: ${payload?.error?.message || "审计会话失败"}`);
        return;
      }
      onSession(payload as AuditSessionResponse);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card upload-card" data-testid="upload-panel" aria-busy={busy}>
      <div className="upload-head">
        <h2>上传轨迹</h2>
        <p>{messages ? "上传 UTF-8 JSONL 文件，完整处理所有轨迹" : "上传 JSON 文件，按所选范围处理轨迹"}</p>
      </div>
      <div className="upload-fields">
        <label className="field upload-source">
          <span>轨迹格式</span>
          <select data-testid="adapter-select" value={adapter} disabled={busy}
            onChange={(event) => { setAdapter(event.target.value); setError(""); }}>
            {SOURCES.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </label>
        {!messages ? <>
          <label className="field">
            <span>处理条数</span>
            <input data-testid="limit-input" type="number" min={1} step={1} value={limit}
              disabled={busy} onChange={(event) => setLimit(event.target.value)} />
          </label>
          <label className="field">
            <span>跳过条数</span>
            <input data-testid="offset-input" type="number" min={0} step={1} value={offset}
              disabled={busy} onChange={(event) => setOffset(event.target.value)} />
          </label>
        </> : null}
      </div>
      <label className={`btn btn-primary file-btn${busy ? " is-busy" : ""}`}>
        <input type="file" accept={messages ? ".jsonl,application/x-ndjson" : "application/json,.json"}
          disabled={busy} onChange={(event) => {
            const file = event.target.files?.[0] || null;
            event.target.value = "";
            void onFileChange(file);
          }} />
        {busy ? "正在处理轨迹…" : messages ? "选择 JSONL 文件" : "选择 JSON 文件"}
      </label>
      {busy ? <p className="upload-note" role="status">正在解析文件并运行审计，请保持页面打开</p> : null}
      {error ? <p className="error" role="alert" data-testid="upload-error">{error}</p> : null}
    </section>
  );
}
