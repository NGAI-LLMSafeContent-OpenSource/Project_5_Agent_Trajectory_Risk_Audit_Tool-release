import { useEffect, useId, useRef } from "react";
import { dimensionLabel, isAvailable } from "../dimensions";
import type { FindingFocus } from "../findingFocus";
import { findingsByEvent, toolCardIdForEvent } from "../findingFocus";
import { EMPTY_TOOL_NAME, highlightExcerpts, previewLine, stringifyPayload } from "../format";
import type { ReviewBundle, ToolResult } from "../types";
import { UNAVAILABLE_LABEL } from "../types";
import { useSourceRowActivation } from "../sourceRowClick";
import { FindingsList } from "./FindingsList";
import { HighlightedPayload } from "./HighlightedPayload";
import { RaceAlgorithmItems } from "./RaceAlgorithmItems";
import { RaceIntegrity } from "./RaceIntegrity";
import { SourceEventButton } from "./SourceEventButton";

type Finding = ReviewBundle["detected_findings"][number];
type Interaction = ReviewBundle["tool_interactions"][number];

type Props = {
  bundle: ReviewBundle;
  focus: FindingFocus;
  sourceEventId: string | null;
  onSelectFinding: (finding: Finding) => void;
  onSelectSourceEvent: (eventId: string) => void;
};

const STATUS_LABEL: Record<string, string> = { success: "成功", error: "失败" };

export function ToolsTab({ bundle, focus, sourceEventId, onSelectFinding, onSelectSourceEvent }: Props) {
  const { tool_interactions, availability, events, detected_findings } = bundle;
  const focusedCardId =
    focus.status === "focused" ? toolCardIdForEvent(focus.eventId, events) : null;
  const excerpts = focus.status === "focused" ? focus.excerpts : [];
  const focusedInteraction = tool_interactions.find((item) => item.call_event_id === focusedCardId);
  const evidenceMarked =
    focusedInteraction != null &&
    excerpts.length > 0 &&
    highlightExcerpts(
      [argsOf(focusedInteraction), outputOf(focusedInteraction)]
        .map((value) => (value == null ? "" : stringifyPayload(value)))
        .join("\n"),
      excerpts,
    ).matched > 0;
  const linkedFindings = findingsByEvent(detected_findings, events);
  const stepByCall = new Map(bundle.race?.C1.calls.map((call) => [call.event_id, call.step_id]));
  const focusedRef = useRef<HTMLLIElement>(null);
  const listTitleId = useId();
  const activateSource = useSourceRowActivation(onSelectSourceEvent);

  useEffect(() => {
    focusedRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focusedCardId]);

  return (
    <div className="tab-panel" data-testid="tab-tools">
      <section className="block">
        <h3>
          <span id={listTitleId}>工具调用明细</span>
          <span className="sub">按调用顺序；展开查看完整参数与返回</span>
        </h3>
        {!tool_interactions.length ? (
          <p className="empty">本次轨迹没有工具调用。</p>
        ) : (
          <ol className="calls" aria-labelledby={listTitleId}>
            {tool_interactions.map((item, index) => {
              const isFocused = focusedCardId === item.call_event_id;
              const results = resultsOf(item);
              const sourceKind =
                sourceEventId === item.call_event_id
                  ? "call"
                  : results.some((result) => result.event_id === sourceEventId)
                    ? "result"
                    : null;
              const findings = uniqueFindings([
                ...(linkedFindings.get(item.call_event_id) || []),
                ...results.flatMap((result) => linkedFindings.get(result.event_id) || []),
              ]);
              const step = stepByCall.get(item.call_event_id);
              return (
                <li
                  key={item.call_event_id}
                  ref={isFocused ? focusedRef : undefined}
                  className={`call-card${findings.length ? " risk" : ""}${isFocused ? " focus" : ""}${sourceKind === "call" ? " source" : ""}`}
                  data-testid={`tool-card-${item.call_event_id}`}
                  data-focused={isFocused ? "true" : "false"}
                  data-source={sourceKind ?? "false"}
                >
                  <div
                    className="call-row"
                    onMouseDown={activateSource.onMouseDown}
                    onClick={(click) => activateSource.onClick(click, item.call_event_id)}
                  >
                    <span className="call-seq" data-testid="tool-seq">
                      {index + 1}
                      <SourceEventButton eventId={item.call_event_id} onSelect={onSelectSourceEvent} />
                    </span>
                    {step != null ? <span className="badge" data-testid="tool-step">步骤 {step}</span> : null}
                    <code className="call-tool" data-testid="tool-name">
                      {item.tool || EMPTY_TOOL_NAME}
                    </code>
                    <code className="call-args" data-testid="tool-args-preview">
                      {argsOf(item) == null ? UNAVAILABLE_LABEL : previewLine(stringifyPayload(argsOf(item)))}
                    </code>
                    <StatusPill item={item} />
                    <span className="call-findings" data-testid="tool-findings">
                      {isFocused ? <span className="focus-tag">当前焦点</span> : null}
                      {findings.length ? (
                        findings.map((finding) => (
                          <button
                            key={finding.finding_id}
                            type="button"
                            className="evchip risk"
                            onClick={() => onSelectFinding(finding)}
                          >
                            {finding.finding_id} · {(finding.categories || []).join(", ") || UNAVAILABLE_LABEL}
                          </button>
                        ))
                      ) : (
                        <span className="muted">无关联发现</span>
                      )}
                    </span>
                  </div>
                  <div className="kv">
                    <Payload
                      kind="args"
                      item={item}
                      value={argsOf(item)}
                      summary={`参数 · ${item.arguments_status || UNAVAILABLE_LABEL}`}
                      focused={isFocused}
                      excerpts={isFocused ? excerpts : []}
                      sourceSelected={false}
                      onSelectSourceEvent={onSelectSourceEvent}
                    />
                    {results.length ? results.map((result, position) => (
                      <Payload
                        key={result.event_id}
                        kind="result"
                        item={item}
                        result={result}
                        testSuffix={position ? `-${position}` : ""}
                        value={result.output ?? null}
                        summary={resultSummary(result)}
                        focused={isFocused}
                        excerpts={isFocused ? excerpts : []}
                        sourceSelected={sourceEventId === result.event_id}
                        onSelectSourceEvent={onSelectSourceEvent}
                      />
                    )) : (
                      <Payload
                        kind="result"
                        item={item}
                        value={null}
                        summary="返回 · 未配对"
                        focused={isFocused}
                        excerpts={[]}
                        sourceSelected={false}
                        onSelectSourceEvent={onSelectSourceEvent}
                      />
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </section>

      {bundle.race ? (
        <>
          <RaceAlgorithmItems race={bundle.race} items={["C2", "C3"]} />
          <RaceIntegrity race={bundle.race} onSelectSourceEvent={onSelectSourceEvent} />
        </>
      ) : null}

      <FindingsList
        findings={detected_findings}
        focus={focus}
        onSelectFinding={onSelectFinding}
        evidenceMarked={evidenceMarked}
          unavailable={bundle.algorithm_status === "failure"}
      />

      {isAvailable(availability, "tool_permissions") ? (
        <section className="block">
          <h3>{dimensionLabel("tools", "tool_permissions")}</h3>
          <pre className="mono soft">{stringifyPayload(bundle.annotations.tool_permissions)}</pre>
        </section>
      ) : null}
    </div>
  );
}

function StatusPill({ item }: { item: Interaction }) {
  if (!item.result) {
    return (
      <span className="st st-unpaired" data-testid="tool-status">
        未配对
      </span>
    );
  }
  const status = item.result.declared_status || "unknown";
  return (
    <span className={`st st-${status}`} data-testid="tool-status">
      {STATUS_LABEL[status] || "未声明"}
    </span>
  );
}

function Payload({
  kind,
  item,
  result,
  testSuffix = "",
  value,
  summary,
  focused,
  excerpts,
  sourceSelected,
  onSelectSourceEvent,
}: {
  kind: "args" | "result";
  item: Interaction;
  result?: ToolResult;
  testSuffix?: string;
  value: unknown;
  summary: string;
  focused: boolean;
  excerpts: string[];
  sourceSelected: boolean;
  onSelectSourceEvent: (eventId: string) => void;
}) {
  const bodyId = `tool-${kind}-body-${item.call_event_id}${testSuffix}`;
  return (
    <details
      open={focused}
      className={sourceSelected ? "source" : undefined}
      data-testid={`tool-${kind}-${item.call_event_id}${testSuffix}`}
      data-source={sourceSelected ? "true" : "false"}
    >
      <summary>
        {result ? (
          <>
            返回 · <SourceEventButton eventId={result.event_id} onSelect={onSelectSourceEvent} />
            {result.link_status ? ` · 配对：${result.link_status}` : ""}
          </>
        ) : (
          summary
        )}
      </summary>
      {value == null ? (
        <pre className="mono soft" data-testid={bodyId}>
          {UNAVAILABLE_LABEL}
        </pre>
      ) : excerpts.length ? (
        <HighlightedPayload value={value} excerpts={excerpts} testId={bodyId} />
      ) : (
        <pre className="mono soft" data-testid={bodyId}>
          {stringifyPayload(value)}
        </pre>
      )}
    </details>
  );
}

function argsOf(item: Interaction): unknown {
  return item.arguments ?? item.arguments_raw ?? null;
}

/** Older archived bundles carry only the single `result`. */
function resultsOf(item: Interaction): ToolResult[] {
  return item.results ?? (item.result ? [item.result] : []);
}

function outputOf(item: Interaction): unknown {
  const outputs = resultsOf(item).map((result) => result.output ?? null);
  return outputs.length > 1 ? outputs.map((output) => stringifyPayload(output)).join("\n") : outputs[0] ?? null;
}

function resultSummary(result: ToolResult): string {
  const link = result.link_status ? ` · 配对：${result.link_status}` : "";
  return `返回 · ${result.event_id}${link}`;
}

function uniqueFindings(findings: Finding[]): Finding[] {
  return [...new Map(findings.map((finding) => [finding.finding_id, finding])).values()];
}
