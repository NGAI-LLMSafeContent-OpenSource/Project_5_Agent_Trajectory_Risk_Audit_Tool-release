# SPDX-License-Identifier: Apache-2.0
"""``topic4-audit-result/1`` 合同校验。

校验 Schema 版本、批次/样本身份、Item 判别联合、状态不变量、Finding/
Evidence、Provider 血缘与未知核心字段。Evidence 的 ``source_pointer`` 必须
解析到输入 Item 轨迹 content 下的字符串叶子；字符区间使用 Unicode
code point 半开区间 ``[start,end)``。
"""

from __future__ import annotations

import re
from typing import Any

from .types import (
    BATCH_STATUSES,
    CHECKS_KEYS,
    COMPONENT_KEYS,
    CONFIDENCE_KEYS,
    COVERAGE_STATUSES,
    EVIDENCE_KEYS,
    EXECUTION_KEYS,
    FAILURE_KEYS,
    FINDING_KEYS,
    ITEM_KEYS,
    MODEL_KEYS,
    PREFLIGHT_STATUSES,
    PROVIDER_KEYS,
    RESULT_KEYS,
    RESULT_SCHEMA,
    SEVERITIES,
    TOP_LEVEL_KEYS,
    VERDICTS,
    ValidationIssue,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SCHEMA_RE = re.compile(r"^topic4-audit-result/(\d+)$")


def validate_result(
    input_batch: dict[str, Any],
    result: Any,
) -> list[ValidationIssue]:
    """校验结果相对输入批次的合同不变量；返回全部问题（不短路）。"""

    issues: list[ValidationIssue] = []
    add = _adder(issues)

    if not isinstance(result, dict):
        add("", "not_object", "结果必须是 JSON 对象")
        return issues

    unknown = set(result) - TOP_LEVEL_KEYS
    for key in sorted(unknown):
        add(key, "unknown_core_field", f"未知核心字段：{key}")

    schema = result.get("schema_version")
    if not isinstance(schema, str) or not schema:
        add("schema_version", "missing_schema_version", "缺少 schema_version")
    else:
        match = _SCHEMA_RE.match(schema)
        if match is None:
            add(
                "schema_version",
                "bad_schema_version",
                f"不受支持的 schema_version：{schema}",
            )
        elif match.group(1) != "1":
            add(
                "schema_version",
                "unsupported_major",
                f"拒绝未知主版本：{schema}",
            )
        elif schema != RESULT_SCHEMA:
            add(
                "schema_version",
                "bad_schema_version",
                f"期望 {RESULT_SCHEMA}，实际 {schema}",
            )

    status = result.get("status")
    if status not in BATCH_STATUSES:
        add("status", "bad_status", f"status 必须是 {BATCH_STATUSES} 之一")

    provider_run_id = result.get("provider_run_id")
    if not isinstance(provider_run_id, str) or not provider_run_id.strip():
        add("provider_run_id", "missing_provider_run_id", "provider_run_id 必填")

    input_batch_id = input_batch.get("batch_id") if isinstance(input_batch, dict) else None
    batch_id = result.get("batch_id")
    if status == "rejected":
        if batch_id is not None and not isinstance(batch_id, str):
            add("batch_id", "bad_batch_id", "batch_id 必须是 string 或 null")
    else:
        if not isinstance(batch_id, str) or not batch_id:
            add("batch_id", "bad_batch_id", "非 rejected 结果必须回显 batch_id")
        elif isinstance(input_batch_id, str) and batch_id != input_batch_id:
            add(
                "batch_id",
                "batch_id_mismatch",
                f"batch_id 与输入不一致：{batch_id!r} != {input_batch_id!r}",
            )

    items = result.get("items")
    if not isinstance(items, list):
        add("items", "bad_items", "items 必须是数组")
        items = []

    rejection = result.get("rejection")
    if status == "rejected":
        if items:
            add("items", "rejected_has_items", "rejected 批次的 items 必须为空")
        if not isinstance(rejection, dict):
            add("rejection", "missing_rejection", "rejected 必须携带 rejection")
        else:
            _validate_failure_like(rejection, "rejection", add)
        _validate_provider(
            result.get("provider"), "provider", add, required_components=False
        )
        _validate_execution(
            result.get("execution"), "execution", add, require_started=False
        )
        return issues

    if rejection is not None:
        add("rejection", "unexpected_rejection", "非 rejected 不得携带 rejection")

    _validate_provider(
        result.get("provider"), "provider", add, required_components=True
    )
    _validate_execution(
        result.get("execution"), "execution", add, require_started=True
    )

    input_items = input_batch.get("items") if isinstance(input_batch, dict) else None
    if not isinstance(input_items, list):
        add("", "bad_input", "输入批次缺少 items")
        return issues

    input_by_id: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(input_items):
        if not isinstance(item, dict):
            continue
        sample_id = item.get("sample_id")
        if isinstance(sample_id, str):
            input_by_id[sample_id] = item

    seen: dict[str, int] = {}
    success_count = 0
    failure_count = 0
    component_ids = _component_ids(result.get("provider"))

    for index, item in enumerate(items):
        path = f"items[{index}]"
        if not isinstance(item, dict):
            add(path, "bad_item", "item 必须是对象")
            continue
        unknown_item = set(item) - ITEM_KEYS
        for key in sorted(unknown_item):
            add(f"{path}.{key}", "unknown_core_field", f"未知核心字段：{key}")

        sample_id = item.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id:
            add(f"{path}.sample_id", "missing_sample_id", "sample_id 必填")
            continue
        if sample_id in seen:
            add(
                f"{path}.sample_id",
                "duplicate_sample",
                f"重复 sample_id：{sample_id}",
            )
        else:
            seen[sample_id] = index
        if sample_id not in input_by_id:
            add(
                f"{path}.sample_id",
                "unknown_sample",
                f"未知 sample_id：{sample_id}",
            )

        sha = item.get("audit_input_sha256")
        if not isinstance(sha, str) or not _SHA256_RE.match(sha):
            add(
                f"{path}.audit_input_sha256",
                "bad_sha",
                "audit_input_sha256 必须是 64 位小写十六进制",
            )
        elif sample_id in input_by_id:
            expected = input_by_id[sample_id].get("audit_input_sha256")
            if sha != expected:
                add(
                    f"{path}.audit_input_sha256",
                    "sha_mismatch",
                    "audit_input_sha256 与输入不一致",
                )

        item_status = item.get("status")
        has_result = "result" in item and item.get("result") is not None
        has_failure = "failure" in item and item.get("failure") is not None
        if item_status == "success":
            success_count += 1
            if not has_result or has_failure:
                add(
                    path,
                    "item_xor",
                    "success 必须有 result 且不得有 failure",
                )
            elif isinstance(item.get("result"), dict):
                _validate_success_result(
                    item["result"],
                    f"{path}.result",
                    add,
                    input_item=input_by_id.get(sample_id),
                    component_ids=component_ids,
                )
            else:
                add(f"{path}.result", "bad_result", "result 必须是对象")
        elif item_status == "failure":
            failure_count += 1
            if not has_failure or has_result:
                add(
                    path,
                    "item_xor",
                    "failure 必须有 failure 且不得有 result",
                )
            elif isinstance(item.get("failure"), dict):
                _validate_failure_like(item["failure"], f"{path}.failure", add)
            else:
                add(f"{path}.failure", "bad_failure", "failure 必须是对象")
        else:
            add(f"{path}.status", "bad_item_status", "status 必须是 success|failure")

    missing = sorted(set(input_by_id) - set(seen))
    for sample_id in missing:
        add("items", "missing_sample", f"缺少输入样本：{sample_id}")

    if status == "complete":
        if failure_count or success_count != len(input_by_id) or missing:
            add(
                "status",
                "status_invariant",
                "complete 要求每个输入恰好一个 success",
            )
    elif status == "partial":
        if not success_count or not failure_count or missing:
            add(
                "status",
                "status_invariant",
                "partial 要求 success 与 failure 同时存在且覆盖全部输入",
            )
    elif status == "failed":
        if success_count or failure_count != len(input_by_id) or missing:
            add(
                "status",
                "status_invariant",
                "failed 要求每个输入恰好一个 failure",
            )

    return issues


def is_valid(input_batch: dict[str, Any], result: Any) -> bool:
    return not validate_result(input_batch, result)


def resolve_json_pointer(document: Any, pointer: str) -> Any:
    """RFC 6901 JSON Pointer；空指针返回整个文档。"""

    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError(f"invalid JSON pointer: {pointer}")
    current = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                index = int(token)
            except ValueError as exc:
                raise ValueError(f"list index must be integer: {token}") from exc
            if index < 0 or index >= len(current):
                raise ValueError(f"list index out of range: {index}")
            current = current[index]
        elif isinstance(current, dict):
            if token not in current:
                raise ValueError(f"key not found: {token}")
            current = current[token]
        else:
            raise ValueError("cannot traverse non-container")
    return current


def unicode_slice(text: str, start: int, end: int) -> str:
    """按 Unicode code point 取半开区间 ``[start,end)``。"""

    points = list(text)
    return "".join(points[start:end])


def _adder(issues: list[ValidationIssue]):
    def add(path: str, code: str, message: str) -> None:
        issues.append(ValidationIssue(path=path, code=code, message=message))

    return add


def _component_ids(provider: Any) -> set[str]:
    if not isinstance(provider, dict):
        return set()
    components = provider.get("components")
    if not isinstance(components, list):
        return set()
    ids: set[str] = set()
    for item in components:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            ids.add(item["id"])
    return ids


def _validate_provider(
    provider: Any,
    path: str,
    add,
    *,
    required_components: bool,
) -> None:
    if not isinstance(provider, dict):
        add(path, "missing_provider_identity", "provider 必须是对象")
        return
    unknown = set(provider) - PROVIDER_KEYS
    for key in sorted(unknown):
        add(f"{path}.{key}", "unknown_core_field", f"未知核心字段：{key}")
    for key in ("implementation", "revision"):
        value = provider.get(key)
        if not isinstance(value, str) or not value.strip():
            add(
                f"{path}.{key}",
                "missing_provider_identity",
                f"provider.{key} 必填",
            )
    version = provider.get("version")
    if version is not None and not isinstance(version, str):
        add(f"{path}.version", "bad_version", "version 必须是 string 或省略")

    if required_components:
        sha = provider.get("config_sha256")
        if not isinstance(sha, str) or not _SHA256_RE.match(sha):
            add(
                f"{path}.config_sha256",
                "bad_config_sha256",
                "config_sha256 必须是 64 位小写十六进制（消毒后摘要）",
            )
        components = provider.get("components")
        if not isinstance(components, list) or not components:
            add(
                f"{path}.components",
                "missing_components",
                "非 rejected 结果必须提供非空 components",
            )
            return
        seen_ids: set[str] = set()
        for index, component in enumerate(components):
            cpath = f"{path}.components[{index}]"
            if not isinstance(component, dict):
                add(cpath, "bad_component", "component 必须是对象")
                continue
            unknown_c = set(component) - COMPONENT_KEYS
            for key in sorted(unknown_c):
                add(f"{cpath}.{key}", "unknown_core_field", f"未知核心字段：{key}")
            cid = component.get("id")
            if not isinstance(cid, str) or not cid:
                add(f"{cpath}.id", "bad_component_id", "component.id 必填")
            elif cid in seen_ids:
                add(f"{cpath}.id", "duplicate_component_id", f"重复 component id：{cid}")
            else:
                seen_ids.add(cid)
            revision = component.get("revision")
            if not isinstance(revision, str) or not revision:
                add(f"{cpath}.revision", "bad_component_revision", "revision 必填")
            preflight = component.get("preflight_status")
            if preflight not in PREFLIGHT_STATUSES:
                add(
                    f"{cpath}.preflight_status",
                    "bad_preflight_status",
                    f"preflight_status 必须是 {PREFLIGHT_STATUSES}",
                )
            version_c = component.get("version")
            if version_c is not None and not isinstance(version_c, str):
                add(f"{cpath}.version", "bad_component_version", "version 必须是 string|null")
            model = component.get("model")
            if model is None:
                continue
            if not isinstance(model, dict):
                add(f"{cpath}.model", "bad_model", "model 必须是对象或 null")
                continue
            unknown_m = set(model) - MODEL_KEYS
            for key in sorted(unknown_m):
                add(f"{cpath}.model.{key}", "unknown_core_field", f"未知核心字段：{key}")
            for key in ("id", "revision"):
                if not isinstance(model.get(key), str) or not model.get(key):
                    add(
                        f"{cpath}.model.{key}",
                        "bad_model",
                        f"model.{key} 必填",
                    )


def _validate_execution(
    execution: Any, path: str, add, *, require_started: bool
) -> None:
    if not isinstance(execution, dict):
        add(path, "bad_execution", "execution 必须是对象")
        return
    unknown = set(execution) - EXECUTION_KEYS
    for key in sorted(unknown):
        add(f"{path}.{key}", "unknown_core_field", f"未知核心字段：{key}")
    finished = execution.get("finished_at")
    if not isinstance(finished, str) or not finished:
        add(f"{path}.finished_at", "missing_finished_at", "finished_at 必填")
    if require_started:
        started = execution.get("started_at")
        if not isinstance(started, str) or not started:
            add(f"{path}.started_at", "missing_started_at", "started_at 必填")


def _validate_failure_like(payload: dict[str, Any], path: str, add) -> None:
    unknown = set(payload) - FAILURE_KEYS
    for key in sorted(unknown):
        add(f"{path}.{key}", "unknown_core_field", f"未知核心字段：{key}")
    for key in ("stage", "code", "reason"):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            add(f"{path}.{key}", "bad_failure_field", f"{key} 必填且为非空字符串")
    if not isinstance(payload.get("retryable"), bool):
        add(f"{path}.retryable", "bad_retryable", "retryable 必须是布尔值")
    details = payload.get("details")
    if details is not None and not isinstance(details, dict):
        add(f"{path}.details", "bad_details", "details 必须是对象")


def _validate_success_result(
    result: dict[str, Any],
    path: str,
    add,
    *,
    input_item: dict[str, Any] | None,
    component_ids: set[str],
) -> None:
    unknown = set(result) - RESULT_KEYS
    for key in sorted(unknown):
        add(f"{path}.{key}", "unknown_core_field", f"未知核心字段：{key}")

    verdict = result.get("verdict")
    if verdict not in VERDICTS:
        add(f"{path}.verdict", "bad_verdict", f"verdict 必须是 {VERDICTS}")

    findings = result.get("findings")
    if not isinstance(findings, list):
        add(f"{path}.findings", "bad_findings", "findings 必须是数组")
        findings = []

    if verdict == "unsafe" and not findings:
        add(f"{path}.findings", "unsafe_without_findings", "unsafe 至少需要一个 finding")
    if verdict in ("safe", "undetermined") and findings:
        add(
            f"{path}.findings",
            "safe_with_findings",
            f"{verdict} 的 findings 必须为空",
        )

    checks = result.get("checks")
    if not isinstance(checks, dict):
        add(f"{path}.checks", "bad_checks", "checks 必须是对象")
        checks = {}
    else:
        unknown_checks = set(checks) - CHECKS_KEYS
        for key in sorted(unknown_checks):
            add(f"{path}.checks.{key}", "unknown_core_field", f"未知核心字段：{key}")
        coverage = checks.get("coverage")
        if not isinstance(coverage, dict) or coverage.get("status") not in COVERAGE_STATUSES:
            add(
                f"{path}.checks.coverage",
                "bad_coverage",
                f"coverage.status 必须是 {COVERAGE_STATUSES}",
            )
        limitations = checks.get("limitations")
        if not isinstance(limitations, list) or any(
            not isinstance(item, str) for item in limitations
        ):
            add(
                f"{path}.checks.limitations",
                "bad_limitations",
                "limitations 必须是字符串数组",
            )

    seen_finding_ids: set[str] = set()
    has_unlocated = False
    for index, finding in enumerate(findings):
        fpath = f"{path}.findings[{index}]"
        if not isinstance(finding, dict):
            add(fpath, "bad_finding", "finding 必须是对象")
            continue
        unknown_f = set(finding) - FINDING_KEYS
        for key in sorted(unknown_f):
            add(f"{fpath}.{key}", "unknown_core_field", f"未知核心字段：{key}")

        finding_id = finding.get("finding_id")
        if not isinstance(finding_id, str) or not finding_id:
            add(f"{fpath}.finding_id", "bad_finding_id", "finding_id 必填")
        elif finding_id in seen_finding_ids:
            add(f"{fpath}.finding_id", "duplicate_finding_id", "finding_id 在 item 内必须唯一")
        else:
            seen_finding_ids.add(finding_id)

        category = finding.get("category")
        if not isinstance(category, str) or not category:
            add(f"{fpath}.category", "bad_category", "category 必填")
        mechanism = finding.get("mechanism")
        if mechanism is not None and (
            not isinstance(mechanism, str) or not mechanism
        ):
            add(f"{fpath}.mechanism", "bad_mechanism", "mechanism 必须是非空字符串或省略")

        scope = finding.get("scope")
        if scope not in ("event", "trajectory"):
            add(f"{fpath}.scope", "bad_scope", "scope 必须是 event|trajectory")

        located = finding.get("located")
        if not isinstance(located, bool):
            add(f"{fpath}.located", "bad_located", "located 必须是布尔值")
            continue

        origin = finding.get("origin_event_ids")
        affected = finding.get("affected_event_ids")
        evidence = finding.get("evidence")
        if not isinstance(origin, list) or not isinstance(affected, list):
            add(fpath, "bad_event_ids", "origin/affected_event_ids 必须是数组")
            origin, affected = [], []
        if not isinstance(evidence, list):
            add(f"{fpath}.evidence", "bad_evidence", "evidence 必须是数组")
            evidence = []
        if input_item is not None:
            known_events = {event["event_id"] for event in input_item["trajectory"]["events"]}
            for event_id in origin + affected:
                if not isinstance(event_id, str) or event_id not in known_events:
                    add(fpath, "unknown_event_id", "origin/affected 必须引用输入中的事件")

        detector_component_id = finding.get("detector_component_id")
        if "detector" in finding and not isinstance(finding["detector"], dict):
            add(f"{fpath}.detector", "bad_detector", "detector 必须是对象")
        if not isinstance(detector_component_id, str) or not detector_component_id:
            add(
                f"{fpath}.detector_component_id",
                "bad_detector_component_id",
                "detector_component_id 必填",
            )
        elif detector_component_id not in component_ids:
            add(
                f"{fpath}.detector_component_id",
                "unknown_detector_component",
                f"未引用 provider.components[].id：{detector_component_id}",
            )

        confidence = finding.get("confidence")
        if not isinstance(confidence, dict):
            add(f"{fpath}.confidence", "bad_confidence", "confidence 必须是对象")
        else:
            unknown_c = set(confidence) - CONFIDENCE_KEYS
            for key in sorted(unknown_c):
                add(
                    f"{fpath}.confidence.{key}",
                    "unknown_core_field",
                    f"未知核心字段：{key}",
                )
            kind = confidence.get("kind")
            if not isinstance(kind, str) or not kind:
                add(f"{fpath}.confidence.kind", "bad_confidence_kind", "kind 必填")
            if not isinstance(confidence.get("calibrated_probability"), bool):
                add(
                    f"{fpath}.confidence.calibrated_probability",
                    "bad_calibrated_probability",
                    "calibrated_probability 必须是布尔值",
                )

        severity = finding.get("severity")
        if severity not in SEVERITIES:
            add(f"{fpath}.severity", "bad_severity", f"severity 必须是 {SEVERITIES}")

        reason = finding.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            add(f"{fpath}.reason", "bad_reason", "reason 必填")

        if located:
            if not origin and not affected:
                add(
                    fpath,
                    "located_without_events",
                    "located=true 时 origin 或 affected 至少一侧非空",
                )
            if not evidence:
                add(
                    f"{fpath}.evidence",
                    "located_without_evidence",
                    "located=true 时 evidence 不得为空",
                )
            for eindex, entry in enumerate(evidence):
                _validate_evidence(
                    entry,
                    f"{fpath}.evidence[{eindex}]",
                    add,
                    input_item=input_item,
                )
        else:
            has_unlocated = True
            if origin or affected or evidence:
                add(
                    fpath,
                    "unlocated_not_empty",
                    "located=false 时 event ids 与 evidence 必须为空",
                )
            limitations = checks.get("limitations") if isinstance(checks, dict) else None
            limitation_text = " ".join(limitations) if isinstance(limitations, list) else ""
            reason_text = reason if isinstance(reason, str) else ""
            blob = f"{limitation_text}\n{reason_text}".lower()
            if "locat" not in blob and "定位" not in blob:
                add(
                    f"{path}.checks.limitations",
                    "missing_location_limitation",
                    "未定位 Finding 必须在 limitations 或 reason 中说明定位限制",
                )

    if has_unlocated and isinstance(checks, dict):
        limitations = checks.get("limitations")
        if isinstance(limitations, list) and not any(
            isinstance(item, str)
            and ("locat" in item.lower() or "定位" in item)
            for item in limitations
        ):
            add(
                f"{path}.checks.limitations",
                "missing_location_limitation",
                "存在未定位 Finding 时 limitations 必须提及定位限制",
            )


def _validate_evidence(
    entry: Any,
    path: str,
    add,
    *,
    input_item: dict[str, Any] | None,
) -> None:
    if not isinstance(entry, dict):
        add(path, "bad_evidence", "evidence 项必须是对象")
        return
    unknown = set(entry) - EVIDENCE_KEYS
    for key in sorted(unknown):
        add(f"{path}.{key}", "unknown_core_field", f"未知核心字段：{key}")

    event_id = entry.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        add(f"{path}.event_id", "bad_event_id", "event_id 必填")

    pointer = entry.get("source_pointer")
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        add(f"{path}.source_pointer", "bad_evidence_pointer", "source_pointer 必须是 JSON Pointer")
        return

    excerpt = entry.get("excerpt")
    start = entry.get("start")
    end = entry.get("end")
    if not isinstance(excerpt, str):
        add(f"{path}.excerpt", "bad_excerpt", "excerpt 必须是字符串")
        return
    if not isinstance(start, int) or not isinstance(end, int) or isinstance(start, bool) or isinstance(end, bool):
        add(path, "bad_span", "start/end 必须是整数")
        return
    if start < 0 or end < start:
        add(path, "bad_span", "必须满足 0 <= start <= end")
        return

    if input_item is None:
        return
    trajectory = input_item.get("trajectory")
    if not isinstance(trajectory, dict):
        return

    events = trajectory.get("events")
    event_ids = {
        event.get("event_id")
        for event in events
        if isinstance(event, dict)
    } if isinstance(events, list) else set()
    if isinstance(event_id, str) and event_id not in event_ids:
        add(f"{path}.event_id", "unknown_event_id", f"event_id 不在输入轨迹中：{event_id}")

    # Pointer must resolve against the INPUT item (trajectory envelope root is the item).
    try:
        leaf = resolve_json_pointer(input_item, pointer)
    except ValueError as exc:
        add(
            f"{path}.source_pointer",
            "bad_evidence_pointer",
            f"pointer 无法解析：{exc}",
        )
        return
    if not isinstance(leaf, str):
        add(
            f"{path}.source_pointer",
            "bad_evidence_pointer",
            "pointer 必须指向字符串叶子",
        )
        return
    event_path = re.match(r"^/trajectory/events/(\d+)/content/", pointer)
    if event_path is None or events[int(event_path.group(1))].get("event_id") != event_id:
        add(f"{path}.source_pointer", "evidence_event_mismatch", "Evidence Pointer 必须指向 event_id 对应事件的 content")
        return
    if "/trajectory/" not in pointer or "/content/" not in pointer:
        add(
            f"{path}.source_pointer",
            "bad_evidence_pointer",
            "pointer 必须指向轨迹事件 content 下的字符串叶子",
        )
        return

    points = list(leaf)
    if end > len(points):
        add(path, "bad_span", "end 超出字符串 code-point 长度")
        return
    actual = "".join(points[start:end])
    if actual != excerpt:
        add(
            f"{path}.excerpt",
            "bad_excerpt",
            "excerpt 必须精确等于输入原文的 [start,end) 区间",
        )
