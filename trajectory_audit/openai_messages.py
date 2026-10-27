# SPDX-License-Identifier: Apache-2.0
"""OpenAI messages 适配器：输入校验与统一事件归一化。

适配器只描述可观察记录：消息、显式 thought 文本字段、工具调用、工具返回和
状态变化。它不声称获得模型内部思维链。未知或不支持的内容会生成
``unsupported_content`` 事件并保留原始片段，而不是被静默丢弃。
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from . import __version__
from .arguments import parse_arguments
from .model import (
    LINK_STATUS_LINKED,
    LINK_STATUS_NO_CALL_ID,
    LINK_STATUS_UNKNOWN_CALL_ID,
    Event,
    NormalizedTrajectory,
    sha256_json,
)
from .normalization import TrajectoryBuilder

ADAPTER_ID = "openai-messages"
ADAPTER_VERSION = __version__
SOURCE_FORMAT = "openai_messages"
DESCRIPTION = "OpenAI messages 轨迹：role/content/tool_calls/tool_call_id 结构"
SUPPORTED_TOP_LEVEL_FIELDS = {"sample_id", "messages"}
KNOWN_MESSAGE_FIELDS = {
    "role",
    "content",
    "tool_calls",
    "tool_call_id",
    "is_error",
    "reasoning_content",
    "name",
}


def validate_record(payload: Any) -> list[dict[str, str]]:
    """返回机器可读的校验问题列表；空列表表示通过。"""
    if not isinstance(payload, dict):
        return [{"path": "", "message": "输入必须是 JSON 对象"}]

    issues: list[dict[str, str]] = []

    sample_id = payload.get("sample_id")
    if sample_id is not None and (not isinstance(sample_id, str) or not sample_id.strip()):
        issues.append({"path": "sample_id", "message": "sample_id 必须是非空字符串"})

    messages = payload.get("messages")
    if not isinstance(messages, list):
        issues.append({"path": "messages", "message": "messages 必须是数组"})
        return issues
    if not messages:
        issues.append({"path": "messages", "message": "messages 不能为空"})

    for index, message in enumerate(messages):
        prefix = f"messages[{index}]"
        if not isinstance(message, dict):
            issues.append({"path": prefix, "message": "消息必须是对象"})
            continue

        role = message.get("role")
        if not isinstance(role, str) or not role.strip():
            issues.append({"path": f"{prefix}.role", "message": "role 必须是非空字符串"})

        content = message.get("content")
        if content is None:
            if role != "assistant":
                issues.append(
                    {"path": f"{prefix}.content", "message": "该角色的 content 不能为空"}
                )
        elif isinstance(content, str):
            pass
        elif isinstance(content, list):
            for part_index, part in enumerate(content):
                if not isinstance(part, dict):
                    issues.append(
                        {
                            "path": f"{prefix}.content[{part_index}]",
                            "message": "content 片段必须是对象",
                        }
                    )
        else:
            issues.append(
                {
                    "path": f"{prefix}.content",
                    "message": "content 必须是字符串、数组或 null",
                }
            )

        tool_calls = message.get("tool_calls")
        if tool_calls is None:
            continue
        if not isinstance(tool_calls, list):
            issues.append({"path": f"{prefix}.tool_calls", "message": "tool_calls 必须是数组"})
            continue
        for call_index, call in enumerate(tool_calls):
            call_path = f"{prefix}.tool_calls[{call_index}]"
            if not isinstance(call, dict):
                issues.append({"path": call_path, "message": "tool_calls 条目必须是对象"})
                continue
            if not isinstance(call.get("function"), dict):
                issues.append(
                    {"path": f"{call_path}.function", "message": "tool_calls 条目必须包含 function 对象"}
                )

    return issues


def sample_identity(payload: Any) -> tuple[str | None, str]:
    if isinstance(payload, dict):
        declared = payload.get("sample_id")
        if isinstance(declared, str) and declared.strip():
            return declared.strip(), "declared"
    return None, "derived"


def inspect_source(payload: Any) -> list[dict[str, Any]]:
    """OpenAI messages 没有跨消息身份引用，因此不存在来源完整性缺陷。"""
    return []


def expected_source_label_count(payload: Any) -> int | None:
    """OpenAI messages 输入不携带来源标签。"""
    return 0 if isinstance(payload, dict) else None


def normalize_record(
    payload: dict[str, Any],
    *,
    record_index: int,
    dataset_id: str,
    dataset_version: str,
    input_sha256: str,
    record_json_path: str,
    record_sha256: str,
    sample_id: str,
) -> list[NormalizedTrajectory]:
    """把一条 OpenAI messages 记录转换为统一轨迹事件。"""
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
        record_raw=payload,
        unmapped_top_level_fields=sorted(set(payload) - SUPPORTED_TOP_LEVEL_FIELDS),
    )
    messages: list[dict[str, Any]] = payload["messages"]

    def emit_unsupported(
        *,
        actor: str,
        message_role: str,
        part_type: Any,
        reason: str,
        json_path: str,
        raw: Any,
    ) -> None:
        builder.emit(
            "unsupported_content",
            actor,
            {
                "part_type": part_type,
                "reason": reason,
                "message_role": message_role,
            },
            json_path=json_path,
            raw=raw,
        )

    for index, message in enumerate(messages):
        base_path = f"{record_json_path}.messages[{index}]" if record_json_path else f"messages[{index}]"
        role = str(message.get("role") or "").strip()
        actor = role or "unknown"

        reasoning = message.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning.strip():
            builder.emit(
                "thought",
                "assistant",
                {"text": reasoning},
                json_path=f"{base_path}.reasoning_content",
                raw=reasoning,
            )
        elif reasoning is not None and not isinstance(reasoning, str):
            emit_unsupported(
                actor=actor,
                message_role=role,
                part_type="reasoning_content",
                reason="invalid_reasoning_content",
                json_path=f"{base_path}.reasoning_content",
                raw=reasoning,
            )

        content = message.get("content")
        texts: list[tuple[str, str]] = []
        has_content_event = False
        if isinstance(content, str):
            if content:
                texts.append((content, base_path))
                has_content_event = True
        elif isinstance(content, list):
            for part_index, part in enumerate(content):
                part_path = f"{base_path}.content[{part_index}]"
                if (
                    isinstance(part, dict)
                    and part.get("type") == "text"
                    and isinstance(part.get("text"), str)
                ):
                    if part["text"]:
                        texts.append((part["text"], part_path))
                        has_content_event = True
                    continue
                part_type = part.get("type") if isinstance(part, dict) else None
                emit_unsupported(
                    actor=actor,
                    message_role=role,
                    part_type=part_type if isinstance(part_type, str) else "unknown",
                    reason="unsupported_content_part",
                    json_path=part_path,
                    raw=part,
                )
                has_content_event = True

        tool_calls = message.get("tool_calls") or []

        if role == "tool":
            call_id = message.get("tool_call_id")
            call_id = call_id if isinstance(call_id, str) and call_id else None
            is_error = message.get("is_error")
            declared_status = (
                "error" if is_error is True else "success" if is_error is False else "unknown"
            )
            output_format = (
                "text" if isinstance(content, str) else "parts" if isinstance(content, list) else "other"
            )
            builder.emit(
                "tool_result",
                "tool",
                {
                    "call_id": call_id,
                    "call_event_id": None,
                    "link_status": (
                        LINK_STATUS_NO_CALL_ID if call_id is None else LINK_STATUS_UNKNOWN_CALL_ID
                    ),
                    "declared_status": declared_status,
                    "output": content,
                    "output_format": output_format,
                },
                json_path=base_path,
                raw=message,
            )
        elif texts:
            builder.emit(
                "message",
                actor,
                {
                    "role": role,
                    "text": "\n".join(text for text, _ in texts),
                    "parts": [{"type": "text", "text": text} for text, _ in texts],
                    "part_paths": [path for _, path in texts],
                },
                json_path=base_path,
                raw=message,
            )

        for call_index, call in enumerate(tool_calls):
            call_path = f"{base_path}.tool_calls[{call_index}]"
            function = call.get("function") if isinstance(call, dict) else None
            function = function if isinstance(function, dict) else {}
            name = function.get("name") if isinstance(function.get("name"), str) else ""
            raw_arguments = function.get("arguments")
            parsed_arguments, arguments_status, arguments_error = parse_arguments(raw_arguments)
            call_id = call.get("id") if isinstance(call, dict) else None
            builder.emit(
                "tool_call",
                "assistant",
                {
                    "call_id": call_id if isinstance(call_id, str) and call_id else None,
                    "tool": name,
                    "arguments_raw": raw_arguments,
                    "arguments": parsed_arguments,
                    "arguments_status": arguments_status,
                    "arguments_error": arguments_error,
                },
                json_path=call_path,
                raw=call,
            )

        has_reasoning = isinstance(reasoning, str) and bool(reasoning.strip())
        if role != "tool" and not has_content_event and not tool_calls and not has_reasoning:
            emit_unsupported(
                actor=actor,
                message_role=role,
                part_type=None,
                reason="missing_content",
                json_path=base_path,
                raw=message,
            )

        for field_name in sorted(set(message) - KNOWN_MESSAGE_FIELDS):
            field_value = message[field_name]
            if field_value is None:
                continue
            emit_unsupported(
                actor=actor,
                message_role=role,
                part_type=field_name,
                reason="unmapped_message_field",
                json_path=f"{base_path}.{field_name}",
                raw=field_value,
            )

    builder.events = _resolve_tool_result_links(builder.events)
    return [builder.finalize()]


def normalize_openai_messages(
    payload: dict[str, Any],
    *,
    sample_id: str,
    input_sha256: str,
    dataset_id: str = ADAPTER_ID,
    dataset_version: str = ADAPTER_VERSION,
) -> NormalizedTrajectory:
    """兼容入口：归一化一条 OpenAI messages 轨迹。"""
    return normalize_record(
        payload,
        record_index=0,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        input_sha256=input_sha256,
        record_json_path="$",
        record_sha256=sha256_json(payload),
        sample_id=sample_id,
    )[0]


def _resolve_tool_result_links(events: list[Event]) -> list[Event]:
    """把工具返回关联到位于其之前的最近工具调用。

    同一 call id 重复出现时，只有结果之前最近的一次调用有效；结果出现在调用
    之前、或找不到对应调用时保留 ``unknown_call_id``，不猜测关联。
    """
    calls: dict[str, list[Event]] = {}
    for event in events:
        if event.type != "tool_call":
            continue
        call_id = event.content.get("call_id")
        if isinstance(call_id, str) and call_id:
            calls.setdefault(call_id, []).append(event)

    resolved: list[Event] = []
    for event in events:
        if event.type != "tool_result":
            resolved.append(event)
            continue
        call_id = event.content.get("call_id")
        call_event_id: str | None = None
        if isinstance(call_id, str) and call_id:
            preceding = [
                call for call in calls.get(call_id, []) if call.sequence < event.sequence
            ]
            if preceding:
                call_event_id = preceding[-1].event_id
        link_status = (
            LINK_STATUS_LINKED
            if call_event_id
            else LINK_STATUS_NO_CALL_ID
            if not call_id
            else LINK_STATUS_UNKNOWN_CALL_ID
        )
        resolved.append(
            replace(
                event,
                content={
                    **event.content,
                    "call_event_id": call_event_id,
                    "link_status": link_status,
                },
            )
        )
    return resolved


__all__ = [
    "ADAPTER_ID",
    "ADAPTER_VERSION",
    "DESCRIPTION",
    "SOURCE_FORMAT",
    "expected_source_label_count",
    "inspect_source",
    "normalize_openai_messages",
    "normalize_record",
    "sample_identity",
    "validate_record",
]
