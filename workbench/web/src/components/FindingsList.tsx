import type { FindingFocus } from "../findingFocus";
import type { ReviewBundle } from "../types";
import { UNAVAILABLE_LABEL } from "../types";

type Props = {
  findings: ReviewBundle["detected_findings"];
  focus: FindingFocus;
  onSelectFinding: (finding: ReviewBundle["detected_findings"][number]) => void;
  evidenceMarked?: boolean;
  unavailable?: boolean;
};

export function FindingsList({
  findings,
  focus,
  onSelectFinding,
  evidenceMarked = false,
  unavailable = false,
}: Props) {
  return (
    <section className="block" aria-label="检测发现">
      <h3>
        检测发现<span className="sub">点击跳到关联事件并核对证据</span>
      </h3>
      {focus.status === "location_failed" ? (
        <p className="location-failure" role="alert" data-testid="location-failure">
          定位失败：发现 {focus.findingId} 无法绑定到轨迹事件，证据不可在事件视图中点回。
        </p>
      ) : null}
      {focus.status === "focused" ? (
        <p className="focus-banner" data-testid="focus-banner">
          当前焦点事件 <code>{focus.eventId}</code>
          {focus.excerpts.length
            ? evidenceMarked
              ? " · 已标出可用证据摘录"
              : " · 有证据摘录，正在事件正文中核对"
            : ""}
        </p>
      ) : null}
      {!findings.length ? (
        <p className="empty">{unavailable ? "算法未完成，检测发现未提供。" : "本条轨迹没有 Detected Finding。"}</p>
      ) : (
        <ul className="findings">
          {findings.map((finding) => {
            const active =
              (focus.status === "focused" || focus.status === "location_failed") &&
              focus.findingId === finding.finding_id;
            return (
              <li key={finding.finding_id}>
                <button
                  type="button"
                  className={`finding-btn${active ? " active" : ""}`}
                  aria-pressed={active}
                  data-testid={`finding-${finding.finding_id}`}
                  onClick={() => onSelectFinding(finding)}
                >
                  <span className="finding-head">
                    <strong>{(finding.categories || []).join(", ") || `类别${UNAVAILABLE_LABEL}`}</strong>
                    <code className="evchip">{finding.finding_id}</code>
                  </span>
                  <span className="finding-reason">{finding.reason || `原因${UNAVAILABLE_LABEL}`}</span>
                  {finding.located === false ? <span className="finding-unlocated">未定位</span> : null}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
