import { locationText, stringifyPayload } from "../format";
import type { RaceItems, RaceLocation } from "../types";
import { UNAVAILABLE_LABEL } from "../types";

type Props = { race: RaceItems; onSelectSourceEvent: (eventId: string) => void };

export function RaceIntegrity({ race, onSelectSourceEvent }: Props) {
  const { C1, C4, C5 } = race;
  const jump = (location: RaceLocation) =>
    location.event_id ? (
      <button type="button" className="evchip" onClick={() => onSelectSourceEvent(location.event_id!)}>
        查看原文
      </button>
    ) : null;

  return (
    <>
      <section className="block" aria-label="未配对返回" data-testid="race-unmatched">
        <h3>
          未配对返回<span className="sub">无法确定对应调用，保留原文，不猜配</span>
        </h3>
        {C1.unmatched.length ? (
          <ol className="race-list">
            {C1.unmatched.map((item) => (
              <li key={item.event_id}>
                <span>{locationText(item)} · tool_call_id：{idText(item.tool_call_id)} · {item.issue}</span>
                {jump(item)}
                <pre className="mono soft">{item.content == null ? UNAVAILABLE_LABEL : stringifyPayload(item.content)}</pre>
              </li>
            ))}
          </ol>
        ) : (
          <p className="hint">无。</p>
        )}
      </section>

      <section className="block" aria-label="完整性检查" data-testid="race-integrity">
        <h3>
          完整性检查<span className="sub">缺失、null 与合法空值分开；只认记录中的明确截断标记</span>
        </h3>
        <dl className="race-items">
          <div data-testid="race-completion">
            <dt>会话完成状态（metadata.record.is_session_completed）</dt>
            <dd>{completionText(C4.completion)}</dd>
          </div>
        </dl>
        <IssueList testId="race-missing-fields" title="必填字段" rows={C4.missing_fields.map((item) => ({
          location: item, text: `${item.field} ${item.issue}`,
        }))} jump={jump} />
        <IssueList testId="race-integrity-unmatched" title="调用与返回配对" rows={C4.unmatched.map((item) => ({
          location: item, text: `tool_call_id：${idText(item.tool_call_id)} · ${item.issue}`,
        }))} jump={jump} />
        <IssueList testId="race-truncations" title="明确截断" rows={C4.truncations.map((item) => ({
          location: item,
          text: item.quote ?? `${item.field} = ${JSON.stringify(item.value)}`,
        }))} jump={jump} />
      </section>

      <section className="block" aria-label="问题定位与原文" data-testid="race-issues">
        <h3>
          问题定位与原文<span className="sub">由重复调用、执行失败与完整性检查派生</span>
        </h3>
        {C5.pending?.map((item) => (
          <p key={item.source_subitem} className="hint" data-testid={`race-pending-${item.source_subitem}`}>
            {item.source_subitem}：{item.status === "failure" ? "失败" : UNAVAILABLE_LABEL}
            {item.reason ? `，${item.reason}` : ""}
          </p>
        ))}
        {C5.issues.length ? (
          <ol className="race-list">
            {C5.issues.map((issue, index) => (
              <li key={index}>
                <span>
                  <b>{issue.source_subitem}</b> · {locationText(issue)}
                  {issue.field && issue.message_index != null ? ` · ${issue.field}` : ""}
                  {issue.issue ? ` · ${issue.issue}` : ""}
                </span>
                {jump(issue)}
                {issue.evidence.map((evidence, at) => (
                  <pre key={at} className="mono soft">{evidence.quote}</pre>
                ))}
              </li>
            ))}
          </ol>
        ) : (
          <p className="hint">完整性检查无问题。</p>
        )}
      </section>
    </>
  );
}

function IssueList({ testId, title, rows, jump }: {
  testId: string;
  title: string;
  rows: Array<{ location: RaceLocation & { field?: string }; text: string }>;
  jump: (location: RaceLocation) => React.ReactNode;
}) {
  return (
    <div data-testid={testId}>
      <h4>{title}</h4>
      {rows.length ? (
        <ul className="race-list">
          {rows.map((row, index) => (
            <li key={index}>
              <span>{locationText(row.location)}：<span className="mono">{row.text}</span></span>
              {jump(row.location)}
            </li>
          ))}
        </ul>
      ) : (
        <p className="hint">无</p>
      )}
    </div>
  );
}

function idText(value: unknown): string {
  if (value === undefined) return "缺失";
  if (value === null) return "null";
  return String(value);
}

function completionText(value: boolean | null | string): string {
  if (value === true) return "true（已完成）";
  if (value === false) return "false（未完成）";
  if (value === null) return "null（来源记录为 null）";
  return value;
}
