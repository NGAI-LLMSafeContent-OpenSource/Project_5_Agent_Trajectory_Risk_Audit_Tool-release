import { useEffect, useId, useRef, useState } from "react";
import { dimensionLabel, isAvailable, metricCounts } from "../dimensions";
import { findingsByEvent, type FindingFocus } from "../findingFocus";
import {
  EMPTY_TOOL_NAME,
  EVENT_TYPE_LABEL,
  highlightExcerpts,
  previewLine,
  stringifyPayload,
  truncate,
} from "../format";
import type { ReviewBundle } from "../types";
import { UNAVAILABLE_LABEL } from "../types";
import { useSourceRowActivation } from "../sourceRowClick";
import { FindingsList } from "./FindingsList";
import { HighlightedPayload } from "./HighlightedPayload";
import { RaceAlgorithmItems } from "./RaceAlgorithmItems";
import { RaceThinking } from "./RaceThinking";
import { SourceEventButton } from "./SourceEventButton";

type Finding = ReviewBundle["detected_findings"][number];
type TrajectoryEvent = ReviewBundle["events"][number];

type Props = {
  bundle: ReviewBundle;
  focus: FindingFocus;
  sourceEventId: string | null;
  onSelectFinding: (finding: Finding) => void;
  onSelectSourceEvent: (eventId: string) => void;
};

const ANNOTATION_KEYS = ["provenance", "key_tokens", "thinking_patterns"] as const;

export function CotTab({ bundle, focus, sourceEventId, onSelectFinding, onSelectSourceEvent }: Props) {
  const { availability, annotations, events, detected_findings } = bundle;
  const focusedEventId = focus.status === "focused" ? focus.eventId : null;
  const excerpts = focus.status === "focused" ? focus.excerpts : [];
  const focusedEvent = events.find((event) => event.event_id === focusedEventId);
  const evidenceMarked =
    focusedEvent != null &&
    excerpts.length > 0 &&
    highlightExcerpts(eventText(focusedEvent), excerpts).matched > 0;
  const linkedFindings = findingsByEvent(detected_findings, events);
  const byType = new Map(metricCounts(bundle.metrics, "by_type") ?? []);
  const thoughtCount = byType.get("thought");
  const focusedRef = useRef<HTMLLIElement>(null);
  const streamTitleId = useId();
  const activateSource = useSourceRowActivation(onSelectSourceEvent);

  useEffect(() => {
    focusedRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focusedEventId]);

  return (
    <div className="tab-panel cot-grid" data-testid="tab-cot">
      <div className="stack-col">
      {bundle.race ? <RaceThinking b1={bundle.race.B1} onSelectSourceEvent={onSelectSourceEvent} /> : null}
      <section className="block">
        <h3>
          <span id={streamTitleId}>思考与交互全文</span>
          <span className="sub">按事件顺序；红色编号为 Detected Finding 引用的事件</span>
        </h3>
        <ol className="stream" aria-labelledby={streamTitleId}>
          {events.map((event) => {
            const isFocused = focusedEventId === event.event_id;
            const isSource = sourceEventId === event.event_id;
            const linked = linkedFindings.get(event.event_id) || [];
            return (
              <li
                key={event.event_id}
                ref={isFocused ? focusedRef : undefined}
                className={`si ty-${event.type}${event.actor === "system" ? " ty-system" : ""}${linked.length ? " risk" : ""}${isFocused ? " focus" : ""}${isSource ? " source" : ""}`}
                data-testid={`event-${event.event_id}`}
                data-focused={isFocused ? "true" : "false"}
                data-source={isSource ? "true" : "false"}
                onMouseDown={activateSource.onMouseDown}
                onClick={(click) => activateSource.onClick(click, event.event_id)}
              >
                <div className="si-gut">
                  <SourceEventButton eventId={event.event_id} onSelect={onSelectSourceEvent} />
                  <b>{eventKind(event)}</b>
                </div>
                <div className="si-body">
                  <EventBody event={event} excerpts={isFocused ? excerpts : []} />
                </div>
                <div className="si-role">
                  {isFocused ? <span className="focus-tag">当前焦点</span> : null}
                  {linked.map((finding) => (
                    <button
                      key={finding.finding_id}
                      type="button"
                      className="evchip risk"
                      onClick={() => onSelectFinding(finding)}
                    >
                      {finding.finding_id}
                    </button>
                  ))}
                </div>
              </li>
            );
          })}
        </ol>
        {!events.length ? <p className="hint">本条轨迹没有可展示的事件。</p> : null}
      </section>
      </div>

      <aside className="cot-side">
        {bundle.race ? <RaceAlgorithmItems race={bundle.race} items={["B2", "B3"]} /> : null}
        <section className="block" aria-label="思考概况">
          <h3>思考概况</h3>
          <dl className="mini">
            <div data-testid="cot-thought-count">
              <dt>思考段落</dt>
              <dd>{thoughtCount ?? UNAVAILABLE_LABEL}</dd>
            </div>
            <div data-testid="cot-message-count">
              <dt>消息</dt>
              <dd>{byType.get("message") ?? UNAVAILABLE_LABEL}</dd>
            </div>
          </dl>
          {thoughtCount === 0 ? (
            <p className="hint">本条轨迹没有可观察分析过程（显式 thought 事件）。</p>
          ) : null}
        </section>

        <FindingsList
          findings={detected_findings}
          focus={focus}
          onSelectFinding={onSelectFinding}
          evidenceMarked={evidenceMarked}
          unavailable={bundle.algorithm_status === "failure"}
        />

        {ANNOTATION_KEYS.filter((key) => isAvailable(availability, key)).map((key) => (
          <section key={key} className="block">
            <h3>{dimensionLabel("cot", key)}</h3>
            <pre className="mono soft">{truncate(annotations[key], 800)}</pre>
          </section>
        ))}
      </aside>
    </div>
  );
}

function EventBody({ event, excerpts }: { event: TrajectoryEvent; excerpts: string[] }) {
  const testId = `event-body-${event.event_id}`;
  if (excerpts.length) {
    return <HighlightedPayload value={eventText(event)} excerpts={excerpts} testId={testId} className="txt" />;
  }
  if (event.type === "tool_call") {
    const args = event.content.arguments ?? event.content.arguments_raw ?? {};
    return (
      <code className="call" data-testid={testId}>
        {`${String(event.content.tool || EMPTY_TOOL_NAME)}(${JSON.stringify(args)})`}
      </code>
    );
  }
  if (event.type === "tool_result") {
    return (
      <code className="res-line" data-testid={testId}>
        {previewLine(stringifyPayload(event.content.output ?? ""))}
      </code>
    );
  }
  if (event.actor === "system") {
    return <SystemPrompt text={eventText(event)} testId={testId} />;
  }
  return (
    <p className="txt" data-testid={testId}>
      {eventText(event)}
    </p>
  );
}

function SystemPrompt({ text, testId }: { text: string; testId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <details className="sys" data-testid={testId} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>{open ? "收起系统提示" : previewLine(text)}</summary>
      <p className="txt">{text}</p>
    </details>
  );
}

function eventKind(event: TrajectoryEvent): string {
  if (event.type === "message") {
    if (event.actor === "system") return "系统提示";
    if (event.actor === "user") return "用户";
    return "答复";
  }
  return EVENT_TYPE_LABEL[event.type] || event.type;
}

function eventText(event: TrajectoryEvent): string {
  const content = event.content || {};
  if (typeof content.text === "string") return content.text;
  if (event.type === "tool_result" && content.output != null) return stringifyPayload(content.output);
  return JSON.stringify(content);
}
