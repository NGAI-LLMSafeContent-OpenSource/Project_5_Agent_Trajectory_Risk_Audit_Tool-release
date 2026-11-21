import { forwardRef, useEffect, useMemo, useRef, useState } from "react";
import { verdictLabel } from "../format";
import { groupSamples, type SampleEntry } from "../sampleIndex";
import type { RaceAggregate, SessionSample } from "../types";

type Props = {
  samples: SessionSample[];
  activeSampleId: string | null;
  onSelect: (sampleId: string) => void;
  aggregate?: RaceAggregate;
  summaryActive: boolean;
  onShowSummary: () => void;
};

// Must stay the complement of the `max-width: 1280px` rule that shows .sidebar-toggle in styles.css.
const FULL_SIDEBAR_QUERY = "(min-width: 1281px)";

export function SampleSidebar({ samples, activeSampleId, onSelect, aggregate, summaryActive, onShowSummary }: Props) {
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const focusToggleAfterRender = useRef(false);
  const groups = useMemo(() => groupSamples(samples, query), [samples, query]);

  useEffect(() => {
    setQuery("");
  }, [samples]);

  useEffect(() => {
    if (focusToggleAfterRender.current) {
      focusToggleAfterRender.current = false;
      toggleRef.current?.focus();
    }
  }, [collapsed]);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const wide = window.matchMedia(FULL_SIDEBAR_QUERY);
    const expandWhenWide = () => {
      if (wide.matches) setCollapsed(false);
    };
    wide.addEventListener("change", expandWhenWide);
    return () => wide.removeEventListener("change", expandWhenWide);
  }, []);

  function toggle() {
    focusToggleAfterRender.current = true;
    setCollapsed((value) => !value);
  }

  const hasSamples = samples.length > 0;

  if (collapsed) {
    return (
      <aside className="sidebar sidebar-collapsed" aria-label="样本">
        <SidebarToggle ref={toggleRef} expanded={false} onClick={toggle} />
        <span className="sidebar-strip-count">{samples.length}</span>
      </aside>
    );
  }

  return (
    <aside className="sidebar" aria-label="样本">
      <div className="samples-head">
        <div className="samples-title">
          <h2>样本</h2>
          <span>{samples.length} 条</span>
          {hasSamples ? <SidebarToggle ref={toggleRef} expanded onClick={toggle} /> : null}
        </div>
        {aggregate && hasSamples ? (
          <button
            type="button"
            className={summaryActive ? "sidebar-summary active" : "sidebar-summary"}
            aria-current={summaryActive ? "page" : undefined}
            onClick={onShowSummary}
          >
            <span><strong>汇总</strong><small>{aggregate.trajectory_ids.length} 条轨迹 · {aggregate.tool_calls} 次工具调用</small></span>
            <span>{summaryActive ? "正在查看" : "查看 →"}</span>
          </button>
        ) : null}
        {hasSamples ? (
          <input
            type="search"
            className="search"
            aria-label="搜索样本"
            placeholder="搜索 sample_id 或标题"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        ) : null}
      </div>
      {!hasSamples ? (
        <p className="sidebar-empty">暂无轨迹，上传后可在此切换查看</p>
      ) : (
        <div className="slist" data-testid="sample-list">
          {groups.length === 0 ? (
            <p className="sidebar-empty">没有匹配的样本。</p>
          ) : (
            groups.map((group) => (
              <section key={group.id} aria-label={group.label}>
                <h3 className="sgroup">
                  {group.label}
                  <em>{group.entries.length} 条</em>
                </h3>
                <ul>
                  {group.entries.map((entry) => (
                    <li key={entry.sample.sample_id}>
                      <SampleRow
                        entry={entry}
                        active={entry.sample.sample_id === activeSampleId}
                        onSelect={onSelect}
                      />
                    </li>
                  ))}
                </ul>
              </section>
            ))
          )}
        </div>
      )}
    </aside>
  );
}

const SidebarToggle = forwardRef<HTMLButtonElement, { expanded: boolean; onClick: () => void }>(
  function SidebarToggle({ expanded, onClick }, ref) {
    return (
      <button
        ref={ref}
        type="button"
        className="sidebar-toggle"
        aria-label={expanded ? "收起样本栏" : "展开样本栏"}
        aria-expanded={expanded}
        onClick={onClick}
      >
        {expanded ? "«" : "»"}
      </button>
    );
  },
);

function SampleRow({
  entry: { sample, title },
  active,
  onSelect,
}: {
  entry: SampleEntry;
  active: boolean;
  onSelect: Props["onSelect"];
}) {
  const bundle = sample.ok ? sample.bundle : undefined;
  return (
    <button
      type="button"
      data-testid={`sample-item-${sample.sample_id}`}
      className={active ? "srow active" : "srow"}
      aria-current={active ? "true" : undefined}
      onClick={() => onSelect(sample.sample_id)}
    >
      <span className="sbody">
        <strong>{sample.sample_id}</strong>
        {title ? <small className="stitle">{title}</small> : null}
      </span>
      {bundle ? (
        <span className="smeta">
          <span className="badge">{bundle.sample.source_format}</span>
          <span
            className={`dot v-${bundle.verdict.value}`}
            role="img"
            aria-label={`检测判定：${verdictLabel(bundle.verdict.value)}`}
          />
        </span>
      ) : (
        <span className="smeta">
          <span className="serr">{sample.error?.code || "sample_failed"}</span>
        </span>
      )}
    </button>
  );
}
