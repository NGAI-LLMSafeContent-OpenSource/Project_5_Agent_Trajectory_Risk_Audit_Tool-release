import {
  dimensionLabel,
  isAvailable,
  metricCounts,
  metricNumber,
  STATS_METRICS,
  type StatsMetricKey,
} from "../dimensions";
import { availabilityText, EMPTY_TOOL_NAME, EVENT_TYPE_LABEL, verdictLabel } from "../format";
import type { AvailabilityEntry, ReviewBundle } from "../types";
import { UNAVAILABLE_LABEL } from "../types";
import { RaceStats } from "./RaceStats";

type Props = { bundle: ReviewBundle };

type KpiItem = { id: string; label: string; value: string; sub: string; tone?: "risk" | "warn" };

export function StatsTab({ bundle }: Props) {
  const { metrics, availability, tool_interactions } = bundle;
  const toolRows = toolUsage(tool_interactions);
  const byType = metricCounts(metrics, "by_type");
  const categories = metricCounts(metrics, "categories");

  const tokenState = tokenQuantityState(availability.token_usage);
  const kpis: KpiItem[] = [];
  for (const key of ["wall_clock"]) {
    if (isAvailable(availability, key)) {
      kpis.push({
        id: key,
        label: dimensionLabel("stats", key),
        value: availabilityText(availability[key]),
        sub: availability[key].basis || "来源提供",
      });
    }
  }
  const metricKpi = (key: StatsMetricKey, sub: (value: number) => string, tone?: (value: number) => KpiItem["tone"]) => {
    const value = metricNumber(metrics, key);
    if (value == null) return;
    kpis.push({ id: key, label: STATS_METRICS[key].label, value: String(value), sub: sub(value), tone: tone?.(value) });
  };
  metricKpi("events", () => "归一后可观察事件");
  metricKpi("tool_calls", () => `${toolRows.length} 种工具`);
  metricKpi("tool_results", () => "可观察的返回事件");
  metricKpi("failed_results", () => "返回声明为错误", (value) => (value > 0 ? "warn" : undefined));
  if (isAvailable(availability, "tool_success_rate")) {
    const rate = availability.tool_success_rate;
    kpis.push({
      id: "tool_success_rate",
      label: dimensionLabel("stats", "tool_success_rate"),
      value: availabilityText(rate, (entry) =>
        entry.value != null ? `${Math.round(entry.value * 100)}%` : UNAVAILABLE_LABEL,
      ),
      sub:
        rate.total != null
          ? `成功 ${rate.success ?? UNAVAILABLE_LABEL} / ${rate.total}`
          : rate.basis || "来源提供",
    });
  }
  metricKpi(
    "findings",
    () => `检测判定 ${verdictLabel(bundle.verdict.value)}`,
    (value) => (value > 0 ? "risk" : undefined),
  );

  const breakdown =
    byType || categories ? (
      <div className="stack-col">
        {byType ? (
          <section className="block" aria-label={STATS_METRICS.by_type.label}>
            <h3>{STATS_METRICS.by_type.label}</h3>
            <Bars
              rows={byType
                .filter(([, count]) => count > 0)
                .map(([key, count]) => ({ key, label: EVENT_TYPE_LABEL[key] || key, count }))}
              cls="e"
            />
          </section>
        ) : null}
        {categories ? (
          <section className="block" aria-label={STATS_METRICS.categories.label}>
            <h3>{STATS_METRICS.categories.label}</h3>
            {categories.length ? (
              <Bars rows={categories.map(([key, count]) => ({ key, label: key, count }))} cls="c" mono />
            ) : (
              <p className="hint">没有检测发现。</p>
            )}
          </section>
        ) : null}
      </div>
    ) : null;

  return (
    <div className="stats" data-testid="tab-stats">
      {bundle.race ? <RaceStats race={bundle.race} /> : (
        <section className="block" aria-label="Token 数量" data-testid="token-quantity">
          <h3>Token 数量</h3>
          <div className="grid-kpi">
            {TOKEN_PARTS.map((part) => (
              <div key={part.id} className="k" data-testid={`token-${part.id}`}>
                <small>{part.label}</small>
                <strong className={tokenState.status === UNAVAILABLE_LABEL ? "is-absent" : undefined}>
                  {tokenState.status}
                </strong>
                <span>{tokenState.reason}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {kpis.length ? (
        <section className="block" aria-label="交互概况">
          <h3>交互概况</h3>
          <div className="grid-kpi quiet">
            {kpis.map((item) => (
              <div
                key={item.id}
                className={`k${item.tone ? ` ${item.tone}` : ""}`}
                data-testid={`kpi-${item.id}`}
              >
                <small>{item.label}</small>
                <strong>{item.value}</strong>
                <span>{item.sub}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <div className={breakdown ? "two" : undefined}>
        <section className="block">
          <h3>工具使用</h3>
          {toolRows.length ? (
            <table className="tbl" aria-label="工具使用">
              <thead>
                <tr>
                  <th>工具</th>
                  <th>调用</th>
                  <th>成功</th>
                  <th>失败或被拒</th>
                  <th>未声明状态</th>
                  <th>未返回</th>
                </tr>
              </thead>
              <tbody>
                {toolRows.map((row) => (
                  <tr key={row.tool}>
                    <td className="mono">{row.tool}</td>
                    <td>{row.calls}</td>
                    <td>{row.success}</td>
                    <td className={row.failed ? "neg" : ""}>{row.failed}</td>
                    <td>{row.undeclared}</td>
                    <td>{row.unreturned}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="hint">本次轨迹没有工具调用。</p>
          )}
        </section>
        {breakdown}
      </div>
    </div>
  );
}

const TOKEN_PARTS = [
  { id: "input", label: "输入 token" },
  { id: "output", label: "输出 token" },
  { id: "thinking", label: "思考 token" },
] as const;

function tokenQuantityState(entry: AvailabilityEntry | undefined): {
  status: string;
  reason: string;
} {
  if (!entry) {
    return {
      status: UNAVAILABLE_LABEL,
      reason: "Review Bundle 未包含该维度",
    };
  }
  if (entry.status === "not_applicable") {
    const reason = entry.reason || "未说明原因";
    return { status: "不适用", reason };
  }
  if (entry.status === "available") {
    return {
      status: UNAVAILABLE_LABEL,
      reason: "来源未分别提供输入、输出、思考 token",
    };
  }
  const reason = entry.reason || "未说明原因";
  return {
    status: UNAVAILABLE_LABEL,
    reason,
  };
}

function Bars({
  rows,
  cls,
  mono = false,
}: {
  rows: Array<{ key: string; label: string; count: number }>;
  cls: string;
  mono?: boolean;
}) {
  const max = Math.max(1, ...rows.map((row) => row.count));
  return (
    <div className="bars">
      {rows.map((row) => (
        <div key={row.key} className="bar-row" data-testid={`bar-${row.key}`}>
          <span className={`bar-label${mono ? " mono" : ""}`}>{row.label}</span>
          <span className="bar-track">
            <i className={`${cls}-${row.key}`} style={{ width: `${(row.count / max) * 100}%` }} />
          </span>
          <span className="bar-num">{row.count}</span>
        </div>
      ))}
    </div>
  );
}

// Buckets follow the statuses Python normalises to (success / error / unknown).
function toolUsage(interactions: ReviewBundle["tool_interactions"]) {
  const rows = new Map<
    string,
    { tool: string; calls: number; success: number; failed: number; undeclared: number; unreturned: number }
  >();
  for (const item of interactions) {
    const tool = item.tool || EMPTY_TOOL_NAME;
    const row = rows.get(tool) || { tool, calls: 0, success: 0, failed: 0, undeclared: 0, unreturned: 0 };
    row.calls += 1;
    const status = item.result?.declared_status;
    if (!item.result) row.unreturned += 1;
    else if (status === "success") row.success += 1;
    else if (status === "error") row.failed += 1;
    else row.undeclared += 1;
    rows.set(tool, row);
  }
  return [...rows.values()];
}
