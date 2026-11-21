import type { RaceAlgorithmItem, RaceItems } from "../types";
import { UNAVAILABLE_LABEL } from "../types";
import { locationText } from "../format";

const LABEL = {
  B2: "有无风险",
  B3: "首次风险步骤与片段",
  C2: "不必要的重复调用",
  C3: "执行失败与错误类型",
} as const;

type Key = keyof typeof LABEL;

/** Algorithm items come only from the Provider result; absence and failure are shown, never filled in. */
export function RaceAlgorithmItems({ race, items }: { race: RaceItems; items: Key[] }) {
  return (
    <section className="block" aria-label="风险与异常判定">
      <h3>风险与异常判定</h3>
      <dl className="race-items">
        {items.map((key) => (
          <div key={key} data-testid={`race-item-${key}`}>
            <dt>{LABEL[key]}</dt>
            <dd><AlgorithmItem item={race[key]} /></dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function AlgorithmItem({ item }: { item: RaceAlgorithmItem | undefined }) {
  if (!item) return UNAVAILABLE_LABEL;
  if (item.status !== "available") return `${item.status === "failure" ? "失败" : UNAVAILABLE_LABEL}：${item.reason}`;
  if ("judgment" in item) return `${item.judgment}（算法最终判定）`;
  const rows = "risks" in item ? item.risks : "markers" in item ? item.markers : item.failures;
  if (!rows.length) return "无发现（算法已完成）";
  return <ol className="race-list">{rows.map((row, index) => <li key={index}>
    <b>{locationText(row)}</b>
    {row.previous_step_id != null ? ` · 重复步骤 ${row.previous_step_id}` : null}
    {row.error_type ? ` · ${row.error_type}` : null}
    {row.risk_quote ? <pre className="mono soft">{row.risk_quote}</pre> : null}
    {row.evidence.map((evidence, at) => <div key={at}>
      <span className="hint">{locationText(evidence)}</span>
      <pre className="mono soft">{evidence.quote}</pre>
    </div>)}
  </li>)}</ol>;
}
