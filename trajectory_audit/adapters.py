# SPDX-License-Identifier: Apache-2.0
"""来源 adapter 注册表与批量输入契约。

数据集差异只存在于 adapter 模块中。runner、batch、metrics、page 与审计
provider 只消费统一轨迹，不读取 ATBench / HINTBench 等数据集名称。

每个 adapter 模块都必须暴露同一套接口，下游与 H1 验证只依赖这套接口：

- ``ADAPTER_ID`` / ``ADAPTER_VERSION`` / ``SOURCE_FORMAT`` / ``DESCRIPTION``
- ``validate_record(record)``：记录级 Schema 校验，返回问题列表
- ``inspect_source(record)``：来源完整性检查（悬空步骤引用等）
- ``sample_identity(record)``：稳定样本身份与来源
- ``expected_source_label_count(record)``：应收到的来源标签条数
- ``normalize_record(...)``：归一为一条或多条统一轨迹
"""

from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import Any

from . import atbench, hintbench, openai_messages
from .errors import RunError

SOURCE_BATCH_SCHEMA_VERSION = "source-batch/1"
OPENAI_MESSAGES_ADAPTER = openai_messages.ADAPTER_ID

ADAPTERS: dict[str, ModuleType] = {
    module.ADAPTER_ID: module for module in (openai_messages, atbench, hintbench)
}

ADAPTER_METHODS = (
    "validate_record",
    "inspect_source",
    "sample_identity",
    "expected_source_label_count",
    "normalize_record",
)


@dataclass(frozen=True)
class DatasetRef:
    dataset_id: str
    dataset_version: str


@dataclass(frozen=True)
class AdapterSpec:
    id: str
    source_format: str
    version: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "source_format": self.source_format,
            "version": self.version,
            "description": self.description,
        }


def get_adapter(adapter_id: str) -> ModuleType:
    adapter = ADAPTERS.get(adapter_id)
    if adapter is None:
        raise RunError(
            "unknown_adapter",
            f"未知来源 adapter：{adapter_id}",
            details={"requested": adapter_id, "available": sorted(ADAPTERS)},
        )
    return adapter


def adapter_spec(adapter_id: str) -> AdapterSpec:
    adapter = get_adapter(adapter_id)
    return AdapterSpec(
        id=adapter.ADAPTER_ID,
        source_format=adapter.SOURCE_FORMAT,
        version=adapter.ADAPTER_VERSION,
        description=adapter.DESCRIPTION,
    )


def validate_batch_input(payload: Any, adapter_id: str) -> list[dict[str, str]]:
    """返回批次输入契约问题；记录级问题由 adapter 逐条报告。"""
    if not isinstance(payload, dict):
        return [{"path": "", "message": "批次输入必须是 JSON 对象"}]

    issues: list[dict[str, str]] = []
    schema_version = payload.get("schema_version")
    if schema_version != SOURCE_BATCH_SCHEMA_VERSION:
        issues.append(
            {
                "path": "schema_version",
                "message": f"schema_version 必须是 {SOURCE_BATCH_SCHEMA_VERSION!r}",
            }
        )

    source = payload.get("source")
    if not isinstance(source, dict):
        issues.append({"path": "source", "message": "source 必须是对象"})
        source = {}

    for field_name in ("dataset_id", "dataset_version"):
        value = source.get(field_name)
        if not isinstance(value, str) or not value.strip():
            issues.append(
                {
                    "path": f"source.{field_name}",
                    "message": f"{field_name} 必须是非空字符串",
                }
            )

    declared_adapter = source.get("adapter_id")
    if declared_adapter is not None and declared_adapter != adapter_id:
        issues.append(
            {
                "path": "source.adapter_id",
                "message": f"source.adapter_id={declared_adapter!r} 与 --adapter={adapter_id!r} 不一致",
            }
        )

    records = payload.get("records")
    if not isinstance(records, list):
        issues.append({"path": "records", "message": "records 必须是数组"})
    elif not records:
        issues.append({"path": "records", "message": "records 不能为空"})

    return issues


def batch_records(payload: dict[str, Any]) -> list[Any]:
    return payload["records"]


def batch_source(payload: dict[str, Any]) -> dict[str, Any]:
    return payload["source"]


__all__ = [
    "ADAPTERS",
    "ADAPTER_METHODS",
    "BATCH_CONFIG_VERSION",
    "SOURCE_BATCH_SCHEMA_VERSION",
    "OPENAI_MESSAGES_ADAPTER",
    "AdapterSpec",
    "DatasetRef",
    "adapter_spec",
    "batch_records",
    "batch_source",
    "get_adapter",
    "validate_batch_input",
]
