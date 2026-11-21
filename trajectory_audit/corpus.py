# SPDX-License-Identifier: Apache-2.0
"""Provider Corpus 构建入口：来源文件 → 白名单投影 → ``topic4-audit-input/1``。

本模块是课题四输入的最高层构建 seam。调用方提供一个单条来源记录或
``source-batch/1`` 批次文件，以及可选 policy；命令负责归一化、白名单投影、
摘要与 Schema 校验。它不读取 Detected Finding，也不把来源标签写入 provider
输入。

真正的 Development Corpus 与 Blind Acceptance Batch 抽样策略属于后续 corpus
版本；本入口只保证任意来源 fixture 都能生成结构正确、无标签泄漏的输入。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .adapters import (
    OPENAI_MESSAGES_ADAPTER,
    SOURCE_BATCH_SCHEMA_VERSION,
    get_adapter,
    validate_batch_input,
)
from .artifacts import read_input, write_json
from .audit_input import (
    AUDIT_INPUT_SCHEMA,
    build_batch,
    canonical_item,
    validate_audit_input,
)
from .errors import ConversionError, RunError
from .model import NormalizedTrajectory, sha256_json
from .policy import Policy

FIXTURE_DATASET_ID = "fixture-source"
FIXTURE_DATASET_VERSION = "fixture/v1"


def build_corpus_batch(
    *,
    source_path: Path,
    adapter_id: str = OPENAI_MESSAGES_ADAPTER,
    policy: Policy | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """从一个来源文件构造 ``topic4-audit-input/1`` 批次。

    返回 ``(payload, stats)``；任何一条记录无法归一化都会以
    ``corpus_source_invalid`` 失败，并列出全部已知问题，而不是静默跳过。
    """
    raw_bytes, payload = read_input(source_path)
    adapter = get_adapter(adapter_id)
    records, resolved_id, resolved_version = _source_records(
        payload,
        adapter_id=adapter_id,
    )

    file_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    items: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    seen_samples: set[str] = set()
    trajectories: list[NormalizedTrajectory] = []

    for record_index, record in enumerate(records):
        defects = adapter.inspect_source(record)
        if defects:
            problems.append(
                {
                    "record_index": record_index,
                    "stage": defects[0]["stage"],
                    "code": defects[0]["code"],
                    "reason": defects[0]["reason"],
                    "details": defects[0].get("details"),
                }
            )
            continue
        issues = adapter.validate_record(record)
        if issues:
            problems.append(
                {
                    "record_index": record_index,
                    "stage": "adapter_validation",
                    "code": "schema_invalid",
                    "reason": "记录不符合来源 Schema",
                    "details": {"issues": issues},
                }
            )
            continue

        fallback_id, _ = adapter.sample_identity(record)
        sample_id = fallback_id or f"record-{record_index:04d}"
        record_sha256 = sha256_json(record)
        try:
            normalized = adapter.normalize_record(
                record,
                record_index=record_index,
                dataset_id=resolved_id,
                dataset_version=resolved_version,
                input_sha256=file_sha256,
                record_json_path="",
                record_sha256=record_sha256,
                sample_id=sample_id,
            )
        except ConversionError as exc:
            problems.append(
                {
                    "record_index": record_index,
                    "sample_id": exc.sample_id or sample_id,
                    "stage": exc.stage,
                    "code": exc.code,
                    "reason": exc.message,
                    "details": exc.details,
                }
            )
            continue
        if not normalized:
            problems.append(
                {
                    "record_index": record_index,
                    "sample_id": sample_id,
                    "stage": "adapter_normalization",
                    "code": "unsupported_record",
                    "reason": "adapter 没有从记录中产生任何轨迹",
                    "details": None,
                }
            )
            continue

        for trajectory in normalized:
            if trajectory.sample_id in seen_samples:
                problems.append(
                    {
                        "record_index": record_index,
                        "sample_id": trajectory.sample_id,
                        "stage": "sample_identity",
                        "code": "duplicate_sample_id",
                        "reason": "同一批次内样本身份重复，拒绝静默覆盖",
                        "details": {"sample_id": trajectory.sample_id},
                    }
                )
                continue
            seen_samples.add(trajectory.sample_id)
            trajectories.append(trajectory)
            items.append(canonical_item(trajectory, _bound_policy(policy, trajectory)))

    if problems:
        raise RunError(
            "corpus_source_invalid",
            "来源记录无法全部投影为 Canonical Audit Item",
            details={"path": str(source_path), "problems": problems},
        )

    batch = build_batch(items)
    issues = validate_audit_input(batch)
    if issues:
        raise RunError(
            "corpus_invalid",
            "生成的审计输入未通过 topic4-audit-input/1 校验",
            details={"path": str(source_path), "issues": issues},
        )

    stats = {
        "records": len(records),
        "trajectories": len(trajectories),
        "policy_items": sum(1 for item in batch["items"] if item["policy"] is not None),
        "null_policy_items": sum(
            1 for item in batch["items"] if item["policy"] is None
        ),
    }
    return batch, stats


def write_corpus_batch(
    *,
    source_path: Path,
    out_path: Path,
    adapter_id: str = OPENAI_MESSAGES_ADAPTER,
    policy: Policy | None = None,
) -> dict[str, Any]:
    """构建并写出一个审计输入批次，返回可打印的摘要。"""
    batch, stats = build_corpus_batch(
        source_path=source_path,
        adapter_id=adapter_id,
        policy=policy,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(out_path, batch)
    return {
        "status": "written",
        "schema_version": AUDIT_INPUT_SCHEMA,
        "batch_id": batch["batch_id"],
        "path": str(out_path),
        "items": len(batch["items"]),
        **stats,
    }


def validate_corpus_file(path: Path) -> dict[str, Any]:
    """读取并校验一个审计输入文件，返回机器可读结论。"""
    _, payload = read_input(path)
    issues = validate_audit_input(payload)
    envelope = payload if isinstance(payload, dict) else {}
    items = envelope.get("items")
    return {
        "status": "pass" if not issues else "fail",
        "schema_version": envelope.get("schema_version"),
        "batch_id": envelope.get("batch_id"),
        "items": len(items) if isinstance(items, list) else 0,
        "issues": issues,
    }


def _source_records(
    payload: Any,
    *,
    adapter_id: str,
) -> tuple[list[Any], str, str]:
    """返回来源记录及其内部使用的数据集身份；身份不会进入投影。"""
    if isinstance(payload, dict) and payload.get("schema_version") == SOURCE_BATCH_SCHEMA_VERSION:
        issues = validate_batch_input(payload, adapter_id)
        if issues:
            raise RunError(
                "input_invalid",
                "来源批次不符合 source-batch/1 契约",
                details={"adapter": adapter_id, "issues": issues},
            )
        source = payload["source"]
        return (
            payload["records"],
            source["dataset_id"],
            source["dataset_version"],
        )
    return [payload], FIXTURE_DATASET_ID, FIXTURE_DATASET_VERSION


def _bound_policy(policy: Policy | None, trajectory: NormalizedTrajectory) -> Any:
    if policy is None:
        return None
    if policy.sample_ids and trajectory.sample_id not in policy.sample_ids:
        return None
    return policy


__all__ = [
    "FIXTURE_DATASET_ID",
    "FIXTURE_DATASET_VERSION",
    "build_corpus_batch",
    "validate_corpus_file",
    "write_corpus_batch",
]
