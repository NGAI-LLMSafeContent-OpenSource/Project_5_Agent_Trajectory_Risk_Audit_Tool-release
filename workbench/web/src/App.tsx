import { useEffect, useRef, useState } from "react";
import { BundleView } from "./components/BundleView";
import { ExportSubmission } from "./components/ExportSubmission";
import { BatchSummary, readSummaryFilters, type SummaryFilters } from "./components/BatchSummary";
import { SampleSidebar } from "./components/SampleSidebar";
import { SourceTrajectoryDrawer } from "./components/SourceTrajectoryDrawer";
import { UploadPanel, type UploadHandlers } from "./components/UploadPanel";
import { UploadPopover } from "./components/UploadPopover";
import { findingsByEvent } from "./findingFocus";
import type { AuditSessionResponse, SessionSample } from "./types";
import { UNAVAILABLE_LABEL } from "./types";

function normalizeSamples(session: AuditSessionResponse): SessionSample[] {
  if (session.samples && session.samples.length > 0) {
    return session.samples.map((sample, index) => ({
      ...sample,
      sample_id: sample.sample_id || `sample-${index}`,
    }));
  }
  if (session.bundle) {
    return [
      {
        sample_id: session.bundle.sample.sample_id,
        ok: true,
        run_id: session.run_id,
        bundle: session.bundle,
      },
    ];
  }
  return [];
}

function updateReviewUrl(
  sessionId: string | null,
  sampleId: string | null,
  summary = false,
  filters?: SummaryFilters,
) {
  const url = new URL(window.location.href);
  if (sessionId) url.searchParams.set("session_id", sessionId);
  else url.searchParams.delete("session_id");
  if (sessionId && sampleId) url.searchParams.set("sample_id", sampleId);
  else url.searchParams.delete("sample_id");
  for (const key of ["view", "summary_status", "summary_tool"]) url.searchParams.delete(key);
  if (summary) {
    url.searchParams.set("view", "summary");
    if (filters?.status && filters.status !== "all") url.searchParams.set("summary_status", filters.status);
    if (filters && filters.tool !== null) url.searchParams.set("summary_tool", filters.tool);
  }
  window.history.replaceState(window.history.state, "", url);
}

export default function App() {
  const [initialQuery] = useState(() => new URLSearchParams(window.location.search));
  const [loading, setLoading] = useState(() => initialQuery.has("session_id"));
  const [historyError, setHistoryError] = useState("");
  const archiveRequest = useRef<AbortController | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [aggregate, setAggregate] = useState<AuditSessionResponse["aggregate"]>();
  const [samples, setSamples] = useState<SessionSample[]>([]);
  const [activeSampleId, setActiveSampleId] = useState<string | null>(null);
  const [sourceEventId, setSourceEventId] = useState<string | null>(null);
  const [summaryRequested, setSummaryRequested] = useState(() => initialQuery.get("view") === "summary");
  const [summaryFilters, setSummaryFilters] = useState(() => readSummaryFilters(initialQuery));
  const mainRef = useRef<HTMLElement>(null);
  const summaryOpen = summaryRequested && Boolean(aggregate) && samples.length > 0;
  const activeSample = samples.find((sample) => sample.sample_id === activeSampleId);

  useEffect(() => {
    if (!summaryOpen) return;
    mainRef.current?.focus();
    if (mainRef.current) mainRef.current.scrollTop = 0;
  }, [summaryOpen]);

  useEffect(() => {
    if (!initialQuery.has("session_id")) return;
    const controller = new AbortController();
    archiveRequest.current = controller;

    async function restoreSession() {
      const id = initialQuery.get("session_id") ?? "";
      const missingSession = "找不到这次审计，请确认链接和工作台地址。";
      try {
        if (!/^[0-9a-f]{32}$/.test(id)) {
          setHistoryError(missingSession);
          return;
        }
        const response = await fetch(`/api/audit-sessions/${id}`, {
          signal: controller.signal,
        });
        if (response.status === 404) {
          if (!controller.signal.aborted) setHistoryError(missingSession);
          return;
        }
        if (!response.ok) throw new Error("Archive request failed");
        const payload: AuditSessionResponse = await response.json();
        if (controller.signal.aborted) return;
        if (!payload.ok || payload.session_id !== id) throw new Error("Invalid archive response");

        const next = normalizeSamples(payload);
        const requested = initialQuery.get("sample_id");
        const selected = requested === null
          ? next[0]
          : next.find((sample) => sample.sample_id === requested);
        setSessionId(id);
        setAggregate(payload.aggregate);
        setSamples(next);
        setActiveSampleId(selected?.sample_id ?? null);
        if (requested !== null && !selected) {
          setHistoryError("这次审计中找不到链接指定的样本。请核对链接，或从左侧选择样本。");
        } else {
          const showSummary = initialQuery.get("view") === "summary" && Boolean(payload.aggregate) && next.length > 0;
          updateReviewUrl(id, selected?.sample_id ?? null, showSummary, readSummaryFilters(initialQuery));
        }
      } catch {
        if (!controller.signal.aborted) {
          setHistoryError("无法加载审计归档，请刷新页面重试。");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    void restoreSession();
    return () => controller.abort();
  }, [initialQuery]);

  function applySamples(next: SessionSample[], id: string | null) {
    archiveRequest.current?.abort();
    const selected = next[0]?.sample_id ?? null;
    setLoading(false);
    setHistoryError("");
    setSessionId(id);
    setSamples(next);
    setActiveSampleId(selected);
    setSourceEventId(null);
    setSummaryRequested(false);
    setSummaryFilters({ status: "all", tool: null });
    updateReviewUrl(id, selected);
  }

  function selectSample(id: string) {
    setSummaryRequested(false);
    setActiveSampleId(id);
    setSourceEventId(null);
    setHistoryError("");
    updateReviewUrl(sessionId, id);
    mainRef.current?.focus();
    if (mainRef.current) mainRef.current.scrollTop = 0;
  }

  function showSummary() {
    setSummaryRequested(true);
    setSourceEventId(null);
    setHistoryError("");
    updateReviewUrl(sessionId, activeSampleId, true, summaryFilters);
  }

  function changeSummaryFilters(filters: SummaryFilters) {
    setSummaryFilters(filters);
    updateReviewUrl(sessionId, activeSampleId, true, filters);
  }

  const uploadHandlers: UploadHandlers = {
    onSession: (session) => {
      setAggregate(session.aggregate);
      applySamples(normalizeSamples(session), session.session_id ?? null);
    },
  };

  const isEmpty = samples.length === 0;
  const sourceOpen = !summaryOpen && sourceEventId !== null && Boolean(activeSample?.bundle);
  const sourceNotes =
    sourceOpen && sourceEventId && activeSample?.bundle
      ? (
          findingsByEvent(activeSample.bundle.detected_findings, activeSample.bundle.events).get(
            sourceEventId,
          ) ?? []
        ).map((finding) => ({
          findingId: finding.finding_id,
          categories: finding.categories,
          reason: finding.reason || `原因${UNAVAILABLE_LABEL}`,
        }))
      : [];

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">AT</span>
          <div>
            <strong>Agent 轨迹风险可视化审计</strong>
            <small>数据统计 · 思维链 · 工具调用</small>
          </div>
        </div>
        <div className="topbar-actions">
          {sessionId ? <ExportSubmission key={sessionId} sessionId={sessionId} /> : null}
          <UploadPopover {...uploadHandlers} />
        </div>
      </header>
      <div className="workspace">
        <SampleSidebar
          samples={samples}
          activeSampleId={summaryOpen ? null : activeSample?.sample_id ?? null}
          onSelect={selectSample}
          aggregate={aggregate}
          summaryActive={summaryOpen}
          onShowSummary={showSummary}
        />
        <div className={sourceOpen ? "review-stage open" : "review-stage"}>
          <main ref={mainRef} tabIndex={-1} className={isEmpty && !historyError && !loading ? "main main-empty" : "main"}>
            {loading ? <p className="soft" role="status">正在读取审计归档…</p> : null}
            {historyError ? <p className="error" role="alert">{historyError}</p> : null}
            {isEmpty && !loading ? <UploadPanel {...uploadHandlers} /> : null}
            {summaryOpen && aggregate ? (
              <BatchSummary
                aggregate={aggregate}
                samples={samples}
                filters={summaryFilters}
                onFiltersChange={changeSummaryFilters}
                onSelect={selectSample}
                onReturn={activeSample ? () => selectSample(activeSample.sample_id) : undefined}
              />
            ) : null}
            <div hidden={summaryOpen}>
              {activeSample && !activeSample.ok ? <p className="error" role="alert" data-testid="sample-error">
                算法失败：{activeSample.error?.code || "sample_failed"}: {activeSample.error?.message || "该样本审计失败"}。检测结果未提供{activeSample.bundle ? "；可继续查看原文与已解析信息。" : "。"}
              </p> : null}
              {activeSample?.bundle ? (
                <BundleView
                  key={activeSample.sample_id}
                  bundle={activeSample.bundle}
                  sourceOpen={sourceOpen}
                  sourceEventId={sourceEventId}
                  onSelectSourceEvent={setSourceEventId}
                />
              ) : null}
            </div>
          </main>
          {sourceOpen && sourceEventId && activeSample?.bundle ? (
            <SourceTrajectoryDrawer
              key={`${sessionId ?? ""}:${activeSample.sample_id}`}
              sessionId={sessionId}
              sampleId={activeSample.sample_id}
              eventId={sourceEventId}
              sourceFormat={activeSample.bundle.sample.source_format}
              notes={sourceNotes}
              onClose={() => setSourceEventId(null)}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}
