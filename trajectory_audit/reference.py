# SPDX-License-Identifier: Apache-2.0
"""项目参考标注草稿与审阅包。

本模块只处理 `acceptance-v1` 中 ATBench 的 24 条验收样本，产出一版**待确认**的
项目参考标注草稿。四条硬约束：

1. 草稿是本项目对轨迹的判断，不是 ATBench 来源真值，也不是双人金标。
2. ATBench 只提供轨迹级 `label`/`risk_source`/`failure_mode`/`reason`，因此草稿
   保持轨迹级、`located=false`，不为任何事件伪造事件级真值。
3. 未经人工在 `review.json` 中确认的草稿不生效：`effective_labels()` 对
   `pending`/`reject` 一律不返回标签，`detected` 与 `reference` 两层从不合并。
4. 生成器是确定性投影：`generator.model` 与 `generator.prompt_template` 显式记录
   为 `null`，避免把人工复制或模型输出伪装成可追溯产物。

审阅包文件布局::

    manifest.json      reference-package/1     输入版本、生成器身份、免责声明
    drafts.json        reference-drafts/1      待确认的参考标注草稿
    transcripts.json   reference-transcripts/1 轨迹事件原文（供事件级修订定位）
    review.json        reference-review/1      人工逐字段确认表
    index.html                               离线审阅页面（无外部资源）
    README.md                                审阅流程说明
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import __version__, pinned
from .acceptance import dataset_path, load_dataset, validate_manifest
from .adapters import get_adapter
from .artifacts import now, read_json, write_json
from .errors import ConversionError, RunError
from .model import sha256_json
from .schema import locate_json_path
from .source_labels import MAPPING_VERSION, atbench_mapping

PACKAGE_SCHEMA = "reference-package/1"
DRAFTS_SCHEMA = "reference-drafts/1"
TRANSCRIPTS_SCHEMA = "reference-transcripts/1"
REVIEW_SCHEMA = "reference-review/1"
DRAFT_SCHEMA = "reference-draft/1"
EFFECTIVE_SCHEMA = "reference-effective-labels/1"
MARKDOWN_FILE = "README.md"
PAGE_FILE = "index.html"

DEFAULT_PACKAGE_ID = "atbench-reference-v1"
DEFAULT_MANIFEST_ID = "acceptance-v1"
DATASET_ID = "atbench"
ACCEPTANCE_ATBENCH_SAMPLES = 24

DECISIONS = (None, "accept", "revise", "reject")
FIELD_DECISIONS = (None, "accept", "revise", "reject")
REVISABLE_FIELDS = ("scope", "origin_event", "evidence", "categories", "reason")
REVIEW_STATUSES = ("pending", "in_progress", "confirmed")
ASSISTANCE_MODES = ("none", "ai_assisted")
CATEGORY_TAXONOMY_ID = "project-reference-risk/1"
REFERENCE_CATEGORIES = (
    "choosing_malicious_tool",
    "failure_to_validate_tool_outputs",
    "flawed_planning_or_reasoning",
    "incorrect_tool_parameters",
    "procedural_deviation_or_inaction",
    "provide_inaccurate_misleading_or_unverified_information",
    "tool_misuse_in_specific_context",
    "unauthorized_information_disclosure",
    "unconfirmed_or_over_privileged_action",
)
SCOPES = ("trajectory", "event", "span")
EVENT_SCOPES = ("event", "span")
EVIDENCE_KINDS = ("source_field", "event", "tool_fact")
EVIDENCE_REQUIRED_KEYS = (
    "kind",
    "event_id",
    "source_step_id",
    "json_path",
    "record_index",
    "record_sha256",
    "quote",
    "char_range",
    "located",
    "failure",
)
QUOTE_LIMIT = 240
SUMMARY_LIMIT = 900

GENERATOR = {
    "kind": "deterministic_projection",
    "id": "atbench-source-label-projection",
    "version": "1",
    "model": None,
    "prompt_template": None,
    "note": "由 ATBench 来源标签确定性投影，未使用辅助模型或提示模板",
}

DISCLAIMERS = (
    "这些是项目参考标注草稿，不是 ATBench 来源数据集真值。",
    "这不是双人金标，也不构成标注者一致性统计。",
    "这不是线上人工复核流程；确认动作只在本目录的 review.json 中留痕。",
    "未经 review.json 确认的草稿不会进入 H2 指标或正式审阅页面。",
    "ATBench 只提供轨迹级标签，因此草稿保持轨迹级、未定位，不声称事件级真值。",
)

LAYERS = (
    {
        "layer": "source",
        "owner": "ATBench",
        "included": True,
        "note": "来源字段原名照抄，不做任何改写；只作事实引用。",
    },
    {
        "layer": "reference",
        "owner": "本项目",
        "included": True,
        "note": "待人工确认的项目参考标注草稿；确认前不生效。",
    },
    {
        "layer": "detected",
        "owner": "审计引擎",
        "included": False,
        "note": "本审阅包不包含任何 detected 结果，三层从不合并。",
    },
)

_EVIDENCE_FIELDS = (
    "id",
    "label",
    "risk_source",
    "failure_mode",
    "real_world_harm",
    "reason",
)


# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------


def build_package(
    *,
    acceptance_manifest: dict[str, Any],
    dataset_root: Path,
    package_id: str = DEFAULT_PACKAGE_ID,
    annotation_version: str | None = None,
    generated_at: str | None = None,
    dataset_id: str = DATASET_ID,
    expected_manifest_id: str | None = DEFAULT_MANIFEST_ID,
) -> dict[str, Any]:
    """为冻结验收集中的 ATBench 样本生成草稿、轨迹原文与确认表。"""
    expected_id = expected_manifest_id or acceptance_manifest.get("manifest_id")
    issues = validate_manifest(acceptance_manifest, expected_id=expected_id)
    if issues:
        raise RunError(
            "acceptance_manifest_invalid",
            "验收清单不自洽，拒绝在其上生成参考标注草稿",
            details={"issues": issues},
        )

    samples = [
        sample
        for sample in acceptance_manifest["samples"]
        if sample["dataset_id"] == dataset_id
    ]
    if not samples:
        raise RunError(
            "acceptance_manifest_empty",
            "验收清单中没有该数据集的样本",
            details={"dataset_id": dataset_id},
        )

    entry = pinned.pinned(dataset_id)
    path = dataset_path(dataset_root, dataset_id)
    if not path.is_file():
        raise RunError(
            "dataset_not_found",
            "固定版本数据集不存在，无法校验草稿证据定位",
            details={"path": str(path), "hint": "先运行 scripts/fetch_datasets.py"},
        )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != entry["sha256"]:
        raise RunError(
            "dataset_hash_mismatch",
            "数据集文件与固定清单不一致",
            details={"expected": entry["sha256"], "actual": digest},
        )
    records = load_dataset(path)
    adapter = get_adapter(entry["adapter_id"])

    version = annotation_version or package_id
    stamp = generated_at or now()
    drafts: list[dict[str, Any]] = []
    transcripts: list[dict[str, Any]] = []

    for sample in sorted(samples, key=lambda item: item["record_index"]):
        record_index = sample["record_index"]
        if record_index >= len(records):
            raise RunError(
                "record_index_out_of_range",
                "验收清单的记录下标超出数据集范围",
                details={"record_index": record_index, "records_total": len(records)},
            )
        record = records[record_index]
        if sha256_json(record) != sample["record_sha256"]:
            raise RunError(
                "record_sha256_changed",
                "记录内容与验收清单不一致",
                details={"record_index": record_index, "sample_id": sample["sample_id"]},
            )
        transcript = _transcript(
            adapter=adapter,
            record=record,
            record_index=record_index,
            sample=sample,
            entry=entry,
            dataset_id=dataset_id,
        )
        transcripts.append(transcript)
        drafts.append(
            _draft(
                sample=sample,
                record=record,
                record_index=record_index,
                annotation_version=version,
                generated_at=stamp,
                acceptance_manifest=acceptance_manifest,
                dataset=entry,
                dataset_id=dataset_id,
                transcript=transcript,
            )
        )

    manifest = {
        "schema_version": PACKAGE_SCHEMA,
        "package_id": package_id,
        "annotation_version": version,
        "status": "pending",
        "generated_at": stamp,
        "tool_version": __version__,
        "generator": dict(GENERATOR),
        "input": {
            "dataset_id": dataset_id,
            "dataset_version": entry["dataset_version"],
            "dataset_sha256": entry["sha256"],
            "dataset_license": entry["license"],
            "acceptance_manifest_id": acceptance_manifest["manifest_id"],
            "acceptance_content_sha256": acceptance_manifest["integrity"][
                "content_sha256"
            ],
            "sample_count": len(drafts),
            "sample_ids": [draft["sample_id"] for draft in drafts],
        },
        "draft_count": len(drafts),
        "locator_failure_count": sum(len(draft["locator_failures"]) for draft in drafts),
        "transcript_event_count": sum(
            len(transcript["events"]) for transcript in transcripts
        ),
        "drafts_file": "drafts.json",
        "transcripts_file": "transcripts.json",
        "review_file": "review.json",
        "page_file": PAGE_FILE,
        "markdown_file": MARKDOWN_FILE,
        "category_taxonomy": {
            "id": CATEGORY_TAXONOMY_ID,
            "categories": list(REFERENCE_CATEGORIES),
            "safe_policy": "safe 标注的项目风险类别必须为空",
            "unsafe_policy": "unsafe 标注必须由人工确认至少一个项目参考类别",
        },
        "layers": [dict(layer) for layer in LAYERS],
        "scope_policy": (
            "ATBench 只提供轨迹级标签：草稿为 trajectory 级、located=false，"
            "不生成事件级或片段级真值"
        ),
        "disclaimers": list(DISCLAIMERS),
    }
    return {
        "manifest": manifest,
        "drafts": {"schema_version": DRAFTS_SCHEMA, "drafts": drafts},
        "transcripts": {"schema_version": TRANSCRIPTS_SCHEMA, "transcripts": transcripts},
        "review": _empty_review(package_id, drafts),
    }


def _transcript(
    *,
    adapter: Any,
    record: dict[str, Any],
    record_index: int,
    sample: dict[str, Any],
    entry: dict[str, Any],
    dataset_id: str,
) -> dict[str, Any]:
    """归一化一条记录，保留可供事件级修订定位的事件身份与原文。"""
    base = {
        "draft_id": f"ref-{sample['sample_id']}",
        "sample_id": sample["sample_id"],
        "dataset_id": dataset_id,
        "dataset_version": entry["dataset_version"],
        "record_index": record_index,
        "record_sha256": sha256_json(record),
        "events": [],
        "problem": None,
    }
    try:
        trajectories = adapter.normalize_record(
            record,
            record_index=record_index,
            dataset_id=dataset_id,
            dataset_version=entry["dataset_version"],
            input_sha256=entry["sha256"],
            record_json_path="",
            record_sha256=base["record_sha256"],
            sample_id=sample["sample_id"],
        )
    except ConversionError as exc:
        base["problem"] = {
            "stage": exc.stage,
            "code": exc.code,
            "reason": exc.message,
        }
        return base

    events: list[dict[str, Any]] = []
    for trajectory in trajectories:
        for event in trajectory.events:
            payload = event.to_dict()
            events.append(
                {
                    "event_id": payload["event_id"],
                    "sequence": payload["sequence"],
                    "trajectory_sample_id": trajectory.sample_id,
                    "type": payload["type"],
                    "actor": payload["actor"],
                    "source_step_id": payload["source_step_id"],
                    "json_path": payload["source"]["json_path"],
                    "record_index": payload["source"]["record_index"],
                    "record_sha256": payload["source"]["record_sha256"],
                    "summary": _event_summary(payload),
                }
            )
    base["events"] = events
    return base


def _event_summary(event: dict[str, Any]) -> str:
    content = event.get("content") or {}
    for key in ("text", "output", "declaration"):
        value = content.get(key)
        if isinstance(value, str) and value.strip():
            return _clip(value, SUMMARY_LIMIT)
    if event.get("type") == "tool_call":
        view = {
            key: content.get(key)
            for key in ("tool", "arguments", "arguments_status")
            if content.get(key) not in (None, "")
        }
        return _clip(json.dumps(view, ensure_ascii=False, sort_keys=True), SUMMARY_LIMIT)
    return _clip(json.dumps(content, ensure_ascii=False, sort_keys=True), SUMMARY_LIMIT)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _draft(
    *,
    sample: dict[str, Any],
    record: dict[str, Any],
    record_index: int,
    annotation_version: str,
    generated_at: str,
    acceptance_manifest: dict[str, Any],
    dataset: dict[str, Any],
    dataset_id: str,
    transcript: dict[str, Any],
) -> dict[str, Any]:
    label = record.get("label")
    unsafe = isinstance(label, int) and not isinstance(label, bool) and label == 1
    source_categories = [
        str(record[field])
        for field in ("risk_source", "failure_mode")
        if isinstance(record.get(field), str) and record[field]
    ]
    reason, reason_basis = _reason(record)
    evidence, failures = _evidence(record, record_index)
    draft_id = f"ref-{sample['sample_id']}"

    return {
        "schema_version": DRAFT_SCHEMA,
        "draft_id": draft_id,
        "sample_id": sample["sample_id"],
        "layer": "reference",
        "status": "pending",
        "annotation_version": annotation_version,
        "generated_at": generated_at,
        "generator": dict(GENERATOR),
        "basis": "source_label_projection",
        "scope": "trajectory",
        "located": False,
        "origin_event": None,
        "verdict": "unsafe" if unsafe else "safe",
        "categories": [],
        "source_categories": source_categories,
        "mapping": {
            **atbench_mapping(),
            "version": MAPPING_VERSION,
            "basis": (
                "ATBench 只有 label / risk_source / failure_mode；"
                "统一类别留待人工确认"
            ),
        },
        "reason": reason,
        "reason_basis": reason_basis,
        "evidence": evidence,
        "locator_failures": failures,
        "source_label": {
            "label": label,
            "risk_source": record.get("risk_source"),
            "failure_mode": record.get("failure_mode"),
            "real_world_harm": record.get("real_world_harm"),
        },
        "transcript": {
            "file": "transcripts.json",
            "event_count": len(transcript["events"]),
            "problem": transcript.get("problem"),
        },
        "input": {
            "dataset_id": dataset_id,
            "dataset_version": dataset["dataset_version"],
            "dataset_sha256": dataset["sha256"],
            "acceptance_manifest_id": acceptance_manifest["manifest_id"],
            "acceptance_content_sha256": acceptance_manifest["integrity"][
                "content_sha256"
            ],
            "record_index": record_index,
            "record_sha256": sha256_json(record),
        },
    }


def _reason(record: dict[str, Any]) -> tuple[str, str]:
    reason = record.get("reason")
    if isinstance(reason, str) and reason.strip():
        return reason.strip(), "source_reason"
    parts = [
        f"{field}={record[field]}"
        for field in ("risk_source", "failure_mode")
        if isinstance(record.get(field), str) and record.get(field)
    ]
    if parts:
        return "来源标注：" + "，".join(parts), "source_fields"
    return "ATBench 未提供理由字段，需人工补充", "missing"


def _evidence(
    record: dict[str, Any], record_index: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把来源字段做成可回读的证据引用；任何不一致都记为定位失败。"""
    evidence: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    record_sha256 = sha256_json(record)
    for field in _EVIDENCE_FIELDS:
        value = record.get(field)
        if value is None or value == "":
            continue
        quote, char_range = _quote(value)
        item = {
            "kind": "source_field",
            "event_id": None,
            "source_step_id": None,
            "json_path": field,
            "record_index": record_index,
            "record_sha256": record_sha256,
            "quote": quote,
            "char_range": char_range,
            "located": False,
            "failure": None,
        }
        problem = check_evidence(item, record)
        if problem is None:
            item["located"] = True
        else:
            item["failure"] = problem
            failures.append({"json_path": field, "reason": problem})
        evidence.append(item)
    return evidence, failures


def _quote(value: Any) -> tuple[str, list[int] | None]:
    if isinstance(value, str):
        text = value if len(value) <= QUOTE_LIMIT else value[:QUOTE_LIMIT]
        return text, [0, len(text)]
    return str(value), None


def check_evidence(item: dict[str, Any], record: Any) -> str | None:
    """按字段路径回读原始记录并核对引用；返回失败原因或 ``None``。"""
    json_path = item.get("json_path")
    if not isinstance(json_path, str) or not json_path:
        return "证据缺少字段路径"
    found, value = locate_json_path(record, json_path)
    if not found:
        return "字段路径无法在原始记录中回读"
    quote = item.get("quote")
    if not isinstance(quote, str) or not quote:
        return "证据缺少原文引用"
    char_range = item.get("char_range")
    if isinstance(value, str):
        if char_range is not None:
            if (
                not isinstance(char_range, list)
                or len(char_range) != 2
                or not all(isinstance(part, int) for part in char_range)
            ):
                return "字符区间格式不合法"
            start, end = char_range
            if not (0 <= start <= end <= len(value)):
                return "字符区间越界"
            if value[start:end] != quote:
                return "字符区间与原文引用不一致"
            return None
        if quote not in value:
            return "原文引用不在字段值中"
        return None
    if str(value) != quote:
        return "非字符串字段的引用与原文不一致"
    return None


def _empty_review(package_id: str, drafts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": REVIEW_SCHEMA,
        "package_id": package_id,
        "status": "pending",
        "reviewer": None,
        "reviewed_at": None,
        "assistance": {
            "mode": "none",
            "product": None,
            "model": None,
            "prompt_protocol": None,
            "final_authority": "human",
            "note": "未使用辅助模型",
        },
        "items": [
            {
                "draft_id": draft["draft_id"],
                "draft_sha256": sha256_json(draft),
                "decision": None,
                "field_decisions": {field: None for field in REVISABLE_FIELDS},
                "revised": None,
                "note": None,
            }
            for draft in drafts
        ],
    }


# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------


def load_package(root: Path) -> dict[str, Any]:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise RunError(
            "package_not_found",
            "参考标注审阅包不存在",
            details={"path": str(root)},
        )
    return {
        "manifest": read_json(manifest_path),
        "drafts": read_json(root / "drafts.json"),
        "transcripts": read_json(root / "transcripts.json"),
        "review": read_json(root / "review.json"),
        "root": str(root),
    }


def write_package(package: dict[str, Any], root: Path, *, force: bool = False) -> None:
    review_path = root / "review.json"
    if review_path.is_file() and not force:
        existing = read_json(review_path)
        decided = [
            item
            for item in (existing.get("items") or [])
            if isinstance(item, dict) and item.get("decision")
        ]
        if decided:
            raise RunError(
                "package_review_in_progress",
                "该审阅包已有确认记录，重建会覆盖人工结论",
                details={
                    "path": str(review_path),
                    "decided": len(decided),
                    "hint": "换一个 annotation_version，或显式传入 --force",
                },
                exit_code=3,
            )
    from .reference_page import render_reference_page, render_reference_readme

    root.mkdir(parents=True, exist_ok=True)
    write_json(root / "manifest.json", package["manifest"])
    write_json(root / "drafts.json", package["drafts"])
    write_json(root / "transcripts.json", package["transcripts"])
    write_json(root / "review.json", package["review"])
    (root / PAGE_FILE).write_text(
        render_reference_page(package), encoding="utf-8", newline="\n"
    )
    (root / MARKDOWN_FILE).write_text(
        render_reference_readme(package), encoding="utf-8", newline="\n"
    )


def refresh_review_artifacts(package: dict[str, Any], root: Path) -> str:
    """同步审阅状态并安全刷新 review.json、页面与 README。

    不重写 manifest、drafts 或 transcripts，因而不会改变草稿身份或证据血缘。
    """
    review = package.get("review") or {}
    review["status"] = review_status(review)
    issues = validate_package(package)
    if issues:
        raise RunError(
            "reference_review_invalid",
            "审阅记录未通过自洽校验，未刷新任何文件",
            details={"issues": issues},
            exit_code=4,
        )
    from .reference_page import render_reference_page, render_reference_readme

    write_json(root / "review.json", review)
    (root / PAGE_FILE).write_text(
        render_reference_page(package), encoding="utf-8", newline="\n"
    )
    (root / MARKDOWN_FILE).write_text(
        render_reference_readme(package), encoding="utf-8", newline="\n"
    )
    return review["status"]


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def validate_package(
    package: dict[str, Any],
    *,
    acceptance_manifest: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """校验审阅包自洽；不读取数据集，可在 CI 中对已提交产物执行。"""
    issues: list[dict[str, str]] = []

    def add(path: str, message: str) -> None:
        issues.append({"path": path, "message": message})

    manifest = package.get("manifest")
    if not isinstance(manifest, dict):
        add("manifest", "缺少 manifest.json")
        return issues
    _check_manifest(manifest, add)
    draft_list = (package.get("drafts") or {}).get("drafts")
    if not isinstance(draft_list, list):
        add("drafts", "drafts.json 必须包含 drafts 数组")
        draft_list = []
    transcript_events = _check_transcripts(package.get("transcripts"), draft_list, add)
    locator_failures = _check_drafts(draft_list, transcript_events, add)
    review_items = _check_review(package.get("review"), manifest, draft_list, add)
    _check_review_against_drafts(review_items, draft_list, add)
    if manifest.get("draft_count") != len(draft_list):
        add("manifest.draft_count", "必须等于 drafts 条目数")
    if manifest.get("locator_failure_count") != locator_failures:
        add("manifest.locator_failure_count", "必须等于实际定位失败数")
    if acceptance_manifest is not None:
        _check_against_manifest(
            draft_list,
            acceptance_manifest,
            (manifest.get("input") or {}).get("dataset_id"),
            add,
        )
    return issues


def _check_manifest(manifest: dict[str, Any], add: Any) -> None:
    if manifest.get("schema_version") != PACKAGE_SCHEMA:
        add("manifest.schema_version", f"必须是 {PACKAGE_SCHEMA!r}")
    for key in ("package_id", "annotation_version", "generated_at"):
        if not isinstance(manifest.get(key), str) or not manifest.get(key, "").strip():
            add(f"manifest.{key}", "必须是非空字符串")
    generator = manifest.get("generator")
    if not isinstance(generator, dict):
        add("manifest.generator", "必须记录生成器身份")
    else:
        for key in ("kind", "id", "version"):
            if not isinstance(generator.get(key), str) or not generator.get(key, "").strip():
                add(f"manifest.generator.{key}", "必须是非空字符串")
        for key in ("model", "prompt_template"):
            if key not in generator:
                add(
                    f"manifest.generator.{key}",
                    "必须显式记录（未使用辅助模型或模板时为 null）",
                )
    if not isinstance(manifest.get("disclaimers"), list) or not manifest["disclaimers"]:
        add("manifest.disclaimers", "必须声明参考标注不是来源真值")

    taxonomy = manifest.get("category_taxonomy")
    if not isinstance(taxonomy, dict):
        add("manifest.category_taxonomy", "必须声明项目参考类别词表")
    else:
        if taxonomy.get("id") != CATEGORY_TAXONOMY_ID:
            add(
                "manifest.category_taxonomy.id",
                f"必须是 {CATEGORY_TAXONOMY_ID!r}",
            )
        if taxonomy.get("categories") != list(REFERENCE_CATEGORIES):
            add("manifest.category_taxonomy.categories", "必须匹配版本化受控词表")

    layers = manifest.get("layers")
    if not isinstance(layers, list) or not layers:
        add("manifest.layers", "必须声明 source / reference / detected 三层")
    else:
        names = {
            layer.get("layer") for layer in layers if isinstance(layer, dict)
        }
        if names != {"source", "reference", "detected"}:
            add("manifest.layers", "必须且只能声明 source / reference / detected 三层")
        detected = [
            layer
            for layer in layers
            if isinstance(layer, dict) and layer.get("layer") == "detected"
        ]
        if detected and detected[0].get("included") is not False:
            add("manifest.layers", "detected 结果不得并入参考标注审阅包")

    inputs = manifest.get("input")
    if not isinstance(inputs, dict):
        add("manifest.input", "必须记录输入数据版本")
        inputs = {}
    for key in (
        "dataset_id",
        "dataset_version",
        "dataset_sha256",
        "acceptance_manifest_id",
        "acceptance_content_sha256",
    ):
        if not isinstance(inputs.get(key), str) or not inputs.get(key, "").strip():
            add(f"manifest.input.{key}", "必须是非空字符串")
    if inputs.get("sample_count") != manifest.get("draft_count"):
        add("manifest.input.sample_count", "必须等于 draft_count")

    draft_count = manifest.get("draft_count")
    if (
        not isinstance(draft_count, int)
        or isinstance(draft_count, bool)
        or draft_count < 0
    ):
        add("manifest.draft_count", "必须是非负整数")
    locator_failure_count = manifest.get("locator_failure_count")
    if (
        not isinstance(locator_failure_count, int)
        or isinstance(locator_failure_count, bool)
        or locator_failure_count < 0
    ):
        add("manifest.locator_failure_count", "必须是非负整数")


def _check_transcripts(
    transcripts: Any, draft_list: list[dict[str, Any]], add: Any
) -> dict[str, set[str]]:
    events_by_draft: dict[str, set[str]] = {}
    if not isinstance(transcripts, dict):
        add("transcripts", "缺少 transcripts.json")
        return events_by_draft
    if transcripts.get("schema_version") != TRANSCRIPTS_SCHEMA:
        add("transcripts.schema_version", f"必须是 {TRANSCRIPTS_SCHEMA!r}")
    entries = transcripts.get("transcripts")
    if not isinstance(entries, list):
        add("transcripts.transcripts", "必须是数组")
        return events_by_draft

    expected = {
        draft.get("draft_id")
        for draft in draft_list
        if isinstance(draft, dict) and isinstance(draft.get("draft_id"), str)
    }
    seen: set[str] = set()
    for position, entry in enumerate(entries):
        path = f"transcripts[{position}]"
        if not isinstance(entry, dict):
            add(path, "轨迹原文条目必须是对象")
            continue
        draft_id = entry.get("draft_id")
        if not isinstance(draft_id, str) or not draft_id:
            add(f"{path}.draft_id", "必须是非空字符串")
            continue
        if draft_id in seen:
            add(f"{path}.draft_id", f"轨迹原文条目重复：{draft_id}")
        seen.add(draft_id)
        events = entry.get("events")
        if not isinstance(events, list):
            add(f"{path}.events", "必须是数组")
            events = []
        identifiers: set[str] = set()
        for index, event in enumerate(events):
            event_path = f"{path}.events[{index}]"
            if not isinstance(event, dict):
                add(event_path, "事件必须是对象")
                continue
            event_id = event.get("event_id")
            if not isinstance(event_id, str) or not event_id:
                add(f"{event_path}.event_id", "必须是非空字符串")
                continue
            if event_id in identifiers:
                add(f"{event_path}.event_id", f"事件身份重复：{event_id}")
            identifiers.add(event_id)
            for key in ("type", "actor", "json_path", "record_index", "record_sha256"):
                if key not in event:
                    add(f"{event_path}.{key}", "缺少必需字段")
        events_by_draft[draft_id] = identifiers
        problem = entry.get("problem")
        if problem is not None and not isinstance(problem, dict):
            add(f"{path}.problem", "无法定位说明必须是对象或 null")
        if problem is None and not events:
            add(f"{path}.events", "可定位的轨迹不得没有任何事件")
    for draft_id in sorted(expected - seen):
        add("transcripts", f"草稿缺少轨迹原文：{draft_id}")
    for draft_id in sorted(seen - expected):
        add("transcripts", f"轨迹原文没有对应草稿：{draft_id}")
    return events_by_draft


def _check_drafts(
    draft_list: list[dict[str, Any]],
    transcript_events: dict[str, set[str]],
    add: Any,
) -> int:
    if not draft_list:
        add("drafts.drafts", "草稿不得为空")
    seen: set[str] = set()
    locator_failures = 0
    for position, draft in enumerate(draft_list):
        path = f"drafts[{position}]"
        if not isinstance(draft, dict):
            add(path, "草稿必须是对象")
            continue
        draft_id = draft.get("draft_id")
        if not isinstance(draft_id, str) or not draft_id:
            add(f"{path}.draft_id", "必须是非空字符串")
        elif draft_id in seen:
            add(f"{path}.draft_id", f"草稿身份重复：{draft_id}")
        else:
            seen.add(draft_id)
        if draft.get("layer") != "reference":
            add(f"{path}.layer", "草稿 layer 必须是 'reference'")
        if draft.get("status") != "pending":
            add(f"{path}.status", "build 产出的草稿必须是 pending")
        if draft.get("scope") not in SCOPES:
            add(f"{path}.scope", f"未知作用范围：{draft.get('scope')!r}")
        if not isinstance(draft.get("located"), bool):
            add(f"{path}.located", "必须是布尔值")
        if draft.get("scope") == "trajectory":
            if draft.get("located") is not False or draft.get("origin_event") is not None:
                add(f"{path}.located", "轨迹级草稿必须 located=false 且无起源事件")
        elif draft.get("scope") in EVENT_SCOPES:
            origin = draft.get("origin_event")
            if not isinstance(origin, str) or not origin:
                add(f"{path}.origin_event", "事件级草稿必须记录起源事件身份")
            elif origin not in transcript_events.get(str(draft_id), set()):
                add(f"{path}.origin_event", "起源事件不在该样本的轨迹原文中")
        if draft.get("basis") != "source_label_projection":
            add(f"{path}.basis", "未确认的草稿必须记录其依据为来源投影")
        if not isinstance(draft.get("generator"), dict):
            add(f"{path}.generator", "必须记录生成器身份")
        if not isinstance(draft.get("input"), dict):
            add(f"{path}.input", "必须记录输入数据版本")
        for key in ("annotation_version", "generated_at"):
            if not isinstance(draft.get(key), str) or not draft.get(key, "").strip():
                add(f"{path}.{key}", "必须是非空字符串")
        for key in ("verdict", "reason"):
            if not isinstance(draft.get(key), str) or not draft.get(key, "").strip():
                add(f"{path}.{key}", "必须是非空字符串")
        if not isinstance(draft.get("categories"), list):
            add(f"{path}.categories", "必须是数组")

        evidence = draft.get("evidence")
        if not isinstance(evidence, list):
            add(f"{path}.evidence", "必须是数组")
            evidence = []
        elif draft.get("scope") in EVENT_SCOPES and not evidence:
            add(f"{path}.evidence", "事件级草稿至少需要一条可定位证据")
        for index, item in enumerate(evidence):
            _check_evidence_entry(
                item, f"{path}.evidence[{index}]", transcript_events.get(str(draft_id)), add
            )
            if not isinstance(item, dict) or item.get("located") is not True:
                locator_failures += 1

        failures = draft.get("locator_failures")
        if not isinstance(failures, list):
            add(f"{path}.locator_failures", "必须是数组")
        elif failures:
            locator_failures += len(failures)
        if len(failures) != sum(
            1
            for item in evidence
            if isinstance(item, dict) and item.get("located") is not True
        ):
            add(f"{path}.locator_failures", "必须与未定位证据逐条对应")

        transcript = draft.get("transcript")
        if not isinstance(transcript, dict):
            add(f"{path}.transcript", "必须指向轨迹原文")
        elif transcript.get("file") != "transcripts.json":
            add(f"{path}.transcript.file", "必须是 'transcripts.json'")
        elif transcript.get("event_count") != len(
            transcript_events.get(str(draft_id), set())
        ):
            add(f"{path}.transcript.event_count", "必须与轨迹原文事件数一致")
    return locator_failures


def _check_evidence_entry(
    item: Any, path: str, event_ids: set[str] | None, add: Any
) -> None:
    if not isinstance(item, dict):
        add(path, "证据引用必须是对象")
        return
    for key in EVIDENCE_REQUIRED_KEYS:
        if key not in item:
            add(f"{path}.{key}", "缺少必需字段")
    if item.get("kind") not in EVIDENCE_KINDS:
        add(f"{path}.kind", f"未知证据类型：{item.get('kind')!r}")
    json_path = item.get("json_path")
    if not isinstance(json_path, str) or not json_path:
        add(f"{path}.json_path", "必须是非空字符串")
    quote = item.get("quote")
    if not isinstance(quote, str) or not quote:
        add(f"{path}.quote", "必须是非空字符串")
    char_range = item.get("char_range")
    if char_range is not None:
        if (
            not isinstance(char_range, list)
            or len(char_range) != 2
            or not all(isinstance(part, int) for part in char_range)
            or char_range[0] < 0
            or char_range[1] < char_range[0]
        ):
            add(f"{path}.char_range", "必须是 [start, end] 两个非负整数")
    if not isinstance(item.get("record_index"), int) or isinstance(
        item.get("record_index"), bool
    ):
        add(f"{path}.record_index", "必须是整数")
    if not isinstance(item.get("record_sha256"), str) or len(item["record_sha256"]) != 64:
        add(f"{path}.record_sha256", "必须是 SHA-256 十六进制字符串")
    if not isinstance(item.get("located"), bool):
        add(f"{path}.located", "必须是布尔值")
    if item.get("located") is True and item.get("failure") is not None:
        add(f"{path}.failure", "已定位的证据不得同时记录定位失败")
    if item.get("located") is False and not isinstance(item.get("failure"), str):
        add(f"{path}.failure", "未定位的证据必须说明失败原因")
    event_id = item.get("event_id")
    if item.get("kind") in ("event", "tool_fact"):
        if not isinstance(event_id, str) or not event_id:
            add(f"{path}.event_id", "事件级证据必须记录稳定事件身份")
        elif event_ids is not None and event_id not in event_ids:
            add(f"{path}.event_id", "事件身份不在该样本的轨迹原文中")


def _check_review(
    review: Any,
    manifest: dict[str, Any],
    draft_list: list[dict[str, Any]],
    add: Any,
) -> list[dict[str, Any]]:
    if not isinstance(review, dict):
        add("review", "缺少 review.json")
        return []
    if review.get("schema_version") != REVIEW_SCHEMA:
        add("review.schema_version", f"必须是 {REVIEW_SCHEMA!r}")
    if review.get("package_id") != manifest.get("package_id"):
        add("review.package_id", "必须与 manifest.package_id 一致")
    expected_status = review_status(review)
    if review.get("status") not in REVIEW_STATUSES:
        add("review.status", f"未知审阅状态：{review.get('status')!r}")
    elif review.get("status") != expected_status:
        add(
            "review.status",
            f"必须与审阅进度一致，当前应为 {expected_status!r}；请运行 reference refresh",
        )
    _check_assistance(review.get("assistance"), "review.assistance", add)
    review_items = review.get("items")
    if not isinstance(review_items, list):
        add("review.items", "必须是数组")
        return []
    draft_ids = {
        draft.get("draft_id") for draft in draft_list if isinstance(draft, dict)
    }
    seen: set[str] = set()
    for position, item in enumerate(review_items):
        path = f"review.items[{position}]"
        if not isinstance(item, dict):
            add(path, "确认项必须是对象")
            continue
        draft_id = item.get("draft_id")
        if isinstance(draft_id, str) and draft_id:
            if draft_id in seen:
                add(f"{path}.draft_id", f"确认项重复：{draft_id}")
            seen.add(draft_id)
        else:
            add(f"{path}.draft_id", "必须是非空字符串")
        if item.get("decision") not in DECISIONS:
            add(f"{path}.decision", f"未知决定：{item.get('decision')!r}")
        field_decisions = item.get("field_decisions")
        if not isinstance(field_decisions, dict):
            add(f"{path}.field_decisions", "必须逐字段记录决定")
            field_decisions = {}
        else:
            for field in REVISABLE_FIELDS:
                if field not in field_decisions:
                    add(f"{path}.field_decisions.{field}", "必须显式记录（未决定时为 null）")
                    continue
                value = field_decisions[field]
                if value not in FIELD_DECISIONS:
                    add(f"{path}.field_decisions.{field}", f"未知决定：{value!r}")
            for field in field_decisions:
                if field not in REVISABLE_FIELDS:
                    add(f"{path}.field_decisions.{field}", "不允许对该字段作决定")
        _check_review_decision(item, field_decisions, path, add)
    for draft_id in sorted(draft_ids - seen):
        add("review.items", f"草稿缺少对应确认项：{draft_id}")
    for draft_id in sorted(seen - draft_ids):
        add("review.items", f"确认项没有对应草稿：{draft_id}")
    all_decided = bool(review_items) and all(
        isinstance(item, dict) and item.get("decision") in DECISIONS[1:]
        for item in review_items
    )
    if all_decided:
        if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
            add("review.reviewer", "完成审阅必须记录非空人工审阅人")
        if not isinstance(review.get("reviewed_at"), str) or not review["reviewed_at"].strip():
            add("review.reviewed_at", "完成审阅必须记录确认时间")
    return review_items


def _check_assistance(assistance: Any, path: str, add: Any) -> None:
    if not isinstance(assistance, dict):
        add(path, "必须显式记录是否使用 AI 辅助")
        return
    mode = assistance.get("mode")
    if mode not in ASSISTANCE_MODES:
        add(f"{path}.mode", f"未知辅助模式：{mode!r}")
        return
    if assistance.get("final_authority") != "human":
        add(f"{path}.final_authority", "最终裁决者必须是 human")
    if mode == "none":
        for key in ("product", "model", "prompt_protocol"):
            if assistance.get(key) is not None:
                add(f"{path}.{key}", "未使用 AI 辅助时必须为 null")
        return
    for key in ("product", "model", "prompt_protocol"):
        if not isinstance(assistance.get(key), str) or not assistance[key].strip():
            add(f"{path}.{key}", "AI 辅助审阅必须记录非空值")


def _check_review_decision(
    item: dict[str, Any], field_decisions: dict[str, Any], path: str, add: Any
) -> None:
    decision = item.get("decision")
    if decision is None:
        if item.get("revised") is not None:
            add(f"{path}.revised", "未作决定时不得携带修订内容")
        if isinstance(item.get("note"), str) and item["note"].strip():
            add(f"{path}.note", "未作决定时不应写备注")
        return
    note = item.get("note")
    if not isinstance(note, str) or not note.strip():
        add(f"{path}.note", "人工决定必须留下说明")
    if decision == "reject":
        if item.get("revised") is not None:
            add(f"{path}.revised", "reject 不得携带修订内容")
        return
    undecided = [
        field for field in REVISABLE_FIELDS if field_decisions.get(field) is None
    ]
    if undecided:
        add(
            f"{path}.field_decisions",
            f"{decision} 必须逐字段签字，未决定：{', '.join(undecided)}",
        )
    revised = item.get("revised")
    if decision == "accept":
        if revised is not None:
            add(f"{path}.revised", "accept 不得携带修订内容")
        if any(value == "revise" for value in field_decisions.values()):
            add(f"{path}.field_decisions", "accept 不应包含逐字段修订")
        return
    if not isinstance(revised, dict) or not revised:
        add(f"{path}.revised", "revise 必须记录修订内容")
        return
    for key in revised:
        if key not in REVISABLE_FIELDS:
            add(f"{path}.revised.{key}", "不允许修订该字段")
        elif field_decisions.get(key) != "revise":
            add(
                f"{path}.revised.{key}",
                "只有字段决定为 revise 才能出现在修订内容中",
            )
    for field, value in field_decisions.items():
        if value == "revise" and field not in revised:
            add(f"{path}.revised.{field}", "字段决定为 revise 但缺少修订内容")


def _check_review_against_drafts(
    review_items: list[dict[str, Any]], draft_list: list[dict[str, Any]], add: Any
) -> None:
    drafts = {
        draft.get("draft_id"): draft
        for draft in draft_list
        if isinstance(draft, dict) and isinstance(draft.get("draft_id"), str)
    }
    for position, item in enumerate(review_items):
        if not isinstance(item, dict):
            continue
        draft = drafts.get(item.get("draft_id"))
        if draft is None:
            continue
        digest = item.get("draft_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            add(f"review.items[{position}].draft_sha256", "必须记录原始草稿哈希")
        elif digest != sha256_json(draft):
            add(
                f"review.items[{position}].draft_sha256",
                "原始草稿已被改写：确认项哈希与当前草稿不一致",
            )
        decision = item.get("decision")
        if decision not in ("accept", "revise"):
            continue
        merged = dict(draft)
        if decision == "revise" and isinstance(item.get("revised"), dict):
            merged.update(item["revised"])
        categories = merged.get("categories")
        if not isinstance(categories, list) or not all(
            isinstance(category, str) for category in categories
        ):
            add(f"review.items[{position}].categories", "生效类别必须是字符串数组")
            continue
        unknown = sorted(set(categories) - set(REFERENCE_CATEGORIES))
        if unknown:
            add(
                f"review.items[{position}].categories",
                f"包含不在 {CATEGORY_TAXONOMY_ID} 中的类别：{', '.join(unknown)}",
            )
        verdict = merged.get("verdict")
        if verdict == "unsafe" and not categories:
            add(
                f"review.items[{position}].categories",
                "unsafe 生效标注必须由人工确认至少一个项目参考类别",
            )
        if verdict == "safe" and categories:
            add(
                f"review.items[{position}].categories",
                "safe 生效标注的项目风险类别必须为空",
            )


def _check_against_manifest(
    draft_list: list[dict[str, Any]],
    acceptance_manifest: dict[str, Any],
    dataset_id: Any,
    add: Any,
) -> None:
    declared = {
        source.get("dataset_id")
        for source in acceptance_manifest.get("sources") or []
        if isinstance(source, dict)
    }
    if not isinstance(dataset_id, str) or dataset_id not in declared:
        add("manifest.input.dataset_id", "数据集不在验收清单的来源声明中")
        return
    expected_samples = [
        sample
        for sample in acceptance_manifest.get("samples") or []
        if isinstance(sample, dict) and sample.get("dataset_id") == dataset_id
    ]
    if not expected_samples:
        add("drafts", "验收清单中没有可用于生成草稿的样本")
        return
    expected_drafts = {f"ref-{sample['sample_id']}" for sample in expected_samples}
    actual = {
        draft.get("draft_id") for draft in draft_list if isinstance(draft, dict)
    }
    if expected_drafts != actual:
        add("drafts", "草稿集合必须与验收清单的 ATBench 样本一致")
    by_sample = {sample["sample_id"]: sample for sample in expected_samples}
    for draft in draft_list:
        if not isinstance(draft, dict):
            continue
        draft_id = draft.get("draft_id")
        sample = by_sample.get(draft.get("sample_id"))
        if sample is None:
            add(f"drafts.{draft_id}", "样本身份不在验收清单中")
            continue
        inputs = draft.get("input") or {}
        if inputs.get("record_index") != sample["record_index"]:
            add(f"drafts.{draft_id}.input", "记录下标与验收清单不一致")
        if inputs.get("record_sha256") != sample["record_sha256"]:
            add(f"drafts.{draft_id}.input", "记录哈希与验收清单不一致")
        if inputs.get("acceptance_content_sha256") != (
            acceptance_manifest.get("integrity") or {}
        ).get("content_sha256"):
            add(f"drafts.{draft_id}.input", "验收清单哈希不一致")


def verify_against_dataset(
    package: dict[str, Any], dataset_root: Path
) -> list[dict[str, str]]:
    """重新读取固定版本数据集，逐条核对记录哈希、证据引用与事件路径。"""
    issues: list[dict[str, str]] = []

    def add(path: str, message: str) -> None:
        issues.append({"path": path, "message": message})

    manifest = package.get("manifest") or {}
    inputs = manifest.get("input") or {}
    dataset_id = inputs.get("dataset_id")
    if not isinstance(dataset_id, str) or dataset_id not in pinned.BY_DATASET:
        add("manifest.input.dataset_id", "未固定的数据集")
        return issues
    entry = pinned.pinned(dataset_id)
    path = dataset_path(dataset_root, dataset_id)
    if not path.is_file():
        add("dataset", f"数据集文件不存在：{path}")
        return issues
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != inputs.get("dataset_sha256"):
        add("manifest.input.dataset_sha256", "数据集文件哈希与本包记录不一致")
        return issues
    if entry["sha256"] != inputs.get("dataset_sha256"):
        add("manifest.input.dataset_sha256", "数据集文件哈希与固定清单不一致")
        return issues
    if entry["dataset_version"] != inputs.get("dataset_version"):
        add("manifest.input.dataset_version", "数据集版本与固定清单不一致")
    records = load_dataset(path)
    draft_list = (package.get("drafts") or {}).get("drafts") or []
    transcripts = {
        item.get("draft_id"): item
        for item in (package.get("transcripts") or {}).get("transcripts") or []
        if isinstance(item, dict)
    }

    for position, draft in enumerate(draft_list):
        path_label = f"drafts[{position}]"
        if not isinstance(draft, dict):
            add(path_label, "草稿必须是对象")
            continue
        inputs = draft.get("input") or {}
        record_index = inputs.get("record_index")
        if not isinstance(record_index, int) or not (
            0 <= record_index < len(records)
        ):
            add(f"{path_label}.input.record_index", "记录下标超出数据集范围")
            continue
        raw = records[record_index]
        if sha256_json(raw) != inputs.get("record_sha256"):
            add(f"{path_label}.input.record_sha256", "记录内容哈希与草稿不一致")
            continue
        for index, item in enumerate(draft.get("evidence") or []):
            if not isinstance(item, dict):
                continue
            item_path = f"{path_label}.evidence[{index}]"
            if item.get("record_sha256") != inputs.get("record_sha256"):
                add(f"{item_path}.record_sha256", "证据的原始记录哈希与草稿不一致")
            if item.get("record_index") != record_index:
                add(f"{item_path}.record_index", "证据的原始记录位置与草稿不一致")
            problem = check_evidence(item, raw)
            if problem is None and item.get("located") is not True:
                add(f"{item_path}.located", "证据可回读但被标记为未定位")
            elif problem is not None and item.get("failure") != problem:
                add(f"{item_path}.failure", f"定位失败原因与实况不一致：{problem}")

        transcript = transcripts.get(draft.get("draft_id")) or {}
        for index, event in enumerate(transcript.get("events") or []):
            if not isinstance(event, dict):
                continue
            if event.get("record_sha256") != inputs.get("record_sha256"):
                add(
                    f"transcripts.{draft.get('draft_id')}.events[{index}].record_sha256",
                    "事件的原始记录哈希与草稿不一致",
                )
            found, value = locate_json_path(raw, event.get("json_path") or "")
            if not found:
                add(
                    f"transcripts.{draft.get('draft_id')}.events[{index}].json_path",
                    "事件路径无法在原始记录中回读",
                )
            elif isinstance(value, dict) and event.get("type") is None:
                add(
                    f"transcripts.{draft.get('draft_id')}.events[{index}]",
                    "事件缺少类型",
                )
    return issues


# ---------------------------------------------------------------------------
# 生效参考标注
# ---------------------------------------------------------------------------


def effective_labels(
    drafts: list[dict[str, Any]],
    review: dict[str, Any],
    transcripts: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """只把人工确认过、且引用自洽的草稿转成生效参考标注。

    返回 ``(labels, problems)``。``pending`` 与 ``reject`` 一律不产生标签，
    因此 H2 指标与正式审阅页面在人工确认前读不到任何生效参考标注。
    """
    labels: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    items = {
        item["draft_id"]: item
        for item in review.get("items") or []
        if isinstance(item, dict) and isinstance(item.get("draft_id"), str)
    }
    event_ids_by_draft: dict[str, set[str]] = {}
    if transcripts is not None:
        for entry in transcripts.get("transcripts") or []:
            if not isinstance(entry, dict):
                continue
            event_ids_by_draft[entry.get("draft_id")] = {
                event.get("event_id")
                for event in entry.get("events") or []
                if isinstance(event, dict) and isinstance(event.get("event_id"), str)
            }

    for draft in drafts:
        draft_id = draft.get("draft_id")
        item = items.get(draft_id)
        if item is None:
            problems.append({"draft_id": draft_id, "code": "review_item_missing"})
            continue
        decision = item.get("decision")
        if decision in (None, "reject"):
            continue

        merged = dict(draft)
        if decision == "revise":
            revised = item.get("revised") or {}
            unknown = sorted(set(revised) - set(REVISABLE_FIELDS))
            if unknown:
                problems.append(
                    {
                        "draft_id": draft_id,
                        "code": "unknown_revised_field",
                        "fields": unknown,
                    }
                )
                continue
            merged.update(revised)

        scope = merged.get("scope")
        if scope not in SCOPES:
            problems.append(
                {"draft_id": draft_id, "code": "unknown_scope", "scope": scope}
            )
            continue
        categories = merged.get("categories")
        if not isinstance(categories, list) or not all(
            isinstance(category, str) for category in categories
        ):
            problems.append({"draft_id": draft_id, "code": "invalid_categories"})
            continue
        unknown_categories = sorted(set(categories) - set(REFERENCE_CATEGORIES))
        if unknown_categories:
            problems.append(
                {
                    "draft_id": draft_id,
                    "code": "unknown_reference_category",
                    "categories": unknown_categories,
                }
            )
            continue
        if merged.get("verdict") == "unsafe" and not categories:
            problems.append(
                {"draft_id": draft_id, "code": "unsafe_without_reference_category"}
            )
            continue
        if merged.get("verdict") == "safe" and categories:
            problems.append(
                {"draft_id": draft_id, "code": "safe_with_reference_category"}
            )
            continue
        evidence = merged.get("evidence") or []
        unlocated = [
            entry
            for entry in evidence
            if not isinstance(entry, dict) or entry.get("located") is not True
        ]
        if unlocated:
            problems.append(
                {
                    "draft_id": draft_id,
                    "code": "evidence_not_located",
                    "paths": [
                        entry.get("json_path") if isinstance(entry, dict) else None
                        for entry in unlocated
                    ],
                }
            )
            continue

        if scope in EVENT_SCOPES:
            origin = merged.get("origin_event")
            if not isinstance(origin, str) or not origin:
                problems.append(
                    {"draft_id": draft_id, "code": "event_scope_without_origin"}
                )
                continue
            known_events = event_ids_by_draft.get(str(draft_id))
            if known_events is not None and origin not in known_events:
                problems.append(
                    {
                        "draft_id": draft_id,
                        "code": "origin_event_not_in_transcript",
                        "origin_event": origin,
                    }
                )
                continue
            event_evidence = [
                entry
                for entry in evidence
                if isinstance(entry, dict) and entry.get("kind") in ("event", "tool_fact")
            ]
            if not event_evidence:
                problems.append(
                    {
                        "draft_id": draft_id,
                        "code": "event_scope_without_locatable_evidence",
                    }
                )
                continue
            scope_event_ids = [
                entry["event_id"]
                for entry in event_evidence
                if isinstance(entry.get("event_id"), str) and entry["event_id"]
            ]
        else:
            scope_event_ids = []

        labels.append(
            {
                "label_id": draft_id,
                "layer": "reference",
                "status": "confirmed",
                "scope": scope,
                "source_step_id": merged.get("source_step_id"),
                "event_ids": sorted(set(scope_event_ids)),
                "located": scope in EVENT_SCOPES,
                "verdict": merged.get("verdict"),
                "categories": list(categories),
                "source_categories": list(merged.get("source_categories") or []),
                "reason": merged.get("reason"),
                "mapping": {
                    "version": MAPPING_VERSION,
                    "status": "human_confirmed",
                    "basis": f"review.json decision={decision}",
                },
                "review": {
                    "decision": decision,
                    "field_decisions": item.get("field_decisions") or {},
                    "note": item.get("note"),
                    "reviewer": review.get("reviewer"),
                    "reviewed_at": review.get("reviewed_at"),
                },
                "original_draft": draft,
            }
        )
    return labels, problems


def export_effective(
    package: dict[str, Any], *, exported_at: str | None = None
) -> dict[str, Any]:
    draft_list = (package.get("drafts") or {}).get("drafts") or []
    review = package.get("review") or {}
    complete = review_is_complete(review)
    if complete:
        labels, problems = effective_labels(
            draft_list, review, package.get("transcripts")
        )
    else:
        labels, problems = [], []
    return {
        "schema_version": EFFECTIVE_SCHEMA,
        "package_id": (package.get("manifest") or {}).get("package_id"),
        "annotation_version": (package.get("manifest") or {}).get("annotation_version"),
        "exported_at": exported_at or now(),
        "status": "confirmed" if complete else review_status(review),
        "draft_count": len(draft_list),
        "effective_count": len(labels),
        "problem_count": len(problems),
        "layer": "reference",
        "category_taxonomy": (package.get("manifest") or {}).get(
            "category_taxonomy"
        ),
        "review": {
            "reviewer": review.get("reviewer"),
            "reviewed_at": review.get("reviewed_at"),
            "assistance": review.get("assistance"),
        },
        "note": (
            "只有 review.json 中已确认且引用自洽的草稿会出现在 labels 中；"
            "pending / reject 一律不生效。"
        ),
        "labels": labels,
        "problems": problems,
    }


def _assistance_is_valid(assistance: Any) -> bool:
    if not isinstance(assistance, dict):
        return False
    mode = assistance.get("mode")
    if mode not in ASSISTANCE_MODES or assistance.get("final_authority") != "human":
        return False
    if mode == "none":
        return all(
            assistance.get(key) is None
            for key in ("product", "model", "prompt_protocol")
        )
    return all(
        isinstance(assistance.get(key), str) and assistance[key].strip()
        for key in ("product", "model", "prompt_protocol")
    )


def review_status(review: dict[str, Any]) -> str:
    items = [item for item in review.get("items") or [] if isinstance(item, dict)]
    decided = [item for item in items if item.get("decision") in DECISIONS[1:]]
    if not decided:
        return "pending"
    metadata_complete = (
        isinstance(review.get("reviewer"), str)
        and bool(review["reviewer"].strip())
        and isinstance(review.get("reviewed_at"), str)
        and bool(review["reviewed_at"].strip())
        and _assistance_is_valid(review.get("assistance"))
    )
    if len(decided) == len(items) and items and metadata_complete:
        return "confirmed"
    return "in_progress"


def review_is_complete(review: dict[str, Any]) -> bool:
    return review_status(review) == "confirmed"


def review_is_pending(review: dict[str, Any]) -> bool:
    """兼容旧调用方：只要整版尚未完成，便仍视为 pending。"""
    return not review_is_complete(review)


__all__ = [
    "DEFAULT_MANIFEST_ID",
    "DEFAULT_PACKAGE_ID",
    "DISCLAIMERS",
    "DRAFTS_SCHEMA",
    "DRAFT_SCHEMA",
    "EFFECTIVE_SCHEMA",
    "PACKAGE_SCHEMA",
    "REVIEW_SCHEMA",
    "CATEGORY_TAXONOMY_ID",
    "REFERENCE_CATEGORIES",
    "REVISABLE_FIELDS",
    "TRANSCRIPTS_SCHEMA",
    "build_package",
    "check_evidence",
    "effective_labels",
    "export_effective",
    "load_package",
    "review_is_pending",
    "review_is_complete",
    "review_status",
    "refresh_review_artifacts",
    "validate_package",
    "verify_against_dataset",
    "write_package",
]
