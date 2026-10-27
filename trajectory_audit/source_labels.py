# SPDX-License-Identifier: Apache-2.0
"""来源标签到统一风险类别的保守映射。

映射只作用于来源标签事实，不生成 ``detected`` 结果。无法可靠映射时保留原始
类别并标记映射状态，不把来源标签冒充为自动检测发现。
"""

from __future__ import annotations

from typing import Any


MAPPING_VERSION = "source-label-map/0.1.0"

UNIFIED_CATEGORIES = (
    "authorization_scope",
    "tool_error",
    "inefficiency",
    "reasoning_process_risk",
    "other",
)

# HINTBench 的五类约束名称到第一层风险类别的保守映射。
HINTBENCH_CATEGORY_MAP: dict[str, str] = {
    "Goal Constraint": "authorization_scope",
    "Factual Constraint": "reasoning_process_risk",
    "Capability Constraint": "tool_error",
    "Procedural Constraint": "reasoning_process_risk",
    "State Constraint": "other",
}


def map_hintbench_category(value: Any) -> tuple[list[str], dict[str, Any]]:
    if not isinstance(value, str) or not value.strip():
        return [], {
            "version": MAPPING_VERSION,
            "status": "unmapped",
            "basis": "来源未提供类别字符串",
        }
    category = value.strip()
    mapped = HINTBENCH_CATEGORY_MAP.get(category)
    if mapped is None:
        return ["other"], {
            "version": MAPPING_VERSION,
            "status": "unmapped_other",
            "basis": f"来源类别 {category!r} 没有已确认的第一层映射",
        }
    return [mapped], {
        "version": MAPPING_VERSION,
        "status": "mapped",
        "basis": f"{category!r} -> {mapped}",
    }


def atbench_mapping() -> dict[str, Any]:
    """ATBench 原生标签没有可对齐的第一层类别，保持未映射。"""
    return {
        "version": MAPPING_VERSION,
        "status": "not_applicable",
        "basis": "ATBench 提供轨迹级 label / risk_source / failure_mode，没有第一层风险类别映射",
    }
