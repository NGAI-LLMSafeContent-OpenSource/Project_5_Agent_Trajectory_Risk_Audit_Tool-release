# SPDX-License-Identifier: Apache-2.0
"""Fake Topic4 Provider 场景生成。

通过配置 ``{"scenario": "..."}`` 选择行为。合同合法的 complete/partial/
failed/rejected 退出 0 并原子发布；transport 失败退出非零。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMPONENT_ID = "fake-topic4-semantic"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sanitize_config(config: dict[str, Any]) -> dict[str, Any]:
    secret_keys = ("api_key", "token", "password", "secret", "access_key")
    out: dict[str, Any] = {}
    for key, value in config.items():
        lower = str(key).lower()
        if any(token in lower for token in secret_keys):
            out[key] = "[REDACTED]"
        elif isinstance(value, dict):
            out[key] = sanitize_config(value)
        else:
            out[key] = value
    return out


def base_provider(config: dict[str, Any]) -> dict[str, Any]:
    sanitized = sanitize_config(config)
    return {
        "implementation": "fake-topic4-provider",
        "version": "0.1.0",
        "revision": "fake-rev-1",
        "config_sha256": sha256_json(sanitized),
        "components": [
            {
                "id": COMPONENT_ID,
                "version": "0.1.0",
                "revision": "fake-component-1",
                "model": {
                    "id": "fake-model",
                    "revision": "fake-model-rev-1",
                },
                "preflight_status": "ready",
            }
        ],
    }


def base_execution() -> dict[str, Any]:
    now = utcnow()
    return {
        "started_at": now,
        "finished_at": now,
        "runtime": "fake",
        "platform": "test",
        "device": "cpu",
    }


def failure_obj(
    *,
    stage: str = "inference",
    code: str = "model_timeout",
    reason: str = "Simulated item failure.",
    retryable: bool = True,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "code": code,
        "reason": reason,
        "retryable": retryable,
    }


def checks(*, limitations: list[str] | None = None) -> dict[str, Any]:
    return {
        "coverage": {"status": "observed_only"},
        "limitations": list(limitations or []),
    }


def _text_bearing_event(events: list[dict[str, Any]]) -> tuple[dict[str, Any], str, int]:
    """Pick an event with textual content for located evidence (ATBench 等非历史形状)."""

    preferred: list[tuple[int, dict[str, Any]]] = []
    fallback: list[tuple[int, dict[str, Any]]] = []
    for index, event in enumerate(events):
        text = (event.get("content") or {}).get("text")
        if not isinstance(text, str) or not text:
            continue
        entry = (index, event)
        if event.get("type") == "message" and event.get("actor") == "assistant":
            preferred.append(entry)
        else:
            fallback.append(entry)
    chosen = preferred[0] if preferred else fallback[0] if fallback else None
    if chosen is None:
        raise ValueError("no textual event available for located finding")
    index, event = chosen
    return event, event["content"]["text"], index


def located_finding(item: dict[str, Any], *, finding_id: str = "fnd-1") -> dict[str, Any]:
    """构造一条已定位 finding。

    当 ``events[1]`` 带文本时，保持历史 openai-messages / 冻结 corpus 口径
    （固定 ``evt-0001`` / ``evt-0002`` 与 ``/trajectory/events/1/...``），避免
    改写已冻结 corpus digest。否则（例如 ATBench 第二事件是工具调用）再按
    文本事件动态选取。
    """

    events = item["trajectory"]["events"]
    historic_text = None
    if len(events) > 1:
        candidate = (events[1].get("content") or {}).get("text")
        if isinstance(candidate, str) and candidate:
            historic_text = candidate

    if historic_text is not None:
        text = historic_text
        origin_ids = ["evt-0001"]
        affected_ids = ["evt-0002"]
        evidence_event_id = "evt-0002"
        source_pointer = "/trajectory/events/1/content/text"
    else:
        text_event, text, text_index = _text_bearing_event(events)
        origin_ids = [events[0]["event_id"]]
        affected_ids = [text_event["event_id"]]
        evidence_event_id = text_event["event_id"]
        source_pointer = f"/trajectory/events/{text_index}/content/text"

    points = list(text)
    start = 0
    end = min(7, len(points))
    excerpt = "".join(points[start:end])
    return {
        "finding_id": finding_id,
        "category": "unauthorized_action",
        "mechanism": "fake_rule",
        "scope": "event",
        "located": True,
        "origin_event_ids": origin_ids,
        "affected_event_ids": affected_ids,
        "evidence": [
            {
                "event_id": evidence_event_id,
                "field": "text",
                "source_pointer": source_pointer,
                "excerpt": excerpt,
                "start": start,
                "end": end,
            }
        ],
        "reason": "Assistant claimed a destructive action.",
        "detector_component_id": COMPONENT_ID,
        "detector": {"rule": "fake-located"},
        "confidence": {
            "kind": "model_assertion",
            "calibrated_probability": False,
        },
        "severity": "high",
    }


def unlocated_finding(*, finding_id: str = "fnd-unlocated") -> dict[str, Any]:
    return {
        "finding_id": finding_id,
        "category": "policy_uncertainty",
        "mechanism": "fake_rule",
        "scope": "trajectory",
        "located": False,
        "origin_event_ids": [],
        "affected_event_ids": [],
        "evidence": [],
        "reason": "Risk judged present but location restriction prevents reliable event binding.",
        "detector_component_id": COMPONENT_ID,
        "detector": {"rule": "fake-unlocated"},
        "confidence": {
            "kind": "model_assertion",
            "calibrated_probability": False,
        },
        "severity": "medium",
    }


def success_item(
    item: dict[str, Any],
    *,
    verdict: str,
    findings: list[dict[str, Any]] | None = None,
    limitations: list[str] | None = None,
    extensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "verdict": verdict,
        "findings": list(findings or []),
        "checks": checks(limitations=limitations),
    }
    if extensions:
        body["extensions"] = extensions
    return {
        "sample_id": item["sample_id"],
        "audit_input_sha256": item["audit_input_sha256"],
        "status": "success",
        "result": body,
    }


def failure_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "sample_id": item["sample_id"],
        "audit_input_sha256": item["audit_input_sha256"],
        "status": "failure",
        "failure": failure_obj(),
    }


def envelope(
    batch: dict[str, Any],
    config: dict[str, Any],
    *,
    status: str,
    items: list[dict[str, Any]],
    rejection: dict[str, Any] | None = None,
    provider: dict[str, Any] | None = None,
    mutate: Any | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": "topic4-audit-result/1",
        "batch_id": batch.get("batch_id"),
        "provider_run_id": f"fake-{uuid.uuid4().hex}",
        "status": status,
        "provider": provider if provider is not None else base_provider(config),
        "execution": base_execution(),
        "items": items,
    }
    if rejection is not None:
        result["rejection"] = rejection
    if mutate is not None:
        mutate(result)
    return result


def build_result(batch: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    scenario = str(config.get("scenario") or "complete_multi")
    items = list(batch.get("items") or [])
    if not items and scenario not in {"rejected_batch"}:
        raise ValueError("input batch has no items")

    if scenario == "complete_single":
        target = items[:1]
        return envelope(
            batch,
            config,
            status="complete",
            items=[success_item(target[0], verdict="safe")],
            mutate=lambda r: r.update(batch_id=batch.get("batch_id")),
        )

    if scenario == "complete_multi":
        return envelope(
            batch,
            config,
            status="complete",
            items=[success_item(item, verdict="safe") for item in items],
        )

    if scenario == "partial_mixed":
        if len(items) < 2:
            raise ValueError("partial_mixed needs >=2 items")
        out = [success_item(items[0], verdict="safe")]
        out.extend(failure_item(item) for item in items[1:])
        return envelope(batch, config, status="partial", items=out)

    if scenario == "failed_all":
        return envelope(
            batch,
            config,
            status="failed",
            items=[failure_item(item) for item in items],
        )

    if scenario == "rejected_batch":
        return envelope(
            batch,
            config,
            status="rejected",
            items=[],
            rejection={
                "stage": "validation",
                "code": "invalid_input_or_config",
                "reason": "Simulated batch rejection.",
                "retryable": False,
            },
            provider={
                "implementation": "fake-topic4-provider",
                "version": "0.1.0",
                "revision": "fake-rev-1",
            },
            mutate=lambda r: r["execution"].pop("started_at", None),
        )

    if scenario == "safe":
        return envelope(
            batch,
            config,
            status="complete",
            items=[success_item(item, verdict="safe") for item in items],
        )

    if scenario == "unsafe_located":
        return envelope(
            batch,
            config,
            status="complete",
            items=[
                success_item(
                    item,
                    verdict="unsafe",
                    findings=[located_finding(item, finding_id=f"fnd-{index}")],
                )
                for index, item in enumerate(items)
            ],
        )

    if scenario == "unsafe_unlocated":
        return envelope(
            batch,
            config,
            status="complete",
            items=[
                success_item(
                    item,
                    verdict="unsafe",
                    findings=[unlocated_finding(finding_id=f"fnd-u-{index}")],
                    limitations=["location restriction: unable to bind risk to events"],
                )
                for index, item in enumerate(items)
            ],
        )

    if scenario == "undetermined":
        return envelope(
            batch,
            config,
            status="complete",
            items=[success_item(item, verdict="undetermined") for item in items],
        )

    if scenario == "bad_schema_version":
        result = envelope(
            batch,
            config,
            status="complete",
            items=[success_item(item, verdict="safe") for item in items],
        )
        result["schema_version"] = "topic4-audit-result/2"
        return result

    if scenario == "bad_batch_id":
        result = envelope(
            batch,
            config,
            status="complete",
            items=[success_item(item, verdict="safe") for item in items],
        )
        result["batch_id"] = "wrong-batch-id"
        return result

    if scenario == "missing_sample":
        return envelope(
            batch,
            config,
            status="complete",
            items=[success_item(items[0], verdict="safe")] if items else [],
        )

    if scenario == "duplicate_sample":
        first = success_item(items[0], verdict="safe")
        return envelope(
            batch,
            config,
            status="complete",
            items=[first, dict(first)],
        )

    if scenario == "unknown_sample":
        forged = success_item(items[0], verdict="safe")
        forged = {
            **forged,
            "sample_id": "sample-does-not-exist",
            "audit_input_sha256": items[0]["audit_input_sha256"],
        }
        return envelope(batch, config, status="complete", items=[forged])

    if scenario == "sha_mismatch":
        bad = success_item(items[0], verdict="safe")
        bad["audit_input_sha256"] = "0" * 64
        rest = [success_item(item, verdict="safe") for item in items[1:]]
        return envelope(batch, config, status="complete", items=[bad, *rest])

    if scenario == "missing_provider_identity":
        result = envelope(
            batch,
            config,
            status="complete",
            items=[success_item(item, verdict="safe") for item in items],
        )
        result["provider"] = {"implementation": "fake-topic4-provider"}
        return result

    if scenario == "bad_evidence_pointer":
        finding = located_finding(items[0])
        finding["evidence"][0]["source_pointer"] = "/trajectory/events/99/content/text"
        return envelope(
            batch,
            config,
            status="complete",
            items=[
                success_item(items[0], verdict="unsafe", findings=[finding]),
                *[success_item(item, verdict="safe") for item in items[1:]],
            ],
        )

    if scenario == "bad_excerpt":
        finding = located_finding(items[0])
        finding["evidence"][0]["excerpt"] = "NOT-THE-REAL-EXCERPT"
        return envelope(
            batch,
            config,
            status="complete",
            items=[
                success_item(items[0], verdict="unsafe", findings=[finding]),
                *[success_item(item, verdict="safe") for item in items[1:]],
            ],
        )

    if scenario == "bad_span":
        finding = located_finding(items[0])
        finding["evidence"][0]["start"] = 0
        finding["evidence"][0]["end"] = 9999
        return envelope(
            batch,
            config,
            status="complete",
            items=[
                success_item(items[0], verdict="unsafe", findings=[finding]),
                *[success_item(item, verdict="safe") for item in items[1:]],
            ],
        )

    if scenario == "unknown_core_field":
        result = envelope(
            batch,
            config,
            status="complete",
            items=[success_item(item, verdict="safe") for item in items],
        )
        result["mystery_core"] = {"should": "fail"}
        return result

    if scenario == "legal_extensions":
        return envelope(
            batch,
            config,
            status="complete",
            items=[
                success_item(
                    item,
                    verdict="safe",
                    extensions={"diag": {"latency_ms": 1}},
                )
                for item in items
            ],
            mutate=lambda r: r.update(
                extensions={"provider_private": {"ok": True}}
            ),
        )

    if scenario == "invalid_json":
        # Handled by CLI before/instead of JSON write.
        raise _TransportScenario("invalid_json")

    if scenario == "missing_output":
        raise _TransportScenario("missing_output")

    if scenario == "nonzero_exit":
        raise _TransportScenario("nonzero_exit")

    if scenario == "timeout":
        time.sleep(float(config.get("sleep_seconds") or 30))
        return envelope(
            batch,
            config,
            status="complete",
            items=[success_item(item, verdict="safe") for item in items],
        )

    raise ValueError(f"unknown scenario: {scenario}")


class _TransportScenario(Exception):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
