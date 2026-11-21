import { useEffect, useRef, useState } from "react";
import { stringifyPayload } from "../format";
import type { SourceTrajectory } from "../types";

export type SourceFindingNote = {
  findingId: string;
  categories: string[];
  reason: string;
};

type Props = {
  sessionId: string | null;
  sampleId: string;
  eventId: string;
  sourceFormat: string;
  notes: SourceFindingNote[];
  onClose: () => void;
};

export function SourceTrajectoryDrawer({
  sessionId,
  sampleId,
  eventId,
  sourceFormat,
  notes,
  onClose,
}: Props) {
  const [trajectory, setTrajectory] = useState<SourceTrajectory | null>(null);
  const [failed, setFailed] = useState(false);
  const highlightedRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (!sessionId) {
      setTrajectory(null);
      setFailed(true);
      return;
    }
    const controller = new AbortController();
    setTrajectory(null);
    setFailed(false);
    const url = `/api/audit-sessions/${sessionId}/samples/${encodeURIComponent(sampleId)}/source-trajectory`;
    fetch(url, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("source trajectory request failed");
        const payload = (await response.json()) as SourceTrajectory;
        if (controller.signal.aborted) return;
        if (!payload.ok) throw new Error("source trajectory unavailable");
        setTrajectory(payload);
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => controller.abort();
  }, [sessionId, sampleId]);

  const available = trajectory?.availability.status === "available";
  const steps = available ? trajectory.steps : [];
  const highlightedPath = steps.find((step) => step.event_ids.includes(eventId))?.json_path;
  const format = trajectory?.source_format || sourceFormat;

  useEffect(() => {
    highlightedRef.current?.scrollIntoView({ block: "nearest" });
  }, [eventId, highlightedPath]);

  return (
    <aside className="source-trajectory" aria-label="原始轨迹">
      <header>
        <div className="source-trajectory-title">
          <strong>原始轨迹</strong>
          <span className="badge" data-testid="source-trajectory-format">
            {format}
          </span>
          <button type="button" className="source-trajectory-close" onClick={onClose}>
            关闭
          </button>
        </div>
        <p>
          当前事件 <b data-testid="source-trajectory-event">{eventId}</b>
        </p>
        {notes.map((note) => (
          <div
            key={note.findingId}
            className="source-finding"
            data-testid={`source-finding-${note.findingId}`}
          >
            <b>{note.findingId}</b>
            <span className="source-finding-categories">{note.categories.join(", ")}</span>
            <span className="source-finding-reason">{note.reason}</span>
          </div>
        ))}
      </header>
      {trajectory === null && !failed ? <p className="source-trajectory-note">正在读取原始轨迹…</p> : null}
      {failed ? <p className="source-trajectory-note">原始轨迹读取失败</p> : null}
      {trajectory?.availability.status === "unavailable" ? (
        <p className="source-trajectory-note">
          {trajectory.availability.reason ?? "原始轨迹不可用"}
        </p>
      ) : null}
      {available && highlightedPath === undefined ? (
        <p className="source-trajectory-note">{eventId} 无法定位</p>
      ) : null}
      <div className="source-trajectory-body">
        {steps.map((step) => {
          const isHighlighted = step.json_path === highlightedPath && highlightedPath !== undefined;
          return (
            <pre
              key={step.json_path}
              ref={isHighlighted ? highlightedRef : undefined}
              className={isHighlighted ? "source-step highlighted" : "source-step"}
              data-testid={`source-step-${step.json_path}`}
              data-highlighted={isHighlighted ? "true" : "false"}
            >
              {stringifyPayload(step.raw)}
            </pre>
          );
        })}
      </div>
    </aside>
  );
}
