# SPDX-License-Identifier: Apache-2.0
"""ATBench 来源 adapter。

ATBench 原生只提供轨迹级 ``label`` / ``risk_source`` / ``failure_mode`` 与
单轮对话记录，没有步骤标签。因此来源标签保持 ``scope=trajectory`` 且未定位，
不根据相邻文本补造步骤真值。
"""

from __future__ import annotations

from typing import Any

from . import __version__
from .errors import STAGE_ADAPTER_NORMALIZATION, STAGE_ADAPTER_VALIDATION, ConversionError
from .model import (
    LINK_STATUS_NO_CALL_ID,
    LINK_STATUS_POSITIONAL,
    SOURCE_LABEL_SCOPE_TRAJECTORY,
    Event,
    NormalizedTrajectory,
)
from .normalization import TrajectoryBuilder
from .source_common import (
    parse_complete_action,
    parse_environment_output,
    parse_tool_action,
)
from .source_labels import atbench_mapping

ADAPTER_ID = "atbench"
ADAPTER_VERSION = __version__
SOURCE_FORMAT = "atbench"
DESCRIPTION = "ATBench 轨迹记录：contents 对话轮 + 轨迹级 label / risk metadata"

CONSUMED_TOP_LEVEL_FIELDS = {
    "id",
    "tool_used",
    "contents",
    "label",
    "risk_source",
    "failure_mode",
    "reason",
    "real_world_harm",
}

LABEL_FIELDS = {
    "id",
    "label",
    "risk_source",
    "failure_mode",
    "reason",
    "real_world_harm",
}


def validate_record(record: Any) -> list[dict[str, str]]:
    """校验一条 ATBench 原始记录；返回机器可读问题列表。"""
    if not isinstance(record, dict):
        return [{"path": "", "message": "ATBench 记录必须是 JSON 对象"}]

    issues: list[dict[str, str]] = []
    record_id = record.get("id")
    if isinstance(record_id, bool) or not isinstance(record_id, (int, str)):
        issues.append({"path": "id", "message": "id 必须是字符串或整数"})
    elif not str(record_id).strip():
        issues.append({"path": "id", "message": "id 不能为空"})

    label = record.get("label")
    if isinstance(label, bool) or not isinstance(label, int) or label not in (0, 1):
        issues.append({"path": "label", "message": "label 必须是整数 0 或 1"})

    contents = record.get("contents")
    if not isinstance(contents, list):
        issues.append({"path": "contents", "message": "contents 必须是数组"})
        return issues
    if not contents:
        issues.append({"path": "contents", "message": "contents 不能为空"})
        return issues

    for conv_index, conversation in enumerate(contents):
        conv_path = f"contents[{conv_index}]"
        if not isinstance(conversation, list):
            issues.append({"path": conv_path, "message": "每个 conversation 必须是数组"})
            continue
        if not conversation:
            issues.append({"path": conv_path, "message": "conversation 不能为空"})
            continue
        for turn_index, turn in enumerate(conversation):
            turn_path = f"{conv_path}[{turn_index}]"
            if not isinstance(turn, dict):
                issues.append({"path": turn_path, "message": "对话轮必须是对象"})
                continue
            role = turn.get("role")
            if not isinstance(role, str) or not role.strip():
                issues.append({"path": f"{turn_path}.role", "message": "role 必须是非空字符串"})
    return issues


def sample_identity(record: Any) -> tuple[str | None, str]:
    """从 ATBench 的 ``id`` 派生稳定样本身份。"""
    if isinstance(record, dict):
        record_id = record.get("id")
        if isinstance(record_id, (int, str)) and not isinstance(record_id, bool):
            value = str(record_id).strip()
            if value:
                return f"atbench-{value}", "derived"
    return None, "derived"


def inspect_source(record: Any) -> list[dict[str, Any]]:
    """ATBench 记录之间没有步骤引用，因此不存在来源完整性缺陷。"""
    return []


def expected_source_label_count(record: Any) -> int | None:
    """ATBench 每条记录只提供一条轨迹级 ``label``。"""
    return 1 if isinstance(record, dict) else None


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
    """把一条 ATBench 记录归一为一条或多条统一轨迹。"""
    issues = validate_record(record)
    if issues:
        raise ConversionError(
            "schema_invalid",
            "ATBench 记录不符合 schema",
            stage=STAGE_ADAPTER_VALIDATION,
            details={"issues": issues, "sample_id": sample_id},
            sample_id=sample_id,
        )

    base_sample_id, _ = sample_identity(record)
    base_sample_id = base_sample_id or sample_id
    conversations: list[list[dict[str, Any]]] = record["contents"]
    trajectories: list[NormalizedTrajectory] = []

    for conv_index, conversation in enumerate(conversations):
        current_sample_id = (
            base_sample_id
            if len(conversations) == 1
            else f"{base_sample_id}-conv{conv_index + 1}"
        )
        builder = TrajectoryBuilder(
            sample_id=current_sample_id,
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
            tool_definitions=record.get("tool_used"),
        )
        previous_calls: list[Event] = []

        for turn_index, turn in enumerate(conversation):
            turn_path = _join_path(
                record_json_path, f"contents[{conv_index}][{turn_index}]"
            )
            role = str(turn.get("role") or "").strip()
            actor = _actor(role)
            builder.add_step(
                source_step_id=None,
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
                            {"role": role, "text": text, "parts": [{"type": "text", "text": text}]},
                            json_path=f"{turn_path}.content",
                            raw=text,
                        )
                    )
                else:
                    turn_events.append(
                        _unsupported(builder, turn_path, turn, actor, "missing_content", None)
                    )
            elif role == "agent":
                turn_events.extend(_agent_events(builder, turn, turn_path))
                if not turn_events:
                    turn_events.append(
                        _unsupported(builder, turn_path, turn, actor, "missing_content", None)
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
                                LINK_STATUS_POSITIONAL
                                if call_event_id
                                else LINK_STATUS_NO_CALL_ID
                            ),
                            "link_basis": "preceding_agent_action",
                            "declared_status": parsed["declared_status"],
                            "declared_status_raw": parsed["declared_status_raw"],
                            "output": output,
                            "output_format": parsed["output_format"],
                        },
                        json_path=turn_path,
                        raw=turn,
                    )
                )
            else:
                turn_events.append(
                    _unsupported(builder, turn_path, turn, actor, "unsupported_role", role)
                )

            previous_calls = [event for event in turn_events if event.type == "tool_call"]

        label = record.get("label")
        builder.add_source_label(
            scope=SOURCE_LABEL_SCOPE_TRAJECTORY,
            source_step_id=None,
            verdict="unsafe" if label == 1 else "safe",
            categories=[],
            source_categories=[
                value
                for value in (
                    record.get("risk_source"),
                    record.get("failure_mode"),
                    record.get("real_world_harm"),
                )
                if isinstance(value, str) and value
            ],
            reason=record.get("reason") if isinstance(record.get("reason"), str) else None,
            mapping=atbench_mapping(),
            raw={key: record.get(key) for key in sorted(LABEL_FIELDS) if key in record},
        )
        trajectories.append(builder.finalize())

    return trajectories


def _agent_events(
    builder: TrajectoryBuilder, turn: dict[str, Any], turn_path: str
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
            )
        )
    elif thought is not None and not isinstance(thought, str):
        events.append(
            _unsupported(
                builder, f"{turn_path}.thought", thought, "assistant", "invalid_thought", "thought"
            )
        )

    action = turn.get("action")
    if isinstance(action, str) and action.strip():
        if action.startswith("Complete"):
            text, declaration = parse_complete_action(action)
            if text:
                events.append(
                    builder.emit(
                        "message",
                        "assistant",
                        {"role": "agent", "text": text, "parts": [{"type": "text", "text": text}]},
                        json_path=f"{turn_path}.action",
                        raw=action,
                    )
                )
            events.append(
                builder.emit(
                    "state_change",
                    "assistant",
                    {
                        "state": "declared_complete",
                        "basis": "source_action",
                        "declaration": declaration,
                    },
                    json_path=f"{turn_path}.action",
                    raw=action,
                )
            )
        elif action.lstrip().startswith("{"):
            tool_content = parse_tool_action(action)
            events.append(
                builder.emit(
                    "tool_call",
                    "assistant",
                    {"call_id": None, **tool_content},
                    json_path=f"{turn_path}.action",
                    raw=action,
                )
            )
        else:
            raise ConversionError(
                "unsupported_record",
                "ATBench agent action 既不是工具调用 JSON 也不是 Complete 声明",
                stage=STAGE_ADAPTER_NORMALIZATION,
                details={"action_prefix": action[:120], "sample_id": builder.sample_id},
                sample_id=builder.sample_id,
            )
    elif action is not None and not isinstance(action, str):
        events.append(
            _unsupported(builder, f"{turn_path}.action", action, "assistant", "invalid_action", "action")
        )

    content = turn.get("content")
    if isinstance(content, str) and content:
        events.append(
            builder.emit(
                "message",
                "assistant",
                {"role": "agent", "text": content, "parts": [{"type": "text", "text": content}]},
                json_path=f"{turn_path}.content",
                raw=content,
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
) -> Event:
    return builder.emit(
        "unsupported_content",
        actor,
        {"part_type": part_type, "reason": reason},
        json_path=turn_path,
        raw=raw,
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
