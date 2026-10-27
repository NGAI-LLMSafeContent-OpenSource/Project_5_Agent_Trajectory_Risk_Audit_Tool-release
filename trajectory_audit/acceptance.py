# SPDX-License-Identifier: Apache-2.0
"""`acceptance-v1` 冻结清单与 H1 数据契约验证。

本模块是**来源侧验证工具**，不是下游运行路径。它允许知道哪些数据集被固定、
如何按来源标签分层，因为冻结清单本身就是来源事实；但它绝不允许这层知识渗漏
到审计、指标和页面中——H1 报告会用静态扫描和真实调用两种方式证明这一点。

分层与抽样只读取**来源标签**，从不读取 `detected` 结果，因此后续规则实现无法
反向影响样本选择。
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import __version__, pinned
from .adapters import ADAPTERS, ADAPTER_METHODS, get_adapter
from .artifacts import now, read_json, write_json
from .errors import (
    OUTCOME_CONVERSION_ERROR,
    OUTCOME_SCHEMA_INVALID,
    OUTCOME_SOURCE_INVALID,
    OUTCOME_VALID,
    ConversionError,
    RunError,
)
from .model import AuditResult, NormalizedTrajectory, canonical_json, sha256_json
from .providers import get_provider
from .schema import TRAJECTORY_SCHEMA_VERSION, locate_json_path, validate_trajectory

MANIFEST_SCHEMA = "acceptance-manifest/1"
H1_REPORT_SCHEMA = "h1-report/1"
INTEGRITY_VERSION = "canonical-json-sha256/1"
DEFAULT_MANIFEST_ID = "acceptance-v1"

# H1 只对下游模块做「不含数据集名称」的静态扫描；来源侧模块不在其中。
DOWNSTREAM_MODULES = (
    "trajectory_audit/baseline.py",
    "trajectory_audit/runner.py",
    "trajectory_audit/batch.py",
    "trajectory_audit/metrics.py",
    "trajectory_audit/page.py",
    "trajectory_audit/providers.py",
    "trajectory_audit/model.py",
    "trajectory_audit/schema.py",
)


@dataclass(frozen=True)
class RecordOutcome:
    dataset_id: str
    record_index: int
    sample_id: str
    status: str
    record_sha256: str
    payload: dict[str, Any] | None = None
    trajectory: NormalizedTrajectory | None = None
    problem: dict[str, Any] | None = None


def dataset_path(dataset_root: Path, dataset_id: str) -> Path:
    return dataset_root / pinned.pinned(dataset_id)["cache_name"]


def _record_blockers(adapter: Any, record: Any) -> list[dict[str, Any]]:
    """不进入归一化的拦截条件：来源完整性缺陷优先于来源 Schema 问题。"""
    defects = adapter.inspect_source(record)
    if defects:
        return defects
    issues = adapter.validate_record(record)
    if issues:
        return [
            {
                "stage": "adapter_validation",
                "code": "schema_invalid",
                "path": "",
                "reason": "记录不符合来源 Schema",
                "details": {"issues": issues},
            }
        ]
    return []


def _exclusion(
    dataset_id: str,
    record_index: int,
    adapter: Any,
    record: Any,
    problem: dict[str, Any],
) -> dict[str, Any]:
    sample_id, _ = adapter.sample_identity(record)
    return {
        "dataset_id": dataset_id,
        "record_index": record_index,
        "sample_id": sample_id or f"record-{record_index:04d}",
        "record_sha256": sha256_json(record),
        "stage": problem["stage"],
        "code": problem["code"],
        "path": problem.get("path"),
        "reason": problem["reason"],
        "details": problem.get("details"),
    }


def load_dataset(path: Path) -> list[Any]:
    try:
        payload = read_json(path)
    except FileNotFoundError:
        raise RunError(
            "dataset_not_found",
            f"数据集文件不存在：{path}",
            details={"path": str(path)},
        ) from None
    if not isinstance(payload, list):
        raise RunError(
            "dataset_not_array",
            "固定的数据集文件必须是 JSON 数组",
            details={"path": str(path), "type": type(payload).__name__},
        )
    return payload


def verify_dataset_file(dataset_root: Path, dataset_id: str) -> dict[str, Any]:
    """按固定清单核对本地文件：存在、字节数与 SHA-256。"""
    entry = pinned.pinned(dataset_id)
    path = dataset_path(dataset_root, dataset_id)
    result: dict[str, Any] = {
        "dataset_id": dataset_id,
        "path": str(path),
        "expected_sha256": entry["sha256"],
        "expected_bytes": entry["bytes"],
        "url": entry["url"],
    }
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        result.update({"present": False, "sha256": None, "bytes": None, "ok": False})
        return result
    except OSError as exc:
        result.update(
            {"present": False, "sha256": None, "bytes": None, "ok": False, "error": str(exc)}
        )
        return result
    digest = hashlib.sha256(raw).hexdigest()
    result.update(
        {
            "present": True,
            "sha256": digest,
            "bytes": len(raw),
            "ok": digest == entry["sha256"] and len(raw) == entry["bytes"],
        }
    )
    return result


# ---------------------------------------------------------------------------
# 冻结清单
# ---------------------------------------------------------------------------


def build_manifest(
    *,
    dataset_root: Path,
    manifest_id: str = DEFAULT_MANIFEST_ID,
    previous: dict[str, Any] | None = None,
    replacement_reason: str | None = None,
    decided_at: str | None = None,
) -> dict[str, Any]:
    """按固定分层策略选出 `acceptance-v1` 的样本并附带完整性哈希。"""
    policy = _policy(manifest_id)
    sources: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for dataset_id, quota in policy["quotas"].items():
        entry = pinned.pinned(dataset_id)
        adapter = get_adapter(entry["adapter_id"])
        path = dataset_path(dataset_root, dataset_id)
        records = load_dataset(path)
        file_check = verify_dataset_file(dataset_root, dataset_id)
        if not file_check["ok"]:
            raise RunError(
                "dataset_hash_mismatch",
                "数据集文件与固定清单不一致，拒绝在其上冻结验收集",
                details={
                    "dataset_id": dataset_id,
                    "path": str(path),
                    "expected_sha256": entry["sha256"],
                    "actual_sha256": file_check["sha256"],
                    "expected_bytes": entry["bytes"],
                    "actual_bytes": file_check["bytes"],
                },
            )

        valid: list[tuple[int, Any, str]] = []
        for record_index, record in enumerate(records):
            problems = _record_blockers(adapter, record)
            if problems:
                excluded.append(
                    _exclusion(dataset_id, record_index, adapter, record, problems[0])
                )
                continue
            sample_id, _ = adapter.sample_identity(record)
            valid.append((record_index, record, sample_id or f"record-{record_index:04d}"))

        stratified = _stratify(valid, policy["stratification"][dataset_id])
        chosen = _select(stratified, quota, policy["seed"], dataset_id)

        for record_index, record, sample_id, stratum, substratum in chosen:
            samples.append(
                {
                    "sample_id": sample_id,
                    "dataset_id": dataset_id,
                    "record_index": record_index,
                    "record_sha256": sha256_json(record),
                    "stratum": stratum,
                    "substratum": substratum,
                    "source_label": _source_label_summary(
                        record, policy["risk_layers"][dataset_id], stratum
                    ),
                }
            )
        sources.append(
            {
                "dataset_id": dataset_id,
                "adapter_id": entry["adapter_id"],
                "dataset_version": entry["dataset_version"],
                "upstream": entry["upstream"],
                "upstream_commit": entry["upstream_commit"],
                "url": entry["url"],
                "license": entry["license"],
                "sha256": entry["sha256"],
                "bytes": entry["bytes"],
                "layout": entry["layout"],
                "records_total": len(records),
                "records_valid": len(valid),
                "records_invalid": len(records) - len(valid),
                "selected": len([item for item in samples if item["dataset_id"] == dataset_id]),
            }
        )

    samples.sort(key=lambda item: (item["dataset_id"], item["record_index"]))
    excluded.sort(key=lambda item: (item["dataset_id"], item["record_index"]))

    replacements: list[dict[str, Any]] = []
    if previous is not None:
        replacements = list(previous.get("replacements") or [])
        added = _identity_diff(previous.get("samples") or [], samples)
        replacements.extend(
            _replacement_entries(
                previous.get("samples") or [],
                samples,
                added,
                reason=replacement_reason,
                decided_at=decided_at or now(),
            )
        )

    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA,
        "manifest_id": manifest_id,
        "created_at": now(),
        "frozen": True,
        "policy": policy,
        "sources": sources,
        "samples": samples,
        "excluded_records": excluded,
        "replacements": replacements,
        "notes": [
            "分层与抽样只读取来源标签，不读取 detected 结果。",
            "全量数据只用于版本、解析、Schema 与可追溯性验证，不打入交付页面。",
            "来源完整性缺陷的记录被如实排除并记录原因，不算 adapter 转换失败。",
        ],
    }
    manifest["integrity"] = {
        "digest_version": INTEGRITY_VERSION,
        "content_sha256": content_sha256(manifest),
    }
    return manifest


def content_sha256(manifest: dict[str, Any]) -> str:
    """只对选择内容取哈希：重建同一选择得到同一指纹，不受时间戳影响。"""
    projection = {
        key: value
        for key, value in manifest.items()
        if key not in {"created_at", "integrity"}
    }
    return hashlib.sha256(canonical_json(projection).encode("utf-8")).hexdigest()


def validate_manifest(
    manifest: Any,
    *,
    expected_id: str | None = None,
    verify_pinned: bool = True,
) -> list[dict[str, str]]:
    """校验冻结清单自洽；问题列表为空表示可用于 H1 验证。"""
    issues: list[dict[str, str]] = []

    def add(path: str, message: str) -> None:
        issues.append({"path": path, "message": message})

    if not isinstance(manifest, dict):
        add("", "清单必须是 JSON 对象")
        return issues
    if manifest.get("schema_version") != MANIFEST_SCHEMA:
        add("schema_version", f"必须是 {MANIFEST_SCHEMA!r}")
    manifest_id = manifest.get("manifest_id")
    if not isinstance(manifest_id, str) or not manifest_id.strip():
        add("manifest_id", "必须是非空字符串")
    elif expected_id is not None and manifest_id != expected_id:
        add("manifest_id", f"期望 {expected_id!r}，实际 {manifest_id!r}")
    if manifest.get("frozen") is not True:
        add("frozen", "冻结清单必须 frozen=true")

    policy = manifest.get("policy")
    if not isinstance(policy, dict):
        add("policy", "policy 必须是对象")
        policy = {}
    quotas = policy.get("quotas") if isinstance(policy.get("quotas"), dict) else {}

    frozen_policy = pinned.ACCEPTANCE_POLICIES.get(manifest_id) if isinstance(manifest_id, str) else None
    if frozen_policy is None:
        add(
            "manifest_id",
            "未知的验收集版本：没有对应的冻结策略，不能作为 H1 依据",
        )
    else:
        if policy.get("manifest_id") != manifest_id:
            add("policy.manifest_id", "必须与清单身份一致")
        for field in (
            "seed",
            "algorithm",
            "selection_basis",
            "quotas",
            "stratification",
            "risk_layers",
        ):
            if policy.get(field) != frozen_policy[field]:
                add(f"policy.{field}", "与代码中的冻结策略不一致")

    integrity = manifest.get("integrity")
    if not isinstance(integrity, dict):
        add("integrity", "integrity 必须是对象")
    elif integrity.get("digest_version") != INTEGRITY_VERSION:
        add("integrity.digest_version", f"必须是 {INTEGRITY_VERSION!r}")
    elif integrity.get("content_sha256") != content_sha256(manifest):
        add("integrity.content_sha256", "内容哈希与清单内容不一致，清单未冻结或被改写")

    sources = manifest.get("sources")
    if not isinstance(sources, list) or not sources:
        add("sources", "sources 必须是非空数组")
        sources = []
    declared_datasets = []
    for position, source in enumerate(sources):
        path = f"sources[{position}]"
        if not isinstance(source, dict):
            add(path, "来源必须是对象")
            continue
        dataset_id = source.get("dataset_id")
        declared_datasets.append(dataset_id)
        entry = pinned.BY_DATASET.get(dataset_id)
        if entry is None:
            add(f"{path}.dataset_id", f"未固定的数据集：{dataset_id!r}")
            continue
        for field in ("dataset_version", "sha256", "bytes", "license", "url"):
            if source.get(field) != entry[field]:
                add(f"{path}.{field}", f"与固定来源清单不一致，期望 {entry[field]!r}")
        if source.get("adapter_id") != entry["adapter_id"]:
            add(f"{path}.adapter_id", f"必须是 {entry['adapter_id']!r}")
        total = source.get("records_total")
        valid = source.get("records_valid")
        invalid = source.get("records_invalid")
        if not all(isinstance(value, int) and value >= 0 for value in (total, valid, invalid)):
            add(f"{path}.records_total", "records_total / valid / invalid 必须是非负整数")
        elif valid + invalid != total:
            add(f"{path}.records_valid", "valid + invalid 必须等于 records_total")

    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        add("samples", "samples 必须是非空数组")
        samples = []
    seen_records: set[tuple[Any, Any]] = set()
    seen_samples: set[str] = set()
    per_stratum: dict[tuple[str, str], int] = Counter()
    for position, sample in enumerate(samples):
        path = f"samples[{position}]"
        if not isinstance(sample, dict):
            add(path, "样本必须是对象")
            continue
        dataset_id = sample.get("dataset_id")
        record_index = sample.get("record_index")
        sample_id = sample.get("sample_id")
        if dataset_id not in declared_datasets:
            add(f"{path}.dataset_id", f"数据集未被 sources 声明：{dataset_id!r}")
        key = (dataset_id, record_index)
        if key in seen_records:
            add(f"{path}.record_index", f"同一记录被重复选中：{key}")
        seen_records.add(key)
        if not isinstance(sample_id, str) or not sample_id:
            add(f"{path}.sample_id", "必须是非空字符串")
        elif sample_id in seen_samples:
            add(f"{path}.sample_id", f"样本身份重复：{sample_id!r}")
        else:
            seen_samples.add(sample_id)
        digest = sample.get("record_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            add(f"{path}.record_sha256", "必须是 SHA-256 十六进制字符串")
        stratum = sample.get("stratum")
        per_stratum[(dataset_id, stratum)] += 1
        if not isinstance(sample.get("substratum"), str) or not sample.get("substratum"):
            add(f"{path}.substratum", "必须是非空字符串")

    for dataset_id, quota in quotas.items():
        if dataset_id not in declared_datasets:
            add("sources", f"配额声明的数据集缺少来源信息：{dataset_id!r}")
            continue
        allocated = sum(
            count for (name, _), count in per_stratum.items() if name == dataset_id
        )
        if allocated != quota.get("total"):
            add(
                "samples",
                f"{dataset_id} 选中 {allocated} 条，配额要求 {quota.get('total')}",
            )
        for stratum, expected in (quota.get("strata") or {}).items():
            actual = per_stratum.get((dataset_id, stratum), 0)
            if actual != expected:
                add(
                    "samples",
                    f"{dataset_id}/{stratum} 选中 {actual} 条，配额要求 {expected}",
                )

    excluded = manifest.get("excluded_records")
    if not isinstance(excluded, list):
        add("excluded_records", "必须是数组")
        excluded = []
    for position, item in enumerate(excluded):
        path = f"excluded_records[{position}]"
        if not isinstance(item, dict):
            add(path, "排除项必须是对象")
            continue
        for field in ("dataset_id", "record_index", "sample_id", "stage", "code", "reason"):
            if field not in item:
                add(f"{path}.{field}", "缺少必需字段")
        if (item.get("dataset_id"), item.get("record_index")) in seen_records:
            add(f"{path}.record_index", "被排除的记录不得同时进入验收集")

    replacements = manifest.get("replacements")
    if not isinstance(replacements, list):
        add("replacements", "必须是数组")
        replacements = []
    replaced_away = {
        sample_id
        for entry in replacements
        if isinstance(entry, dict)
        for sample_id in [entry.get("removed", {}).get("sample_id")]
        if sample_id
    }
    for position, entry in enumerate(replacements):
        path = f"replacements[{position}]"
        if not isinstance(entry, dict):
            add(path, "替换记录必须是对象")
            continue
        removed = entry.get("removed")
        added_entry = entry.get("added")
        if not isinstance(removed, dict) or "sample_id" not in removed:
            add(f"{path}.removed", "必须记录被替换样本的身份")
        if not isinstance(added_entry, dict) or "sample_id" not in added_entry:
            add(f"{path}.added", "必须记录替换后样本的身份")
        if not isinstance(entry.get("reason"), str) or not entry["reason"].strip():
            add(f"{path}.reason", "替换必须保留原因")
        if not isinstance(entry.get("decided_at"), str) or not entry["decided_at"].strip():
            add(f"{path}.decided_at", "替换必须保留决定时间")
    for sample in samples:
        if isinstance(sample, dict) and sample.get("sample_id") in replaced_away:
            add("replacements", f"已替换掉的样本仍在验收集中：{sample.get('sample_id')!r}")

    return issues


# ---------------------------------------------------------------------------
# H1 验证
# ---------------------------------------------------------------------------


def verify_manifest(
    *,
    manifest: dict[str, Any],
    dataset_root: Path,
) -> dict[str, Any]:
    """对固定版本的全部记录执行 H1 数据契约验证，返回机读报告。"""
    manifest_issues = validate_manifest(manifest, expected_id=manifest.get("manifest_id"))
    report: dict[str, Any] = {
        "schema_version": H1_REPORT_SCHEMA,
        "manifest_id": manifest.get("manifest_id"),
        "manifest_content_sha256": (manifest.get("integrity") or {}).get("content_sha256"),
        "generated_at": now(),
        "tool_version": __version__,
        "trajectory_schema_version": TRAJECTORY_SCHEMA_VERSION,
        "manifest_issues": manifest_issues,
        "sources": [],
        "acceptance": {},
        "downstream": {},
        "failures": [],
    }

    samples_by_dataset: dict[str, dict[int, dict[str, Any]]] = {}
    for sample in manifest.get("samples") or []:
        samples_by_dataset.setdefault(sample["dataset_id"], {})[sample["record_index"]] = sample
    excluded_by_dataset: dict[str, dict[int, dict[str, Any]]] = {}
    for item in manifest.get("excluded_records") or []:
        excluded_by_dataset.setdefault(item["dataset_id"], {})[item["record_index"]] = item

    probes: dict[str, NormalizedTrajectory] = {}
    for source in manifest.get("sources") or []:
        dataset_id = source["dataset_id"]
        summary, probe = _verify_source(
            dataset_id=dataset_id,
            dataset_root=dataset_root,
            expected_samples=samples_by_dataset.get(dataset_id, {}),
            expected_excluded=excluded_by_dataset.get(dataset_id, {}),
        )
        report["sources"].append(summary)
        report["failures"].extend(summary["failures"])
        if probe is not None:
            probes[dataset_id] = probe

    report["acceptance"] = _acceptance_summary(manifest, report["sources"])
    report["downstream"] = _downstream_summary(probes)
    report["checks"] = _checks(manifest, report)
    report["status"] = "pass" if all(check["status"] == "pass" for check in report["checks"]) else "fail"
    return report


def _verify_source(
    *,
    dataset_id: str,
    dataset_root: Path,
    expected_samples: dict[int, dict[str, Any]],
    expected_excluded: dict[int, dict[str, Any]],
) -> tuple[dict[str, Any], NormalizedTrajectory | None]:
    entry = pinned.pinned(dataset_id)
    adapter = get_adapter(entry["adapter_id"])
    file_check = verify_dataset_file(dataset_root, dataset_id)
    records = load_dataset(dataset_path(dataset_root, dataset_id)) if file_check["present"] else []

    counters: Counter[str] = Counter()
    schema_failures: list[dict[str, Any]] = []
    conversion_failures: list[dict[str, Any]] = []
    invalid_records: list[dict[str, Any]] = []
    traceability_failures: list[dict[str, Any]] = []
    label_failures: list[dict[str, Any]] = []
    sample_failures: list[dict[str, Any]] = []
    accepted_records: dict[int, str] = {}
    verified_records: set[int] = set()
    event_types: Counter[str] = Counter()
    probe: NormalizedTrajectory | None = None

    for record_index, record in enumerate(records):
        outcome = _convert_record(
            dataset_id=dataset_id,
            adapter=adapter,
            dataset_version=entry["dataset_version"],
            file_sha256=entry["sha256"],
            record_index=record_index,
            record=record,
        )
        counters[outcome.status] += 1

        if outcome.status == OUTCOME_SOURCE_INVALID:
            invalid_records.append(
                {
                    "record_index": record_index,
                    "sample_id": outcome.sample_id,
                    "stage": outcome.problem["stage"],
                    "code": outcome.problem["code"],
                    "path": outcome.problem.get("path"),
                    "reason": outcome.problem["reason"],
                    "details": outcome.problem.get("details"),
                }
            )
            expected = expected_excluded.get(record_index)
            if expected is None:
                sample_failures.append(
                    {
                        "record_index": record_index,
                        "code": "unexpected_invalid_record",
                        "reason": "运行期判定为来源无效，但冻结清单没有记录该排除项",
                    }
                )
            elif expected.get("code") != outcome.problem["code"]:
                sample_failures.append(
                    {
                        "record_index": record_index,
                        "code": "invalid_record_code_changed",
                        "reason": (
                            f"冻结清单记录 {expected.get('code')!r}，"
                            f"实际 {outcome.problem['code']!r}"
                        ),
                    }
                )
            elif expected.get("record_sha256") not in (None, outcome.record_sha256):
                sample_failures.append(
                    {
                        "record_index": record_index,
                        "code": "excluded_record_sha256_changed",
                        "reason": "被排除记录的内容哈希与冻结清单不一致",
                    }
                )
            continue

        if outcome.status != OUTCOME_VALID:
            problem = outcome.problem or {}
            conversion_failures.append(
                {
                    "record_index": record_index,
                    "sample_id": outcome.sample_id,
                    "stage": problem.get("stage"),
                    "code": problem.get("code"),
                    "reason": problem.get("reason"),
                    "issues": problem.get("issues"),
                }
            )
            continue

        payload = outcome.payload or {}
        issues = validate_trajectory(payload)
        if issues:
            schema_failures.append(
                {"record_index": record_index, "sample_id": outcome.sample_id, "issues": issues}
            )
            continue

        accepted_records[record_index] = outcome.record_sha256
        event_types.update(event["type"] for event in payload["events"])
        traceability_failures.extend(
            _traceability_failures(payload, record, dataset_id, entry["dataset_version"])
        )
        label_failures.extend(_label_failures(adapter, record, payload))
        if probe is None and outcome.trajectory is not None:
            probe = outcome.trajectory

    observed_invalid = {item["record_index"] for item in invalid_records}
    for record_index, item in expected_excluded.items():
        if record_index not in observed_invalid:
            sample_failures.append(
                {
                    "record_index": record_index,
                    "code": "excluded_record_now_valid",
                    "reason": (
                        f"冻结清单把该记录记为 {item.get('code')!r}，本次转换却成功了"
                    ),
                }
            )

    for record_index, sample in expected_samples.items():
        if record_index not in accepted_records:
            sample_failures.append(
                {
                    "record_index": record_index,
                    "sample_id": sample.get("sample_id"),
                    "code": "acceptance_sample_missing",
                    "reason": "冻结清单选中的样本未能通过转换与 Schema 校验",
                }
            )
        elif accepted_records[record_index] != sample.get("record_sha256"):
            sample_failures.append(
                {
                    "record_index": record_index,
                    "sample_id": sample.get("sample_id"),
                    "code": "record_sha256_changed",
                    "reason": "记录内容哈希与冻结清单不一致",
                }
            )
        else:
            verified_records.add(record_index)

    summary = {
        "dataset_id": dataset_id,
        "adapter_id": entry["adapter_id"],
        "dataset_version": entry["dataset_version"],
        "license": entry["license"],
        "url": entry["url"],
        "file": file_check,
        "records_total": len(records),
        "records_converted": counters[OUTCOME_VALID],
        "records_source_invalid": counters[OUTCOME_SOURCE_INVALID],
        "records_schema_invalid": counters[OUTCOME_SCHEMA_INVALID],
        "records_conversion_error": counters[OUTCOME_CONVERSION_ERROR],
        "acceptance_samples_expected": len(expected_samples),
        "acceptance_samples_verified": len(verified_records),
        "event_types": dict(sorted(event_types.items())),
        "events_total": sum(event_types.values()),
        "schema_failures": schema_failures,
        "conversion_failures": conversion_failures,
        "invalid_records": invalid_records,
        "traceability_failures": traceability_failures,
        "label_failures": label_failures,
        "sample_failures": sample_failures,
        "failure_count": (
            len(schema_failures)
            + len(conversion_failures)
            + len(traceability_failures)
            + len(label_failures)
            + len(sample_failures)
            + (0 if file_check["ok"] else 1)
        ),
    }
    summary["failures"] = _source_failures(dataset_id, summary, file_check)
    return summary, probe


def _convert_record(
    *,
    dataset_id: str,
    adapter: Any,
    dataset_version: str,
    file_sha256: str,
    record_index: int,
    record: Any,
) -> RecordOutcome:
    sample_id, _ = adapter.sample_identity(record)
    fallback = sample_id or f"record-{record_index:04d}"
    record_sha256 = sha256_json(record)

    defects = adapter.inspect_source(record)
    if defects:
        first = defects[0]
        return RecordOutcome(
            dataset_id=dataset_id,
            record_index=record_index,
            sample_id=fallback,
            status=OUTCOME_SOURCE_INVALID,
            record_sha256=record_sha256,
            problem=first,
        )

    issues = adapter.validate_record(record)
    if issues:
        return RecordOutcome(
            dataset_id=dataset_id,
            record_index=record_index,
            sample_id=fallback,
            status=OUTCOME_SCHEMA_INVALID,
            record_sha256=record_sha256,
            problem={
                "stage": "adapter_validation",
                "code": "schema_invalid",
                "reason": "记录不符合来源 Schema",
                "issues": issues,
            },
        )

    try:
        trajectories = adapter.normalize_record(
            record,
            record_index=record_index,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            input_sha256=file_sha256,
            record_json_path="",
            record_sha256=record_sha256,
            sample_id=fallback,
        )
    except ConversionError as exc:
        return RecordOutcome(
            dataset_id=dataset_id,
            record_index=record_index,
            sample_id=exc.sample_id or fallback,
            status=OUTCOME_CONVERSION_ERROR,
            record_sha256=record_sha256,
            problem={
                "stage": exc.stage,
                "code": exc.code,
                "reason": exc.message,
                "details": exc.details,
            },
        )
    if len(trajectories) != 1:
        return RecordOutcome(
            dataset_id=dataset_id,
            record_index=record_index,
            sample_id=fallback,
            status=OUTCOME_CONVERSION_ERROR,
            record_sha256=record_sha256,
            problem={
                "stage": "adapter_normalization",
                "code": "multiple_samples_per_record",
                "reason": (
                    "冻结验收集以记录为单位，当前适配器把一条记录拆成多条轨迹，"
                    "需要先决定样本计数口径"
                ),
                "details": {"sample_count": len(trajectories)},
            },
        )

    # 走一次 JSON 往返：H1 校验的是落盘后的结构，而不是内存对象。
    payload = json.loads(json.dumps(trajectories[0].to_dict(), ensure_ascii=False))
    return RecordOutcome(
        dataset_id=dataset_id,
        record_index=record_index,
        sample_id=trajectories[0].sample_id,
        status=OUTCOME_VALID,
        record_sha256=record_sha256,
        payload=payload,
        trajectory=trajectories[0],
    )


def _traceability_failures(
    payload: dict[str, Any], record: Any, dataset_id: str, dataset_version: str
) -> list[dict[str, Any]]:
    """逐事件核对：路径可回读、原文一致、来源版本一致、原始记录哈希一致。"""
    failures: list[dict[str, Any]] = []
    record_view = json.loads(json.dumps(record, ensure_ascii=False))
    expected_record_sha256 = sha256_json(record)
    record_meta = payload["source"]["record"]

    if record_meta["sha256"] != expected_record_sha256:
        failures.append(
            {
                "code": "record_sha256_mismatch",
                "path": "source.record.sha256",
                "reason": "来源记录哈希与重新计算值不一致",
            }
        )
    if record_meta["raw"] != record_view:
        failures.append(
            {
                "code": "record_raw_mismatch",
                "path": "source.record.raw",
                "reason": "保存的原始记录与输入不一致",
            }
        )
    if record_meta["index"] != payload["events"][0]["source"]["record_index"]:
        failures.append(
            {
                "code": "record_index_mismatch",
                "path": "source.record.index",
                "reason": "轨迹级记录下标与事件定位不一致",
            }
        )

    for event in payload["events"]:
        path = event["source"]["json_path"]
        found, value = locate_json_path(record_view, path)
        if not found:
            failures.append(
                {
                    "code": "event_path_missing",
                    "event_id": event["event_id"],
                    "path": path,
                    "reason": "事件定位路径无法在原始记录中回读",
                }
            )
            continue
        if value != event["raw"]:
            failures.append(
                {
                    "code": "event_raw_mismatch",
                    "event_id": event["event_id"],
                    "path": path,
                    "reason": "事件 raw 与该路径回读到的原始值不一致",
                }
            )
        if event["source"]["dataset_id"] != dataset_id:
            failures.append(
                {
                    "code": "event_dataset_mismatch",
                    "event_id": event["event_id"],
                    "path": path,
                    "reason": "事件的来源数据集与轨迹级不一致",
                }
            )
        if event["source"]["dataset_version"] != dataset_version:
            failures.append(
                {
                    "code": "event_version_mismatch",
                    "event_id": event["event_id"],
                    "path": path,
                    "reason": "事件的来源版本与轨迹级不一致",
                }
            )
    return failures


def _label_failures(
    adapter: Any, record: Any, payload: dict[str, Any]
) -> list[dict[str, Any]]:
    """来源标签不得丢失、错位，也不得把原始标签改写成统一类别。"""
    failures: list[dict[str, Any]] = []
    labels = payload["source_labels"]
    expected = adapter.expected_source_label_count(record)
    if expected is not None and len(labels) != expected:
        failures.append(
            {
                "code": "source_label_count_mismatch",
                "reason": f"应有 {expected} 条来源标签，实际 {len(labels)} 条",
            }
        )
    for label in labels:
        if not isinstance(label.get("mapping"), dict) or "version" not in label["mapping"]:
            failures.append(
                {
                    "code": "source_label_mapping_missing",
                    "event_id": None,
                    "path": label["label_id"],
                    "reason": "来源标签缺少映射版本记录",
                }
            )
        if label["scope"] == "source_step":
            raw = label.get("raw")
            if not isinstance(raw, dict):
                failures.append(
                    {
                        "code": "source_label_raw_missing",
                        "path": label["label_id"],
                        "reason": "已定位来源标签必须保留原始标签内容",
                    }
                )
                continue
            declared = raw.get("step_id")
            if declared is None:
                declared = raw.get("risk_origin_step")
            if declared != label["source_step_id"]:
                failures.append(
                    {
                        "code": "source_label_step_mismatch",
                        "path": label["label_id"],
                        "reason": (
                            f"原文声明步骤 {declared!r}，"
                            f"统一记录 {label['source_step_id']!r}"
                        ),
                    }
                )
    return failures


def _acceptance_summary(
    manifest: dict[str, Any], sources: list[dict[str, Any]]
) -> dict[str, Any]:
    expected = len(manifest.get("samples") or [])
    verified = sum(source["acceptance_samples_verified"] for source in sources)
    missing = [
        failure
        for source in sources
        for failure in source["sample_failures"]
        if failure["code"] == "acceptance_sample_missing"
    ]
    samples = manifest.get("samples") or []
    return {
        "manifest_id": manifest.get("manifest_id"),
        "selected": expected,
        "verified": verified,
        "by_dataset": {
            source["dataset_id"]: {
                "expected": source["acceptance_samples_expected"],
                "verified": source["acceptance_samples_verified"],
            }
            for source in sources
        },
        "by_stratum": [
            {"dataset_id": dataset_id, "stratum": stratum, "count": count}
            for (dataset_id, stratum), count in sorted(
                Counter((sample["dataset_id"], sample["stratum"]) for sample in samples).items()
            )
        ],
        "by_substratum": [
            {
                "dataset_id": dataset_id,
                "stratum": stratum,
                "substratum": substratum,
                "count": count,
            }
            for (dataset_id, stratum, substratum), count in sorted(
                Counter(
                    (sample["dataset_id"], sample["stratum"], sample["substratum"])
                    for sample in samples
                ).items()
            )
        ],
        "missing": missing,
    }


def _downstream_summary(probes: dict[str, NormalizedTrajectory]) -> dict[str, Any]:
    """静态扫描 + 真实调用，证明下游只消费统一轨迹。"""
    mentions: list[dict[str, str]] = []
    root = Path(__file__).resolve().parents[1]
    for relative in DOWNSTREAM_MODULES:
        text = (root / relative).read_text(encoding="utf-8").lower()
        for dataset_id in pinned.BY_DATASET:
            if dataset_id in text:
                mentions.append({"module": relative, "token": dataset_id})

    adapter_interfaces = {
        adapter_id: sorted(
            method for method in ADAPTER_METHODS if not hasattr(module, method)
        )
        for adapter_id, module in ADAPTERS.items()
    }

    probe_results: dict[str, Any] = {}
    for dataset_id, trajectory in probes.items():
        probe_results[dataset_id] = _probe_pipeline(trajectory)

    payload_shapes = [
        json.dumps(result.get("payload_shape"), sort_keys=True)
        for result in probe_results.values()
    ]
    shapes_consistent = len(set(payload_shapes)) <= 1

    return {
        "modules_scanned": list(DOWNSTREAM_MODULES),
        "dataset_name_mentions": mentions,
        "adapter_missing_methods": {
            key: value for key, value in adapter_interfaces.items() if value
        },
        "shared_adapter_interface": not any(adapter_interfaces.values()),
        "pipeline_probes": probe_results,
        "pipeline_shapes_consistent": shapes_consistent,
    }


def _probe_pipeline(trajectory: NormalizedTrajectory) -> dict[str, Any]:
    """对一条真实轨迹执行 audit → metrics → page，确认下游可共用。"""
    from .metrics import compute_metrics
    from .page import render_review_page

    try:
        provider = get_provider("deterministic-baseline")
        output = provider.audit(trajectory)
        audit_result = AuditResult(
            sample_id=trajectory.sample_id,
            run_id="run-h1-probe",
            provider=provider.provider_identity(),
            verdict=output.verdict,
            checks=output.checks,
            findings=output.findings,
            evaluated_at=now(),
            facts=output.facts,
            policy=output.policy,
            judge=output.judge,
            degraded=output.degraded,
        )
        metrics = compute_metrics(trajectory, audit_result)
        trajectory_dict = json.loads(json.dumps(trajectory.to_dict(), ensure_ascii=False))
        run = {
            "schema_version": "run/1",
            "run_id": "run-h1-probe",
            "completed_at": now(),
            "tool": {
                "name": "trajectory_audit",
                "version": __version__,
                "entry_point": "python -m trajectory_audit acceptance verify",
            },
            "artifacts": {},
        }
        page = render_review_page(
            run=run,
            trajectory=trajectory_dict,
            audit_result=audit_result.to_dict(),
            metrics=metrics,
        )
    except Exception as exc:  # noqa: BLE001 - 探针失败必须记录而不是抛出
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return {
        "ok": True,
        "sample_id": trajectory.sample_id,
        "payload_shape": {
            "trajectory": sorted(trajectory_dict),
            "metrics": sorted(metrics),
            "audit_result": sorted(audit_result.to_dict()),
        },
        "verdict": audit_result.verdict.value,
        "check_status": audit_result.checks.status,
        "finding_count": len(audit_result.findings),
        "metrics_tools": sorted(metrics["tools"]),
        "page_bytes": len(page.encode("utf-8")),
    }


def _checks(manifest: dict[str, Any], report: dict[str, Any]) -> list[dict[str, Any]]:
    sources = report["sources"]
    pinned_ok = all(
        isinstance(source.get("dataset_version"), str) and source.get("license")
        for source in manifest.get("sources") or []
    )
    files_ok = bool(sources) and all(source["file"]["ok"] for source in sources)
    manifest_ok = not report["manifest_issues"]

    def total(key: str) -> int:
        return sum(source[key] for source in sources)

    accounted_ok = all(
        source["records_total"]
        == source["records_converted"]
        + source["records_source_invalid"]
        + source["records_schema_invalid"]
        + source["records_conversion_error"]
        for source in sources
    )
    zero_errors = all(
        source["records_conversion_error"] == 0 and source["records_schema_invalid"] == 0
        for source in sources
    )
    exclusion_codes = {
        "unexpected_invalid_record",
        "excluded_record_now_valid",
        "invalid_record_code_changed",
        "excluded_record_sha256_changed",
    }
    exclusions_ok = not any(
        failure["code"] in exclusion_codes for failure in report["failures"]
    )
    schema_ok = all(not source["schema_failures"] for source in sources)
    traceability_ok = all(not source["traceability_failures"] for source in sources)
    labels_ok = all(not source["label_failures"] for source in sources)
    downstream = report["downstream"]
    downstream_ok = (
        not downstream.get("dataset_name_mentions")
        and downstream.get("shared_adapter_interface") is True
        and all(probe.get("ok") for probe in downstream.get("pipeline_probes", {}).values())
        and downstream.get("pipeline_shapes_consistent") is True
    )
    acceptance = report["acceptance"]
    acceptance_ok = acceptance["selected"] == acceptance["verified"] and not acceptance["missing"]

    def check(identifier: str, title: str, ok: bool, detail: str) -> dict[str, Any]:
        return {
            "id": identifier,
            "title": title,
            "status": "pass" if ok else "fail",
            "detail": detail,
        }

    return [
        check(
            "sources_pinned",
            "来源、许可、版本与 SHA-256 已固定",
            pinned_ok,
            "；".join(
                f"`{source.get('dataset_id')}` {source.get('license')} "
                f"{str(source.get('sha256'))[:12]}…"
                for source in manifest.get("sources") or []
            ),
        ),
        check(
            "files_verified",
            "本地文件与固定清单一致",
            files_ok,
            "；".join(
                f"{source['dataset_id']}: {'ok' if source['file']['ok'] else 'mismatch'}"
                for source in sources
            ),
        ),
        check(
            "manifest_frozen",
            "冻结清单自洽且内容哈希匹配",
            manifest_ok,
            "无问题" if manifest_ok else f"{len(report['manifest_issues'])} 个问题",
        ),
        check(
            "records_accounted",
            "每条记录都有确定去向，无静默跳过",
            accounted_ok,
            "；".join(
                f"{source['dataset_id']}: {source['records_total']} = "
                f"{source['records_converted']} 转换 + "
                f"{source['records_source_invalid']} 来源无效"
                for source in sources
            ),
        ),
        check(
            "zero_conversion_errors",
            "有效记录零转换错误",
            zero_errors,
            f"转换错误 {total('records_conversion_error')} 条，"
            f"Schema 无效 {total('records_schema_invalid')} 条",
        ),
        check(
            "exclusions_match_frozen_manifest",
            "来源无效记录与冻结清单逐条一致",
            exclusions_ok,
            f"冻结排除 {len(manifest.get('excluded_records') or [])} 条，"
            f"不一致 {sum(1 for failure in report['failures'] if failure['code'] in exclusion_codes)} 条",
        ),
        check(
            "schema_valid",
            f"全部轨迹通过 {TRAJECTORY_SCHEMA_VERSION}",
            schema_ok,
            f"Schema 失败 {sum(len(source['schema_failures']) for source in sources)} 条记录",
        ),
        check(
            "event_traceability",
            "每个事件可回溯到原始记录位置且原文一致",
            traceability_ok,
            f"{total('events_total')} 个事件，"
            f"{sum(len(source['traceability_failures']) for source in sources)} 个问题",
        ),
        check(
            "source_label_traceability",
            "来源标签无丢失、无错位，原始标签与映射分开保存",
            labels_ok,
            f"{sum(len(source['label_failures']) for source in sources)} 个问题",
        ),
        check(
            "downstream_source_neutral",
            "下游不按数据集名称分支，且共用同一流水线",
            downstream_ok,
            f"模块提及 {len(downstream.get('dataset_name_mentions') or [])} 处，"
            f"探针 {len(downstream.get('pipeline_probes') or {})} 个",
        ),
        check(
            "acceptance_samples_complete",
            "acceptance-v1 选中的样本全部通过验证",
            acceptance_ok,
            f"{acceptance['verified']}/{acceptance['selected']}",
        ),
    ]


def _source_failures(
    dataset_id: str, summary: dict[str, Any], file_check: dict[str, Any]
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    if not file_check["ok"]:
        failures.append(
            {
                "dataset_id": dataset_id,
                "kind": "dataset_file",
                "code": "dataset_hash_mismatch" if file_check.get("present") else "dataset_not_found",
                "reason": "本地数据集文件与固定清单不一致或不存在",
            }
        )
    for item in summary["conversion_failures"]:
        failures.append({"dataset_id": dataset_id, "kind": "conversion", **item})
    for item in summary["schema_failures"]:
        failures.append({"dataset_id": dataset_id, "kind": "schema", **item})
    for item in summary["traceability_failures"]:
        failures.append({"dataset_id": dataset_id, "kind": "traceability", **item})
    for item in summary["label_failures"]:
        failures.append({"dataset_id": dataset_id, "kind": "source_label", **item})
    for item in summary["sample_failures"]:
        failures.append({"dataset_id": dataset_id, "kind": "acceptance_sample", **item})
    return failures


# ---------------------------------------------------------------------------
# 分层与抽样
# ---------------------------------------------------------------------------


def _policy(manifest_id: str) -> dict[str, Any]:
    policy = pinned.ACCEPTANCE_POLICIES.get(manifest_id)
    if not policy:
        raise RunError(
            "unknown_acceptance_manifest",
            f"未知的验收集清单：{manifest_id}",
            details={"requested": manifest_id, "available": sorted(pinned.ACCEPTANCE_POLICIES)},
        )
    return {
        "manifest_id": manifest_id,
        "seed": policy["seed"],
        "algorithm": policy["algorithm"],
        "selection_basis": policy["selection_basis"],
        # 深拷贝：清单不得与模块级冻结策略共享可变对象，否则一次就地修改会同时
        # 改写「历史清单」和「当前策略」。
        "stratification": copy.deepcopy(policy["stratification"]),
        "quotas": copy.deepcopy(policy["quotas"]),
        "risk_layers": copy.deepcopy(policy["risk_layers"]),
    }


def _matches_layer(record: Any, layer: dict[str, Any]) -> bool:
    """按严格类型比较风险层，避免 ``False == 0`` 之类的隐式相等。"""
    if not isinstance(record, dict):
        return False
    value = record.get(layer["field"])
    expected = layer.get("true_value")
    return type(value) is type(expected) and value == expected


def _stratify(
    valid: Iterable[tuple[int, Any, str]], spec: dict[str, dict[str, Any]]
) -> dict[str, dict[str, list[tuple[int, Any, str, str]]]]:
    groups: dict[str, dict[str, list[tuple[int, Any, str, str]]]] = {
        stratum: {} for stratum in spec
    }
    for record_index, record, sample_id in valid:
        matched = None
        for stratum, stratum_spec in spec.items():
            if _matches_layer(record, stratum_spec["layer"]):
                matched = stratum
                substratum = _substratum(record, stratum_spec)
                groups[stratum].setdefault(substratum, []).append(
                    (record_index, record, sample_id, stratum)
                )
                break
        if matched is None:
            raise RunError(
                "risk_layer_unmatched",
                "记录无法归入任何风险层，拒绝静默丢弃",
                details={
                    "record_index": record_index,
                    "sample_id": sample_id,
                    "layers": sorted(spec),
                },
            )
    return groups


def _substratum(record: Any, spec: dict[str, Any]) -> str:
    kind = spec.get("kind", "field")
    if kind == "field":
        value = record.get(spec["field"]) if isinstance(record, dict) else None
    elif kind == "primary_category":
        value = _primary_category(record)
    else:  # pragma: no cover - 固定清单只声明已知 kind
        raise RunError(
            "unknown_stratification_kind",
            f"未知分层方式：{kind}",
            details={"kind": kind},
        )
    return str(value) if value is not None else "(none)"


def _primary_category(record: Any) -> str | None:
    labels = record.get("risk_labels") if isinstance(record, dict) else None
    if not isinstance(labels, list):
        return None
    for label in labels:
        if not isinstance(label, dict):
            continue
        category = label.get("category") or label.get("big_category")
        if isinstance(category, str) and category.strip():
            return category.strip()
    return None


def _select(
    groups: dict[str, dict[str, list[tuple[int, Any, str, str]]]],
    quota: dict[str, Any],
    seed: int,
    dataset_id: str,
) -> list[tuple[int, Any, str, str, str]]:
    chosen: list[tuple[int, Any, str, str, str]] = []
    for stratum, expected in quota["strata"].items():
        substrata = groups.get(stratum) or {}
        available = sum(len(items) for items in substrata.values())
        if available < expected:
            raise RunError(
                "quota_exceeds_candidates",
                "固定版本中该风险层的可用记录少于冻结配额",
                details={
                    "dataset_id": dataset_id,
                    "stratum": stratum,
                    "required": expected,
                    "available": available,
                },
            )
        allocation = _allocate(substrata, expected)
        for substratum, count in sorted(allocation.items()):
            candidates = sorted(substrata[substratum], key=lambda item: item[0])
            rng = random.Random(f"{seed}:{dataset_id}:{stratum}:{substratum}")
            picked = sorted(rng.sample(candidates, count), key=lambda item: item[0])
            chosen.extend(
                (record_index, record, sample_id, stratum, substratum)
                for record_index, record, sample_id, _ in picked
            )
    return chosen


def _allocate(groups: dict[str, list[Any]], quota: int) -> dict[str, int]:
    """最大余数法分配配额，保证总数恰好等于配额且每层不超过候选数。"""
    total = sum(len(items) for items in groups.values())
    if total == 0 or quota <= 0:
        return {}
    base: dict[str, int] = {}
    fractions: list[tuple[float, int, str]] = []
    for key, items in groups.items():
        exact = quota * len(items) / total
        floor = int(exact)
        base[key] = floor
        fractions.append((exact - floor, len(items), key))
    remaining = quota - sum(base.values())
    fractions.sort(key=lambda item: (-item[0], -item[1], item[2]))
    for _, _, key in fractions[:remaining]:
        base[key] += 1
    return {key: count for key, count in base.items() if count > 0}


def _source_label_summary(
    record: Any, risk_layers: dict[str, dict[str, Any]], stratum: str
) -> dict[str, Any]:
    """只记录来源侧事实，供冻结清单复核；不包含任何 detected 结果。"""
    summary: dict[str, Any] = {
        "risk_layer": stratum,
        "source_fields": {
            layer["field"]: (record.get(layer["field"]) if isinstance(record, dict) else None)
            for layer in risk_layers.values()
        },
    }
    labels = record.get("risk_labels") if isinstance(record, dict) else None
    if isinstance(labels, list):
        summary["risk_label_count"] = len(labels)
        summary["categories"] = sorted(
            {
                str(label.get("category") or label.get("big_category"))
                for label in labels
                if isinstance(label, dict)
                and (label.get("category") or label.get("big_category"))
            }
        )
    return summary


def _identity_diff(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    previous_ids = {
        (item.get("dataset_id"), item.get("record_index"), item.get("sample_id"))
        for item in previous
    }
    current_ids = {
        (item.get("dataset_id"), item.get("record_index"), item.get("sample_id"))
        for item in current
    }
    return [
        {
            "dataset_id": dataset_id,
            "record_index": record_index,
            "sample_id": sample_id,
        }
        for dataset_id, record_index, sample_id in sorted(
            current_ids - previous_ids, key=lambda item: (str(item[0]), str(item[1]))
        )
    ]


def _replacement_entries(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
    added: list[dict[str, Any]],
    *,
    reason: str | None,
    decided_at: str,
) -> list[dict[str, Any]]:
    current_ids = {
        (item.get("dataset_id"), item.get("record_index"), item.get("sample_id"))
        for item in current
    }
    removed = [
        {
            "dataset_id": item.get("dataset_id"),
            "record_index": item.get("record_index"),
            "sample_id": item.get("sample_id"),
        }
        for item in previous
        if (item.get("dataset_id"), item.get("record_index"), item.get("sample_id"))
        not in current_ids
    ]
    if not removed and not added:
        return []
    if not reason or not reason.strip():
        raise RunError(
            "replacement_reason_required",
            "样本集合发生变化时必须提供替换原因",
            details={
                "removed": [item["sample_id"] for item in removed],
                "added": [item["sample_id"] for item in added],
            },
        )
    entries: list[dict[str, Any]] = []
    paired = max(len(removed), len(added))
    for position in range(paired):
        left = removed[position] if position < len(removed) else None
        right = added[position] if position < len(added) else None
        entries.append(
            {
                "removed": left or {"sample_id": None},
                "added": right or {"sample_id": None},
                "reason": reason.strip(),
                "decided_at": decided_at,
            }
        )
    return entries


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------


def render_h1_markdown(report: dict[str, Any], manifest: dict[str, Any]) -> str:
    generated = report["generated_at"]
    lines: list[str] = []
    add = lines.append

    add(f"# H1 数据契约报告 · {report['manifest_id']}")
    add("")
    add(f"- 生成时间：`{generated}`")
    add(f"- 工具版本：`{report['tool_version']}`")
    add(f"- 轨迹 Schema：`{report['trajectory_schema_version']}`")
    add(f"- 清单内容哈希：`{report['manifest_content_sha256']}`")
    add(f"- 总体状态：**{report['status']}**")
    add("")
    add("本报告由 `python -m trajectory_audit acceptance verify` 生成，只覆盖 H1 数据契约，"
        "不覆盖 H2 效果指标、H3 可审阅性或 H4 现场链路。")
    add("")
    add("冻结时点说明：确定性 baseline 目前只有 #2 交付的最小规则桩，#7 的正式规则集尚未实现；"
        "本清单的分层与抽样只读取来源标签，不读取任何 detected 结果，因此后续规则实现"
        "无法反向影响样本选择。若未来需要改动分层或样本，必须新建 `acceptance-v2`，"
        "而不是改写本清单。")
    add("")

    add("## 检查项")
    add("")
    add("| 检查 | 状态 | 说明 |")
    add("| --- | --- | --- |")
    for check in report["checks"]:
        add(f"| `{check['id']}` · {check['title']} | {check['status']} | {check['detail']} |")
    add("")

    add("## 来源版本与完整性")
    add("")
    add("| 数据集 | 版本 | 许可 | 文件 SHA-256 | 字节 | 校验 |")
    add("| --- | --- | --- | --- | --- | --- |")
    for source in report["sources"]:
        file_check = source["file"]
        add(
            f"| `{source['dataset_id']}` | `{source['dataset_version']}` | "
            f"{source['license']} | `{file_check.get('sha256') or '—'}` | "
            f"{file_check.get('bytes') or '—'} | "
            f"{'一致' if file_check['ok'] else '不一致'} |"
        )
    add("")

    add("## 记录去向")
    add("")
    add("| 数据集 | 记录总数 | 已转换 | 来源无效 | Schema 无效 | 转换错误 | 事件 |")
    add("| --- | --- | --- | --- | --- | --- | --- |")
    for source in report["sources"]:
        add(
            f"| `{source['dataset_id']}` | {source['records_total']} | "
            f"{source['records_converted']} | {source['records_source_invalid']} | "
            f"{source['records_schema_invalid']} | {source['records_conversion_error']} | "
            f"{source['events_total']} |"
        )
    add("")

    invalid_rows = [
        (source["dataset_id"], item)
        for source in report["sources"]
        for item in source["invalid_records"]
    ]
    add("## 来源无效记录")
    add("")
    if invalid_rows:
        add("这些记录被如实排除并记录原因，不计入 adapter 转换失败。")
        add("")
        add("| 数据集 | record | 样本 | 代码 | 位置 | 原因 |")
        add("| --- | --- | --- | --- | --- | --- |")
        for dataset_id, item in invalid_rows:
            add(
                f"| `{dataset_id}` | {item['record_index']} | `{item['sample_id']}` | "
                f"`{item['code']}` | `{item.get('path') or '—'}` | {item['reason']} |"
            )
    else:
        add("无。")
    add("")

    add("## acceptance-v1 样本")
    add("")
    acceptance = report["acceptance"]
    add(f"- 选中 {acceptance['selected']} 条，验证通过 {acceptance['verified']} 条。")
    for dataset_id, counts in acceptance["by_dataset"].items():
        add(f"- `{dataset_id}`：{counts['verified']}/{counts['expected']}")
    add("")
    add("| 数据集 | 风险层 | 条数 |")
    add("| --- | --- | --- |")
    for row in acceptance["by_stratum"]:
        add(f"| `{row['dataset_id']}` | `{row['stratum']}` | {row['count']} |")
    add("")
    add("### 分层明细")
    add("")
    add("| 数据集 | 风险层 | 分层键 | 条数 |")
    add("| --- | --- | --- | --- |")
    for row in acceptance["by_substratum"]:
        add(
            f"| `{row['dataset_id']}` | `{row['stratum']}` | "
            f"`{row['substratum']}` | {row['count']} |"
        )
    add("")
    add("配额按各层记录数用最大余数法分配，再在层内以固定种子抽样；"
        "因此分层明细可与固定版本的全量分布逐项复核。")
    add("")

    add("## 下游来源中立性")
    add("")
    downstream = report["downstream"]
    add(f"- 静态扫描模块：{', '.join(f'`{name}`' for name in downstream['modules_scanned'])}")
    add(
        "- 数据集名称提及："
        + (
            "无"
            if not downstream["dataset_name_mentions"]
            else ", ".join(
                f"`{item['module']}` → `{item['token']}`"
                for item in downstream["dataset_name_mentions"]
            )
        )
    )
    add(f"- adapter 接口完整：{'是' if downstream['shared_adapter_interface'] else '否'}")
    add(
        f"- 流水线探针形态一致：{'是' if downstream['pipeline_shapes_consistent'] else '否'}"
    )
    add("")
    if downstream["pipeline_probes"]:
        add("| 数据集 | 探针样本 | 判定 | 检查状态 | 页面字节 |")
        add("| --- | --- | --- | --- | --- |")
        for dataset_id, probe in sorted(downstream["pipeline_probes"].items()):
            if not probe.get("ok"):
                add(f"| `{dataset_id}` | — | 失败 | `{probe.get('error')}` | — |")
                continue
            add(
                f"| `{dataset_id}` | `{probe['sample_id']}` | {probe['verdict']} | "
                f"`{probe['check_status']}` | {probe['page_bytes']} |"
            )
    add("")

    if report["failures"]:
        add("## 失败明细")
        add("")
        add("| 数据集 | 类别 | record | 代码 | 原因 |")
        add("| --- | --- | --- | --- | --- |")
        for failure in report["failures"]:
            add(
                f"| `{failure.get('dataset_id')}` | {failure.get('kind')} | "
                f"{failure.get('record_index', '—')} | `{failure.get('code')}` | "
                f"{failure.get('reason')} |"
            )
        add("")

    add("## 为什么这不覆盖全部验收")
    add("")
    add("- H1 只证明数据契约：版本固定、零转换错误、可追溯、来源与映射分离、下游不按来源分支。")
    add("- H2 效果指标需要 #7 的规则实现与 #9 的已确认参考标注；#10 才出 H2 报告。")
    add("- H3 可审阅性由 #11 的工作台验收；本报告只调用现有最小页面确认下游可共用。")
    add("- H4 现场链路由 #12 的预检与无预生成结果演练覆盖。")
    add("")

    add("## 复现")
    add("")
    add("```bash")
    add("python scripts/fetch_datasets.py --verify-only          # 校验本地固定版本")
    add("python -m trajectory_audit acceptance build \\")
    add("  --dataset-root .cache/datasets --out acceptance/acceptance-v1.json")
    add("python -m trajectory_audit acceptance verify \\")
    add("  --manifest acceptance/acceptance-v1.json --dataset-root .cache/datasets \\")
    add("  --out acceptance/acceptance-v1.h1.json --report acceptance/acceptance-v1.h1.md")
    add("```")
    add("")
    if manifest.get("policy"):
        add(
            f"抽样种子 `{manifest['policy']['seed']}`，算法 "
            f"`{manifest['policy']['algorithm']}`，"
            f"依据「{manifest['policy']['selection_basis']}」。"
        )
        add("")
    return "\n".join(lines)


def write_report(
    *,
    manifest: dict[str, Any],
    dataset_root: Path,
    out_json: Path,
    out_markdown: Path | None = None,
) -> dict[str, Any]:
    report = verify_manifest(manifest=manifest, dataset_root=dataset_root)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    write_json(out_json, report)
    if out_markdown is not None:
        out_markdown.parent.mkdir(parents=True, exist_ok=True)
        out_markdown.write_text(
            render_h1_markdown(report, manifest), encoding="utf-8", newline="\n"
        )
    return report


__all__ = [
    "DEFAULT_MANIFEST_ID",
    "DOWNSTREAM_MODULES",
    "H1_REPORT_SCHEMA",
    "MANIFEST_SCHEMA",
    "build_manifest",
    "content_sha256",
    "dataset_path",
    "load_dataset",
    "render_h1_markdown",
    "validate_manifest",
    "verify_dataset_file",
    "verify_manifest",
    "write_report",
]
