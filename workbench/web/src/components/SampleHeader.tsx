import { verdictLabel } from "../format";
import { bundleTitle } from "../sampleIndex";
import type { ReviewBundle } from "../types";
import { UNAVAILABLE_LABEL } from "../types";

type Props = { bundle: ReviewBundle };

const DETECTED_LAYER = "检测判定";
// The archive note only repeats that the verdict came from the audit algorithm; it says nothing a reviewer needs.
const ARCHIVE_VERDICT_NOTES = new Set([
  "算法原始判定，详见算法结果与原始归档。",
  "课题四原始判定，详见 provider_result 和原始归档。",
]);

export function SampleHeader({ bundle }: Props) {
  const { sample, verdict, provenance } = bundle;
  const verdictNote =
    verdict.reason && ARCHIVE_VERDICT_NOTES.has(verdict.reason)
      ? null
      : verdict.reason || verdict.basis || `判定依据：${UNAVAILABLE_LABEL}`;

  return (
    <header className="case" data-testid="sample-header">
      <section
        className={`layer-detected v-${verdict.value}`}
        aria-label={DETECTED_LAYER}
        data-testid="verdict-badge"
      >
        <small>{DETECTED_LAYER}</small>
        <strong>{verdictLabel(verdict.value)}</strong>
        {verdictNote ? <span>{verdictNote}</span> : null}
      </section>
      <div className="case-main">
        <h1>{bundleTitle(bundle)}</h1>
        <p className="case-id mono">{sample.sample_id}</p>
        <div className="case-meta">
          <span className="badge">来源格式：{sample.source_format}</span>
          <span>
            数据集：{sample.dataset.id}@{sample.dataset.version}
          </span>
          <span className="mono">run_id：{provenance.run_id}</span>
        </div>
      </div>
    </header>
  );
}
