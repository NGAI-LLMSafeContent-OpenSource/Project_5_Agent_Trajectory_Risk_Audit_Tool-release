# SPDX-License-Identifier: Apache-2.0
"""HINTBench 来源 adapter。

HINTBench 的每个对话轮带 ``step_id``，风险标签通过 ``risk_origin_step`` 或
``step_id`` 指向具体步骤。adapter 保留步骤身份并把标签解析到同一步骤产出的
事件；标签指向不存在的步骤时产生 ``source_locator_failed``，不静默丢弃。
"""

from __future__ import annotations

from typing import Any

from . import __version__
from .errors import (
    STAGE_ADAPTER_NORMALIZATION,
    STAGE_ADAPTER_VALIDATION,
    STAGE_SOURCE_INTEGRITY,
    ConversionError,
)
from .model import (
    LINK_STATUS_NO_CALL_ID,
    LINK_STATUS_POSITIONAL,
    SOURCE_LABEL_SCOPE_STEP,
    SOURCE_LABEL_SCOPE_TRAJECTORY,
    Event,
    NormalizedTrajectory,
)
from .normalization import TrajectoryBuilder
from .source_common import parse_environment_output, parse_tool_action
from .source_labels import MAPPING_VERSION, map_hintbench_category

ADAPTER_ID = "hintbench"
ADAPTER_VERSION = __version__
SOURCE_FORMAT = "hintbench"
DESCRIPTION = "HINTBench 轨迹记录：带 step_id 的对话轮 + 轨迹级与步骤级风险标签"

CONSUMED_TOP_LEVEL_FIELDS = {
    "task_id",
    "id",
    "is_risky",
    "risk_labels",
    "trajectory",
    "env",
}


def validate_record(record: Any) -> list[dict[str, str]]:
    """校验一条 HINTBench 原始记录，包括 ``step_id`` 唯一性。"""
    if not isinstance(record, dict):
        return [{"path": "", "message": "HINTBench 记录必须是 JSON 对象"}]

    issues: list[dict[str, str]] = []
    task_id = record.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        issues.append({"path": "task_id", "message": "task_id 必须是非空字符串"})

    is_risky = record.get("is_risky")
    if not isinstance(is_risky, bool):
        issues.append({"path": "is_risky", "message": "is_risky 必须是布尔值"})

    risk_labels = record.get("risk_labels")
    if risk_labels is not None and not isinstance(risk_labels, list):
        issues.append({"path": "risk_labels", "message": "risk_labels 必须是数组"})

    trajectory = record.get("trajectory")
    if not isinstance(trajectory, list):
        issues.append({"path": "trajectory", "message": "trajectory 必须是数组"})
        return issues
    if not trajectory:
        issues.append({"path": "trajectory", "message": "trajectory 不能为空"})
        return issues

    seen_steps: set[Any] = set()
    for index, turn in enumerate(trajectory):
        turn_path = f"trajectory[{index}]"
        if not isinstance(turn, dict):
            issues.append({"path": turn_path, "message": "对话轮必须是对象"})
            continue
        role = turn.get("role")
        if not isinstance(role, str) or not role.strip():
            issues.append({"path": f"{turn_path}.role", "message": "role 必须是非空字符串"})
        step_id = turn.get("step_id")
        if isinstance(step_id, bool) or not isinstance(step_id, (int, str)):
            issues.append(
                {"path": f"{turn_path}.step_id", "message": "step_id 必须是整数或字符串"}
            )
        elif step_id in seen_steps:
            issues.append(
                {"path": f"{turn_path}.step_id", "message": f"step_id 重复：{step_id!r}"}
            )
        else:
            seen_steps.add(step_id)

    return issues


def sample_identity(record: Any) -> tuple[str | None, str]:
    """使用 HINTBench 的 ``task_id`` 作为声明样本身份。"""
    if isinstance(record, dict):
        task_id = record.get("task_id")
        if isinstance(task_id, str) and task_id.strip():
            return task_id.strip(), "declared"
    return None, "derived"


def inspect_source(record: Any) -> list[dict[str, Any]]:
    """检查 HINTBench 的风险标签是否指向真实存在的来源步骤。

    HINTBench 的部分记录在发布前删掉过若干轮，``step_id`` 序列因此断号，而
    风险标签仍指向被删掉的步骤。这类记录不是 adapter 的转换缺陷，而是来源
    完整性缺陷：必须如实排除并记录原因，不能伪造定位，也不能静默丢弃。
    """
    if not isinstance(record, dict):
        return []
    trajectory = record.get("trajectory")
    labels = record.get("risk_labels")
    if not isinstance(trajectory, list) or not isinstance(labels, list):
        return []

    step_ids = {
        turn.get("step_id") for turn in trajectory if isinstance(turn, dict)
    }
    defects: list[dict[str, Any]] = []
    for position, label in enumerate(labels):
        if not isinstance(label, dict):
            continue
        field = "step_id"
        step_ref = label.get("step_id")
        if step_ref is None:
            field = "risk_origin_step"
            step_ref = label.get("risk_origin_step")
        path = f"risk_labels[{position}].{field}"
        if step_ref is None:
            defects.append(
                {
                    "stage": STAGE_SOURCE_INTEGRITY,
                    "code": "missing_source_step_reference",
                    "path": path,
                    "reason": "风险标签既没有 step_id 也没有 risk_origin_step",
                    "details": {"risk_label": label},
                }
            )
            continue
        if step_ref not in step_ids:
            defects.append(
                {
                    "stage": STAGE_SOURCE_INTEGRITY,
                    "code": "dangling_source_step_reference",
                    "path": path,
                    "reason": (
                        f"风险标签指向来源步骤 {step_ref!r}，"
                        "但该步骤在 trajectory 中不存在"
                    ),
                    "details": {
                        "source_step_id": step_ref,
                        "declared_field": field,
                        "known_source_step_ids": sorted(
                            (value for value in step_ids if value is not None),
                            key=lambda value: (str(type(value)), str(value)),
                        ),
                    },
                }
            )
    return defects


def expected_source_label_count(record: Any) -> int | None:
    """轨迹级 ``is_risky`` 一条，加上每条 ``risk_labels``。"""
    if not isinstance(record, dict):
        return None
    labels = record.get("risk_labels")
    return 1 + (len(labels) if isinstance(labels, list) else 0)


def normalize_record(
    record: dict[str, Any],
    *,
    record_index: int,
    dataset_id: str,
    dataset_version: str,
    input_sha256: str,
    record_json_path: str,
    record_sha256: str,
    sample_id: str,
) -> list[NormalizedTrajectory]:
    """把一条 HINTBench 记录归一为一条统一轨迹。"""
    issues = validate_record(record)
    if issues:
        raise ConversionError(
            "schema_invalid",
            "HINTBench 记录不符合 schema",
            stage=STAGE_ADAPTER_VALIDATION,
            details={"issues": issues, "sample_id": sample_id},
            sample_id=sample_id,
        )

    defects = inspect_source(record)
    if defects:
        first = defects[0]
        raise ConversionError(
            first["code"],
            first["reason"],
            stage=first["stage"],
            details={
                "path": first["path"],
                "sample_id": sample_id,
                "record_index": record_index,
                **first["details"],
            },
            sample_id=sample_id,
        )

    builder = TrajectoryBuilder(
        sample_id=sample_id,
        source_format=SOURCE_FORMAT,
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        input_sha256=input_sha256,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        record_index=record_index,
        record_json_path=record_json_path,
        record_sha256=record_sha256,
        record_raw=record,
        unmapped_top_level_fields=sorted(set(record) - CONSUMED_TOP_LEVEL_FIELDS),
    )
    previous_calls: list[Event] = []

    for turn_index, turn in enumerate(record["trajectory"]):
        turn_path = _join_path(record_json_path, f"trajectory[{turn_index}]")
        role = str(turn.get("role") or "").strip()
        step_id = turn.get("step_id")
        actor = _actor(role)
        builder.add_step(
            source_step_id=step_id,
            source_index=turn_index,
            json_path=turn_path,
            role=role,
            raw=turn,
        )
        turn_events: list[Event] = []

        if role in {"system", "user"}:
            text = turn.get("content")
            if isinstance(text, str) and text:
                turn_events.append(
                    builder.emit(
                        "message",
                        actor,
                        {
                            "role": role,
                            "text": text,
                            "parts": [{"type": "text", "text": text}],
                        },
                        json_path=f"{turn_path}.content",
                        raw=text,
                        source_step_id=step_id,
                    )
                )
            else:
                turn_events.append(
                    _unsupported(builder, turn_path, turn, actor, "missing_content", None, step_id)
                )
        elif role == "agent":
            turn_events.extend(_agent_events(builder, turn, turn_path, step_id))
            if not turn_events:
                turn_events.append(
                    _unsupported(
                        builder, turn_path, turn, actor, "missing_content", None, step_id
                    )
                )
        elif role == "environment":
            output = turn.get("content")
            parsed = parse_environment_output(output)
            call_event_id = previous_calls[-1].event_id if previous_calls else None
            turn_events.append(
                builder.emit(
                    "tool_result",
                    "tool",
                    {
                        "call_id": None,
                        "call_event_id": call_event_id,
                        "link_status": (
                            LINK_STATUS_POSITIONAL if call_event_id else LINK_STATUS_NO_CALL_ID
                        ),
                        "link_basis": "preceding_agent_action",
                        "declared_status": parsed["declared_status"],
                        "declared_status_raw": parsed["declared_status_raw"],
                        "output": output,
                        "output_format": parsed["output_format"],
                    },
                    json_path=turn_path,
                    raw=turn,
                    source_step_id=step_id,
                )
            )
        else:
            turn_events.append(
                _unsupported(
                    builder, turn_path, turn, actor, "unsupported_role", role, step_id
                )
            )

        previous_calls = [event for event in turn_events if event.type == "tool_call"]

    _add_source_labels(builder, record)
    return [builder.finalize()]


def _add_source_labels(builder: TrajectoryBuilder, record: dict[str, Any]) -> None:
    builder.add_source_label(
        scope=SOURCE_LABEL_SCOPE_TRAJECTORY,
        source_step_id=None,
        verdict="unsafe" if record.get("is_risky") is True else "safe",
        categories=[],
        source_categories=[],
        reason=None,
        mapping={
            "version": MAPPING_VERSION,
            "status": "not_applicable",
            "basis": "is_risky 是来源轨迹级真值，不含第一层类别",
        },
        raw={"is_risky": record.get("is_risky")},
    )

    for label in record.get("risk_labels") or []:
        if not isinstance(label, dict):
            raise ConversionError(
                "schema_invalid",
                "risk_labels 条目必须是对象",
                stage=STAGE_ADAPTER_VALIDATION,
                details={"risk_label": label, "sample_id": builder.sample_id},
                sample_id=builder.sample_id,
            )
        step_ref = label.get("step_id")
        if step_ref is None:
            step_ref = label.get("risk_origin_step")
        if step_ref is None:
            # 正常情况下 inspect_source 已提前拦截；此处是防御性兜底。
            raise ConversionError(
                "missing_source_step_reference",
                "risk_labels 条目缺少 step_id / risk_origin_step，无法追溯到来源步骤",
                stage=STAGE_SOURCE_INTEGRITY,
                details={"sample_id": builder.sample_id, "risk_label": label},
                sample_id=builder.sample_id,
            )
        category = (
            label.get("category")
            if isinstance(label.get("category"), str)
            else label.get("big_category")
        )
        categories, mapping = map_hintbench_category(category)
        source_categories = [
            value
            for value in (
                category,
                label.get("risk_type"),
                label.get("risk_subtype"),
                label.get("risk_name"),
            )
            if isinstance(value, str) and value
        ]
        reason = label.get("description") or label.get("risk_name")
        builder.add_source_label(
            scope=SOURCE_LABEL_SCOPE_STEP,
            source_step_id=step_ref,
            verdict="unsafe",
            categories=categories,
            source_categories=source_categories,
            reason=reason if isinstance(reason, str) and reason else None,
            mapping=mapping,
            raw=label,
        )


def _agent_events(
    builder: TrajectoryBuilder,
    turn: dict[str, Any],
    turn_path: str,
    step_id: Any,
) -> list[Event]:
    events: list[Event] = []
    thought = turn.get("thought")
    if isinstance(thought, str) and thought.strip():
        events.append(
            builder.emit(
                "thought",
                "assistant",
                {"text": thought},
                json_path=f"{turn_path}.thought",
                raw=thought,
                source_step_id=step_id,
            )
        )
    elif thought is not None and not isinstance(thought, str):
        events.append(
            _unsupported(
                builder,
                f"{turn_path}.thought",
                thought,
                "assistant",
                "invalid_thought",
                "thought",
                step_id,
            )
        )

    action = turn.get("action")
    if isinstance(action, str) and action.strip():
        if not action.lstrip().startswith("{"):
            raise ConversionError(
                "unsupported_record",
                "HINTBench agent action 不是工具调用 JSON，未支持该记录形态",
                stage=STAGE_ADAPTER_NORMALIZATION,
                details={"action_prefix": action[:120], "sample_id": builder.sample_id},
                sample_id=builder.sample_id,
            )
        tool_content = parse_tool_action(action)
        events.append(
            builder.emit(
                "tool_call",
                "assistant",
                {"call_id": None, **tool_content},
                json_path=f"{turn_path}.action",
                raw=action,
                source_step_id=step_id,
            )
        )
    elif action is not None and not isinstance(action, str):
        events.append(
            _unsupported(
                builder,
                f"{turn_path}.action",
                action,
                "assistant",
                "invalid_action",
                "action",
                step_id,
            )
        )

    content = turn.get("content")
    if isinstance(content, str) and content:
        events.append(
            builder.emit(
                "message",
                "assistant",
                {
                    "role": "agent",
                    "text": content,
                    "parts": [{"type": "text", "text": content}],
                },
                json_path=f"{turn_path}.content",
                raw=content,
                source_step_id=step_id,
            )
        )
    return events


def _unsupported(
    builder: TrajectoryBuilder,
    turn_path: str,
    raw: Any,
    actor: str,
    reason: str,
    part_type: Any,
    source_step_id: Any,
) -> Event:
    return builder.emit(
        "unsupported_content",
        actor,
        {"part_type": part_type, "reason": reason},
        json_path=turn_path,
        raw=raw,
        source_step_id=source_step_id,
    )


def _actor(role: str) -> str:
    if role == "agent":
        return "assistant"
    if role == "environment":
        return "tool"
    return role or "unknown"


def _join_path(base: str, suffix: str) -> str:
    return f"{base}.{suffix}" if base else suffix


__all__ = [
    "ADAPTER_ID",
    "ADAPTER_VERSION",
    "DESCRIPTION",
    "SOURCE_FORMAT",
    "expected_source_label_count",
    "inspect_source",
    "normalize_record",
    "sample_identity",
    "validate_record",
]
