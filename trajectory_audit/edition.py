# SPDX-License-Identifier: Apache-2.0
"""整版 Provider Corpus：Development 与 Blind Acceptance 两个子集的封套。

``topic4-provider-corpus-v1`` 是一个**整版**交付目录，同时包含：

- ``development/``：80 条开发自测批次；
- ``blind-acceptance/``：48 条盲测验收批次；
- ``manifest.json``：整版清单，记录两个子集的数量、Schema 版本、批次身份、
  文件摘要、内容摘要与关联关系；
- ``README.md``：三层数据（Development / Blind Acceptance / 后续 Provider
  Contract Test Suite）的职责与运行边界。

整版清单不复制子集里的逐条样本，只声明子集的产物身份与关系证据；逐条映射
留在各子集的 ``manifest.json`` 中。验证命令会重算两个子集的摘要、交叉零重叠
证据与整版内容摘要，因此任何手工改写或内容漂移都会被检出。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from . import acceptance, blind_acceptance, development, pinned
from .artifacts import now, read_json
from .corpus_common import (
    INPUT_FILENAME,
    INTEGRITY_VERSION,
    MANIFEST_FILENAME,
    README_FILENAME,
    artifact_digests,
    content_sha256,
    identities,
    json_bytes,
)
from .errors import RunError
from .model import sha256_json

EDITION_SCHEMA = "provider-corpus-edition/1"

DEVELOPMENT_DIR = "development"
BLIND_DIR = blind_acceptance.SUBSET_DIR

#: 整版 README 必须说明的三层数据与运行边界。
README_REQUIRED_TOKENS = (
    "Development Corpus",
    "Blind Acceptance Batch",
    "Provider Contract Test Suite",
    "deterministic-baseline",
    "独立",
    "revision",
    "不得覆盖",
)


def _subset_entry(
    *,
    subset_id: str,
    directory: str,
    manifest: Mapping[str, Any],
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    input_payload = artifacts[INPUT_FILENAME]
    input_raw = json_bytes(input_payload)
    entry: dict[str, Any] = {
        "id": subset_id,
        "path": directory,
        "manifest": f"{directory}/{MANIFEST_FILENAME}",
        "input": f"{directory}/{INPUT_FILENAME}",
        "manifest_schema": manifest["schema_version"],
        "input_schema_version": manifest["input_schema_version"],
        "trajectory_schema_version": manifest["trajectory_schema_version"],
        "samples": len(manifest["samples"]),
        "batch_id": manifest["batch_id"],
        "input_sha256": hashlib.sha256(input_raw).hexdigest(),
        "content_sha256": manifest["integrity"]["content_sha256"],
    }
    if subset_id == blind_acceptance.SUBSET_ID:
        entry["source_manifest"] = {
            "manifest_id": manifest["source_manifest"]["manifest_id"],
            "content_sha256": manifest["source_manifest"]["content_sha256"],
        }
    return entry


def _cross_relation(
    *, development_manifest: Mapping[str, Any], blind_manifest: Mapping[str, Any]
) -> dict[str, Any]:
    development_samples, development_records = identities(development_manifest)
    blind_samples, blind_records = identities(blind_manifest)
    sample_intersection = sorted(development_samples & blind_samples)
    record_intersection = [
        {"dataset_id": dataset_id, "record_index": record_index}
        for dataset_id, record_index in sorted(development_records & blind_records)
    ]
    material = {
        "subsets": ["development", blind_acceptance.SUBSET_ID],
        "development_content_sha256": (
            development_manifest.get("integrity") or {}
        ).get("content_sha256"),
        "blind_content_sha256": (blind_manifest.get("integrity") or {}).get(
            "content_sha256"
        ),
        "development_sample_ids": sorted(development_samples),
        "development_records": [
            {"dataset_id": dataset_id, "record_index": record_index}
            for dataset_id, record_index in sorted(development_records)
        ],
        "blind_sample_ids": sorted(blind_samples),
        "blind_records": [
            {"dataset_id": dataset_id, "record_index": record_index}
            for dataset_id, record_index in sorted(blind_records)
        ],
        "sample_id_intersection": sample_intersection,
        "record_intersection": record_intersection,
    }
    return {
        "subsets": material["subsets"],
        "development_content_sha256": material["development_content_sha256"],
        "blind_content_sha256": material["blind_content_sha256"],
        "sample_id_intersection": sample_intersection,
        "record_intersection": record_intersection,
        "evidence_sha256": sha256_json(material),
    }


def build_edition(
    *,
    version: str,
    dataset_root: Path,
    acceptance_manifest: Mapping[str, Any],
    timestamps: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """生成整版清单与全部待落盘文件（相对路径 → 内容）。

    ``timestamps`` 给出已有交付的 ``created_at``（键为 ``edition``、
    ``development``、``blind_acceptance``）。重建比对时沿用它，才能逐字节
    比较产物；否则子集清单里的时间戳会让每次都“不同”。
    """
    development_manifest, development_artifacts = development.build_development_corpus(
        version=version,
        dataset_root=dataset_root,
        acceptance_manifest=acceptance_manifest,
    )
    blind_manifest, blind_artifacts = blind_acceptance.build_blind_acceptance(
        version=version,
        dataset_root=dataset_root,
        acceptance_manifest=acceptance_manifest,
        development_manifest=development_manifest,
    )
    stamps = dict(timestamps or {})
    for key, subset_manifest in (
        ("development", development_manifest),
        (blind_acceptance.SUBSET_ID, blind_manifest),
    ):
        if isinstance(stamps.get(key), str):
            subset_manifest["created_at"] = stamps[key]

    relation = _cross_relation(
        development_manifest=development_manifest, blind_manifest=blind_manifest
    )
    if relation["sample_id_intersection"] or relation["record_intersection"]:
        raise RunError(
            "corpus_overlap_detected",
            "整版 Provider Corpus 的两个子集出现身份重叠",
            details={"relation": relation},
        )

    files: dict[str, Any] = {}
    for name, payload in development_artifacts.items():
        files[f"{DEVELOPMENT_DIR}/{name}"] = payload
    files[f"{DEVELOPMENT_DIR}/{MANIFEST_FILENAME}"] = development_manifest
    for name, payload in blind_artifacts.items():
        files[f"{BLIND_DIR}/{name}"] = payload
    files[f"{BLIND_DIR}/{MANIFEST_FILENAME}"] = blind_manifest

    manifest: dict[str, Any] = {
        "schema_version": EDITION_SCHEMA,
        "corpus_version": version,
        "created_at": now(),
        "frozen": True,
        "subsets": [
            _subset_entry(
                subset_id="development",
                directory=DEVELOPMENT_DIR,
                manifest=development_manifest,
                artifacts=development_artifacts,
            ),
            _subset_entry(
                subset_id=blind_acceptance.SUBSET_ID,
                directory=BLIND_DIR,
                manifest=blind_manifest,
                artifacts=blind_artifacts,
            ),
        ],
        "relations": {
            "subsets_isolated": relation,
            "blind_acceptance_matches_acceptance": {
                "manifest_id": blind_manifest["relations"]["equals_acceptance"][
                    "manifest_id"
                ],
                "acceptance_content_sha256": blind_manifest["relations"][
                    "equals_acceptance"
                ]["acceptance_content_sha256"],
                "samples": blind_manifest["relations"]["equals_acceptance"]["samples"],
                "sample_identity_equal": True,
                "record_identity_equal": True,
                "evidence_sha256": blind_manifest["relations"]["equals_acceptance"][
                    "evidence_sha256"
                ],
            },
        },
        "usage": {
            "development": {
                "purpose": "development",
                "tuning_allowed": True,
                "description": "供课题四反复自测、调试 prompt 与规则。",
            },
            "blind_acceptance": {
                "purpose": "blind_acceptance",
                "tuning_allowed": False,
                "freeze_before_run": ["provider", "model", "prompt", "config_revision"],
                "description": "只在实现冻结后用于首次正式运行；不得用于开发或 prompt 调整。",
            },
            "provider_contract_test_suite": {
                "status": "not_in_corpus",
                "description": (
                    "Provider Contract Test Suite 是后续的夹具与契约测试，不是真实轨迹"
                    " corpus，不包含在本交付里，也不得被描述为真实样本。"
                ),
            },
            "run_policy": {
                "independent_runs": True,
                "baseline_fallback_allowed": False,
                "description": (
                    "正式课题四 provider 与 deterministic-baseline 必须各自产生独立 run；"
                    "provider 失败时不得静默用 baseline 结果替代。"
                ),
                "rerun_policy": (
                    "仅在 transport/文件故障且没有产生可靠语义结果时，才允许用相同 revision "
                    "重跑；完整结果可见之后的任何实现变化都必须产生新 revision 与新 run，"
                    "首次结果不得覆盖。"
                ),
            },
        },
        "notes": [
            "整版清单只声明子集身份与关系；逐条样本映射留在各子集的 manifest.json。",
            "两个子集的样本身份与记录身份交集均为空，证据可由 verify 命令重算。",
            "开发与验收子集使用同一白名单投影与同一 topic4-audit-input/1 封套。",
        ],
        "artifacts": [],
        "integrity": {},
    }

    readme = render_readme(manifest)
    files[README_FILENAME] = readme
    manifest["artifacts"] = artifact_digests(files)
    manifest["integrity"] = {
        "digest_version": INTEGRITY_VERSION,
        "content_sha256": content_sha256(manifest),
    }
    files[MANIFEST_FILENAME] = manifest
    return manifest, files


def _timestamps_of(root: Path) -> dict[str, str]:
    """读取已有交付的三个 created_at，供重建比对逐字节还原。"""
    stamps: dict[str, str] = {}
    edition_manifest = _read_manifest(root / MANIFEST_FILENAME) or {}
    if isinstance(edition_manifest.get("created_at"), str):
        stamps["edition"] = edition_manifest["created_at"]
    for key, directory in (
        ("development", DEVELOPMENT_DIR),
        (blind_acceptance.SUBSET_ID, BLIND_DIR),
    ):
        subset_manifest = _read_manifest(root / directory / MANIFEST_FILENAME) or {}
        if isinstance(subset_manifest.get("created_at"), str):
            stamps[key] = subset_manifest["created_at"]
    return stamps


def render_readme(manifest: Mapping[str, Any]) -> str:
    version = manifest["corpus_version"]
    subsets = {entry["id"]: entry for entry in manifest["subsets"]}
    development_entry = subsets["development"]
    blind_entry = subsets[blind_acceptance.SUBSET_ID]
    return "\n".join(
        [
            f"# {version}",
            "",
            "本目录是课题四（Provider）的整版冻结交付，包含两个职责隔离的真实轨迹"
            "子集。它们共用同一套白名单投影与 `topic4-audit-input/1` 封套，但样本身份"
            "与记录身份零重叠。",
            "",
            "| 子集 | 目录 | 条数 | 用途 |",
            "| --- | --- | --- | --- |",
            f"| Development Corpus | `{DEVELOPMENT_DIR}/` | {development_entry['samples']}"
            " | 课题四反复自测与调试 |",
            f"| Blind Acceptance Batch | `{BLIND_DIR}/` | {blind_entry['samples']}"
            " | 实现冻结后的首次正式运行 |",
            "",
            "## 三层数据不要混用",
            "",
            "1. **Development Corpus**：可以反复运行、可以据其调整 prompt 与规则。",
            "2. **Blind Acceptance Batch**：不得用于开发、prompt 调整或规则调试；"
            "正式运行前冻结 provider、model、prompt 与 config revision。",
            "3. **Provider Contract Test Suite**：后续交付的夹具与契约测试，"
            "不是真实轨迹 corpus，也不会出现在本目录里。",
            "",
            "## 正式运行纪律",
            "",
            "- 正式课题四 provider 与 deterministic-baseline 必须各自产生**独立 run**；"
            "provider 失败时不得静默用 baseline 结果替代。",
            "- 只有在 transport/文件故障且没有产生可靠语义结果时，才允许用相同 revision"
            "重跑。",
            "- 完整结果可见之后的任何实现变化都必须产生新 revision 与新 run；"
            "首次结果不得覆盖。",
            "",
            "## 输入与校验",
            "",
            "```bash",
            "# 开发自测",
            "topic4-audit run \\",
            f"  --input corpus/{version}/{DEVELOPMENT_DIR}/{INPUT_FILENAME} \\",
            "  --output <result.json> --config <config.json>",
            "",
            "# 盲测验收（实现冻结后只跑一次正式运行）",
            "topic4-audit run \\",
            f"  --input corpus/{version}/{BLIND_DIR}/{INPUT_FILENAME} \\",
            "  --output <result.json> --config <config.json>",
            "",
            "# 整版校验（文件摘要、Schema、子集关系、可重放性）",
            "python -m trajectory_audit corpus edition verify \\",
            f"  --root corpus/{version} --dataset-root .cache/datasets \\",
            "  --acceptance acceptance/acceptance-v1.json",
            "```",
            "",
            "## 版本与不可改写",
            "",
            "同一 corpus 版本一旦交付就不可原地改写；样本或布局变化必须通过新版本表达。"
            "整版清单记录两个子集的批次身份、文件摘要、内容摘要与交叉零重叠证据，"
            "任一文件被手工改写都会被校验命令检出。",
            "",
            "## 数据层次免责声明",
            "",
            "Source Label 是 benchmark 的来源侧事实；项目参考标注是人工确认过的另一层"
            "事实；Detected Finding 是 provider 的检测输出。三者不可互相替代，"
            "`self-check-reference.json` 也不是项目真值。",
            "",
        ]
    )


def _write_files(out_dir: Path, files: Mapping[str, Any]) -> None:
    for relative in sorted(files):
        if relative == MANIFEST_FILENAME:
            continue
        target = out_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = files[relative]
        if isinstance(payload, str):
            target.write_text(payload, encoding="utf-8", newline="\n")
        else:
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
    manifest = files[MANIFEST_FILENAME]
    (out_dir / MANIFEST_FILENAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_edition(
    *,
    version: str,
    dataset_root: Path,
    acceptance_manifest: Mapping[str, Any],
    out_dir: Path,
) -> dict[str, Any]:
    """写出整版目录；同内容报告 `unchanged`，内容不同拒绝覆盖。"""
    timestamps = _timestamps_of(out_dir) if out_dir.exists() else None
    manifest, files = build_edition(
        version=version,
        dataset_root=dataset_root,
        acceptance_manifest=acceptance_manifest,
        timestamps=timestamps,
    )

    if out_dir.exists():
        existing = _read_manifest(out_dir / MANIFEST_FILENAME)
        if existing is None or existing.get("schema_version") != EDITION_SCHEMA:
            raise RunError(
                "corpus_directory_not_frozen",
                "目标目录已存在但没有可读的整版清单，拒绝覆盖",
                details={"path": str(out_dir)},
                exit_code=3,
            )
        existing_digest = (existing.get("integrity") or {}).get("content_sha256")
        if existing_digest != manifest["integrity"]["content_sha256"]:
            raise _conflict(out_dir, version, existing_digest, manifest)
        manifest["created_at"] = existing.get("created_at", manifest["created_at"])
        files[MANIFEST_FILENAME] = manifest
        if _matches(out_dir, files):
            return {
                "status": "unchanged",
                "corpus_version": version,
                "path": str(out_dir),
                "content_sha256": manifest["integrity"]["content_sha256"],
                "subsets": {entry["id"]: entry["samples"] for entry in manifest["subsets"]},
            }
        raise _conflict(out_dir, version, existing_digest, manifest)

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_files(out_dir, files)
    return {
        "status": "created",
        "corpus_version": version,
        "path": str(out_dir),
        "content_sha256": manifest["integrity"]["content_sha256"],
        "subsets": {entry["id"]: entry["samples"] for entry in manifest["subsets"]},
    }


def _read_manifest(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        loaded = read_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _matches(out_dir: Path, files: Mapping[str, Any]) -> bool:
    for relative, payload in files.items():
        target = out_dir / relative
        if not target.is_file():
            return False
        expected = (
            payload.encode("utf-8")
            if isinstance(payload, str)
            else (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        )
        if target.read_bytes() != expected:
            return False
    return True


def _conflict(
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
            "new_content_sha256": (manifest.get("integrity") or {}).get("content_sha256"),
            "hint": "内容变化必须新建 corpus 版本，而不是覆盖已交付版本",
        },
        exit_code=3,
    )


def _readme_token_issues(root: Path, relative: str, tokens: tuple[str, ...]) -> list[str]:
    path = root / relative
    if not path.is_file():
        return [f"缺少 {relative}"]
    text = path.read_text(encoding="utf-8")
    return [f"{relative} 缺少必需说明：{token}" for token in tokens if token not in text]


def verify_edition(
    *,
    root: Path,
    dataset_root: Path | None = None,
    acceptance_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """校验整版交付：产物摘要、两个子集、交叉关系与可重放性。"""
    issues: list[dict[str, str]] = []

    def add(path: str, message: str) -> None:
        issues.append({"path": path, "message": message})

    manifest = _read_manifest(root / MANIFEST_FILENAME)
    if manifest is None:
        raise RunError(
            "corpus_manifest_not_found",
            "整版目录缺少可读的 manifest.json",
            details={"path": str(root / MANIFEST_FILENAME)},
        )

    if manifest.get("schema_version") != EDITION_SCHEMA:
        add("schema_version", f"必须是 {EDITION_SCHEMA!r}")
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
        if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
            add(f"{path}.sha256", "产物摘要不一致，文件被篡改或损坏")

    subsets = manifest.get("subsets")
    if not isinstance(subsets, list) or len(subsets) != 2:
        add("subsets", "整版必须声明 development 与 blind_acceptance 两个子集")
        subsets = []
    declared = {
        entry.get("id"): entry for entry in subsets if isinstance(entry, dict)
    }

    development_entry = declared.get("development")
    blind_entry = declared.get(blind_acceptance.SUBSET_ID)
    development_manifest: dict[str, Any] | None = None
    blind_manifest: dict[str, Any] | None = None

    if development_entry is None:
        add("subsets", "缺少 development 子集声明")
    else:
        development_dir = root / development_entry.get("path", DEVELOPMENT_DIR)
        development_manifest = _read_manifest(development_dir / MANIFEST_FILENAME)
        if development_manifest is None:
            add("subsets.development.manifest", "缺少开发子集清单")
        else:
            report = development.verify_development_corpus(
                root=development_dir, acceptance_manifest=acceptance_manifest
            )
            for issue in report["issues"]:
                add(f"subsets.development:{issue['path']}", issue["message"])
            _check_subset_entry(
                add,
                "development",
                development_entry,
                development_manifest,
                development_dir,
            )

    if blind_entry is None:
        add("subsets", "缺少 blind_acceptance 子集声明")
    else:
        blind_dir = root / blind_entry.get("path", BLIND_DIR)
        blind_manifest = _read_manifest(blind_dir / MANIFEST_FILENAME)
        if blind_manifest is None:
            add("subsets.blind_acceptance.manifest", "缺少验收子集清单")
        else:
            report = blind_acceptance.verify_blind_acceptance(
                root=blind_dir,
                acceptance_manifest=acceptance_manifest,
                development_manifest=development_manifest,
            )
            for issue in report["issues"]:
                add(f"subsets.blind_acceptance:{issue['path']}", issue["message"])
            _check_subset_entry(
                add,
                "blind_acceptance",
                blind_entry,
                blind_manifest,
                blind_dir,
            )

    relations = manifest.get("relations")
    if not isinstance(relations, dict):
        add("relations", "整版必须声明子集关系")
        relations = {}
    cross = relations.get("subsets_isolated")
    if not isinstance(cross, dict):
        add("relations.subsets_isolated", "缺少两个子集的零重叠证据")
    elif development_manifest is not None and blind_manifest is not None:
        expected = _cross_relation(
            development_manifest=development_manifest, blind_manifest=blind_manifest
        )
        if expected["sample_id_intersection"] or expected["record_intersection"]:
            add("relations.subsets_isolated", "两个子集出现身份重叠")
        if cross.get("evidence_sha256") != expected["evidence_sha256"]:
            add("relations.subsets_isolated.evidence_sha256", "零重叠证据无法重算，证据已被改写")

    equality = relations.get("blind_acceptance_matches_acceptance")
    if not isinstance(equality, dict):
        add("relations.blind_acceptance_matches_acceptance", "缺少与验收清单的身份关系")
    elif blind_manifest is not None:
        inner = (blind_manifest.get("relations") or {}).get("equals_acceptance") or {}
        if equality.get("evidence_sha256") != inner.get("evidence_sha256"):
            add(
                "relations.blind_acceptance_matches_acceptance.evidence_sha256",
                "与验收子集记录的证据不一致",
            )

    usage = manifest.get("usage")
    if not isinstance(usage, dict):
        add("usage", "整版必须声明使用边界")
    else:
        if (usage.get("blind_acceptance") or {}).get("tuning_allowed") is not False:
            add("usage.blind_acceptance.tuning_allowed", "盲测批次不得允许用于调试")
        if (usage.get("development") or {}).get("tuning_allowed") is not True:
            add("usage.development.tuning_allowed", "开发子集必须允许反复自测")
        if (usage.get("provider_contract_test_suite") or {}).get("status") != "not_in_corpus":
            add(
                "usage.provider_contract_test_suite",
                "契约测试套件不是真实轨迹 corpus，必须显式声明不在本交付内",
            )
        run_policy = usage.get("run_policy") or {}
        if run_policy.get("baseline_fallback_allowed") is not False:
            add("usage.run_policy.baseline_fallback_allowed", "禁止用 baseline 静默替代 provider")
        if run_policy.get("independent_runs") is not True:
            add("usage.run_policy.independent_runs", "provider 与 baseline 必须独立 run")

    for relative in (README_FILENAME,):
        for message in _readme_token_issues(root, relative, README_REQUIRED_TOKENS):
            add(relative, message)
    if development_entry is not None:
        for message in _readme_token_issues(
            root,
            f"{development_entry.get('path', DEVELOPMENT_DIR)}/{README_FILENAME}",
            ("topic4-audit run", "policy=null", "self-check", "不是项目真值"),
        ):
            add("README", message)
    if blind_entry is not None:
        for message in _readme_token_issues(
            root,
            f"{blind_entry.get('path', BLIND_DIR)}/{README_FILENAME}",
            ("Blind Acceptance", "revision", "独立 run", "不用于"),
        ):
            add("README", message)

    reproducible = "skipped"
    if dataset_root is not None and acceptance_manifest is not None:
        cache_present = all(
            (dataset_root / pinned.pinned(dataset_id)["cache_name"]).is_file()
            for dataset_id in pinned.BY_DATASET
        )
        if not cache_present:
            add("subsets", "固定数据缓存不完整，无法执行全量重建比对")
        else:
            try:
                rebuilt, _ = build_edition(
                    version=manifest["corpus_version"],
                    dataset_root=dataset_root,
                    acceptance_manifest=acceptance_manifest,
                    timestamps=_timestamps_of(root),
                )
            except RunError as exc:
                add("integrity.content_sha256", f"用固定缓存重建失败：{exc.code}")
                reproducible = "error"
            else:
                if rebuilt["integrity"]["content_sha256"] != content_digest:
                    add("integrity.content_sha256", "用固定缓存重建得到不同内容；整版不可重放")
                    reproducible = "mismatch"
                else:
                    reproducible = "verified"

    checks = {
        "artifacts_intact": not any(
            issue["path"].startswith("artifacts[") for issue in issues
        ),
        "subsets_valid": not any(
            issue["path"].startswith("subsets") for issue in issues
        ),
        "identity_relations_ok": not any(
            issue["path"].startswith("relations") for issue in issues
        ),
        "usage_boundaries_declared": not any(
            issue["path"].startswith("usage") for issue in issues
        ),
        "reproducibility_verified": reproducible == "verified",
    }
    return {
        "status": "pass" if not issues else "fail",
        "corpus_version": manifest.get("corpus_version"),
        "subsets": {
            entry.get("id"): entry.get("samples")
            for entry in subsets
            if isinstance(entry, dict)
        },
        "content_sha256": content_digest,
        "checks": checks,
        "reproducible": reproducible,
        "issues": issues,
    }


def _check_subset_entry(
    add: Any,
    subset_id: str,
    entry: Mapping[str, Any],
    manifest: Mapping[str, Any],
    directory: Path,
) -> None:
    prefix = f"subsets.{subset_id}"
    if entry.get("samples") != len(manifest.get("samples") or []):
        add(f"{prefix}.samples", "声明的条数与子集清单不一致")
    if entry.get("content_sha256") != (manifest.get("integrity") or {}).get(
        "content_sha256"
    ):
        add(f"{prefix}.content_sha256", "声明的子集内容摘要与子集清单不一致")
    batch_id = manifest.get("batch_id")
    if entry.get("batch_id") != batch_id:
        add(f"{prefix}.batch_id", "声明的批次身份与子集清单不一致")
    input_path = directory / INPUT_FILENAME
    if input_path.is_file():
        digest = hashlib.sha256(input_path.read_bytes()).hexdigest()
        if entry.get("input_sha256") != digest:
            add(f"{prefix}.input_sha256", "声明的输入摘要与文件不一致")
    else:
        add(f"{prefix}.input", "缺少子集正式输入文件")
    if entry.get("input_schema_version") != manifest.get("input_schema_version"):
        add(f"{prefix}.input_schema_version", "与子集清单的输入 Schema 不一致")
    if entry.get("trajectory_schema_version") != manifest.get(
        "trajectory_schema_version"
    ):
        add(f"{prefix}.trajectory_schema_version", "与子集清单的轨迹 Schema 不一致")


__all__ = [
    "BLIND_DIR",
    "DEVELOPMENT_DIR",
    "EDITION_SCHEMA",
    "README_REQUIRED_TOKENS",
    "build_edition",
    "render_readme",
    "verify_edition",
    "write_edition",
]
