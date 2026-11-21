import { previewLine, stepsText } from "../format";
import type { RaceItems } from "../types";

type Props = { b1: RaceItems["B1"]; onSelectSourceEvent: (eventId: string) => void };

export function RaceThinking({ b1, onSelectSourceEvent }: Props) {
  return (
    <section className="block" aria-label="思考内容原文" data-testid="race-thinking">
      <h3>
        思考内容原文<span className="sub">按原始顺序展示已保存的 reasoning_content；同一回复共享的思考只出现一次</span>
      </h3>
      {b1.segments.length ? (
        <ol className="race-segments">
          {b1.segments.map((segment) => (
            <li key={segment.event_id}>
              <details data-testid={`race-segment-${segment.event_id}`}>
                <summary>
                  <b>{stepsText(segment.step_ids)}</b> · messages[{segment.message_index}] · {segment.characters} 字符
                  <span className="muted"> {previewLine(segment.text)}</span>
                </summary>
                <pre className="mono soft" data-testid="race-thinking-text">{segment.text}</pre>
              </details>
              <button type="button" className="evchip" onClick={() => onSelectSourceEvent(segment.event_id)}>
                查看原文
              </button>
            </li>
          ))}
        </ol>
      ) : (
        <p className="hint">未提供思考正文</p>
      )}
      {b1.segments.length && b1.absent_body_step_ids.length ? (
        <p className="hint">部分正文未提供</p>
      ) : null}
    </section>
  );
}
