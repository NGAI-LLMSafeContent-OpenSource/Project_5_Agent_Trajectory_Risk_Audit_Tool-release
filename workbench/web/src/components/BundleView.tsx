import * as Tabs from "@radix-ui/react-tabs";
import { useState } from "react";
import {
  resolveFindingFocus,
  tabForEvent,
  type FindingFocus,
} from "../findingFocus";
import type { ReviewBundle, TabId } from "../types";
import { TABS } from "../types";
import { CotTab } from "./CotTab";
import { SampleHeader } from "./SampleHeader";
import { StatsTab } from "./StatsTab";
import { ToolsTab } from "./ToolsTab";

type Props = {
  bundle: ReviewBundle;
  sourceOpen: boolean;
  sourceEventId?: string | null;
  onSelectSourceEvent: (eventId: string) => void;
};

export function BundleView({ bundle, sourceOpen, sourceEventId = null, onSelectSourceEvent }: Props) {
  const [tab, setTab] = useState<TabId>("stats");
  const [focus, setFocus] = useState<FindingFocus>({ status: "idle" });

  function onSelectFinding(finding: ReviewBundle["detected_findings"][number]) {
    const next = resolveFindingFocus(finding, bundle.events);
    setFocus(next);
    if (next.status === "focused") {
      setTab(tabForEvent(next.eventId, bundle.events));
      if (sourceOpen) onSelectSourceEvent(next.eventId);
      return;
    }
    if (next.status === "location_failed") {
      setTab("cot");
    }
  }

  return (
    <div className="bundle-view" data-testid="bundle-view">
      <SampleHeader bundle={bundle} />

      <Tabs.Root value={tab} onValueChange={(value) => setTab(value as TabId)}>
        <div className="tabbar">
          <Tabs.List className="tabs" aria-label="审阅页签">
            {TABS.map((item) => (
              <Tabs.Trigger key={item.id} value={item.id}>
                <b>{item.no}</b>
                {item.label}
              </Tabs.Trigger>
            ))}
          </Tabs.List>
        </div>

        {TABS.map((item) => (
          <Tabs.Content key={item.id} value={item.id} className="tabbody">
            {item.id === "stats" ? <StatsTab bundle={bundle} /> : null}
            {item.id === "cot" ? (
              <CotTab
                bundle={bundle}
                focus={focus}
                sourceEventId={sourceEventId}
                onSelectFinding={onSelectFinding}
                onSelectSourceEvent={onSelectSourceEvent}
              />
            ) : null}
            {item.id === "tools" ? (
              <ToolsTab
                bundle={bundle}
                focus={focus}
                sourceEventId={sourceEventId}
                onSelectFinding={onSelectFinding}
                onSelectSourceEvent={onSelectSourceEvent}
              />
            ) : null}
          </Tabs.Content>
        ))}
      </Tabs.Root>
    </div>
  );
}
