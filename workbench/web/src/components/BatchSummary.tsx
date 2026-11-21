import type { RaceAggregate, SessionSample } from "../types";
import { EMPTY_TOOL_NAME } from "../format";
import { UNAVAILABLE_LABEL } from "../types";
import "./BatchSummary.css";

const STATUS_LABELS = {
  all: "全部", unsafe: "有风险", safe: "无风险", undetermined: "未判定", failed: "审计失败",
} as const;
type SummaryStatus = keyof typeof STATUS_LABELS;
export type SummaryFilters = { status: SummaryStatus; tool: string | null };

export function readSummaryFilters(query: URLSearchParams): SummaryFilters {
  const status = query.get("summary_status") ?? "all";
  return {
    status: Object.hasOwn(STATUS_LABELS, status) ? status as SummaryStatus : "all",
    tool: query.has("summary_tool") ? query.get("summary_tool") : null,
  };
}

function statusOf(sample: SessionSample): SummaryStatus {
  if (!sample.ok || sample.bundle?.algorithm_status === "failure") return "failed";
  const verdict = sample.bundle?.verdict.value;
  return verdict === "safe" || verdict === "unsafe" ? verdict : "undetermined";
}

function Metric({ value }: { value: number | string | undefined }) {
  return typeof value === "number"
    ? <>{value.toLocaleString("zh-CN")}</>
    : <span className="batch-unavailable">{UNAVAILABLE_LABEL}</span>;
}

type Props = {
  aggregate: RaceAggregate;
  samples: SessionSample[];
  filters: SummaryFilters;
  onFiltersChange: (filters: SummaryFilters) => void;
  onSelect: (sampleId: string) => void;
  onReturn?: () => void;
};

export function BatchSummary({ aggregate, samples, filters, onFiltersChange, onSelect, onReturn }: Props) {
  const tools = Object.entries(aggregate.tool_counts).sort(([a, ac], [b, bc]) => bc - ac || a.localeCompare(b));
  const largest = Math.max(1, ...tools.map(([, count]) => count));
  const statuses = Object.keys(STATUS_LABELS) as SummaryStatus[];
  const count = (status: SummaryStatus) => status === "all" ? samples.length : samples.filter((sample) => statusOf(sample) === status).length;
  const visible = samples.filter((sample) =>
    (filters.status === "all" || statusOf(sample) === filters.status) &&
    (filters.tool === null || (sample.bundle?.race?.A3.tool_counts[filters.tool] ?? 0) > 0),
  );
  const changeTool = (name: string) => onFiltersChange({ ...filters, tool: filters.tool === name ? null : name });

  return (
    <section className="batch-summary" data-testid="batch-summary" aria-labelledby="batch-summary-title">
      <header className="batch-heading">
        <div>
          <h1 id="batch-summary-title">批次汇总</h1>
        </div>
        {onReturn ? <button type="button" className="btn btn-ghost" onClick={onReturn}>返回当前样本 →</button> : null}
      </header>

      <div className="batch-metrics">
        <div><span>轨迹数量</span><strong>{samples.length}<small>条</small></strong></div>
        <div><span>工具调用总次数</span><strong data-testid="aggregate-tool-calls"><Metric value={aggregate.tool_calls} /><small>次</small></strong></div>
        <div><span>工具种类</span><strong>{tools.length}<small>种</small></strong></div>
        <div><span>审计结果</span><div className="batch-verdict-counts">
          {statuses.filter((status) => status !== "all" && (status !== "undetermined" || count(status) > 0)).map((status) =>
            <div key={status} className={status}><b>{count(status)}</b><small>{STATUS_LABELS[status]}</small></div>,
          )}
        </div></div>
      </div>

      <div className="batch-content">
        <section className="batch-table-card" aria-labelledby="batch-statistics-title">
          <header className="batch-section-heading"><h2 id="batch-statistics-title">各轨迹统计</h2><span>点击轨迹，进入现有审阅页</span></header>
          <div className="batch-filters" role="group" aria-label="筛选审计结果">
            {statuses.filter((status) => status !== "undetermined" || count(status) > 0 || filters.status === status).map((status) =>
              <button type="button" key={status} aria-pressed={filters.status === status} onClick={() => onFiltersChange({ ...filters, status })}>
                {STATUS_LABELS[status]}<span>{count(status)}</span>
              </button>,
            )}
            {filters.tool !== null ? <button type="button" className="batch-tool-filter" onClick={() => onFiltersChange({ ...filters, tool: null })}>
              工具：{filters.tool || EMPTY_TOOL_NAME} ×
            </button> : null}
          </div>
          <div className="batch-table-scroll" tabIndex={0} role="region" aria-label="各轨迹统计表，可横向滚动">
            <table aria-labelledby="batch-statistics-title">
              <thead><tr>
                <th scope="col">轨迹</th><th scope="col">审计结果</th><th scope="col">输入 token</th><th scope="col">输出 token</th>
                <th scope="col">思考字符</th><th scope="col">工具调用 / 种类</th>
              </tr></thead>
              <tbody>{visible.map((sample) => {
                const race = sample.bundle?.race;
                const status = statusOf(sample);
                return <tr key={sample.sample_id}>
                  <th scope="row"><button type="button" className="batch-sample" onClick={() => onSelect(sample.sample_id)}>
                    {sample.sample_id}<span aria-hidden="true">↗</span>
                  </button></th>
                  <td><span className={`batch-verdict ${status}`}>{STATUS_LABELS[status]}</span></td>
                  <td><Metric value={race?.A1.input_tokens} /></td><td><Metric value={race?.A1.output_tokens} /></td>
                  <td>{race && race.A2.absent_body_step_ids.length > 0 && race.B1.segments.length === 0
                    ? <span className="batch-unavailable">未提供思考正文</span>
                    : <><Metric value={race?.A2.known_characters} />
                      {race && race.A2.absent_body_step_ids.length > 0
                        ? <span className="batch-thinking-note">部分正文未提供</span> : null}</>}</td>
                  <td><Metric value={race?.A3.tool_calls} /><span className="batch-slash"> / </span><Metric value={race?.A3.tool_kinds} /></td>
                </tr>;
              })}</tbody>
            </table>
          </div>
          {visible.length === 0 ? <div className="batch-empty">
            <p>当前筛选没有匹配轨迹。</p>
            <button type="button" className="btn btn-ghost" onClick={() => onFiltersChange({ status: "all", tool: null })}>清除筛选</button>
          </div> : null}
          <p className="batch-table-note" role="status">显示 {visible.length} / {samples.length} 条</p>
        </section>

        <section className="batch-tools-card" aria-labelledby="batch-tools-title">
          <header className="batch-section-heading"><h2 id="batch-tools-title">全批次各工具调用次数</h2><span>{aggregate.tool_calls} 次</span></header>
          <p className="batch-help">点击工具筛选已提供调用明细的轨迹</p>
          {tools.length > 0 ? <ul className="batch-tool-bars">{tools.map(([name, total]) => <li key={name}>
            <button type="button" aria-pressed={filters.tool === name} onClick={() => changeTool(name)}>
              <span className="batch-tool-name">{name || EMPTY_TOOL_NAME}</span>
              <span className="batch-bar" aria-hidden="true"><i style={{ width: `${total / largest * 100}%` }} /></span><b>{total}</b>
            </button>
          </li>)}</ul> : <p className="batch-help">本批次没有工具调用。</p>}
        </section>
      </div>
    </section>
  );
}
