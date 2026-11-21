export type AvailabilityStatus = "available" | "unavailable" | "not_applicable";

export type AvailabilityEntry = {
  status: AvailabilityStatus;
  reason?: string;
  value?: number;
  basis?: string;
  success?: number;
  total?: number;
};

/** A source location: race steps start at 1, message indexes at 0; metadata entries carry only a field. */
export type RaceLocation = { step_id?: number; message_index?: number; event_id?: string | null };

export type RaceAlgorithmItem =
  | { status: "available"; judgment: "有风险" | "无风险" }
  | { status: "available"; risks: RaceAlgorithmFinding[] }
  | { status: "available"; markers: RaceAlgorithmFinding[] }
  | { status: "available"; failures: RaceAlgorithmFinding[] }
  | { status: "unavailable" | "failure"; reason: string };

export type RaceAlgorithmFinding = RaceLocation & {
  risk_quote?: string;
  previous_step_id?: number;
  error_type?: string;
  evidence: Array<RaceLocation & { quote: string }>;
};

export type RaceItems = {
  A1: { input_tokens: number | string; output_tokens: number | string };
  A2: { known_characters: number; absent_body_step_ids: number[] };
  A3: { tool_calls: number; tool_kinds: number; tool_counts: Record<string, number> };
  B1: {
    segments: Array<{ step_id: number; step_ids: number[]; message_index: number; event_id: string; text: string; characters: number }>;
    absent_body_step_ids: number[];
  };
  C1: {
    calls: Array<{
      step_id: number;
      message_index: number;
      event_id: string;
      tool_call_id?: unknown;
      tool_name?: unknown;
      arguments?: unknown;
      returns: Array<{ message_index: number; event_id: string; content: unknown }>;
    }>;
    unmatched: Array<{ message_index: number; event_id: string; tool_call_id?: unknown; issue: string; content: unknown }>;
  };
  C4: {
    missing_fields: Array<RaceLocation & { field: string; issue: string }>;
    unmatched: Array<RaceLocation & { tool_call_id?: unknown; issue: string }>;
    truncations: Array<RaceLocation & { field: string; quote?: string; value?: unknown }>;
    completion: boolean | null | string;
  };
  B2?: RaceAlgorithmItem;
  B3?: RaceAlgorithmItem;
  C2?: RaceAlgorithmItem;
  C3?: RaceAlgorithmItem;
  C5: {
    issues: Array<RaceLocation & {
      source_subitem: "C2" | "C3" | "C4";
      kind: string;
      field?: string;
      issue?: string;
      evidence: Array<RaceLocation & { field?: string; quote: string }>;
    }>;
    pending?: Array<{ source_subitem: "C2" | "C3"; status: string; reason?: string }>;
  };
};

export type RaceAggregate = { trajectory_ids: string[]; tool_calls: number; tool_counts: Record<string, number> };

export type ReviewBundle = {
  race?: RaceItems;
  source_mapping?: {
    steps: Array<{ step_id: number; message_index: number; tool_call_index: number | null; event_ids: string[] }>;
    events: Array<{ event_id: string; step_ids: number[]; source_field: string; line_number: number }>;
  };
  algorithm_status?: "failure";
  schema_version: string;
  sample: {
    sample_id: string;
    source_format: string;
    dataset: { id: string; version: string };
    record: { index: number; sha256: string };
    sample_id_source?: string | null;
  };
  verdict: {
    value: string;
    basis?: string;
    reason?: string;
  };
  source_labels: Array<{
    label_id: string;
    layer?: string;
    scope?: string;
    verdict?: string | null;
    categories?: string[];
    source_categories?: string[];
    reason?: string | null;
    located?: boolean;
  }>;
  detected_findings: Array<{
    finding_id: string;
    categories: string[];
    reason?: string;
    title?: string;
    severity?: string | null;
    type?: string;
    located?: boolean;
    origin_event_ids?: string[];
    affected_event_ids?: string[];
    evidence?: Array<{
      kind?: string;
      event_id?: string | null;
      field_path?: string | null;
      excerpt?: string;
      json_path?: string | null;
      located?: boolean;
    }>;
  }>;
  events: Array<{
    event_id: string;
    sequence: number;
    type: string;
    actor: string;
    content: Record<string, unknown>;
  }>;
  tool_interactions: Array<{
    call_event_id: string;
    sequence: number;
    tool?: string | null;
    call_id?: string | null;
    arguments?: unknown;
    arguments_raw?: unknown;
    arguments_status?: string | null;
    result?: ToolResult | null;
    results?: ToolResult[];
  }>;
  metrics: {
    events?: Record<string, unknown>;
    tools?: Record<string, unknown>;
    audit?: Record<string, unknown>;
    source?: Record<string, unknown>;
  };
  availability: Record<string, AvailabilityEntry>;
  annotations: {
    key_tokens: unknown;
    thinking_patterns: unknown;
    provenance: unknown;
    tool_permissions: unknown;
  };
  provenance: {
    run_id: string;
    provider?: Record<string, unknown>;
    adapter?: Record<string, unknown>;
  };
};

export type ToolResult = {
  event_id: string;
  declared_status?: string | null;
  link_status?: string | null;
  output?: unknown;
};

export type SourceTrajectoryStep = {
  json_path: string;
  raw: unknown;
  event_ids: string[];
};

export type SourceTrajectory = {
  ok: boolean;
  sample_id: string;
  source_format?: string | null;
  availability: AvailabilityEntry;
  steps: SourceTrajectoryStep[];
};

export const TABS = [
  { id: "stats", no: "①", label: "数据统计" },
  { id: "cot", no: "②", label: "思维链" },
  { id: "tools", no: "③", label: "工具调用" },
] as const;

export type TabId = (typeof TABS)[number]["id"];

export type SessionSample = {
  sample_id: string;
  ok: boolean;
  run_id?: string;
  bundle?: ReviewBundle;
  error?: {
    code?: string;
    message?: string;
    details?: Record<string, unknown>;
  };
};

export type AuditSessionResponse = {
  ok: boolean;
  session_id?: string;
  mode?: "single" | "batch";
  batch_id?: string;
  run_id?: string;
  bundle?: ReviewBundle;
  selection?: {
    offset: number;
    limit: number;
    total_records: number;
    selected: number;
  };
  samples?: SessionSample[];
  aggregate?: RaceAggregate;
};

export const UNAVAILABLE_LABEL = "未提供";
