# SPDX-License-Identifier: Apache-2.0
"""把合法 Topic4 结果映射为课题五可消费的 Detected Finding 层结构。

映射保留课题四原始 verdict、category、mechanism、reason、Evidence、
confidence、severity 与 detector attribution，不混入 Source Label 或
Project Reference Annotation，也不改写课题四判定。
"""

from __future__ import annotations

from typing import Any


def map_topic4_result(result: dict[str, Any]) -> dict[str, Any]:
    """映射已通过合同校验的结果。

    返回 dict 形结构，便于归档与后续投影：

    - ``batch``：批次事实（status、provider_run_id、provider、execution）
    - ``success_items``：成功项及其 Detected Finding 兼容字段
    - ``failure_items``：逐项失败
    - ``rejection``：批次拒绝（若有）
    """

    status = result.get("status")
    provider_run_id = result.get("provider_run_id")
    batch_facts = {
        "schema_version": result.get("schema_version"),
        "batch_id": result.get("batch_id"),
        "provider_run_id": provider_run_id,
        "status": status,
        "provider": result.get("provider"),
        "execution": result.get("execution"),
    }
    if status == "rejected":
        return {
            "batch": batch_facts,
            "success_items": [],
            "failure_items": [],
            "rejection": result.get("rejection"),
            "detected_findings": [],
        }

    success_items: list[dict[str, Any]] = []
    failure_items: list[dict[str, Any]] = []
    detected: list[dict[str, Any]] = []

    for item in result.get("items") or []:
        if not isinstance(item, dict):
            continue
        sample_id = item.get("sample_id")
        audit_input_sha256 = item.get("audit_input_sha256")
        trace = {
            "provider_run_id": provider_run_id,
            "sample_id": sample_id,
            "audit_input_sha256": audit_input_sha256,
        }
        if item.get("status") == "failure":
            failure_items.append(
                {
                    **trace,
                    "failure": item.get("failure"),
                }
            )
            continue

        result_body = item.get("result") or {}
        findings = []
        for finding in result_body.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            mapped_finding = {
                "finding_id": finding.get("finding_id"),
                "category": finding.get("category"),
                "categories": [finding["category"]]
                if isinstance(finding.get("category"), str)
                else [],
                "mechanism": finding.get("mechanism"),
                "scope": finding.get("scope"),
                "located": finding.get("located"),
                "origin_event_ids": list(finding.get("origin_event_ids") or []),
                "affected_event_ids": list(finding.get("affected_event_ids") or []),
                "evidence": [
                    dict(entry) for entry in (finding.get("evidence") or [])
                    if isinstance(entry, dict)
                ],
                "reason": finding.get("reason"),
                "detector_component_id": finding.get("detector_component_id"),
                "detector": finding.get("detector"),
                "confidence": finding.get("confidence"),
                "severity": finding.get("severity"),
                "provider_native": True,
                "layer": "detected",
                **trace,
            }
            findings.append(mapped_finding)
            detected.append(mapped_finding)

        success_items.append(
            {
                **trace,
                "verdict": result_body.get("verdict"),
                "checks": result_body.get("checks"),
                "findings": findings,
            }
        )

    return {
        "batch": batch_facts,
        "success_items": success_items,
        "failure_items": failure_items,
        "rejection": None,
        "detected_findings": detected,
    }
