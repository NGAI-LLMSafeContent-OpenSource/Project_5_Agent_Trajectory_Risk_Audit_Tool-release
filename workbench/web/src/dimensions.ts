import type { ReviewBundle, TabId } from "./types";

type Metrics = ReviewBundle["metrics"];

export const TAB_DIMENSIONS: Record<TabId, Array<{ key: string; label: string }>> = {
  stats: [
    { key: "wall_clock", label: "耗时" },
    { key: "tool_success_rate", label: "工具成功率" },
  ],
  cot: [
    { key: "key_tokens", label: "关键 token" },
    { key: "thinking_patterns", label: "危险思维模式" },
    { key: "provenance", label: "风险溯源" },
  ],
  tools: [{ key: "tool_permissions", label: "权限对照" }],
};

export const STATS_METRICS = {
  events: { label: "事件数", read: (m: Metrics) => m.events?.total },
  tool_calls: { label: "工具调用", read: (m: Metrics) => m.tools?.calls },
  tool_results: { label: "工具返回", read: (m: Metrics) => m.tools?.results },
  failed_results: { label: "失败或被拒", read: (m: Metrics) => m.tools?.error_declared_results },
  findings: { label: "检测发现", read: (m: Metrics) => m.audit?.finding_count },
  by_type: { label: "事件构成", read: (m: Metrics) => m.events?.by_type },
  categories: { label: "检测发现类别", read: (m: Metrics) => m.audit?.categories },
} as const;

export type StatsMetricKey = keyof typeof STATS_METRICS;

export function dimensionLabel(tab: TabId, key: string): string {
  return TAB_DIMENSIONS[tab].find((item) => item.key === key)?.label ?? key;
}

export function isAvailable(availability: ReviewBundle["availability"], key: string): boolean {
  return availability[key]?.status === "available";
}

export function metricNumber(metrics: Metrics, key: StatsMetricKey): number | null {
  const value = STATS_METRICS[key].read(metrics);
  return typeof value === "number" ? value : null;
}

export function metricCounts(metrics: Metrics, key: StatsMetricKey): Array<[string, number]> | null {
  const value = STATS_METRICS[key].read(metrics);
  if (!value || typeof value !== "object") return null;
  return Object.entries(value).filter((entry): entry is [string, number] => typeof entry[1] === "number");
}
