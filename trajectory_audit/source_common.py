# SPDX-License-Identifier: Apache-2.0
"""来源 adapter 共用的保守解析：工具 action 与环境返回。"""

from __future__ import annotations

import json
from typing import Any

from .arguments import parse_arguments
from .errors import STAGE_ADAPTER_NORMALIZATION, ConversionError


def parse_tool_action(action: str) -> dict[str, Any]:
    """解析 ``{"name": ..., "arguments": ...}`` 形式的工具调用。"""
    try:
        payload = json.loads(action)
    except json.JSONDecodeError as exc:
        raise ConversionError(
            "unsupported_record",
            "agent action 不是可解析的工具调用 JSON，未支持该记录形态",
            stage=STAGE_ADAPTER_NORMALIZATION,
            details={
                "action_prefix": action[:120],
                "error": exc.msg,
                "line": exc.lineno,
                "column": exc.colno,
            },
        ) from None
    if not isinstance(payload, dict):
        raise ConversionError(
            "unsupported_record",
            "agent action 的 JSON 顶层不是对象，未支持该记录形态",
            stage=STAGE_ADAPTER_NORMALIZATION,
            details={"action_prefix": action[:120], "type": type(payload).__name__},
        )

    name = payload.get("name")
    raw_arguments = payload.get("arguments")
    parsed_arguments, arguments_status, arguments_error = parse_arguments(raw_arguments)
    return {
        "tool": name if isinstance(name, str) else "",
        "arguments_raw": raw_arguments,
        "arguments": parsed_arguments,
        "arguments_status": arguments_status,
        "arguments_error": arguments_error,
    }


def parse_complete_action(action: str) -> tuple[str, dict[str, Any]]:
    """解析 ATBench ``Complete{...}`` response 包装，不推断任务状态。"""
    remainder = action[len("Complete") :].strip()
    try:
        payload = json.loads(remainder)
    except json.JSONDecodeError as exc:
        raise ConversionError(
            "unsupported_record",
            "Complete action 不是可解析的 JSON，未支持该记录形态",
            stage=STAGE_ADAPTER_NORMALIZATION,
            details={
                "action_prefix": action[:120],
                "error": exc.msg,
                "line": exc.lineno,
                "column": exc.colno,
            },
        ) from None
    if not isinstance(payload, dict):
        raise ConversionError(
            "unsupported_record",
            "Complete action 的 JSON 顶层不是对象，未支持该记录形态",
            stage=STAGE_ADAPTER_NORMALIZATION,
            details={"action_prefix": action[:120], "type": type(payload).__name__},
        )
    response = payload.get("response")
    if isinstance(response, str):
        text = response
    elif response is None:
        text = ""
    else:
        text = json.dumps(response, ensure_ascii=False)
    return text, payload


def classify_declared_status(value: Any) -> str:
    if not isinstance(value, str):
        return "unknown"
    normalized = value.strip().lower()
    if normalized in {"success", "ok", "succeeded", "completed"}:
        return "success"
    if normalized in {
        "error",
        "failure",
        "failed",
        "timeout",
        "timed_out",
        "denied",
        "rejected",
        "partial_success",
    }:
        return "error"
    return "unknown"


def parse_environment_output(output: Any) -> dict[str, Any]:
    """从环境返回文本中提取声明的状态，同时保留原始输出。"""
    if isinstance(output, str):
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError:
            return {
                "declared_status": "unknown",
                "declared_status_raw": None,
                "output_format": "text",
            }
        if isinstance(parsed, dict):
            raw_status = parsed.get("status")
            return {
                "declared_status": classify_declared_status(raw_status),
                "declared_status_raw": raw_status if isinstance(raw_status, str) else None,
                "output_format": "json_text",
            }
        return {
            "declared_status": "unknown",
            "declared_status_raw": None,
            "output_format": "json_text",
        }
    return {
        "declared_status": "unknown",
        "declared_status_raw": None,
        "output_format": "other",
    }
