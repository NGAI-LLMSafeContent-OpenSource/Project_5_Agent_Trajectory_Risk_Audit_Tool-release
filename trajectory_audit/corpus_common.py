# SPDX-License-Identifier: Apache-2.0
"""Provider corpus 子集共享的产物、身份与校验工具。

Development Corpus 与 Blind Acceptance Batch 必须使用同一套白名单投影、同一套
不透明样本身份规则和同一套摘要算法，否则两个子集会在结构上漂移。这里收纳的
就是这些共同部分：

- 正式输入的固定文件名与禁止字段集合；
- 由记录身份派生的不透明 provider 样本身份；
- 统一轨迹归一化与来源标签摘要；
- 产物的规范化字节、逐文件摘要与整版内容摘要。

模块本身不决定选样策略，也不读写冻结目录；那些属于各子集的交付模块。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from .artifacts import write_json
from .audit_input import AUDIT_INPUT_SCHEMA, AUDIT_TRAJECTORY_SCHEMA
from .errors import ConversionError, RunError
from .model import NormalizedTrajectory, canonical_json, sha256_json
from .source_labels import map_hintbench_category

INTEGRITY_VERSION = "canonical-json-sha256/1"

INPUT_FILENAME = "input-batch.json"
MANIFEST_FILENAME = "manifest.json"
COVERAGE_FILENAME = "coverage.json"
REFERENCE_FILENAME = "self-check-reference.json"
README_FILENAME = "README.md"

#: 正式 provider 输入里绝对不能出现的键。它们要么是来源侧事实（标签、原始
#: 记录、定位），要么是课题五的内部结论（detected、reference）。
FORBIDDEN_INPUT_KEYS = frozenset(
    {
        "dataset_id",
        "dataset_version",
        "detected",
        "findings",
        "is_risky",
        "label",
        "raw",
        "record",
        "record_raw",
        "reference",
        "risk_labels",
        "risk_source",
        "failure_mode",
        "source",
        "source_labels",
        "source_steps",
        "unmapped_top_level_fields",
    }
)

REFERENCE_NOTICE = (
    "本文件只保留来源标签与来源类别，用于运行后的自测比对；它不是项目真值，"
    "也不得作为 provider 推理输入。Source Label 与项目人工确认的参考标注是两层"
    "不同的事实。"
)

#: provider 可见样本身份的唯一合法形状：不透明、不可反推来源标签或数据集身份。
PROVIDER_SAMPLE_ID_PATTERN = re.compile(r"sample-[0-9a-f]{16}")


def provider_sample_id(dataset_id: str, record_index: int, record_sha256: str) -> str:
    """由记录身份派生不透明的 provider 样本身份。

    来源适配器的 ``sample_id`` 携带 benchmark 与标签线索（例如
    ``atbench-17`` 或 ``..._task_0005_risk_v4``），直接交给 provider 等于泄漏
    数据集身份与来源标签。corpus 因此只暴露这个稳定摘要派生身份，映射关系留在
    manifest 的来源侧字段里。
    """
    material = f"{dataset_id}:{record_index}:{record_sha256}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"sample-{digest[:16]}"


def is_provider_sample_id(value: Any) -> bool:
    return isinstance(value, str) and bool(PROVIDER_SAMPLE_ID_PATTERN.fullmatch(value))


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def payload_bytes(payload: Any) -> bytes:
    return payload.encode("utf-8") if isinstance(payload, str) else json_bytes(payload)


def artifact_digests(artifacts: Mapping[str, Any]) -> list[dict[str, Any]]:
    digests: list[dict[str, Any]] = []
    for name in sorted(artifacts):
        raw = payload_bytes(artifacts[name])
        digests.append(
            {
                "path": name,
                "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
    return digests


def content_sha256(manifest: Mapping[str, Any]) -> str:
    """只对交付内容取哈希：同一选择重复构建得到同一指纹。"""
    projection = {
        key: value
        for key, value in manifest.items()
        if key not in {"created_at", "integrity"}
    }
    return hashlib.sha256(canonical_json(projection).encode("utf-8")).hexdigest()


def existing_matches(out_dir: Path, payloads: Mapping[str, Any]) -> bool:
    for name, payload in payloads.items():
        path = out_dir / name
        if not path.is_file():
            return False
        if path.read_bytes() != payload_bytes(payload):
            return False
    return True


def write_payloads(out_dir: Path, payloads: Mapping[str, Any], *, manifest_name: str) -> None:
    """先写产物再写清单：清单最后落盘，作为目录冻结的完成标记。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in sorted(payloads):
        if name == manifest_name:
            continue
        payload = payloads[name]
        target = out_dir / name
        if isinstance(payload, str):
            target.write_text(payload, encoding="utf-8", newline="\n")
        else:
            write_json(target, payload)
    write_json(out_dir / manifest_name, payloads[manifest_name])


def normalize_record(
    adapter: Any,
    record: Any,
    *,
    record_index: int,
    dataset_id: str,
    dataset_version: str,
    file_sha256: str,
    record_sha256: str,
    sample_id: str,
) -> NormalizedTrajectory:
    """把一条来源记录归一化为统一轨迹；必须恰好一条。"""
    try:
        trajectories = adapter.normalize_record(
            record,
            record_index=record_index,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            input_sha256=file_sha256,
            record_json_path="",
            record_sha256=record_sha256,
            sample_id=sample_id,
        )
    except ConversionError as exc:
        raise RunError(
            "corpus_conversion_failed",
            "选中的来源记录无法归一化为统一轨迹",
            details={
                "dataset_id": dataset_id,
                "record_index": record_index,
                "sample_id": exc.sample_id or sample_id,
                "stage": exc.stage,
                "code": exc.code,
                "reason": exc.message,
            },
        ) from None
    if len(trajectories) != 1:
        raise RunError(
            "corpus_record_not_single_sample",
            "corpus 以记录为单位计数，适配器却拆出了多条轨迹",
            details={
                "dataset_id": dataset_id,
                "record_index": record_index,
                "sample_count": len(trajectories),
            },
        )
    return trajectories[0]


def label_categories(record: Any) -> list[str]:
    """按来源声明顺序返回去重后的约束类别。"""
    labels = record.get("risk_labels") if isinstance(record, dict) else None
    if not isinstance(labels, list):
        return []
    found: list[str] = []
    for label in labels:
        if not isinstance(label, dict):
            continue
        category = label.get("category") or label.get("big_category")
        if isinstance(category, str) and category.strip() and category.strip() not in found:
            found.append(category.strip())
    return found


def source_label(record: Any, dataset_id: str, stratum: str) -> dict[str, Any]:
    """只记录来源侧事实，供自测比对；不包含任何 detected 结果。"""
    label: dict[str, Any] = {
        "kind": "source_label",
        "dataset_id": dataset_id,
        "risk_layer": stratum,
    }
    if not isinstance(record, dict):
        return label
    for key in ("id", "task_id", "label", "is_risky", "risk_source", "failure_mode", "env"):
        if key in record:
            label[key] = record[key]
    categories = label_categories(record)
    if categories:
        label["categories"] = categories
        label["mapped_categories"] = sorted(
            {
                mapped
                for category in categories
                for mapped in map_hintbench_category(category)[0]
            }
        )
    return label


def identities(manifest: Mapping[str, Any]) -> tuple[set[str], set[tuple[str, int]]]:
    """清单声明的 (样本身份集合, 记录身份集合)。"""
    samples = manifest.get("samples") or []
    sample_ids = {
        sample["sample_id"]
        for sample in samples
        if isinstance(sample, dict) and isinstance(sample.get("sample_id"), str)
    }
    records = {
        (sample["dataset_id"], sample["record_index"])
        for sample in samples
        if isinstance(sample, dict)
        and isinstance(sample.get("dataset_id"), str)
        and isinstance(sample.get("record_index"), int)
    }
    return sample_ids, records


def structural_keys(batch: Any) -> Iterable[str]:
    """只遍历 Exchange Envelope 与 Canonical Audit Item 的结构层。

    事件 content、工具定义 parameters 与 policy 的约束体是来源载荷，键名由来源
    决定（例如某个工具参数就叫 ``findings``），不是泄漏信号。泄漏只可能发生在
    结构层：批次、条目、轨迹、coverage、事件、工具定义与 policy 外壳。
    """
    if not isinstance(batch, dict):
        return
    yield from batch
    for item in batch.get("items") or []:
        if not isinstance(item, dict):
            continue
        yield from item
        trajectory = item.get("trajectory")
        if isinstance(trajectory, dict):
            yield from trajectory
            coverage = trajectory.get("coverage")
            if isinstance(coverage, dict):
                yield from coverage
            for event in trajectory.get("events") or []:
                if isinstance(event, dict):
                    yield from event
            for definition in trajectory.get("tool_definitions") or []:
                if not isinstance(definition, dict):
                    continue
                yield from definition
                function = definition.get("function")
                if isinstance(function, dict):
                    yield from function
        policy = item.get("policy")
        if isinstance(policy, dict):
            yield from policy


def forbidden_field_hits(batch: Any) -> list[str]:
    """结构层禁止字段扫描；事件 content 与工具 parameters 不在扫描范围。"""
    return sorted(set(structural_keys(batch)) & FORBIDDEN_INPUT_KEYS)


def schema_versions() -> dict[str, str]:
    return {
        "input_schema_version": AUDIT_INPUT_SCHEMA,
        "trajectory_schema_version": AUDIT_TRAJECTORY_SCHEMA,
    }


__all__ = [
    "COVERAGE_FILENAME",
    "FORBIDDEN_INPUT_KEYS",
    "INPUT_FILENAME",
    "INTEGRITY_VERSION",
    "MANIFEST_FILENAME",
    "PROVIDER_SAMPLE_ID_PATTERN",
    "README_FILENAME",
    "REFERENCE_FILENAME",
    "REFERENCE_NOTICE",
    "artifact_digests",
    "content_sha256",
    "existing_matches",
    "forbidden_field_hits",
    "identities",
    "is_provider_sample_id",
    "json_bytes",
    "label_categories",
    "normalize_record",
    "payload_bytes",
    "provider_sample_id",
    "schema_versions",
    "source_label",
    "structural_keys",
    "write_payloads",
]
