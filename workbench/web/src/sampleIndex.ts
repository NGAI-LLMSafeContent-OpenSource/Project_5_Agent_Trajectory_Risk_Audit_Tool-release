import { verdictLabel } from "./format";
import type { ReviewBundle, SessionSample } from "./types";

const TITLE_MAX_CHARS = 40;

export type SampleGroupId = "unsafe" | "safe" | "other" | "failed";

export type SampleEntry = {
  sample: SessionSample;
  title: string;
};

export type SampleGroup = {
  id: SampleGroupId;
  label: string;
  entries: SampleEntry[];
};

const GROUPS: { id: SampleGroupId; label: string }[] = [
  { id: "unsafe", label: verdictLabel("unsafe") },
  { id: "safe", label: verdictLabel("safe") },
  { id: "other", label: "其他" },
  { id: "failed", label: "审计失败" },
];

export function sampleTitle(sample: SessionSample): string {
  return sample.bundle ? bundleSubtitle(sample.bundle) : "";
}

/** First user message that is not an XML/environment injection. */
export function bundleSubtitle(bundle: ReviewBundle): string {
  const firstReal = bundle.events.find(
    (event) =>
      event.type === "message" &&
      event.actor === "user" &&
      typeof event.content.text === "string" &&
      !event.content.text.trimStart().startsWith("<"),
  );
  const raw = firstReal?.content.text;
  const text = typeof raw === "string" ? raw.replace(/\s+/g, " ").trim() : "";
  if (!text) return "";
  const chars = Array.from(text);
  if (chars.length <= TITLE_MAX_CHARS) return text;
  return `${chars.slice(0, TITLE_MAX_CHARS).join("").trimEnd()}…`;
}

/** Kept for external callers that need the full title string. */
export function bundleTitle(bundle: ReviewBundle): string {
  return bundleSubtitle(bundle) || bundle.sample.sample_id;
}

/** Must read only the Detected Finding verdict, never Source Labels. */
function groupOf(sample: SessionSample): SampleGroupId {
  if (!sample.ok || !sample.bundle) return "failed";
  const verdict = sample.bundle.verdict.value;
  if (verdict === "unsafe") return "unsafe";
  if (verdict === "safe") return "safe";
  return "other";
}

export function groupSamples(samples: SessionSample[], query: string): SampleGroup[] {
  const needle = query.trim().toLowerCase();
  const entries = samples
    .map((sample) => ({ sample, title: sampleTitle(sample) }))
    .filter(
      ({ sample, title }) =>
        !needle ||
        sample.sample_id.toLowerCase().includes(needle) ||
        title.toLowerCase().includes(needle),
    );
  return GROUPS.map((group) => ({
    ...group,
    entries: entries.filter(({ sample }) => groupOf(sample) === group.id),
  })).filter((group) => group.entries.length > 0);
}
