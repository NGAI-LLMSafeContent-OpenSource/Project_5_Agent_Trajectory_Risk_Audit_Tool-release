# SPDX-License-Identifier: Apache-2.0
"""由统一轨迹与审计结果计算运行指标。

指标口径保持诚实：来源未提供的值标记 unavailable，不用估算值冒充来源值。
下游指标不按数据集名称分支；来源身份只作为被记录的字段。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .model import (
    LINK_STATUS_LINKED,
    LINK_STATUS_POSITIONAL,
    LINK_STATUSES_WITH_CALL,
    REFERENCE_STATUS_ABSENT,
    SOURCE_LABEL_SCOPE_TRAJECTORY,
    AuditResult,
    EVENT_TYPES,
    NormalizedTrajectory,
)


def compute_metrics(trajectory: NormalizedTrajectory, audit_result: AuditResult) -> dict[str, Any]:
    by_type = Counter(event.type for event in trajectory.events)
    tool_calls = [event for event in trajectory.events if event.type == "tool_call"]
    tool_results = [event for event in trajectory.events if event.type == "tool_result"]

    explicit_linked_call_ids = {
        event.content.get("call_event_id")
        for event in tool_results
        if event.content.get("link_status") == LINK_STATUS_LINKED
    }
    positional_linked_call_ids = {
        event.content.get("call_event_id")
        for event in tool_results
        if event.content.get("link_status") == LINK_STATUS_POSITIONAL
    }
    paired_call_ids = explicit_linked_call_ids | positional_linked_call_ids
    unpaired_calls = [
        event.event_id for event in tool_calls if event.event_id not in paired_call_ids
    ]
    explicit_linked_results = [
        event for event in tool_results if event.content.get("link_status") == LINK_STATUS_LINKED
    ]
    positional_linked_results = [
        event
        for event in tool_results
        if event.content.get("link_status") == LINK_STATUS_POSITIONAL
    ]
    unlinked_results = [
        event
        for event in tool_results
        if event.content.get("link_status") not in LINK_STATUSES_WITH_CALL
    ]
    declared_results = [
        event
        for event in tool_results
        if event.content.get("declared_status") in {"error", "success"}
    ]
    error_declared = [
        event for event in declared_results if event.content.get("declared_status") == "error"
    ]

    categories = Counter(
        category for finding in audit_result.findings for category in finding.categories
    )
    source_categories = Counter(
        category
        for label in trajectory.source_labels
        for category in label.categories
    )
    reference_labels = trajectory.reference.get("labels")
    reference_count = len(reference_labels) if isinstance(reference_labels, list) else 0

    return {
        "schema_version": "metrics/2",
        "sample_id": trajectory.sample_id,
        "run_id": audit_result.run_id,
        "source": {
            "dataset_id": trajectory.dataset_id,
            "dataset_version": trajectory.dataset_version,
            "record_index": trajectory.record_index,
            "source_steps": len(trajectory.source_steps),
            "source_labels": len(trajectory.source_labels),
            "located_source_labels": sum(
                1 for label in trajectory.source_labels if label.located
            ),
            "trajectory_scoped_labels": sum(
                1
                for label in trajectory.source_labels
                if label.scope == SOURCE_LABEL_SCOPE_TRAJECTORY
            ),
            "categories": dict(sorted(source_categories.items())),
        },
        "reference": {
            "status": trajectory.reference.get("status", REFERENCE_STATUS_ABSENT),
            "labels": reference_count,
        },
        "events": {
            "total": len(trajectory.events),
            "by_type": {event_type: by_type.get(event_type, 0) for event_type in EVENT_TYPES},
            "unsupported_count": len(trajectory.unsupported_events()),
        },
        "tools": {
            "calls": len(tool_calls),
            "results": len(tool_results),
            "linked_results": len(explicit_linked_results),
            "positionally_linked_results": len(positional_linked_results),
            "unlinked_results": len(unlinked_results),
            "unpaired_call_event_ids": unpaired_calls,
            "error_declared_results": len(error_declared),
        },
        "audit": {
            "verdict": audit_result.verdict.value,
            "check_status": audit_result.checks.status,
            "finding_count": len(audit_result.findings),
            "categories": dict(sorted(categories.items())),
        },
        "availability": {
            "token_usage": {
                "status": "unavailable",
                "reason": "输入未提供 token 使用量",
            },
            "wall_clock": {
                "status": "unavailable",
                "reason": "输入未提供单步或整条轨迹耗时",
            },
            "tool_success_rate": _tool_success_rate(tool_results, declared_results),
        },
    }


def _tool_success_rate(tool_results: list[Any], declared_results: list[Any]) -> dict[str, Any]:
    if not tool_results:
        return {"status": "unavailable", "reason": "本次轨迹没有工具返回"}
    if len(declared_results) != len(tool_results):
        return {
            "status": "unavailable",
            "reason": "并非每次工具返回都提供明确状态，按口径不计算成功率",
        }
    failures = sum(
        1 for event in declared_results if event.content.get("declared_status") == "error"
    )
    success = len(declared_results) - failures
    return {
        "status": "available",
        "value": round(success / len(declared_results), 4),
        "basis": "explicit_result_status",
        "success": success,
        "total": len(declared_results),
    }
