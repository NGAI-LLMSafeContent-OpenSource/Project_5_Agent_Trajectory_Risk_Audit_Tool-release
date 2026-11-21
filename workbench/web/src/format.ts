import type { AvailabilityEntry } from "./types";
import { UNAVAILABLE_LABEL } from "./types";

export function availabilityText(
  entry: AvailabilityEntry | undefined,
  formatter?: (entry: AvailabilityEntry) => string,
): string {
  if (!entry || entry.status !== "available") {
    return UNAVAILABLE_LABEL;
  }
  if (formatter) {
    return formatter(entry);
  }
  if (entry.value != null) {
    return String(entry.value);
  }
  return "已提供";
}

export function stringifyPayload(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  return JSON.stringify(value, null, 2) ?? "";
}

export function truncate(value: unknown, max = 240): string {
  const text = stringifyPayload(value);
  if (text.length <= max) {
    return text;
  }
  return `${text.slice(0, max)}…`;
}

const PREVIEW_CHARS = 160;

export function previewLine(text: string): string {
  const line = text.replace(/\s+/g, " ").trim();
  return line.length > PREVIEW_CHARS ? `${line.slice(0, PREVIEW_CHARS)}…` : line;
}

export type HighlightResult = {
  parts: Array<{ type: "text" | "mark"; value: string }>;
  matched: number;
  missed: number;
};

/** Highlight the first occurrence of each excerpt inside plain text. */
export function highlightExcerpts(
  text: string,
  excerpts: string[],
): HighlightResult {
  if (!excerpts.length || !text) {
    return {
      parts: [{ type: "text", value: text }],
      matched: 0,
      missed: excerpts.length,
    };
  }
  const parts: Array<{ type: "text" | "mark"; value: string }> = [];
  let cursor = 0;
  let matched = 0;
  let missed = 0;
  const sorted = [...excerpts]
    .filter(Boolean)
    .map((excerpt) => ({ excerpt, at: text.indexOf(excerpt) }))
    .sort((a, b) => {
      if (a.at < 0 && b.at < 0) return 0;
      if (a.at < 0) return 1;
      if (b.at < 0) return -1;
      return a.at - b.at;
    });

  for (const item of sorted) {
    if (item.at < 0) {
      missed += 1;
      continue;
    }
    if (item.at < cursor) {
      missed += 1;
      continue;
    }
    if (item.at > cursor) {
      parts.push({ type: "text", value: text.slice(cursor, item.at) });
    }
    parts.push({ type: "mark", value: item.excerpt });
    matched += 1;
    cursor = item.at + item.excerpt.length;
  }
  if (cursor < text.length) {
    parts.push({ type: "text", value: text.slice(cursor) });
  }
  return {
    parts: parts.length ? parts : [{ type: "text", value: text }],
    matched,
    missed,
  };
}

export const EVENT_TYPE_LABEL: Record<string, string> = {
  message: "消息",
  thought: "思考",
  tool_call: "工具调用",
  tool_result: "工具返回",
  state_change: "状态变化",
  unsupported_content: "未支持内容",
};

export const EMPTY_TOOL_NAME = "（空工具名）";

export function stepsText(stepIds: number[]): string {
  return stepIds.length ? `步骤 ${stepIds.join("、")}` : "无";
}

/** Where a race item points: a step, a raw message, or a metadata field. */
export function locationText(location: { step_id?: number; message_index?: number; field?: string }): string {
  const parts = [];
  if (location.step_id != null) parts.push(`步骤 ${location.step_id}`);
  if (location.message_index != null) parts.push(`messages[${location.message_index}]`);
  if (location.message_index == null && location.field) parts.push(location.field);
  return parts.join(" · ");
}

export function verdictLabel(value: string): string {
  if (value === "unsafe") return "不安全";
  if (value === "safe") return "安全";
  if (value === "undetermined") return "未判定";
  return value;
}
