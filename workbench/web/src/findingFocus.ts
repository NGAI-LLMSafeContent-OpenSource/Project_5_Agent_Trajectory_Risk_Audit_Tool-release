import type { ReviewBundle } from "./types";

type Finding = ReviewBundle["detected_findings"][number];

export type FindingEvidence = NonNullable<
  ReviewBundle["detected_findings"][number]["evidence"]
>[number];

export type FindingFocus =
  | { status: "idle" }
  | {
      status: "focused";
      findingId: string;
      eventId: string;
      excerpts: string[];
    }
  | { status: "location_failed"; findingId: string };

export function usableEvidence(
  entry: FindingEvidence | undefined,
): entry is FindingEvidence & { event_id: string } {
  if (!entry || !entry.event_id) return false;
  // Do not invent located=true: only use explicitly located evidence, or
  // entries that omit the flag but already carry an excerpt to show.
  if (entry.located === false) return false;
  if (entry.located === true) return true;
  return Boolean(entry.excerpt);
}

export function resolveFindingFocus(
  finding: ReviewBundle["detected_findings"][number],
  events: ReviewBundle["events"],
): FindingFocus {
  const findingId = finding.finding_id;
  const eventIds = new Set(events.map((event) => event.event_id));

  if (finding.located === false) {
    return { status: "location_failed", findingId };
  }

  const evidence = (finding.evidence || [])
    .filter(usableEvidence)
    .filter((entry) => eventIds.has(entry.event_id));

  const preferred =
    evidence[0]?.event_id ||
    finding.origin_event_ids?.find((id) => eventIds.has(id)) ||
    finding.affected_event_ids?.find((id) => eventIds.has(id));

  if (!preferred) {
    return { status: "location_failed", findingId };
  }

  const excerpts = evidence
    .filter((entry) => entry.event_id === preferred && entry.excerpt)
    .map((entry) => entry.excerpt as string);

  return {
    status: "focused",
    findingId,
    eventId: preferred,
    excerpts,
  };
}

// Same binding rules as resolveFindingFocus, so a marked event never leads to a location failure.
export function findingsByEvent(
  findings: Finding[],
  events: ReviewBundle["events"],
): Map<string, Finding[]> {
  const eventIds = new Set(events.map((event) => event.event_id));
  const byEvent = new Map<string, Finding[]>();
  for (const finding of findings) {
    if (finding.located === false) continue;
    const ids = new Set([
      ...(finding.origin_event_ids || []),
      ...(finding.affected_event_ids || []),
      ...(finding.evidence || []).filter(usableEvidence).map((entry) => entry.event_id),
    ]);
    for (const id of ids) {
      if (eventIds.has(id)) byEvent.set(id, [...(byEvent.get(id) || []), finding]);
    }
  }
  return byEvent;
}

export function tabForEvent(
  eventId: string,
  events: ReviewBundle["events"],
): "cot" | "tools" {
  const event = events.find((item) => item.event_id === eventId);
  if (event?.type === "tool_call" || event?.type === "tool_result") {
    return "tools";
  }
  return "cot";
}

export function toolCardIdForEvent(
  eventId: string,
  events: ReviewBundle["events"],
): string {
  const event = events.find((item) => item.event_id === eventId);
  if (event?.type === "tool_result") {
    const callId = event.content?.call_event_id;
    if (typeof callId === "string" && callId) {
      return callId;
    }
  }
  return eventId;
}
