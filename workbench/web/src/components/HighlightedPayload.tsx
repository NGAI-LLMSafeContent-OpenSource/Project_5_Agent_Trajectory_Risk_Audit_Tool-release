import { highlightExcerpts, stringifyPayload } from "../format";

type Props = {
  value: unknown;
  excerpts: string[];
  testId: string;
  markTestId?: string;
  failureTestId?: string;
  className?: string;
};

export function HighlightedPayload({
  value,
  excerpts,
  testId,
  markTestId = "evidence-mark",
  failureTestId = "evidence-miss",
  className = "mono soft",
}: Props) {
  const text = stringifyPayload(value);
  const { parts, matched, missed } = highlightExcerpts(text, excerpts);
  return (
    <>
      {excerpts.length > 0 && matched === 0 ? (
        <p className="location-failure" role="alert" data-testid={failureTestId}>
          证据摘录未能在该事件正文中定位，请人工核对原文。
        </p>
      ) : null}
      {excerpts.length > 0 && matched > 0 && missed > 0 ? (
        <p className="hint" data-testid={`${failureTestId}-partial`}>
          部分证据摘录未能命中原文。
        </p>
      ) : null}
      <pre className={className} data-testid={testId}>
        {parts.map((part, index) =>
          part.type === "mark" ? (
            <mark key={`m-${index}`} data-testid={markTestId}>
              {part.value}
            </mark>
          ) : (
            <span key={`t-${index}`}>{part.value}</span>
          ),
        )}
      </pre>
    </>
  );
}
