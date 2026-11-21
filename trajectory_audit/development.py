# SPDX-License-Identifier: Apache-2.0
"""Development Corpus：覆盖优先的确定性抽样与冻结交付。

本模块把固定版本的真实轨迹一次性选成 80 条 Development Corpus，并通过 #15
建立的白名单投影生成唯一的正式 provider 输入。它与 ``acceptance`` 的分工是：

- 选样只读来源标签与来源元数据，从不读取 Detected Finding；
- Development 与 Blind Acceptance 使用同一投影与同一 Envelope，但样本身份与
  记录身份零重叠，且各自冻结在不同目录下；
- 构建产物包含正式输入、manifest、coverage matrix、self-check reference 与
  README；来源标签只出现在 self-check reference 中，不进入 provider 输入。

抽样分两阶段：先用贪心集合覆盖满足全部已声明覆盖维度，再按固定种子的真实
分布补足配额。任何声明必须覆盖却没有候选记录的维度都会让构建失败，而不是
静默生成不完整 corpus。
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import acceptance, pinned
from .adapters import get_adapter
from .artifacts import now, read_json
from .audit_input import (
    AUDIT_INPUT_SCHEMA,
    AUDIT_TRAJECTORY_SCHEMA,
    build_batch,
    canonical_item,
    validate_audit_input,
)
from .corpus_common import (
    COVERAGE_FILENAME,
    FORBIDDEN_INPUT_KEYS,
    INPUT_FILENAME,
    INTEGRITY_VERSION,
    MANIFEST_FILENAME,
    README_FILENAME,
    REFERENCE_FILENAME,
    REFERENCE_NOTICE,
    artifact_digests,
    content_sha256,
    existing_matches,
    forbidden_field_hits,
    identities,
    is_provider_sample_id,
    json_bytes,
    label_categories,
    normalize_record,
    provider_sample_id,
    source_label,
    write_payloads,
)
from .errors import RunError
from .model import NormalizedTrajectory, sha256_json
from .selection import allocate_counts, matches_layer

CORPUS_MANIFEST_SCHEMA = "provider-corpus-manifest/1"
COVERAGE_SCHEMA = "provider-corpus-coverage/1"
REFERENCE_SCHEMA = "provider-corpus-reference/1"
DEFAULT_CORPUS_VERSION = "topic4-provider-corpus-v2"

#: manifest 不计入自身摘要，因此单独写出。
SUPPORTING_ARTIFACTS = (
    INPUT_FILENAME,
    COVERAGE_FILENAME,
    REFERENCE_FILENAME,
    README_FILENAME,
)


@dataclass(frozen=True)
class Candidate:
    """一条可被选中的来源记录，及其已声明的覆盖贡献。"""

    dataset_id: str
    record_index: int
    source_sample_id: str
    record: Any
    record_sha256: str
    stratum: str
    coverage: frozenset[tuple[str, str]]
    distribution_key: str

    @property
    def sample_id(self) -> str:
        """provider 可见的样本身份：不可反推来源标签或 benchmark 身份。"""
        return provider_sample_id(
            self.dataset_id, self.record_index, self.record_sha256
        )


def corpus_policy(version: str) -> dict[str, Any]:
    """返回某个 corpus 版本的冻结策略深拷贝。"""
    policy = pinned.DEVELOPMENT_CORPUS_POLICIES.get(version)
    if policy is None:
        raise RunError(
            "unknown_corpus_version",
            f"未知的 corpus 版本：{version}",
            details={
                "requested": version,
                "available": sorted(pinned.DEVELOPMENT_CORPUS_POLICIES),
            },
        )
    return copy.deepcopy(policy)


# ---------------------------------------------------------------------------
# 覆盖维度
# ---------------------------------------------------------------------------


def _distribution_value(record: Any, field: str) -> str:
    if field == "primary_category":
        categories = label_categories(record)
        return categories[0] if categories else "(none)"
    value = record.get(field) if isinstance(record, dict) else None
    return str(value) if value is not None else "(none)"


def _dimension_coverage(
    record: Any, dimension: Mapping[str, Any]
) -> frozenset[tuple[str, str]]:
    """一条记录对某个覆盖维度的贡献；空集表示完全不覆盖。"""
    identifier = dimension["id"]
    kind = dimension["kind"]
    if kind == "field":
        value = record.get(dimension["field"]) if isinstance(record, dict) else None
        if not isinstance(value, str):
            return frozenset()
        return (
            frozenset({(identifier, value)})
            if value in dimension["values"]
            else frozenset()
        )
    if kind == "categories":
        categories = set(label_categories(record))
        return frozenset(
            (identifier, value)
            for value in dimension["values"]
            if value in categories
        )
    raise RunError(
        "unknown_coverage_kind",
        f"未知的覆盖维度类型：{kind}",
        details={"dimension": identifier, "kind": kind},
    )


def _stratum_dimensions(
    source_spec: Mapping[str, Any], stratum: str
) -> list[dict[str, Any]]:
    return [
        dict(dimension)
        for dimension in source_spec["coverage"]
        if stratum in dimension["strata"]
    ]


def _required_pairs(dimensions: Sequence[Mapping[str, Any]]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for dimension in dimensions:
        for value in dimension["values"]:
            pair = (dimension["id"], value)
            if pair not in pairs:
                pairs.append(pair)
    return pairs


# ---------------------------------------------------------------------------
# 抽样
# ---------------------------------------------------------------------------


def _fill_by_distribution(
    remaining: Sequence[Candidate],
    need: int,
    distribution_field: str,
    *,
    seed: int,
    dataset_id: str,
    stratum: str,
) -> list[Candidate]:
    """按真实分布补足配额；层内用固定种子抽样，层间按最大余数法分配。"""
    groups: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in remaining:
        groups[candidate.distribution_key].append(candidate)
    ordered = {
        key: sorted(items, key=lambda item: item.record_index)
        for key, items in groups.items()
    }
    allocation = allocate_counts(
        {key: len(items) for key, items in ordered.items()}, need
    )
    chosen: list[Candidate] = []
    for key in sorted(allocation):
        count = allocation[key]
        rng = random.Random(f"{seed}:{dataset_id}:{stratum}:{distribution_field}:{key}")
        picked = rng.sample(ordered[key], count)
        chosen.extend(sorted(picked, key=lambda item: item.record_index))
    return chosen


def _select_stratum(
    candidates: Sequence[Candidate],
    quota: int,
    dimensions: Sequence[Mapping[str, Any]],
    distribution_field: str,
    *,
    seed: int,
    dataset_id: str,
    stratum: str,
) -> tuple[list[Candidate], list[tuple[str, str]]]:
    """先覆盖后补足；返回 (选中候选, 未闭合的覆盖取值)。"""
    pool = sorted(candidates, key=lambda item: item.record_index)
    uncovered = _required_pairs(dimensions)
    chosen: list[Candidate] = []
    taken: set[int] = set()

    while uncovered and len(chosen) < quota:
        best: Candidate | None = None
        best_key: tuple[int, int] | None = None
        for candidate in pool:
            if candidate.record_index in taken:
                continue
            gain = sum(1 for pair in uncovered if pair in candidate.coverage)
            if gain == 0:
                continue
            key = (-gain, candidate.record_index)
            if best_key is None or key < best_key:
                best, best_key = candidate, key
        if best is None:
            break
        chosen.append(best)
        taken.add(best.record_index)
        uncovered = [pair for pair in uncovered if pair not in best.coverage]

    need = quota - len(chosen)
    if need > 0:
        remaining = [item for item in pool if item.record_index not in taken]
        filled = _fill_by_distribution(
            remaining,
            min(need, len(remaining)),
            distribution_field,
            seed=seed,
            dataset_id=dataset_id,
            stratum=stratum,
        )
        chosen.extend(filled)
    return chosen, uncovered


def _candidate_pool(
    *,
    dataset_id: str,
    adapter: Any,
    records: Sequence[Any],
    source_spec: Mapping[str, Any],
    stratum: str,
    invalid_records: set[int],
    excluded_records: set[tuple[str, int]],
    excluded_samples: set[str],
) -> list[Candidate]:
    layer = source_spec["risk_layers"][stratum]
    dimensions = _stratum_dimensions(source_spec, stratum)
    distribution_field = source_spec["distribution"][stratum]
    pool: list[Candidate] = []
    for record_index, record in enumerate(records):
        if record_index in invalid_records:
            # 来源完整性或来源 Schema 不成立的记录不参与抽样，也不冒充转换失败。
            continue
        if not matches_layer(record, layer):
            continue
        if (dataset_id, record_index) in excluded_records:
            continue
        source_sample_id, _ = adapter.sample_identity(record)
        source_sample_id = source_sample_id or f"record-{record_index:04d}"
        if source_sample_id in excluded_samples:
            continue
        coverage: frozenset[tuple[str, str]] = frozenset()
        for dimension in dimensions:
            coverage |= _dimension_coverage(record, dimension)
        pool.append(
            Candidate(
                dataset_id=dataset_id,
                record_index=record_index,
                source_sample_id=source_sample_id,
                record=record,
                record_sha256=sha256_json(record),
                stratum=stratum,
                coverage=coverage,
                distribution_key=_distribution_value(record, distribution_field),
            )
        )
    return pool


def _coverage_matrix(
    dimensions: Sequence[Mapping[str, Any]],
    selected: Sequence[Candidate],
) -> list[dict[str, Any]]:
    matrix: list[dict[str, Any]] = []
    for dimension in dimensions:
        identifier = dimension["id"]
        rows: list[dict[str, Any]] = []
        for value in dimension["values"]:
            holders = sorted(
                item.sample_id
                for item in selected
                if (identifier, value) in item.coverage
            )
            rows.append({"value": value, "count": len(holders), "sample_ids": holders})
        matrix.append(
            {
                "id": identifier,
                "dataset_id": dimension.get("dataset_id"),
                "strata": list(dimension["strata"]),
                "kind": dimension["kind"],
                "field": dimension.get("field"),
                "detail": dimension.get("detail", ""),
                "required_values": list(dimension["values"]),
                "covered": [row for row in rows if row["count"] > 0],
                "uncovered": [row["value"] for row in rows if row["count"] == 0],
            }
        )
    return matrix


# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------


def build_development_corpus(
    *,
    version: str = DEFAULT_CORPUS_VERSION,
    dataset_root: Path,
    acceptance_manifest: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """按冻结策略选样并生成全部交付产物。

    返回 ``(manifest, artifacts)``；``artifacts`` 是文件名到已序列化 JSON/文本
    的映射，字节即最终落盘内容。任何配额、覆盖或来源问题都会抛出
    ``RunError``，不会返回不完整 corpus。
    """
    policy = corpus_policy(version)
    acceptance_samples, acceptance_records = identities(acceptance_manifest)
    excluded_samples: set[str] = set()
    excluded_records: set[tuple[str, int]] = set()
    sources: list[dict[str, Any]] = []
    selections: list[Candidate] = []
    trajectories: list[NormalizedTrajectory] = []
    coverage_blocks: list[dict[str, Any]] = []
    distribution_summary: dict[str, Any] = {}

    for dataset_id, source_spec in policy["sources"].items():
        entry = pinned.pinned(dataset_id)
        adapter = get_adapter(entry["adapter_id"])
        path = acceptance.dataset_path(dataset_root, dataset_id)
        file_check = acceptance.verify_dataset_file(dataset_root, dataset_id)
        if not file_check["ok"]:
            raise RunError(
                "dataset_hash_mismatch" if file_check["present"] else "dataset_not_found",
                "数据集文件与固定清单不一致或不存在，拒绝在其上冻结 Development Corpus",
                details={
                    "dataset_id": dataset_id,
                    "path": str(path),
                    "expected_sha256": entry["sha256"],
                    "actual_sha256": file_check["sha256"],
                    "expected_bytes": entry["bytes"],
                    "actual_bytes": file_check["bytes"],
                },
            )
        records = acceptance.load_dataset(path)
        if not isinstance(records, list):
            raise RunError(
                "dataset_not_array",
                "固定的数据集文件必须是 JSON 数组",
                details={"path": str(path)},
            )

        invalid = 0
        invalid_indices: set[int] = set()
        dataset_sample_ids: set[str] = set()
        for record_index, record in enumerate(records):
            if adapter.inspect_source(record) or adapter.validate_record(record):
                invalid += 1
                invalid_indices.add(record_index)
                continue
            declared, _ = adapter.sample_identity(record)
            dataset_sample_ids.add(declared or f"record-{record_index:04d}")
        excluded_samples |= dataset_sample_ids & acceptance_samples
        excluded_records |= acceptance_records

        selected_for_dataset: list[Candidate] = []
        for stratum, quota in source_spec["quotas"].items():
            pool = _candidate_pool(
                dataset_id=dataset_id,
                adapter=adapter,
                records=records,
                source_spec=source_spec,
                stratum=stratum,
                invalid_records=invalid_indices,
                excluded_records=acceptance_records,
                excluded_samples=acceptance_samples,
            )
            dimensions = _stratum_dimensions(source_spec, stratum)
            if len(pool) < quota:
                raise RunError(
                    "corpus_quota_unmet",
                    "可用候选少于冻结配额，拒绝生成不完整 corpus",
                    details={
                        "corpus_version": version,
                        "dataset_id": dataset_id,
                        "stratum": stratum,
                        "required": quota,
                        "available": len(pool),
                    },
                )
            chosen, uncovered = _select_stratum(
                pool,
                quota,
                dimensions,
                source_spec["distribution"][stratum],
                seed=policy["seed"],
                dataset_id=dataset_id,
                stratum=stratum,
            )
            if uncovered:
                raise RunError(
                    "corpus_coverage_gap",
                    "声明必须覆盖的维度在本地固定版本中没有可用样本",
                    details={
                        "corpus_version": version,
                        "dataset_id": dataset_id,
                        "stratum": stratum,
                        "missing": [
                            {"dimension": dimension, "value": value}
                            for dimension, value in uncovered
                        ],
                    },
                )
            if len(chosen) < quota:
                raise RunError(
                    "corpus_quota_unmet",
                    "可用候选少于冻结配额，拒绝生成不完整 corpus",
                    details={
                        "corpus_version": version,
                        "dataset_id": dataset_id,
                        "stratum": stratum,
                        "required": quota,
                        "available": len(chosen),
                    },
                )
            selected_for_dataset.extend(chosen)
        selected_for_dataset.sort(key=lambda item: item.record_index)

        for candidate in selected_for_dataset:
            trajectory = normalize_record(
                adapter,
                candidate.record,
                record_index=candidate.record_index,
                dataset_id=dataset_id,
                dataset_version=entry["dataset_version"],
                file_sha256=file_check["sha256"],
                record_sha256=candidate.record_sha256,
                sample_id=candidate.source_sample_id,
            )
            # provider 只看到不透明身份；来源身份留在 manifest 的来源侧字段里。
            trajectories.append(replace(trajectory, sample_id=candidate.sample_id))
            selections.append(candidate)

        covered_by_stratum: dict[str, list[Candidate]] = defaultdict(list)
        for candidate in selected_for_dataset:
            covered_by_stratum[candidate.stratum].append(candidate)
        for stratum in source_spec["quotas"]:
            dimensions = _stratum_dimensions(source_spec, stratum)
            for dimension in dimensions:
                dimension["dataset_id"] = dataset_id
            coverage_blocks.extend(
                _coverage_matrix(dimensions, covered_by_stratum.get(stratum, []))
            )
            distribution_summary[f"{dataset_id}/{stratum}"] = {
                "dimension": source_spec["distribution"][stratum],
                "counts": dict(
                    sorted(
                        Counter(
                            candidate.distribution_key
                            for candidate in covered_by_stratum.get(stratum, [])
                        ).items()
                    )
                ),
            }

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
                "records_invalid": invalid,
                "selected": len(selected_for_dataset),
                "quotas": dict(source_spec["quotas"]),
            }
        )

    # ---- 产物：正式输入、coverage、self-check reference -------------------
    # 按不透明样本身份排序，避免批次顺序暴露数据集分组。
    ordered = sorted(zip(selections, trajectories), key=lambda pair: pair[0].sample_id)
    selections = [candidate for candidate, _ in ordered]
    trajectories = [trajectory for _, trajectory in ordered]
    items = [canonical_item(trajectory, None) for trajectory in trajectories]
    batch = build_batch(items)
    issues = validate_audit_input(batch)
    if issues:
        raise RunError(
            "corpus_invalid",
            "生成的 Development Corpus 正式输入未通过 topic4-audit-input/1 校验",
            details={"corpus_version": version, "issues": issues},
        )
    leaked = forbidden_field_hits(batch)
    if leaked:
        raise RunError(
            "corpus_input_leak",
            "正式输入的结构层出现禁止字段",
            details={"corpus_version": version, "fields": leaked},
        )

    coverage_payload = {
        "schema_version": COVERAGE_SCHEMA,
        "corpus_version": version,
        "dimensions": coverage_blocks,
        "distribution": distribution_summary,
        "quotas": {
            dataset_id: dict(source_spec["quotas"])
            for dataset_id, source_spec in policy["sources"].items()
        },
    }

    reference_samples = [
        {
            "sample_id": candidate.sample_id,
            "source_sample_id": candidate.source_sample_id,
            "dataset_id": candidate.dataset_id,
            "record_index": candidate.record_index,
            "record_sha256": candidate.record_sha256,
            "source_label": source_label(
                candidate.record, candidate.dataset_id, candidate.stratum
            ),
        }
        for candidate in selections
    ]
    reference_payload = {
        "schema_version": REFERENCE_SCHEMA,
        "corpus_version": version,
        "notice": REFERENCE_NOTICE,
        "is_project_truth": False,
        "source_labels_are_benchmark_facts": True,
        "samples": reference_samples,
    }

    selection_by_sample = {item.sample_id: item for item in selections}
    sample_rows: list[dict[str, Any]] = []
    for item in batch["items"]:
        candidate = selection_by_sample[item["sample_id"]]
        sample_rows.append(
            {
                "sample_id": item["sample_id"],
                "source_sample_id": candidate.source_sample_id,
                "dataset_id": candidate.dataset_id,
                "record_index": candidate.record_index,
                "record_sha256": candidate.record_sha256,
                "stratum": candidate.stratum,
                "audit_input_sha256": item["audit_input_sha256"],
                "coverage": [
                    {"dimension": dimension, "value": value}
                    for dimension, value in sorted(candidate.coverage)
                ],
            }
        )
    sample_rows.sort(key=lambda row: row["sample_id"])

    artifacts: dict[str, Any] = {
        INPUT_FILENAME: batch,
        COVERAGE_FILENAME: coverage_payload,
        REFERENCE_FILENAME: reference_payload,
    }

    isolation = _isolation_evidence(
        acceptance_manifest=acceptance_manifest,
        acceptance_sample_ids=acceptance_samples,
        acceptance_record_keys=acceptance_records,
        samples=sample_rows,
        excluded_sample_ids=excluded_samples,
        excluded_record_keys=excluded_records,
    )

    manifest: dict[str, Any] = {
        "schema_version": CORPUS_MANIFEST_SCHEMA,
        "subset": "development",
        "corpus_version": version,
        "created_at": now(),
        "frozen": True,
        "input_schema_version": AUDIT_INPUT_SCHEMA,
        "trajectory_schema_version": AUDIT_TRAJECTORY_SCHEMA,
        "batch_id": batch["batch_id"],
        "policy": {
            "seed": policy["seed"],
            "algorithm": policy["algorithm"],
            "selection_basis": policy["selection_basis"],
            "isolated_from": policy["isolated_from"],
            "quotas": coverage_payload["quotas"],
            "coverage_dimensions": [
                {
                    "id": dimension["id"],
                    "dataset_id": dimension["dataset_id"],
                    "strata": dimension["strata"],
                    "kind": dimension["kind"],
                    "field": dimension["field"],
                    "required_values": dimension["required_values"],
                }
                for dimension in coverage_blocks
            ],
            "distribution": {
                dataset_id: dict(source_spec["distribution"])
                for dataset_id, source_spec in policy["sources"].items()
            },
        },
        "sources": sources,
        "isolation": isolation,
        "samples": sample_rows,
        "coverage_summary": {
            "dimensions": len(coverage_blocks),
            "required_values": sum(
                len(block["required_values"]) for block in coverage_blocks
            ),
            "covered_values": sum(len(block["covered"]) for block in coverage_blocks),
            "uncovered_values": sum(
                len(block["uncovered"]) for block in coverage_blocks
            ),
            "artifact": COVERAGE_FILENAME,
        },
        "notes": [
            "选样只读来源标签与来源元数据，不读取任何 Detected Finding。",
            "来源自测参考保存在 self-check-reference.json，不进入 provider 推理输入。",
            "统一 taxonomy、severity 与 confidence 不属于本 corpus；输入不含 requested_taxonomy。",
        ],
    }

    readme = render_readme(manifest, artifacts[COVERAGE_FILENAME])
    artifacts[README_FILENAME] = readme

    manifest["artifacts"] = artifact_digests(artifacts)
    manifest["integrity"] = {
        "digest_version": INTEGRITY_VERSION,
        "content_sha256": content_sha256(manifest),
    }
    return manifest, artifacts


def _isolation_evidence(
    *,
    acceptance_manifest: Mapping[str, Any],
    acceptance_sample_ids: set[str],
    acceptance_record_keys: set[tuple[str, int]],
    samples: Sequence[Mapping[str, Any]],
    excluded_sample_ids: Iterable[str],
    excluded_record_keys: Iterable[tuple[str, int]],
) -> dict[str, Any]:
    """Development 与 Blind Acceptance 的零重叠证据，可由命令重算。"""
    development_samples = sorted(sample["sample_id"] for sample in samples)
    development_records = sorted(
        {(sample["dataset_id"], sample["record_index"]) for sample in samples}
    )
    sample_intersection = sorted(set(development_samples) & acceptance_sample_ids)
    record_intersection = [
        {"dataset_id": dataset_id, "record_index": record_index}
        for dataset_id, record_index in sorted(
            set(development_records) & acceptance_record_keys
        )
    ]
    material = {
        "isolated_from": acceptance_manifest.get("manifest_id"),
        "acceptance_content_sha256": (acceptance_manifest.get("integrity") or {}).get(
            "content_sha256"
        ),
        "acceptance_sample_ids": sorted(acceptance_sample_ids),
        "acceptance_records": [
            {"dataset_id": dataset_id, "record_index": record_index}
            for dataset_id, record_index in sorted(acceptance_record_keys)
        ],
        "development_sample_ids": development_samples,
        "development_records": [
            {"dataset_id": dataset_id, "record_index": record_index}
            for dataset_id, record_index in development_records
        ],
        "sample_id_intersection": sample_intersection,
        "record_intersection": record_intersection,
    }
    return {
        "isolated_from": acceptance_manifest.get("manifest_id"),
        "acceptance_content_sha256": material["acceptance_content_sha256"],
        "acceptance_samples": len(acceptance_sample_ids),
        "excluded_candidates": {
            "sample_ids": len(set(excluded_sample_ids)),
            "records": len(set(excluded_record_keys)),
        },
        "sample_id_intersection": sample_intersection,
        "record_intersection": record_intersection,
        "evidence_sha256": sha256_json(material),
    }


def render_readme(manifest: Mapping[str, Any], coverage: Mapping[str, Any]) -> str:
    version = manifest["corpus_version"]
    quotas = manifest["policy"]["quotas"]
    quota_text = "、".join(
        f"{dataset_id} {sum(spec.values())} 条（"
        + "，".join(f"{stratum} {count}" for stratum, count in spec.items())
        + "）"
        for dataset_id, spec in quotas.items()
    )
    lines = [
        f"# {version} · Development Corpus",
        "",
        "本目录是整版 `" + version + "` 的 **Development Corpus** 子集，供课题四"
        "（Provider）反复自测与调试。它由固定版本的 ATBench 与 HINTBench 通过课题五的"
        "白名单投影一次性生成，属私有仓库交付物；全量原始数据仍只保存在固定缓存中。",
        "",
        f"- 正式输入：`{INPUT_FILENAME}`（唯一权威批次，{len(manifest['samples'])} 条）",
        f"- 清单：`{MANIFEST_FILENAME}`",
        f"- 覆盖矩阵：`{COVERAGE_FILENAME}`",
        f"- 自测参考：`{REFERENCE_FILENAME}`",
        f"- corpus 版本：`{version}`；输入 Schema：`{manifest['input_schema_version']}`；"
        f"轨迹 Schema：`{manifest['trajectory_schema_version']}`",
        f"- 批次身份：`{manifest['batch_id']}`",
        f"- 配额：{quota_text}",
        "",
        "## 课题四如何调用",
        "",
        "```bash",
        "topic4-audit run \\",
        f"  --input corpus/{version}/development/{INPUT_FILENAME} \\",
        "  --output <result.json> \\",
        "  --config <config.json>",
        "```",
        "",
        "课题四 CLI 内部可以按需启动在线模型服务，但课题五只依赖这个文件式外部"
        " interface：输入与结果都通过文件交换。",
        "",
        "## 输入字段",
        "",
        "```text",
        "{",
        '  schema_version: "topic4-audit-input/1",',
        "  batch_id,",
        "  items: [{",
        "    sample_id,            # 稳定样本身份，回显到结果即可",
        "    audit_input_sha256,   # 需要回显的关联摘要，由课题五计算",
        "    trajectory: {",
        '      schema_version: "audit-trajectory/1",',
        "      events: [{event_id, sequence, type, actor, source_step_id, content}],",
        "      tool_definitions: [],",
        "      coverage: {event_count, event_types, unsupported_event_ids,",
        "                 source_step_ids, tool_definition_count}",
        "    },",
        "    policy: null",
        "  }]",
        "}",
        "```",
        "",
        "`sample_id` 与 `event_id` 是稳定身份，结果里的证据可以直接指回输入事件。"
        "缺失工具定义与 unsupported 事件集合规范化为空数组 `[]`，不需要猜测 `null` 语义。",
        "",
        "## `policy=null` 的含义",
        "",
        "所有真实样本的 `policy` 都是显式 `null`。**`null` 不表示全部动作均获授权**："
        "它只表示没有额外的授权依据，policy 检查保持“无依据”，判定不会因此变成安全。"
        "本 corpus 不根据来源标签事后补造授权依据。",
        "",
        "## 自测参考怎么用",
        "",
        f"`{REFERENCE_FILENAME}` 保留 Source Label 与来源类别，**不是项目真值**，"
        "也不得作为 provider 推理输入。正确顺序是：先让 provider 只看正式输入产出结果，"
        "再运行后比对参考文件；不要把它拼进 prompt、检索上下文或工具输出。",
        "",
        "## 不包含什么",
        "",
        "- 不含 `requested_taxonomy`：统一风险 taxonomy 尚未冻结，severity 与 confidence 同理。",
        "- 不含 Source Label、Project Reference Annotation、来源 reason、benchmark 身份、"
        "完整原始记录或既有 Detected Finding。",
        "- 不含盲测验收批次：Blind Acceptance Batch（`acceptance-v1` 的 48 条，位于"
        " `../blind-acceptance/`）与本地样本身份和记录身份零重叠，只用于实现冻结后的"
        "首次正式运行，不得用于开发或 prompt 调整。",
        "",
        "## 数据层次免责声明",
        "",
        "- Source Label 是 benchmark 的来源侧事实；项目参考标注是人工确认过的另一层事实；"
        "Detected Finding 是 provider 的检测输出。三者不可互相替代。",
        "- 覆盖矩阵中的“完整”指覆盖矩阵无缺口，不表示穷举字段组合或真实世界分布。",
        "",
        "## 复现",
        "",
        "```bash",
        "python scripts/fetch_datasets.py --verify-only",
        "python -m trajectory_audit corpus development build \\",
        f"  --version {version} --dataset-root .cache/datasets \\",
        "  --acceptance acceptance/acceptance-v1.json \\",
        f"  --out corpus/{version}/development",
        f"python -m trajectory_audit corpus development verify \\",
        f"  --root corpus/{version}/development \\",
        "  --dataset-root .cache/datasets --acceptance acceptance/acceptance-v1.json",
        "```",
        "",
        "整版（含 `../blind-acceptance/`）用 `corpus edition verify` 校验："
        "它会重算两个子集的摘要、交叉零重叠证据与整版内容摘要。",
        "",
        f"覆盖矩阵覆盖 {coverage['schema_version']} 的 "
        f"{len(coverage['dimensions'])} 个维度；重复构建得到相同内容与相同产物摘要。",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 冻结写入
# ---------------------------------------------------------------------------


def write_development_corpus(
    *,
    version: str = DEFAULT_CORPUS_VERSION,
    dataset_root: Path,
    acceptance_manifest: Mapping[str, Any],
    out_dir: Path,
) -> dict[str, Any]:
    """写出冻结 corpus；同版本内容相同报告 `unchanged`，不同则拒绝覆盖。"""
    manifest, artifacts = build_development_corpus(
        version=version,
        dataset_root=dataset_root,
        acceptance_manifest=acceptance_manifest,
    )
    payloads = dict(artifacts)
    payloads[MANIFEST_FILENAME] = manifest

    existing: dict[str, Any] | None = None
    if out_dir.exists():
        manifest_path = out_dir / MANIFEST_FILENAME
        if manifest_path.is_file():
            try:
                loaded = read_json(manifest_path)
            except (OSError, json.JSONDecodeError):
                loaded = None
            existing = loaded if isinstance(loaded, dict) else None
        if existing is None:
            raise RunError(
                "corpus_directory_not_frozen",
                "目标目录已存在但没有可读的 corpus 清单，拒绝覆盖",
                details={"path": str(out_dir)},
                exit_code=3,
            )
        existing_digest = (existing.get("integrity") or {}).get("content_sha256")
        if existing_digest != manifest["integrity"]["content_sha256"]:
            raise _version_conflict(out_dir, version, existing_digest, manifest)
        # 同一选择：沿用已冻结的时间戳，使重复构建得到逐字节相同的清单。
        manifest["created_at"] = existing.get("created_at", manifest["created_at"])
        payloads[MANIFEST_FILENAME] = manifest
        if existing_matches(out_dir, payloads):
            return {
                "status": "unchanged",
                "corpus_version": version,
                "path": str(out_dir),
                "content_sha256": manifest["integrity"]["content_sha256"],
                "samples": len(manifest["samples"]),
            }
        raise _version_conflict(out_dir, version, existing_digest, manifest)

    out_dir.mkdir(parents=True, exist_ok=True)
    write_payloads(out_dir, payloads, manifest_name=MANIFEST_FILENAME)
    return {
        "status": "created",
        "corpus_version": version,
        "path": str(out_dir),
        "content_sha256": manifest["integrity"]["content_sha256"],
        "samples": len(manifest["samples"]),
    }


def _version_conflict(
    out_dir: Path,
    version: str,
    existing_digest: Any,
    manifest: Mapping[str, Any],
) -> RunError:
    return RunError(
        "corpus_version_conflict",
        "同一 corpus 版本已冻结且内容不同；冻结交付物不可原地改写",
        details={
            "path": str(out_dir),
            "corpus_version": version,
            "existing_content_sha256": existing_digest,
            "new_content_sha256": (manifest.get("integrity") or {}).get(
                "content_sha256"
            ),
            "hint": "内容变化必须新建 corpus 版本，而不是覆盖已交付版本",
        },
        exit_code=3,
    )


def _isolation_evidence_hash(
    *,
    acceptance_manifest: Mapping[str, Any],
    samples: Sequence[Mapping[str, Any]],
) -> str:
    """只重算证据摘要；排除计数不属于可重算材料。"""
    acceptance_samples, acceptance_records = identities(
        acceptance_manifest
    )
    return _isolation_evidence(
        acceptance_manifest=acceptance_manifest,
        acceptance_sample_ids=acceptance_samples,
        acceptance_record_keys=acceptance_records,
        samples=samples,
        excluded_sample_ids=[],
        excluded_record_keys=[],
    )["evidence_sha256"]


def verify_development_corpus(
    *,
    root: Path,
    dataset_root: Path | None = None,
    acceptance_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """校验已交付 corpus：产物摘要、Schema、隔离与可重放性。"""
    issues: list[dict[str, str]] = []

    def add(path: str, message: str) -> None:
        issues.append({"path": path, "message": message})

    manifest_path = root / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise RunError(
            "corpus_manifest_not_found",
            "corpus 目录缺少 manifest.json",
            details={"path": str(manifest_path)},
        )
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise RunError(
            "invalid_corpus_manifest",
            "corpus 清单必须是 JSON 对象",
            details={"path": str(manifest_path)},
        )

    if manifest.get("schema_version") != CORPUS_MANIFEST_SCHEMA:
        add("schema_version", f"必须是 {CORPUS_MANIFEST_SCHEMA!r}")
    if manifest.get("subset") != "development":
        add("subset", "开发子集清单必须声明 subset='development'")
    if manifest.get("frozen") is not True:
        add("frozen", "冻结 corpus 必须 frozen=true")
    integrity = manifest.get("integrity")
    content_digest = (
        integrity.get("content_sha256") if isinstance(integrity, dict) else None
    )
    if not isinstance(integrity, dict):
        add("integrity", "integrity 必须是对象")
    elif integrity.get("digest_version") != INTEGRITY_VERSION:
        add("integrity.digest_version", f"必须是 {INTEGRITY_VERSION!r}")
    elif content_digest != content_sha256(manifest):
        add("integrity.content_sha256", "内容哈希与清单内容不一致，清单被改写")

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        add("artifacts", "artifacts 必须是非空数组")
        artifacts = []
    for position, entry in enumerate(artifacts):
        path = f"artifacts[{position}]"
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            add(path, "产物条目必须包含 path")
            continue
        target = root / entry["path"]
        if not target.is_file():
            add(f"{path}.path", f"产物缺失：{entry['path']}")
            continue
        raw = target.read_bytes()
        if len(raw) != entry.get("bytes"):
            add(f"{path}.bytes", f"字节数不一致：期望 {entry.get('bytes')}，实际 {len(raw)}")
        digest = hashlib.sha256(raw).hexdigest()
        if digest != entry.get("sha256"):
            add(f"{path}.sha256", "产物摘要不一致，文件被篡改或损坏")

    # ---- 正式输入 ---------------------------------------------------------
    batch: Any = None
    input_path = root / INPUT_FILENAME
    if input_path.is_file():
        try:
            batch = read_json(input_path)
        except (OSError, json.JSONDecodeError) as exc:
            add(INPUT_FILENAME, f"无法读取正式输入：{exc}")
    else:
        add(INPUT_FILENAME, "缺少正式输入文件")

    if isinstance(batch, dict):
        for issue in validate_audit_input(batch):
            add(f"{INPUT_FILENAME}:{issue['path']}", issue["message"])
        items = batch.get("items")
        if isinstance(items, list):
            expected = len(manifest.get("samples") or [])
            if len(items) != expected:
                add(f"{INPUT_FILENAME}", f"包含 {len(items)} 条，清单记录 {expected} 条")
            declared = {
                sample.get("sample_id"): sample
                for sample in manifest.get("samples") or []
                if isinstance(sample, dict)
            }
            for item in items:
                if not isinstance(item, dict):
                    continue
                sample_id = item.get("sample_id")
                if item.get("policy", None) is not None:
                    add(
                        f"{INPUT_FILENAME}:{sample_id}.policy",
                        "真实样本的 policy 必须显式为 null",
                    )
                row = declared.get(sample_id)
                if row is None:
                    add(f"{INPUT_FILENAME}:{sample_id}", "清单没有声明该样本身份")
                elif row.get("audit_input_sha256") != item.get("audit_input_sha256"):
                    add(
                        f"{INPUT_FILENAME}:{sample_id}.audit_input_sha256",
                        "与清单记录的摘要不一致",
                    )
                if not is_provider_sample_id(sample_id):
                    add(
                        f"{INPUT_FILENAME}:{sample_id}",
                        "样本身份必须是不透明的 corpus 身份，不能是来源 id",
                    )
        forbidden = forbidden_field_hits(batch)
        if forbidden:
            add(f"{INPUT_FILENAME}", f"正式输入出现禁止字段：{forbidden}")

    # ---- 自测参考 ---------------------------------------------------------
    reference_path = root / REFERENCE_FILENAME
    reference: Any = None
    if reference_path.is_file():
        try:
            reference = read_json(reference_path)
        except (OSError, json.JSONDecodeError) as exc:
            add(REFERENCE_FILENAME, f"无法读取自测参考：{exc}")
    else:
        add(REFERENCE_FILENAME, "缺少自测参考文件")
    if isinstance(reference, dict):
        if reference.get("schema_version") != REFERENCE_SCHEMA:
            add(f"{REFERENCE_FILENAME}.schema_version", f"必须是 {REFERENCE_SCHEMA!r}")
        if reference.get("is_project_truth") is not False:
            add(
                f"{REFERENCE_FILENAME}.is_project_truth",
                "来源标签不是项目真值，必须显式声明 false",
            )
        reference_samples = {
            sample.get("sample_id")
            for sample in reference.get("samples") or []
            if isinstance(sample, dict)
        }
        manifest_samples = {
            sample.get("sample_id") for sample in manifest.get("samples") or []
        }
        if reference_samples != manifest_samples:
            add(
                f"{REFERENCE_FILENAME}.samples",
                "自测参考的样本集合必须与正式输入完全一致",
            )
    else:
        reference_samples = set()

    # ---- 覆盖矩阵 ---------------------------------------------------------
    coverage_path = root / COVERAGE_FILENAME
    coverage: Any = None
    if coverage_path.is_file():
        try:
            coverage = read_json(coverage_path)
        except (OSError, json.JSONDecodeError) as exc:
            add(COVERAGE_FILENAME, f"无法读取覆盖矩阵：{exc}")
    else:
        add(COVERAGE_FILENAME, "缺少覆盖矩阵")
    if isinstance(coverage, dict):
        if coverage.get("schema_version") != COVERAGE_SCHEMA:
            add(f"{COVERAGE_FILENAME}.schema_version", f"必须是 {COVERAGE_SCHEMA!r}")
        for block in coverage.get("dimensions") or []:
            if not isinstance(block, dict):
                add(f"{COVERAGE_FILENAME}.dimensions", "覆盖维度必须是对象")
                continue
            if block.get("uncovered"):
                add(
                    f"{COVERAGE_FILENAME}:{block.get('id')}",
                    f"声明的取值没有任何样本：{block['uncovered']}",
                )

    # ---- 与 Blind Acceptance 的隔离 ---------------------------------------
    if acceptance_manifest is not None:
        acceptance_samples, acceptance_records = identities(
            acceptance_manifest
        )
        development_sample_ids = {
            sample.get("sample_id") for sample in manifest.get("samples") or []
        }
        development_records = {
            (sample.get("dataset_id"), sample.get("record_index"))
            for sample in manifest.get("samples") or []
        }
        if development_sample_ids & acceptance_samples:
            add(
                "isolation.sample_id_intersection",
                "Development 与 Blind Acceptance 的样本身份出现交集",
            )
        if development_records & acceptance_records:
            add(
                "isolation.record_intersection",
                "Development 与 Blind Acceptance 的记录身份出现交集",
            )
        isolation = manifest.get("isolation")
        if not isinstance(isolation, dict):
            add("isolation", "清单缺少与 Blind Acceptance 的隔离证据")
        else:
            if isolation.get("sample_id_intersection") or isolation.get(
                "record_intersection"
            ):
                add("isolation", "清单记录的隔离证据不是零重叠")
            if isolation.get("acceptance_content_sha256") != (
                acceptance_manifest.get("integrity") or {}
            ).get("content_sha256"):
                add(
                    "isolation.acceptance_content_sha256",
                    "隔离证据引用的验收清单与传入的 acceptance-v1 不一致",
                )
            expected_evidence = _isolation_evidence_hash(
                acceptance_manifest=acceptance_manifest,
                samples=manifest.get("samples") or [],
            )
            if isolation.get("evidence_sha256") != expected_evidence:
                add(
                    "isolation.evidence_sha256",
                    "隔离证据摘要无法重算，证据已被改写",
                )

    # ---- 可重放性 ---------------------------------------------------------
    reproducible = "skipped"
    cache_present = dataset_root is not None and all(
        (dataset_root / pinned.pinned(source["dataset_id"])["cache_name"]).is_file()
        for source in manifest.get("sources") or []
        if isinstance(source, dict) and source.get("dataset_id") in pinned.BY_DATASET
    )
    if not manifest.get("sources"):
        add("sources", "清单必须声明来源固定版本")
    elif dataset_root is not None and acceptance_manifest is not None:
        if not cache_present:
            add("sources", "固定数据缓存不完整，无法执行全量重建比对")
        else:
            try:
                rebuilt, _ = build_development_corpus(
                    version=manifest["corpus_version"],
                    dataset_root=dataset_root,
                    acceptance_manifest=acceptance_manifest,
                )
            except RunError as exc:
                add(
                    "integrity.content_sha256",
                    f"用固定缓存重建失败：{exc.code}",
                )
                reproducible = "error"
            else:
                if rebuilt["integrity"]["content_sha256"] != content_digest:
                    add(
                        "integrity.content_sha256",
                        "用固定缓存重建得到不同内容；corpus 不可重放",
                    )
                    reproducible = "mismatch"
                else:
                    reproducible = "verified"

    checks = {
        "artifacts_intact": not any(
            issue["path"].startswith("artifacts[") for issue in issues
        ),
        "input_schema_valid": not any(
            issue["path"].startswith(INPUT_FILENAME) for issue in issues
        ),
        "coverage_complete": not any(
            issue["path"].startswith(COVERAGE_FILENAME) for issue in issues
        ),
        "isolated_from_acceptance": not any(
            issue["path"].startswith("isolation") for issue in issues
        ),
        "reproducibility_verified": reproducible == "verified",
    }
    return {
        "status": "pass" if not issues else "fail",
        "corpus_version": manifest.get("corpus_version"),
        "samples": len(manifest.get("samples") or []),
        "content_sha256": content_digest,
        "checks": checks,
        "reproducible": reproducible,
        "issues": issues,
    }


__all__ = [
    "SUPPORTING_ARTIFACTS",
    "COVERAGE_FILENAME",
    "COVERAGE_SCHEMA",
    "CORPUS_MANIFEST_SCHEMA",
    "DEFAULT_CORPUS_VERSION",
    "FORBIDDEN_INPUT_KEYS",
    "INPUT_FILENAME",
    "MANIFEST_FILENAME",
    "README_FILENAME",
    "REFERENCE_FILENAME",
    "REFERENCE_SCHEMA",
    "build_development_corpus",
    "corpus_content_sha256",
    "corpus_policy",
    "render_readme",
    "verify_development_corpus",
    "write_development_corpus",
]
