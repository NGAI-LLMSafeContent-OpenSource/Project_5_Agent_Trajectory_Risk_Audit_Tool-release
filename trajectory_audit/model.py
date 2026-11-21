# SPDX-License-Identifier: Apache-2.0
"""统一轨迹、风险发现与审计结果的领域对象。

字段命名与规格一致：轨迹事件描述可观察记录，风险发现支持 trajectory /
event / span 三种作用范围，判定与检查状态分开表达。

来源事实、项目参考标注与自动检测结果分属三层：

- ``source``：来源数据集提供的记录、步骤标签与轨迹级标签；
- ``reference``：本项目人工确认的参考标注（当前样本尚未建立时为 absent）；
- ``detected``：审计 provider 产生的判定与风险发现，位于 audit_result。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


EVENT_TYPES = (
    "message",
    "thought",
    "tool_call",
    "tool_result",
    "state_change",
    "unsupported_content",
)

# 工具调用参数状态：只有 object 视为可解析的参数对象。
ARGUMENTS_STATUS_OBJECT = "object"
ARGUMENTS_STATUS_MISSING = "missing"
ARGUMENTS_STATUS_INVALID_JSON = "invalid_json"
ARGUMENTS_STATUS_NOT_OBJECT = "not_object"
UNUSABLE_ARGUMENTS_STATUSES = (ARGUMENTS_STATUS_INVALID_JSON, ARGUMENTS_STATUS_NOT_OBJECT)

# 工具返回与工具调用的关联状态。
LINK_STATUS_LINKED = "linked"
LINK_STATUS_POSITIONAL = "positional"
LINK_STATUS_UNKNOWN_CALL_ID = "unknown_call_id"
LINK_STATUS_NO_CALL_ID = "no_call_id"
LINK_STATUSES_WITH_CALL = (LINK_STATUS_LINKED, LINK_STATUS_POSITIONAL)

# 来源标签作用范围。trajectory 表示来源只提供轨迹级依据，不伪造步骤位置。
SOURCE_LABEL_SCOPE_TRAJECTORY = "trajectory"
SOURCE_LABEL_SCOPE_STEP = "source_step"
SOURCE_LABEL_SCOPES = (SOURCE_LABEL_SCOPE_TRAJECTORY, SOURCE_LABEL_SCOPE_STEP)

REFERENCE_STATUS_ABSENT = "absent"
REFERENCE_STATUS_PRESENT = "present"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_text(canonical_json(value))


@dataclass(frozen=True)
class SourceLocator:
    """回到原始输入记录的位置，并记录来源数据版本。"""

    record_index: int
    json_path: str
    record_sha256: str
    dataset_id: str
    dataset_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_index": self.record_index,
            "json_path": self.json_path,
            "record_sha256": self.record_sha256,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
        }


@dataclass(frozen=True)
class Event:
    event_id: str
    sequence: int
    type: str
    actor: str
    source_step_id: int | str | None
    content: dict[str, Any]
    source: SourceLocator
    raw: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "sequence": self.sequence,
            "type": self.type,
            "actor": self.actor,
            "source_step_id": self.source_step_id,
            "content": self.content,
            "source": self.source.to_dict(),
            "raw": self.raw,
        }


@dataclass(frozen=True)
class SourceStep:
    """来源数据集暴露的一个步骤；步骤身份不凭空生成。"""

    source_step_id: int | str | None
    source_index: int
    json_path: str
    role: str
    raw: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_step_id": self.source_step_id,
            "source_index": self.source_index,
            "json_path": self.json_path,
            "role": self.role,
            "raw": self.raw,
        }


@dataclass(frozen=True)
class SourceLabel:
    """来源数据集的标签事实。

    ``located`` 为 false 时，标签只支持轨迹级结论；不得借由相似文本补出步骤
    位置。``event_ids`` 只由具有相同 ``source_step_id`` 的事件解析得到。
    """

    label_id: str
    scope: str
    source_step_id: int | str | None
    event_ids: list[str]
    located: bool
    verdict: str | None
    categories: list[str]
    source_categories: list[str]
    reason: str | None
    mapping: dict[str, Any]
    raw: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "label_id": self.label_id,
            "layer": "source",
            "scope": self.scope,
            "source_step_id": self.source_step_id,
            "event_ids": self.event_ids,
            "located": self.located,
            "verdict": self.verdict,
            "categories": self.categories,
            "source_categories": self.source_categories,
            "reason": self.reason,
            "mapping": self.mapping,
            "raw": self.raw,
        }


@dataclass
class NormalizedTrajectory:
    sample_id: str
    source_format: str
    adapter_id: str
    adapter_version: str
    input_sha256: str
    dataset_id: str
    dataset_version: str
    record_index: int
    record_json_path: str
    record_sha256: str
    record_raw: Any
    events: list[Event]
    source_steps: list[SourceStep] = field(default_factory=list)
    source_labels: list[SourceLabel] = field(default_factory=list)
    tool_definitions: Any = None
    unmapped_top_level_fields: list[str] = field(default_factory=list)
    reference: dict[str, Any] = field(
        default_factory=lambda: {
            "status": REFERENCE_STATUS_ABSENT,
            "labels": [],
            "note": "本项目尚未为该样本建立人工确认的 reference 标注。",
        }
    )

    def unsupported_events(self) -> list[Event]:
        return [event for event in self.events if event.type == "unsupported_content"]

    def to_dict(self) -> dict[str, Any]:
        unsupported = self.unsupported_events()
        source: dict[str, Any] = {
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "record": {
                "index": self.record_index,
                "json_path": self.record_json_path,
                "sha256": self.record_sha256,
                "raw": self.record_raw,
            },
        }
        if self.tool_definitions is not None:
            source["tool_definitions"] = self.tool_definitions
        return {
            "schema_version": "trajectory/2",
            "sample": {
                "sample_id": self.sample_id,
                "source_format": self.source_format,
                "adapter": {"id": self.adapter_id, "version": self.adapter_version},
                "input_sha256": self.input_sha256,
                "unmapped_top_level_fields": self.unmapped_top_level_fields,
            },
            "source": source,
            "source_steps": [step.to_dict() for step in self.source_steps],
            "source_labels": [label.to_dict() for label in self.source_labels],
            "reference": self.reference,
            "events": [event.to_dict() for event in self.events],
            "unsupported": {
                "count": len(unsupported),
                "event_ids": [event.event_id for event in unsupported],
            },
        }


@dataclass(frozen=True)
class ProviderIdentity:
    kind: str
    id: str
    version: str
    selection: str
    implementation: str
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": self.id,
            "version": self.version,
            "selection": self.selection,
            "implementation": self.implementation,
            "description": self.description,
        }


@dataclass(frozen=True)
class Evidence:
    kind: str
    event_id: str | None
    field_path: str | None
    excerpt: str
    json_path: str | None
    located: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "event_id": self.event_id,
            "field_path": self.field_path,
            "excerpt": self.excerpt,
            "json_path": self.json_path,
            "located": self.located,
        }


@dataclass(frozen=True)
class Finding:
    finding_id: str
    categories: list[str]
    scope: str
    origin_event_ids: list[str]
    affected_event_ids: list[str]
    located: bool
    evidence: list[Evidence]
    reason: str
    rule_id: str
    rule_version: str
    basis: str = "structural_rule"
    provider_native: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "categories": self.categories,
            "scope": self.scope,
            "origin_event_ids": self.origin_event_ids,
            "affected_event_ids": self.affected_event_ids,
            "located": self.located,
            "evidence": [item.to_dict() for item in self.evidence],
            "reason": self.reason,
            "rule": {
                "id": self.rule_id,
                "version": self.rule_version,
                "basis": self.basis,
            },
            **({"provider_native": self.provider_native} if self.provider_native is not None else {}),
        }


@dataclass(frozen=True)
class Fact:
    """确定性事实：只陈述可观察结构，本身不构成风险发现。"""

    kind: str
    event_id: str | None
    detail: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "event_id": self.event_id,
            "detail": self.detail,
            "data": self.data,
        }


@dataclass(frozen=True)
class Verdict:
    value: str
    basis: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "basis": self.basis, "reason": self.reason}


@dataclass(frozen=True)
class Checks:
    status: str
    finding_count: int
    rules_evaluated: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "finding_count": self.finding_count,
            "rules_evaluated": self.rules_evaluated,
        }


@dataclass
class AuditResult:
    sample_id: str
    run_id: str
    provider: ProviderIdentity
    verdict: Verdict
    checks: Checks
    findings: list[Finding]
    evaluated_at: str
    facts: list[Fact] = field(default_factory=list)
    policy: dict[str, Any] | None = None
    judge: dict[str, Any] | None = None
    degraded: dict[str, Any] | None = None
    provider_result: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "audit-result/3",
            "layer": "detected",
            "sample_id": self.sample_id,
            "run_id": self.run_id,
            "provider": self.provider.to_dict(),
            "verdict": self.verdict.to_dict(),
            "checks": self.checks.to_dict(),
            "findings": [finding.to_dict() for finding in self.findings],
            "facts": [fact.to_dict() for fact in self.facts],
            "policy": self.policy,
            "judge": self.judge,
            "degraded": self.degraded,
            "evaluated_at": self.evaluated_at,
            **({"provider_result": self.provider_result} if self.provider_result is not None else {}),
        }


@dataclass(frozen=True)
class ProviderOutput:
    findings: list[Finding]
    verdict: Verdict
    checks: Checks
    facts: list[Fact] = field(default_factory=list)
    policy: dict[str, Any] | None = None
    judge: dict[str, Any] | None = None
    degraded: dict[str, Any] | None = None
    provider_result: dict[str, Any] | None = None
