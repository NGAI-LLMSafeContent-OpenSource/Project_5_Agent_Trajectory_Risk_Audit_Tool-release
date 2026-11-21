# SPDX-License-Identifier: Apache-2.0
"""Blind Acceptance Batch：冻结验收样本的无标签正式输入。

本模块把 ``acceptance-v1`` 已冻结的 48 条样本投影成 ``topic4-audit-input/1``
批次。它与 Development Corpus 共用同一白名单投影、同一规范事件模型和同一套
不透明样本身份，区别只在职责：

- Development Corpus 供课题四反复自测与调试；
- Blind Acceptance Batch 只在实现冻结后用于首次正式运行，不得用于 prompt 调整；
- 两者样本身份与记录身份零重叠，且都不含来源标签、项目参考标注或预期结果。

验收批次保留 acceptance-v1 的样本身份与记录身份作为映射证据，但 provider 可见
的 ``sample_id`` 仍是不可反推来源的不透明身份；映射留在 manifest 里。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

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
    normalize_record,
    provider_sample_id,
    source_label,
    write_payloads,
)
from .errors import RunError
from .model import sha256_json

SUBSET_ID = "blind_acceptance"
SUBSET_DIR = "blind-acceptance"
#: 子集清单与 Development Corpus 使用同一个清单 Schema，便于整版统一重算。
BLIND_MANIFEST_SCHEMA = "provider-corpus-manifest/1"
BLIND_REFERENCE_SCHEMA = "provider-corpus-reference/1"
USAGE_NOTICE = (
    "本批次是 Blind Acceptance Batch：不得用于课题四开发、prompt 调整或规则调试。"
    "正式运行前必须冻结 provider、model、prompt 与 config revision，并与其他"
    "provider 各自产生独立 run。"
)


def _acceptance_records(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    samples = manifest.get("samples")
    if not isinstance(samples, list) or not samples:
        raise RunError(
            "corpus_source_invalid",
            "验收清单没有可用的样本，无法生成 Blind Acceptance Batch",
            details={"manifest_id": manifest.get("manifest_id")},
        )
    return sorted(
        (sample for sample in samples if isinstance(sample, dict)),
        key=lambda sample: (sample.get("dataset_id"), sample.get("record_index")),
    )


def _validate_acceptance(manifest: Mapping[str, Any]) -> None:
    """校验验收清单；已注册的冻结清单走完整校验，玩具/测试清单只查结构。"""
    manifest_id = manifest.get("manifest_id")
    if manifest_id in pinned.ACCEPTANCE_POLICIES:
        issues = acceptance.validate_manifest(manifest, expected_id=manifest_id)
        if issues:
            raise RunError(
                "corpus_source_invalid",
                "验收清单自身不自洽，拒绝在其上生成 Blind Acceptance Batch",
                details={"manifest_id": manifest_id, "issues": issues[:10]},
            )
    for position, sample in enumerate(_acceptance_records(manifest)):
        missing = [
            field
            for field in ("sample_id", "dataset_id", "record_index", "record_sha256")
            if sample.get(field) in (None, "")
        ]
        if missing:
            raise RunError(
                "corpus_source_invalid",
                "验收样本缺少生成盲测输入所需的身份或记录摘要",
                details={
                    "manifest_id": manifest_id,
                    "position": position,
                    "missing": missing,
                },
            )


def _load_records(
    dataset_root: Path, dataset_id: str
) -> tuple[list[Any], dict[str, Any], dict[str, Any]]:
    entry = pinned.pinned(dataset_id)
    path = acceptance.dataset_path(dataset_root, dataset_id)
    file_check = acceptance.verify_dataset_file(dataset_root, dataset_id)
    if not file_check["ok"]:
        raise RunError(
            "dataset_hash_mismatch" if file_check["present"] else "dataset_not_found",
            "数据集文件与固定清单不一致或不存在，拒绝在其上生成验收批次",
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
    return records, entry, file_check


def _disjoint_evidence(
    *,
    development_manifest: Mapping[str, Any],
    samples: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    development_samples, development_records = identities(development_manifest)
    blind_samples, blind_records = identities({"samples": list(samples)})
    sample_intersection = sorted(blind_samples & development_samples)
    record_intersection = [
        {"dataset_id": dataset_id, "record_index": record_index}
        for dataset_id, record_index in sorted(blind_records & development_records)
    ]
    material = {
        "development_content_sha256": (
            development_manifest.get("integrity") or {}
        ).get("content_sha256"),
        "blind_sample_ids": sorted(blind_samples),
        "blind_records": [
            {"dataset_id": dataset_id, "record_index": record_index}
            for dataset_id, record_index in sorted(blind_records)
        ],
        "sample_id_intersection": sample_intersection,
        "record_intersection": record_intersection,
    }
    return {
        "development_content_sha256": material["development_content_sha256"],
        "sample_id_intersection": sample_intersection,
        "record_intersection": record_intersection,
        "evidence_sha256": sha256_json(material),
    }


def _acceptance_equality(
    *, acceptance_manifest: Mapping[str, Any], samples: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    acceptance_samples, acceptance_records = identities(acceptance_manifest)
    blind_samples = {
        row["source_sample_id"]
        for row in samples
        if isinstance(row, Mapping) and isinstance(row.get("source_sample_id"), str)
    }
    blind_records = {
        (row.get("dataset_id"), row.get("record_index"))
        for row in samples
        if isinstance(row, Mapping)
    }
    material = {
        "manifest_id": acceptance_manifest.get("manifest_id"),
        "acceptance_content_sha256": (
            acceptance_manifest.get("integrity") or {}
        ).get("content_sha256"),
        "acceptance_sample_ids": sorted(acceptance_samples),
        "acceptance_records": [
            {"dataset_id": dataset_id, "record_index": record_index}
            for dataset_id, record_index in sorted(acceptance_records)
        ],
        "blind_sample_ids": sorted(blind_samples),
        "blind_records": [
            {"dataset_id": dataset_id, "record_index": record_index}
            for dataset_id, record_index in sorted(blind_records)
        ],
    }
    return {
        "manifest_id": material["manifest_id"],
        "acceptance_content_sha256": material["acceptance_content_sha256"],
        "sample_identity_equal": blind_samples == acceptance_samples,
        "record_identity_equal": blind_records == acceptance_records,
        "samples": len(blind_samples),
        "evidence_sha256": sha256_json(material),
    }


def build_blind_acceptance(
    *,
    version: str,
    dataset_root: Path,
    acceptance_manifest: Mapping[str, Any],
    development_manifest: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """生成 Blind Acceptance Batch 的清单与全部产物。

    返回 ``(manifest, artifacts)``。任何验收身份、记录摘要或隔离关系的漂移都会
    抛出 ``RunError``，不会产出一份“看起来能跑”的验收批次。
    """
    _validate_acceptance(acceptance_manifest)
    claimed = _acceptance_records(acceptance_manifest)

    cache: dict[str, tuple[list[Any], dict[str, Any], dict[str, Any]]] = {}
    items: list[dict[str, Any]] = []
    sample_rows: list[dict[str, Any]] = []
    derived_labels: dict[str, Any] = {}

    for sample in claimed:
        dataset_id = sample.get("dataset_id")
        record_index = sample.get("record_index")
        source_sample_id = sample.get("sample_id")
        if not isinstance(dataset_id, str) or not isinstance(record_index, int):
            raise RunError(
                "corpus_source_invalid",
                "验收样本缺少可定位的记录身份",
                details={"sample_id": source_sample_id},
            )
        if dataset_id not in cache:
            cache[dataset_id] = _load_records(dataset_root, dataset_id)
        records, entry, file_check = cache[dataset_id]
        if not 0 <= record_index < len(records):
            raise RunError(
                "corpus_source_invalid",
                "验收样本引用了固定数据中不存在的记录",
                details={"dataset_id": dataset_id, "record_index": record_index},
            )
        record = records[record_index]
        record_sha256 = sha256_json(record)
        if record_sha256 != sample.get("record_sha256"):
            raise RunError(
                "corpus_source_invalid",
                "固定数据中的记录摘要与验收清单不一致；数据或清单已被改动",
                details={
                    "dataset_id": dataset_id,
                    "record_index": record_index,
                    "expected_record_sha256": sample.get("record_sha256"),
                    "actual_record_sha256": record_sha256,
                },
            )
        adapter = get_adapter(entry["adapter_id"])
        declared, _ = adapter.sample_identity(record)
        declared = declared or f"record-{record_index:04d}"
        if declared != source_sample_id:
            raise RunError(
                "corpus_source_invalid",
                "固定数据中的样本身份与验收清单不一致",
                details={
                    "dataset_id": dataset_id,
                    "record_index": record_index,
                    "expected_sample_id": source_sample_id,
                    "actual_sample_id": declared,
                },
            )

        trajectory = normalize_record(
            adapter,
            record,
            record_index=record_index,
            dataset_id=dataset_id,
            dataset_version=entry["dataset_version"],
            file_sha256=file_check["sha256"],
            record_sha256=record_sha256,
            sample_id=source_sample_id,
        )
        opaque_id = provider_sample_id(dataset_id, record_index, record_sha256)
        # provider 只看不透明身份；来源身份留在清单的来源侧字段里。
        item = canonical_item(replace(trajectory, sample_id=opaque_id), None)
        items.append(item)
        derived_labels[opaque_id] = source_label(
            record, dataset_id, str(sample.get("stratum"))
        )
        sample_rows.append(
            {
                "sample_id": opaque_id,
                "source_sample_id": source_sample_id,
                "dataset_id": dataset_id,
                "record_index": record_index,
                "record_sha256": record_sha256,
                "stratum": sample.get("stratum"),
                "substratum": sample.get("substratum"),
                "audit_input_sha256": item["audit_input_sha256"],
            }
        )

    sample_rows.sort(key=lambda row: row["sample_id"])
    items.sort(key=lambda item: item["sample_id"])
    batch = build_batch(items)
    issues = validate_audit_input(batch)
    if issues:
        raise RunError(
            "corpus_invalid",
            "生成的 Blind Acceptance Batch 未通过 topic4-audit-input/1 校验",
            details={"corpus_version": version, "issues": issues[:10]},
        )
    leaked = forbidden_field_hits(batch)
    if leaked:
        raise RunError(
            "corpus_input_leak",
            "验收批次的结构层出现禁止字段",
            details={"corpus_version": version, "fields": leaked},
        )
    for item in items:
        if not is_provider_sample_id(item["sample_id"]):
            raise RunError(
                "corpus_input_leak",
                "验收批次的样本身份不是不透明 corpus 身份",
                details={"sample_id": item["sample_id"]},
            )

    equality = _acceptance_equality(
        acceptance_manifest=acceptance_manifest, samples=sample_rows
    )
    if not equality["sample_identity_equal"] or not equality["record_identity_equal"]:
        raise RunError(
            "corpus_source_invalid",
            "Blind Acceptance Batch 与验收清单的样本集合不一致",
            details={"relation": equality},
        )
    disjoint = _disjoint_evidence(
        development_manifest=development_manifest, samples=sample_rows
    )
    if disjoint["sample_id_intersection"] or disjoint["record_intersection"]:
        raise RunError(
            "corpus_overlap_detected",
            "Blind Acceptance Batch 与 Development Corpus 出现身份重叠",
            details={"relation": disjoint},
        )

    reference_samples = [
        {
            "sample_id": row["sample_id"],
            "source_sample_id": row["source_sample_id"],
            "dataset_id": row["dataset_id"],
            "record_index": row["record_index"],
            "record_sha256": row["record_sha256"],
            "source_label": derived_labels.get(row["sample_id"]),
        }
        for row in sample_rows
    ]
    reference_payload = {
        "schema_version": BLIND_REFERENCE_SCHEMA,
        "corpus_version": version,
        "subset": SUBSET_ID,
        "notice": REFERENCE_NOTICE,
        "is_project_truth": False,
        "source_labels_are_benchmark_facts": True,
        "samples": reference_samples,
    }

    artifacts: dict[str, Any] = {
        INPUT_FILENAME: batch,
        REFERENCE_FILENAME: reference_payload,
    }

    manifest: dict[str, Any] = {
        "schema_version": BLIND_MANIFEST_SCHEMA,
        "subset": SUBSET_ID,
        "corpus_version": version,
        "created_at": now(),
        "frozen": True,
        "input_schema_version": AUDIT_INPUT_SCHEMA,
        "trajectory_schema_version": AUDIT_TRAJECTORY_SCHEMA,
        "batch_id": batch["batch_id"],
        "usage": {
            "purpose": "blind_acceptance",
            "development_allowed": False,
            "prompt_tuning_allowed": False,
            "freeze_before_run": ["provider", "model", "prompt", "config_revision"],
            "notice": USAGE_NOTICE,
        },
        "source_manifest": {
            "manifest_id": acceptance_manifest.get("manifest_id"),
            "content_sha256": equality["acceptance_content_sha256"],
            "samples": equality["samples"],
        },
        "samples": sample_rows,
        "relations": {
            "equals_acceptance": equality,
            "disjoint_from_development": disjoint,
        },
        "notes": [
            "本批次不进入课题四的开发与调试流程，只用于实现冻结后的首次正式运行。",
            "来源标签与来源类别留在 self-check-reference.json，不进入推理输入。",
            "验收批次的 sample_id 仍是不透明 corpus 身份；来源身份只出现在清单里。",
        ],
        "artifacts": [],
        "integrity": {},
    }
    readme = render_readme(manifest)
    artifacts[README_FILENAME] = readme
    manifest["artifacts"] = artifact_digests(artifacts)
    manifest["integrity"] = {
        "digest_version": INTEGRITY_VERSION,
        "content_sha256": content_sha256(manifest),
    }
    return manifest, artifacts


def render_readme(manifest: Mapping[str, Any]) -> str:
    version = manifest["corpus_version"]
    samples = len(manifest["samples"])
    source = manifest["source_manifest"]
    return "\n".join(
        [
            f"# Blind Acceptance Batch · {version}",
            "",
            "本目录是课题四（Provider）**盲测验收批次**：由冻结的"
            f" `{source['manifest_id']}` 的 {samples} 条样本通过课题五的白名单投影生成。",
            "它与 Development Corpus 样本身份和记录身份零重叠，不得合并使用。",
            "",
            "## 使用边界",
            "",
            "- 本批次**不用于**课题四开发、prompt 调整、规则调试或样例挑选。",
            "- 正式运行前必须冻结 provider、model、prompt 与 config revision；"
            "冻结之后本批次只用于首次正式运行。",
            "- 正式课题四 provider 与 deterministic baseline 必须各自产生独立 run；"
            "provider 失败时不得静默用 baseline 结果替代。",
            "- 只有在 transport/文件故障且没有产生可靠语义结果时，才允许用相同 revision"
            "重跑；一旦完整结果可见，任何实现变化都必须产生新 revision 与新 run，"
            "首次结果不得被覆盖。",
            "",
            "## 输入",
            "",
            f"- 正式输入：`{INPUT_FILENAME}`（{samples} 条，`topic4-audit-input/1`）",
            f"- 自测参考：`{REFERENCE_FILENAME}`（运行后再比对，**不是项目真值**）",
            f"- 批次身份：`{manifest['batch_id']}`",
            f"- 来源清单：`{source['manifest_id']}`（内容摘要 `{source['content_sha256']}`）",
            "",
            "```bash",
            "topic4-audit run \\",
            f"  --input corpus/{version}/{SUBSET_DIR}/{INPUT_FILENAME} \\",
            "  --output <result.json> \\",
            "  --config <config.json>",
            "```",
            "",
            "## 样本身份",
            "",
            "provider 只看到不透明的 `sample-<16 hex>` 身份。验收清单里的来源"
            "样本身份与记录身份保存在 `manifest.json` 的 `samples[].source_sample_id` /"
            " `samples[].dataset_id` / `samples[].record_index` 中，用于运行后把结果"
            "关联回验收参考；这些字段不进入推理输入。",
            "",
            "## `policy=null` 的含义",
            "",
            "所有真实样本的 `policy` 都是显式 `null`：它表示没有额外授权依据，"
            "不表示全部动作均获授权。本批次不会根据来源标签事后补造授权依据。",
            "",
            "## 复现与校验",
            "",
            "```bash",
            "python -m trajectory_audit corpus edition verify \\",
            f"  --root corpus/{version} --dataset-root .cache/datasets \\",
            "  --acceptance acceptance/acceptance-v1.json",
            "```",
            "",
        ]
    )


def write_blind_acceptance(
    *,
    version: str,
    dataset_root: Path,
    acceptance_manifest: Mapping[str, Any],
    development_manifest: Mapping[str, Any],
    out_dir: Path,
) -> dict[str, Any]:
    """写出冻结子集；同内容报告 `unchanged`，内容不同拒绝覆盖。"""
    manifest, artifacts = build_blind_acceptance(
        version=version,
        dataset_root=dataset_root,
        acceptance_manifest=acceptance_manifest,
        development_manifest=development_manifest,
    )
    payloads = dict(artifacts)
    payloads[MANIFEST_FILENAME] = manifest

    if out_dir.exists():
        existing = _read_existing_manifest(out_dir)
        if existing is None:
            raise RunError(
                "corpus_directory_not_frozen",
                "目标目录已存在但没有可读的 corpus 清单，拒绝覆盖",
                details={"path": str(out_dir)},
                exit_code=3,
            )
        existing_digest = (existing.get("integrity") or {}).get("content_sha256")
        if existing_digest != manifest["integrity"]["content_sha256"]:
            raise _conflict(out_dir, version, existing_digest, manifest)
        manifest["created_at"] = existing.get("created_at", manifest["created_at"])
        payloads[MANIFEST_FILENAME] = manifest
        if existing_matches(out_dir, payloads):
            return {
                "status": "unchanged",
                "subset": SUBSET_ID,
                "corpus_version": version,
                "path": str(out_dir),
                "content_sha256": manifest["integrity"]["content_sha256"],
                "samples": len(manifest["samples"]),
            }
        raise _conflict(out_dir, version, existing_digest, manifest)

    write_payloads(out_dir, payloads, manifest_name=MANIFEST_FILENAME)
    return {
        "status": "created",
        "subset": SUBSET_ID,
        "corpus_version": version,
        "path": str(out_dir),
        "content_sha256": manifest["integrity"]["content_sha256"],
        "samples": len(manifest["samples"]),
    }


def _read_existing_manifest(out_dir: Path) -> dict[str, Any] | None:
    path = out_dir / MANIFEST_FILENAME
    if not path.is_file():
        return None
    try:
        loaded = read_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _conflict(
    out_dir: Path,
    version: str,
    existing_digest: Any,
    manifest: Mapping[str, Any],
) -> RunError:
    return RunError(
        "corpus_version_conflict",
        "同一 corpus 版本的验收批次已冻结且内容不同；冻结交付物不可原地改写",
        details={
            "path": str(out_dir),
            "corpus_version": version,
            "subset": SUBSET_ID,
            "existing_content_sha256": existing_digest,
            "new_content_sha256": (manifest.get("integrity") or {}).get("content_sha256"),
            "hint": "内容变化必须新建 corpus 版本，而不是覆盖已交付版本",
        },
        exit_code=3,
    )


def verify_blind_acceptance(
    *,
    root: Path,
    dataset_root: Path | None = None,
    acceptance_manifest: Mapping[str, Any] | None = None,
    development_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """校验已交付的验收子集：摘要、Schema、身份关系与可重放性。"""
    issues: list[dict[str, str]] = []

    def add(path: str, message: str) -> None:
        issues.append({"path": path, "message": message})

    manifest_path = root / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise RunError(
            "corpus_manifest_not_found",
            "验收子集缺少 manifest.json",
            details={"path": str(manifest_path)},
        )
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise RunError(
            "invalid_corpus_manifest",
            "corpus 清单必须是 JSON 对象",
            details={"path": str(manifest_path)},
        )

    if manifest.get("schema_version") != BLIND_MANIFEST_SCHEMA:
        add("schema_version", f"必须是 {BLIND_MANIFEST_SCHEMA!r}")
    if manifest.get("subset") != SUBSET_ID:
        add("subset", f"必须是 {SUBSET_ID!r}")
    if manifest.get("frozen") is not True:
        add("frozen", "冻结子集必须 frozen=true")
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
        if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
            add(f"{path}.sha256", "产物摘要不一致，文件被篡改或损坏")

    usage = manifest.get("usage")
    if not isinstance(usage, dict):
        add("usage", "清单必须声明使用边界")
    else:
        if usage.get("development_allowed") is not False:
            add("usage.development_allowed", "盲测批次不得允许用于开发")
        if usage.get("prompt_tuning_allowed") is not False:
            add("usage.prompt_tuning_allowed", "盲测批次不得允许用于 prompt 调整")
        if sorted(usage.get("freeze_before_run") or []) != sorted(
            ["config_revision", "model", "prompt", "provider"]
        ):
            add("usage.freeze_before_run", "正式运行前必须冻结 provider/model/prompt/config")

    batch: Any = None
    input_path = root / INPUT_FILENAME
    if input_path.is_file():
        try:
            batch = read_json(input_path)
        except (OSError, ValueError) as exc:
            add(INPUT_FILENAME, f"无法读取正式输入：{exc}")
    else:
        add(INPUT_FILENAME, "缺少正式输入文件")

    if isinstance(batch, dict):
        for issue in validate_audit_input(batch):
            add(f"{INPUT_FILENAME}:{issue['path']}", issue["message"])
        items = batch.get("items")
        expected = len(manifest.get("samples") or [])
        if isinstance(items, list):
            if len(items) != expected:
                add(INPUT_FILENAME, f"包含 {len(items)} 条，清单记录 {expected} 条")
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
                    add(f"{INPUT_FILENAME}:{sample_id}.policy", "真实样本的 policy 必须显式为 null")
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
        leaked = forbidden_field_hits(batch)
        if leaked:
            add(INPUT_FILENAME, f"正式输入出现禁止字段：{leaked}")

    reference_path = root / REFERENCE_FILENAME
    reference: Any = None
    if reference_path.is_file():
        try:
            reference = read_json(reference_path)
        except (OSError, ValueError) as exc:
            add(REFERENCE_FILENAME, f"无法读取自测参考：{exc}")
    else:
        add(REFERENCE_FILENAME, "缺少自测参考文件")
    if isinstance(reference, dict):
        if reference.get("schema_version") != BLIND_REFERENCE_SCHEMA:
            add(f"{REFERENCE_FILENAME}.schema_version", f"必须是 {BLIND_REFERENCE_SCHEMA!r}")
        if reference.get("is_project_truth") is not False:
            add(f"{REFERENCE_FILENAME}.is_project_truth", "来源标签不是项目真值，必须显式声明 false")
        reference_samples = {
            sample.get("sample_id")
            for sample in reference.get("samples") or []
            if isinstance(sample, dict)
        }
        manifest_samples = {
            sample.get("sample_id") for sample in manifest.get("samples") or []
        }
        if reference_samples != manifest_samples:
            add(f"{REFERENCE_FILENAME}.samples", "自测参考的样本集合必须与正式输入完全一致")

    relations = manifest.get("relations")
    if not isinstance(relations, dict):
        add("relations", "清单必须声明与验收清单、开发子集的关系")
        relations = {}

    equality = relations.get("equals_acceptance")
    if not isinstance(equality, dict):
        add("relations.equals_acceptance", "缺少与验收清单的身份关系证据")
    else:
        if equality.get("sample_identity_equal") is not True:
            add("relations.equals_acceptance.sample_identity_equal", "样本身份必须与验收清单完全一致")
        if equality.get("record_identity_equal") is not True:
            add("relations.equals_acceptance.record_identity_equal", "记录身份必须与验收清单完全一致")
        if acceptance_manifest is not None:
            expected = _acceptance_equality(
                acceptance_manifest=acceptance_manifest,
                samples=manifest.get("samples") or [],
            )
            if equality.get("evidence_sha256") != expected["evidence_sha256"]:
                add("relations.equals_acceptance.evidence_sha256", "身份关系证据无法重算，证据已被改写")
            if expected["sample_identity_equal"] is False or expected[
                "record_identity_equal"
            ] is False:
                add("relations.equals_acceptance", "与传入的验收清单不一致")

    disjoint = relations.get("disjoint_from_development")
    if not isinstance(disjoint, dict):
        add("relations.disjoint_from_development", "缺少与开发子集的零重叠证据")
    else:
        if disjoint.get("sample_id_intersection") or disjoint.get("record_intersection"):
            add("relations.disjoint_from_development", "与开发子集出现身份重叠")
        if development_manifest is not None:
            expected = _disjoint_evidence(
                development_manifest=development_manifest,
                samples=manifest.get("samples") or [],
            )
            if expected["sample_id_intersection"] or expected["record_intersection"]:
                add("relations.disjoint_from_development", "与开发子集出现身份重叠")
            if disjoint.get("evidence_sha256") != expected["evidence_sha256"]:
                add(
                    "relations.disjoint_from_development.evidence_sha256",
                    "零重叠证据无法重算，证据已被改写",
                )

    reproducible = "skipped"
    cache_present = dataset_root is not None and all(
        (dataset_root / pinned.pinned(dataset_id)["cache_name"]).is_file()
        for dataset_id in {
            sample.get("dataset_id")
            for sample in manifest.get("samples") or []
            if isinstance(sample, dict)
        }
        if dataset_id in pinned.BY_DATASET
    )
    if dataset_root is not None and acceptance_manifest is not None and development_manifest is not None:
        if not cache_present:
            add("sources", "固定数据缓存不完整，无法执行全量重建比对")
        else:
            try:
                rebuilt, _ = build_blind_acceptance(
                    version=manifest["corpus_version"],
                    dataset_root=dataset_root,
                    acceptance_manifest=acceptance_manifest,
                    development_manifest=development_manifest,
                )
            except RunError as exc:
                add("integrity.content_sha256", f"用固定缓存重建失败：{exc.code}")
                reproducible = "error"
            else:
                if rebuilt["integrity"]["content_sha256"] != content_digest:
                    add("integrity.content_sha256", "用固定缓存重建得到不同内容；子集不可重放")
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
        "identity_relation_ok": not any(
            issue["path"].startswith("relations") for issue in issues
        ),
        "usage_boundary_declared": not any(
            issue["path"].startswith("usage") for issue in issues
        ),
        "reproducibility_verified": reproducible == "verified",
    }
    return {
        "status": "pass" if not issues else "fail",
        "subset": SUBSET_ID,
        "samples": len(manifest.get("samples") or []),
        "content_sha256": content_digest,
        "checks": checks,
        "reproducible": reproducible,
        "issues": issues,
    }


__all__ = [
    "BLIND_MANIFEST_SCHEMA",
    "BLIND_REFERENCE_SCHEMA",
    "SUBSET_DIR",
    "SUBSET_ID",
    "USAGE_NOTICE",
    "build_blind_acceptance",
    "render_readme",
    "verify_blind_acceptance",
    "write_blind_acceptance",
]
