# SPDX-License-Identifier: Apache-2.0
"""把 topic4-audit-input/1 批次显式降级到确定性 baseline。

仅在调用方 ``allow_fallback=True`` 时使用。从白名单投影事件重建最小
``NormalizedTrajectory``，逐条运行 baseline，并在结果中保留 degraded 与
原始 Topic4 失败；不会静默替代正式 Provider。
"""

from __future__ import annotations

from typing import Any

from .. import baseline
from ..model import Event, NormalizedTrajectory, SourceLocator


def run_baseline_batch_fallback(
    input_batch: dict[str, Any],
    *,
    degraded: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """对 Exchange Envelope 中每条 item 运行 deterministic-baseline。"""

    items_out: list[dict[str, Any]] = []
    for index, item in enumerate(input_batch.get("items") or []):
        if not isinstance(item, dict):
            items_out.append(
                {
                    "sample_id": None,
                    "status": "failure",
                    "failure": {
                        "stage": "fallback",
                        "code": "invalid_item",
                        "reason": "input item 不是对象",
                        "retryable": False,
                    },
                }
            )
            continue
        sample_id = item.get("sample_id")
        try:
            trajectory = _trajectory_from_audit_item(item, record_index=index)
            output = baseline.audit(trajectory, degraded=degraded)
            items_out.append(
                {
                    "sample_id": sample_id,
                    "audit_input_sha256": item.get("audit_input_sha256"),
                    "status": "success",
                    "result": {
                        "verdict": output.verdict.value,
                        "checks": output.checks.to_dict(),
                        "findings": [finding.to_dict() for finding in output.findings],
                        "facts": [fact.to_dict() for fact in output.facts],
                        "provider": baseline.PROVIDER_ID,
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001 - 单条失败不丢弃批次
            items_out.append(
                {
                    "sample_id": sample_id,
                    "audit_input_sha256": item.get("audit_input_sha256"),
                    "status": "failure",
                    "failure": {
                        "stage": "fallback",
                        "code": "baseline_failed",
                        "reason": f"{type(exc).__name__}: {exc}",
                        "retryable": False,
                    },
                }
            )

    success = sum(1 for row in items_out if row.get("status") == "success")
    if success == len(items_out) and items_out:
        status = "complete"
    elif success == 0:
        status = "failed"
    else:
        status = "partial"

    identity = baseline.provider_identity()
    return {
        "schema_version": "topic5-baseline-fallback/1",
        "batch_id": input_batch.get("batch_id"),
        "status": status,
        "provider": identity.to_dict(),
        "degraded": degraded,
        "items": items_out,
    }


def _trajectory_from_audit_item(
    item: dict[str, Any], *, record_index: int
) -> NormalizedTrajectory:
    sample_id = str(item.get("sample_id") or f"sample-{record_index}")
    audit_sha = str(item.get("audit_input_sha256") or "")
    trajectory = item.get("trajectory")
    if not isinstance(trajectory, dict):
        raise ValueError("item.trajectory 必须是对象")
    locator = SourceLocator(
        record_index=record_index,
        json_path=f"items[{record_index}]",
        record_sha256=audit_sha or ("0" * 64),
        dataset_id="topic4-audit-input",
        dataset_version="1",
    )
    events: list[Event] = []
    for raw_event in trajectory.get("events") or []:
        if not isinstance(raw_event, dict):
            continue
        events.append(
            Event(
                event_id=str(raw_event["event_id"]),
                sequence=int(raw_event["sequence"]),
                type=str(raw_event["type"]),
                actor=str(raw_event["actor"]),
                source_step_id=raw_event.get("source_step_id"),
                content=dict(raw_event.get("content") or {}),
                source=locator,
                raw=raw_event,
            )
        )
    if not events:
        raise ValueError("trajectory.events 不能为空")
    return NormalizedTrajectory(
        sample_id=sample_id,
        source_format="audit-trajectory/1",
        adapter_id="topic4-audit-input",
        adapter_version="1",
        input_sha256=audit_sha or ("0" * 64),
        dataset_id="topic4-audit-input",
        dataset_version="1",
        record_index=record_index,
        record_json_path=f"items[{record_index}]",
        record_sha256=audit_sha or ("0" * 64),
        record_raw=item,
        events=events,
        tool_definitions=trajectory.get("tool_definitions"),
    )


__all__ = ["run_baseline_batch_fallback"]
