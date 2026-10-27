# SPDX-License-Identifier: Apache-2.0
"""工具调用参数的保守解析。"""

from __future__ import annotations

import json
from typing import Any

from .model import (
    ARGUMENTS_STATUS_INVALID_JSON,
    ARGUMENTS_STATUS_MISSING,
    ARGUMENTS_STATUS_NOT_OBJECT,
    ARGUMENTS_STATUS_OBJECT,
)


def parse_arguments(raw: Any) -> tuple[Any, str, str | None]:
    """返回 (解析值, 参数状态, 解析错误)。

    只有 JSON 对象视为可解析参数；其他类型保留原值并标记状态。
    """
    if raw is None:
        return None, ARGUMENTS_STATUS_MISSING, None
    if isinstance(raw, dict):
        return raw, ARGUMENTS_STATUS_OBJECT, None
    if isinstance(raw, str):
        if not raw.strip():
            return None, ARGUMENTS_STATUS_MISSING, None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            return (
                None,
                ARGUMENTS_STATUS_INVALID_JSON,
                f"{exc.msg} (line {exc.lineno}, column {exc.colno})",
            )
        if not isinstance(parsed, dict):
            return (
                parsed,
                ARGUMENTS_STATUS_NOT_OBJECT,
                f"参数 JSON 类型为 {type(parsed).__name__}，需要对象",
            )
        return parsed, ARGUMENTS_STATUS_OBJECT, None
    return (
        None,
        ARGUMENTS_STATUS_NOT_OBJECT,
        f"参数类型为 {type(raw).__name__}，需要 JSON 对象",
    )
