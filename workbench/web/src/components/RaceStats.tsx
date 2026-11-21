import { EMPTY_TOOL_NAME } from "../format";
import type { RaceItems } from "../types";

type Props = { race: RaceItems };

export function RaceStats({ race }: Props) {
  const { A1, A2, A3 } = race;
  const missingThinking = A2.absent_body_step_ids.length > 0;
  const noThinkingBody = missingThinking && race.B1.segments.length === 0;
  return (
    <section className="block" aria-label="数据统计指标" data-testid="race-stats">
      <h3>数据统计指标</h3>
      <div className="grid-kpi">
        <Kpi id="race-input-tokens" label="输入 token" value={A1.input_tokens} sub={tokenSub(A1.input_tokens)} />
        <Kpi id="race-output-tokens" label="输出 token" value={A1.output_tokens} sub={tokenSub(A1.output_tokens)} />
        <Kpi
          id="race-thinking-characters"
          label="已保存思考字符数"
          value={noThinkingBody ? "未提供思考正文" : A2.known_characters}
          sub={noThinkingBody ? "" : missingThinking ? "仅统计已保存正文，部分正文未提供" : "含空格和换行"}
        />
        <Kpi id="race-tool-calls" label="工具调用总次数" value={A3.tool_calls} sub="按原始调用计数" />
        <Kpi id="race-tool-kinds" label="工具种类数" value={A3.tool_kinds} sub="名称区分大小写" />
      </div>
      <p className="hint race-basis">取自当前保存请求的 usage，思考按 Unicode 码点计数，含空格和换行</p>
    </section>
  );
}

function tokenSub(value: number | string): string {
  return typeof value === "number" ? "当前保存的请求" : "来源没有这项";
}

function Kpi({ id, label, value, sub }: { id: string; label: string; value: number | string; sub: string }) {
  return (
    <div className="k" data-testid={id}>
      <small>{label}</small>
      <strong className={typeof value === "number" ? undefined : "is-absent"}>{value}</strong>
      <span>{sub}</span>
    </div>
  );
}

export function ToolCounts({ label, counts }: { label: string; counts: Record<string, number> }) {
  const rows = Object.entries(counts);
  if (!rows.length) return <p className="hint">没有工具调用。</p>;
  return (
    <table className="tbl" aria-label={label}>
      <thead>
        <tr>
          <th>工具（原始名称）</th>
          <th>调用次数</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([name, count]) => (
          <tr key={name}>
            <td className="mono">{name || EMPTY_TOOL_NAME}</td>
            <td>{count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
