# SPDX-License-Identifier: Apache-2.0
"""Topic 4 Provider Adapter 公共类型与常量。

类型只描述 Adapter 边界上的稳定结构；不把课题四内部模型对象引入课题五。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RESULT_SCHEMA = "topic4-audit-result/1"
INPUT_SCHEMA = "topic4-audit-input/1"

BATCH_STATUSES = ("complete", "partial", "failed", "rejected")
ITEM_STATUSES = ("success", "failure")
VERDICTS = ("safe", "unsafe", "undetermined")
SEVERITIES = ("low", "medium", "high", "unspecified")
PREFLIGHT_STATUSES = ("ready", "failed", "not_run")
COVERAGE_STATUSES = ("observed_only", "partial")

TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "batch_id",
        "provider_run_id",
        "status",
        "provider",
        "execution",
        "items",
        "rejection",
        "extensions",
    }
)
PROVIDER_KEYS = frozenset(
    {
        "implementation",
        "version",
        "revision",
        "config_sha256",
        "components",
        "extensions",
    }
)
COMPONENT_KEYS = frozenset(
    {
        "id",
        "version",
        "revision",
        "model",
        "preflight_status",
        "extensions",
    }
)
MODEL_KEYS = frozenset({"id", "revision", "extensions"})
EXECUTION_KEYS = frozenset(
    {
        "started_at",
        "finished_at",
        "runtime",
        "platform",
        "device",
        "extensions",
    }
)
ITEM_KEYS = frozenset(
    {
        "sample_id",
        "audit_input_sha256",
        "status",
        "result",
        "failure",
        "extensions",
    }
)
RESULT_KEYS = frozenset(
    {
        "verdict",
        "findings",
        "checks",
        "extensions",
    }
)
FINDING_KEYS = frozenset(
    {
        "finding_id",
        "category",
        "mechanism",
        "scope",
        "located",
        "origin_event_ids",
        "affected_event_ids",
        "evidence",
        "reason",
        "detector_component_id",
        "detector",
        "confidence",
        "severity",
        "extensions",
    }
)
EVIDENCE_KEYS = frozenset(
    {
        "event_id",
        "field",
        "source_pointer",
        "excerpt",
        "start",
        "end",
        "extensions",
    }
)
FAILURE_KEYS = frozenset(
    {
        "stage",
        "code",
        "reason",
        "retryable",
        "details",
        "extensions",
    }
)
CHECKS_KEYS = frozenset({"coverage", "limitations", "extensions"})
CONFIDENCE_KEYS = frozenset(
    {"kind", "calibrated_probability", "value", "extensions"}
)


@dataclass(frozen=True)
class ProcessRunResult:
    """外部进程一次调用的可观察事实。"""

    argv: list[str]
    cwd: str
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    duration_seconds: float
    started: bool
    error: str | None = None


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    code: str
    message: str


@dataclass
class AdapterOutcome:
    """Adapter 编排一次 Topic4 CLI 调用后的汇总。"""

    ok: bool
    kind: str
    """semantic | transport | contract | fallback"""

    input_path: str
    output_path: str | None
    archive_dir: str | None
    process: ProcessRunResult | None = None
    raw_result: dict[str, Any] | None = None
    validation_issues: list[ValidationIssue] = field(default_factory=list)
    mapped: dict[str, Any] | None = None
    failure: dict[str, Any] | None = None
    degraded: dict[str, Any] | None = None
    archive_manifest: dict[str, Any] | None = None
