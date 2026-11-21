# SPDX-License-Identifier: Apache-2.0
"""把不可变 run 包投影为 ``review-bundle/1`` 展示缝。

Python 拥有事件规范化、工具配对、finding 定位与 availability；React 只消费
本投影，不重新推断风险，也不用估算值冒充未提供字段。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifacts import read_json, write_json

SCHEMA_VERSION = "review-bundle/1"

# CoT / 工具权限标注字段：假 provider 通常不提供，保持 unavailable。
ANNOTATION_FIELD_KEYS = (
    "key_tokens",
    "thinking_patterns",
    "provenance",
    "tool_permissions",
)

METRIC_AVAILABILITY_KEYS = (
    "token_usage",
    "wall_clock",
    "tool_success_rate",
)


def project_review_bundle_from_run_dir(
    run_dir: Path,
    *,
    write_artifact: bool = False,
) -> dict[str, Any]:
    """从已完成的 run 目录投影 Review Bundle。"""

    run_dir = Path(run_dir)
    run = read_json(run_dir / "run.json")
    trajectory = read_json(run_dir / "trajectory.json")
    audit = read_json(run_dir / "audit_result.json")
    metrics = read_json(run_dir / "metrics.json")
    bundle = project_review_bundle(
        run=run,
        trajectory=trajectory,
        audit_result=audit,
        metrics=metrics,
    )
    if write_artifact:
        write_json(run_dir / "review-bundle.json", bundle)
        artifacts = run.setdefault("artifacts", {})
        if artifacts.get("review_bundle") != "review-bundle.json":
            artifacts["review_bundle"] = "review-bundle.json"
            write_json(run_dir / "run.json", run)
    return bundle


def project_review_bundle(
    *,
    run: dict[str, Any],
    trajectory: dict[str, Any],
    audit_result: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    """纯函数投影：四份 run 产物 → ``review-bundle/1``。"""

    annotations, annotation_availability = _project_annotations(audit_result)
    metric_availability = dict(metrics.get("availability") or {})
    availability = {
        **{key: metric_availability.get(key, _unavailable("指标未记录")) for key in METRIC_AVAILABILITY_KEYS},
        **annotation_availability,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "sample": {
            "sample_id": run["sample"]["sample_id"],
            "source_format": run["sample"]["source_format"],
            "dataset": dict(run["sample"]["dataset"]),
            "record": dict(run["sample"]["record"]),
            "sample_id_source": run["sample"].get("sample_id_source"),
        },
        "verdict": dict(audit_result["verdict"]),
        "source_labels": list(trajectory.get("source_labels") or []),
        "detected_findings": [
            _project_finding(finding) for finding in audit_result.get("findings") or []
        ],
        "events": [_project_event(event) for event in trajectory.get("events") or []],
        "tool_interactions": _pair_tool_interactions(trajectory.get("events") or []),
        "metrics": {
            "events": metrics.get("events"),
            "tools": metrics.get("tools"),
            "audit": metrics.get("audit"),
            "source": metrics.get("source"),
        },
        "availability": availability,
        "annotations": annotations,
        "provenance": {
            "run_id": run["run_id"],
            "created_at": run.get("created_at"),
            "completed_at": run.get("completed_at"),
            "provider": dict(run.get("provider") or {}),
            "adapter": dict(run.get("adapter") or {}),
            "audit_config_version": run.get("audit_config_version"),
            "audit_rules_sha256": run.get("audit_rules_sha256"),
            "tool": dict(run.get("tool") or {}),
            "degraded": run.get("degraded"),
        },
    }


def _project_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event["event_id"],
        "sequence": event["sequence"],
        "type": event["type"],
        "actor": event["actor"],
        "source_step_id": event.get("source_step_id"),
        "content": event.get("content") or {},
        "source": event.get("source") or {},
    }


def _project_finding(finding: dict[str, Any]) -> dict[str, Any]:
    """投影公开 Detected Finding 字段；不透传课题四 ``provider_native`` 私有形状。"""

    projected: dict[str, Any] = {
        "finding_id": finding["finding_id"],
        "categories": list(finding.get("categories") or []),
        "scope": finding.get("scope"),
        "origin_event_ids": list(finding.get("origin_event_ids") or []),
        "affected_event_ids": list(finding.get("affected_event_ids") or []),
        "located": finding.get("located"),
        "evidence": list(finding.get("evidence") or []),
        "reason": finding.get("reason"),
        "rule": finding.get("rule"),
    }
    # 仅把原生结果里已约定的展示字段抬到公开层，不保留 provider_native 整包。
    native = finding.get("provider_native")
    if isinstance(native, dict):
        if not projected.get("reason") and isinstance(native.get("explanation"), str):
            projected["reason"] = native["explanation"]
        for key in ("title", "severity", "type"):
            if key in native and key not in projected:
                projected[key] = native[key]
    return projected


def _pair_tool_interactions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results_by_call: dict[Any, list[dict[str, Any]]] = {}
    for event in events:
        if event.get("type") == "tool_result":
            results_by_call.setdefault(event["content"].get("call_event_id"), []).append(event)
    interactions: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "tool_call":
            continue
        content = event.get("content") or {}
        results = [_result_payload(result) for result in results_by_call.get(event["event_id"], [])]
        interactions.append(
            {
                "call_event_id": event["event_id"],
                "sequence": event["sequence"],
                "tool": content.get("tool"),
                "call_id": content.get("call_id"),
                "arguments": content.get("arguments"),
                "arguments_raw": content.get("arguments_raw"),
                "arguments_status": content.get("arguments_status"),
                "result": results[0] if results else None,
                "results": results,
            }
        )
    return interactions


def _result_payload(result: dict[str, Any]) -> dict[str, Any]:
    content = result.get("content") or {}
    return {
        "event_id": result["event_id"],
        "sequence": result["sequence"],
        "declared_status": content.get("declared_status"),
        "link_status": content.get("link_status"),
        "output": content.get("output"),
        "output_format": content.get("output_format"),
    }


def _project_annotations(
    audit_result: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    provider_result = audit_result.get("provider_result") or {}
    extensions = provider_result.get("extensions")
    if not isinstance(extensions, dict):
        extensions = {}
    annotations: dict[str, Any] = {}
    availability: dict[str, Any] = {}
    for key in ANNOTATION_FIELD_KEYS:
        value = extensions.get(key)
        if value is None or value == [] or value == {}:
            annotations[key] = None
            availability[key] = _unavailable("算法未提供该标注字段")
        else:
            annotations[key] = value
            availability[key] = {"status": "available"}
    return annotations, availability


def _unavailable(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason}


__all__ = [
    "ANNOTATION_FIELD_KEYS",
    "SCHEMA_VERSION",
    "project_review_bundle",
    "project_review_bundle_from_run_dir",
]
