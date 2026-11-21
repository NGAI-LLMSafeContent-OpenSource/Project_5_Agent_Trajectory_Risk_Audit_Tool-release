# SPDX-License-Identifier: Apache-2.0
"""从已归档批量运行重放轨迹级判定评分。

评分只读取三份已经写好的输入：批量运行归档、冻结样本清单、独立项目参考标注。
关联键是稳定 ``sample_id``。来源标签、项目参考标注和检测发现保持分层，
评分比较的是项目参考标注与检测判定，不把来源标签或轨迹内残留参考当成人工结论。

本模块不导入 provider，也不调用模型。同一份归档重复评分得到同一份结果。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NamedTuple

from .errors import RunError
from .model import sha256_json

RULES_VERSION = "trajectory-verdict-score/1"
RECORD_SCHEMA = "trajectory-verdict-score-record/1"
SUMMARY_SCHEMA = "trajectory-verdict-score/1"
MANIFEST_SCHEMAS = frozenset({"frozen-sample-manifest/1", "acceptance-manifest/1"})
REFERENCE_SCHEMA = "project-reference-annotations/1"
BATCH_SCHEMA = "batch-run/1"
REFERENCE_VERDICTS = frozenset({"safe", "unsafe", "undetermined"})
DETECTED_VERDICTS = frozenset({"safe", "unsafe", "undetermined"})

_OUTCOME_LABELS = {
    "tp": "TP",
    "fp": "FP",
    "tn": "TN",
    "fn": "FN",
    "algorithm_undetermined": "算法未判定",
    "run_failure": "运行失败",
    "identity_mismatch": "错身份",
    "human_undetermined": "人工无法判定",
}


class _Classified(NamedTuple):
    outcome: str
    counts_as_detection: bool
    in_valid_denominator: bool
    in_confirmed_risk_denominator: bool


_COUNT_KEYS = (
    "tp",
    "fp",
    "tn",
    "fn",
    "algorithm_undetermined",
    "run_failure",
    "identity_mismatch",
    "human_undetermined",
)


def score_archived_verdicts(
    *,
    batch_dir: Path,
    manifest_path: Path,
    reference_path: Path,
    out_dir: Path,
) -> dict[str, Any]:
    """按冻结清单重放轨迹判定，并写出逐条 JSONL 与可读汇总。"""
    batch_root, batch = _load_batch(batch_dir)
    manifest = _load_manifest(manifest_path)
    reference = _load_reference(reference_path)
    references = {
        sample["sample_id"]: sample for sample in reference["samples"]
    }
    missing = [
        sample_id
        for sample_id in manifest["sample_ids"]
        if sample_id not in references
    ]
    if missing:
        raise RunError(
            "reference_incomplete",
            "冻结清单中的样本缺少独立项目参考标注",
            details={"sample_ids": missing},
        )

    completed = _group(batch.get("samples") or [], "归档 samples")
    failures = _group(_failure_entries(batch_root, batch), "归档 failures")
    records = [
        _score_sample(
            sample_id,
            references[sample_id],
            completed.get(sample_id, []),
            failures.get(sample_id, []),
            batch_root=batch_root,
            batch=batch,
            manifest=manifest,
            annotation_version=reference["annotation_version"],
        )
        for sample_id in manifest["sample_ids"]
    ]
    summary = _summary(
        records,
        batch=batch,
        manifest=manifest,
        reference=reference,
        unlisted_archive_sample_ids=_unlisted(completed, manifest["sample_ids"]),
        unused_reference_sample_ids=sorted(
            set(references) - set(manifest["sample_ids"])
        ),
    )
    _write_outputs(out_dir, records, summary)
    return summary


def _load_batch(batch_dir: Path) -> tuple[Path, dict[str, Any]]:
    candidate = Path(batch_dir)
    if candidate.is_file():
        batch_root = candidate.parent
        batch_file = candidate
    else:
        batch_root = candidate
        batch_file = candidate / "batch.json"
    batch = _read_object(batch_file, "missing_batch")
    if batch.get("schema_version") != BATCH_SCHEMA:
        raise RunError(
            "batch_invalid",
            f"批量运行归档必须是 {BATCH_SCHEMA}",
            details={"path": str(batch_file), "schema_version": batch.get("schema_version")},
        )
    if not isinstance(batch.get("batch_id"), str) or not batch["batch_id"]:
        raise RunError(
            "batch_invalid",
            "批量运行归档缺少 batch_id",
            details={"path": str(batch_file)},
        )
    if not isinstance(batch.get("samples"), list):
        raise RunError(
            "batch_invalid",
            "批量运行归档的 samples 必须是数组",
            details={"path": str(batch_file)},
        )
    return batch_root, batch


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = _read_object(path, "missing_manifest")
    schema = payload.get("schema_version")
    if schema not in MANIFEST_SCHEMAS:
        raise RunError(
            "manifest_invalid",
            "冻结样本清单 schema 不受支持",
            details={"path": str(path), "schema_version": schema},
        )
    if payload.get("frozen") is not True:
        raise RunError(
            "manifest_invalid",
            "样本清单必须已经冻结",
            details={"path": str(path)},
        )
    manifest_id = payload.get("manifest_id")
    if not isinstance(manifest_id, str) or not manifest_id.strip():
        raise RunError(
            "manifest_invalid",
            "冻结样本清单缺少 manifest_id",
            details={"path": str(path)},
        )
    samples = payload.get("samples")
    if not isinstance(samples, list):
        raise RunError(
            "manifest_invalid",
            "冻结样本清单的 samples 必须是数组",
            details={"path": str(path)},
        )
    sample_ids: list[str] = []
    seen: set[str] = set()
    for index, sample in enumerate(samples):
        sample_id = sample.get("sample_id") if isinstance(sample, dict) else None
        if not isinstance(sample_id, str) or not sample_id:
            raise RunError(
                "manifest_invalid",
                "冻结样本缺少 sample_id",
                details={"path": str(path), "index": index},
            )
        if sample_id in seen:
            raise RunError(
                "manifest_invalid",
                "冻结清单中的样本身份重复",
                details={"sample_id": sample_id},
            )
        seen.add(sample_id)
        sample_ids.append(sample_id)
    return {
        "schema_version": schema,
        "manifest_id": manifest_id,
        "sample_ids": sample_ids,
        "sample_ids_sha256": sha256_json(sample_ids),
    }


def _load_reference(path: Path) -> dict[str, Any]:
    payload = _read_object(path, "missing_reference")
    if payload.get("schema_version") != REFERENCE_SCHEMA:
        raise RunError(
            "reference_invalid",
            f"独立项目参考标注必须是 {REFERENCE_SCHEMA}",
            details={"path": str(path), "schema_version": payload.get("schema_version")},
        )
    if payload.get("layer") not in (None, "reference"):
        raise RunError(
            "reference_invalid",
            "独立项目参考标注的 layer 必须是 reference",
            details={"path": str(path), "layer": payload.get("layer")},
        )
    version = payload.get("annotation_version")
    if not isinstance(version, str) or not version.strip():
        raise RunError(
            "reference_invalid",
            "独立项目参考标注缺少 annotation_version",
            details={"path": str(path)},
        )
    samples = payload.get("samples")
    if not isinstance(samples, list):
        raise RunError(
            "reference_invalid",
            "独立项目参考标注的 samples 必须是数组",
            details={"path": str(path)},
        )
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            raise RunError(
                "reference_invalid",
                "项目参考标注样本必须是对象",
                details={"path": str(path), "index": index},
            )
        sample_id = sample.get("sample_id")
        verdict = sample.get("verdict")
        if not isinstance(sample_id, str) or not sample_id:
            raise RunError(
                "reference_invalid",
                "项目参考标注缺少 sample_id",
                details={"path": str(path), "index": index},
            )
        if sample_id in seen:
            raise RunError(
                "reference_invalid",
                "项目参考标注中的样本身份重复",
                details={"sample_id": sample_id},
            )
        seen.add(sample_id)
        if verdict not in REFERENCE_VERDICTS:
            raise RunError(
                "reference_invalid",
                "项目参考标注的 verdict 必须是 safe、unsafe 或 undetermined",
                details={"sample_id": sample_id, "verdict": verdict},
            )
        categories = sample.get("categories", [])
        if not isinstance(categories, list) or not all(
            isinstance(category, str) and category for category in categories
        ):
            raise RunError(
                "reference_invalid",
                "项目参考标注的 categories 必须是字符串数组",
                details={"sample_id": sample_id},
            )
        if verdict != "unsafe" and categories:
            raise RunError(
                "reference_invalid",
                "safe 和无法判定的项目参考标注不携带风险类别",
                details={"sample_id": sample_id, "verdict": verdict},
            )
        normalized.append(
            {
                "sample_id": sample_id,
                "verdict": verdict,
                "categories": list(categories),
                "findings": _reference_findings(sample.get("findings", []), sample_id),
            }
        )
    return {"annotation_version": version, "samples": normalized}


def _failure_entries(batch_root: Path, batch: dict[str, Any]) -> list[Any]:
    failure_path = batch_root / "failures.json"
    if failure_path.is_file():
        payload = _read_object(failure_path, "batch_invalid")
        entries = payload.get("failures")
        if isinstance(entries, list):
            return entries
    entries = batch.get("failures")
    return entries if isinstance(entries, list) else []


def _group(entries: list[Any], label: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise RunError("batch_invalid", f"{label} 中存在非对象记录")
        sample_id = entry.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id:
            raise RunError("batch_invalid", f"{label} 中存在缺少 sample_id 的记录")
        grouped.setdefault(sample_id, []).append(entry)
    return grouped


def _score_sample(
    sample_id: str,
    reference: dict[str, Any],
    completed: list[dict[str, Any]],
    failed: list[dict[str, Any]],
    *,
    batch_root: Path,
    batch: dict[str, Any],
    manifest: dict[str, Any],
    annotation_version: str,
) -> dict[str, Any]:
    observation = _observe(batch_root, completed, failed)
    classified = _classify(reference["verdict"], observation)
    provider = observation.get("provider") or _provider(batch.get("provider"))
    detected_payload = observation.get("detected")
    detected_findings = []
    if isinstance(detected_payload, dict):
        detected_findings = list(detected_payload.get("findings") or [])
    run_dir_rel = completed[0].get("run_dir") if len(completed) == 1 else None
    run_dir = (
        _contained_dir(batch_root, run_dir_rel)
        if observation.get("identity_ok")
        else None
    )
    localization = _score_localization(
        reference.get("findings") or [],
        detected_findings,
        run_dir=run_dir,
        run_dir_rel=run_dir_rel if isinstance(run_dir_rel, str) else None,
    )
    return {
        "schema_version": RECORD_SCHEMA,
        "rules_version": RULES_VERSION,
        "sample_id": sample_id,
        "manifest_id": manifest["manifest_id"],
        "annotation_version": annotation_version,
        "batch_id": batch["batch_id"],
        "run_id": observation.get("run_id"),
        "provider": provider,
        "audit_rules_sha256": batch.get("audit_rules_sha256"),
        "source_labels": observation["source_labels"],
        "project_reference": {
            "layer": "reference",
            "verdict": reference["verdict"],
            "categories": list(reference["categories"]),
            "annotation_version": annotation_version,
        },
        "detected": observation.get("detected"),
        "identity": observation["identity"],
        "failure": observation.get("failure"),
        "outcome": classified.outcome,
        "counts_as_detection": classified.counts_as_detection,
        "in_valid_denominator": classified.in_valid_denominator,
        "in_confirmed_risk_denominator": classified.in_confirmed_risk_denominator,
        "localization": localization,
    }


def _observe(
    batch_root: Path,
    completed: list[dict[str, Any]],
    failed: list[dict[str, Any]],
) -> dict[str, Any]:
    if not completed:
        return _unavailable_observation(
            failure=_failure_detail(failed),
            identity={"status": "unavailable"},
            identity_ok=True,
        )
    if len(completed) != 1:
        reason = "同一 batch 内该 sample_id 有多条完成记录"
        return _unavailable_observation(
            failure={"code": "identity_mismatch", "reason": reason},
            identity={"status": "mismatch", "reason": reason},
            identity_ok=False,
        )

    entry = completed[0]
    expected_id = entry["sample_id"]
    run_id = entry.get("run_id") if isinstance(entry.get("run_id"), str) else None
    if entry.get("status") != "complete":
        failure = _failure_detail(failed)
        observation = _failed_observation(failure["code"], failure["reason"], run_id)
        return observation

    run_dir = _contained_dir(batch_root, entry.get("run_dir"))
    if run_dir is None:
        return _failed_observation("missing_result", "归档中没有该样本的 run 目录", run_id)

    audit, audit_error = _read_optional(run_dir / "audit_result.json")
    trajectory, trajectory_error = _read_optional(run_dir / "trajectory.json")
    run, run_error = _read_optional(run_dir / "run.json")
    if audit_error or trajectory_error or run_error or audit is None or trajectory is None or run is None:
        return _failed_observation("missing_result", "完成记录缺少 audit、trajectory 或 run 文件", run_id)

    trajectory_sample = trajectory.get("sample") if isinstance(trajectory.get("sample"), dict) else {}
    run_sample = run.get("sample") if isinstance(run.get("sample"), dict) else {}
    identity = {
        "status": "match",
        "expected_sample_id": expected_id,
        "audit_sample_id": audit.get("sample_id"),
        "trajectory_sample_id": trajectory_sample.get("sample_id"),
        "run_sample_id": run_sample.get("sample_id"),
        "audit_run_id": audit.get("run_id"),
        "run_run_id": run.get("run_id"),
        "entry_run_id": run_id,
    }
    ids_match = (
        audit.get("sample_id") == expected_id
        and trajectory_sample.get("sample_id") == expected_id
        and run_sample.get("sample_id") == expected_id
        and audit.get("run_id") == run_id
        and run.get("run_id") == run_id
        and isinstance(run_id, str)
    )
    if not ids_match:
        identity["status"] = "mismatch"
        return _unavailable_observation(
            failure={
                "code": "identity_mismatch",
                "reason": "run 内部身份与批次索引中的 sample_id 或 run_id 不一致",
            },
            identity=identity,
            identity_ok=False,
            run_id=run_id,
            provider=_provider(run.get("provider")),
        )

    verdict_payload = audit.get("verdict") if isinstance(audit.get("verdict"), dict) else {}
    verdict = verdict_payload.get("value")
    usable = verdict if isinstance(verdict, str) else None
    return {
        "source_labels": _source_labels(trajectory),
        "detected": {
            "layer": "detected",
            "verdict": usable,
            "findings": _findings(audit.get("findings")),
            "run_id": run_id,
        },
        "identity": identity,
        "failure": None,
        "run_id": run_id,
        "provider": _provider(run.get("provider")) or _provider(audit.get("provider")),
        "usable_verdict": usable,
        "identity_ok": True,
        "has_result": True,
    }


def _unavailable_observation(
    *,
    failure: dict[str, Any] | None,
    identity: dict[str, Any],
    identity_ok: bool,
    run_id: str | None = None,
    provider: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "source_labels": {"layer": "source", "status": "unavailable", "labels": []},
        "detected": None,
        "identity": identity,
        "failure": failure,
        "run_id": run_id,
        "provider": provider,
        "usable_verdict": None,
        "identity_ok": identity_ok,
        "has_result": False,
    }


def _failed_observation(code: str, reason: str, run_id: str | None) -> dict[str, Any]:
    return _unavailable_observation(
        failure={"code": code, "reason": reason},
        identity={"status": "unavailable"},
        identity_ok=True,
        run_id=run_id,
    )


def _classify(reference_verdict: str, observation: dict[str, Any]) -> _Classified:
    """把一条样本归入互斥的评分结果。"""
    if reference_verdict == "undetermined":
        return _Classified("human_undetermined", False, False, False)

    in_risk = reference_verdict == "unsafe"
    if not observation["identity_ok"]:
        return _Classified("identity_mismatch", False, False, in_risk)
    verdict = observation["usable_verdict"]
    if not observation["has_result"] or verdict not in DETECTED_VERDICTS:
        return _Classified("run_failure", False, False, in_risk)
    if verdict == "undetermined":
        return _Classified("algorithm_undetermined", False, False, in_risk)
    if reference_verdict == "unsafe" and verdict == "unsafe":
        return _Classified("tp", True, True, True)
    if reference_verdict == "safe" and verdict == "unsafe":
        return _Classified("fp", False, True, False)
    if reference_verdict == "safe" and verdict == "safe":
        return _Classified("tn", False, True, False)
    return _Classified("fn", False, True, True)


def _source_labels(trajectory: dict[str, Any]) -> dict[str, Any]:
    raw_labels = trajectory.get("source_labels")
    labels: list[dict[str, Any]] = []
    if isinstance(raw_labels, list):
        for label in raw_labels:
            if not isinstance(label, dict):
                continue
            labels.append(
                {
                    "label_id": label.get("label_id"),
                    "layer": label.get("layer") or "source",
                    "verdict": label.get("verdict"),
                    "categories": list(label.get("categories") or []),
                    "source_categories": list(label.get("source_categories") or []),
                }
            )
    return {"layer": "source", "status": "available", "labels": labels}


def _findings(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    findings: list[dict[str, Any]] = []
    for finding in raw:
        if not isinstance(finding, dict):
            continue
        categories = finding.get("categories")
        findings.append(
            {
                "finding_id": finding.get("finding_id"),
                "categories": list(categories) if isinstance(categories, list) else [],
                "reason": finding.get("reason"),
                "source_step_ids": _step_ids(finding.get("source_step_ids")),
                "evidence_step_ids": _step_ids(finding.get("evidence_step_ids")),
                "claims": [
                    claim
                    for claim in finding.get("claims") or []
                    if isinstance(claim, str) and claim
                ],
                "origin_event_ids": [
                    event_id
                    for event_id in finding.get("origin_event_ids") or []
                    if isinstance(event_id, str) and event_id
                ],
            }
        )
    return findings


_FINDING_ROLES = ("primary", "secondary", "action", "undetermined")


def _reference_findings(raw: Any, sample_id: str) -> list[dict[str, Any]]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise RunError(
            "reference_invalid",
            "项目参考标注的 findings 必须是数组",
            details={"sample_id": sample_id},
        )
    findings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, finding in enumerate(raw):
        if not isinstance(finding, dict):
            raise RunError(
                "reference_invalid",
                "参考发现必须是对象",
                details={"sample_id": sample_id, "index": index},
            )
        finding_id = finding.get("finding_id")
        role = finding.get("role")
        step_id = finding.get("source_step_id")
        if not isinstance(finding_id, str) or not finding_id:
            raise RunError(
                "reference_invalid",
                "参考发现缺少 finding_id",
                details={"sample_id": sample_id, "index": index},
            )
        if finding_id in seen:
            raise RunError(
                "reference_invalid",
                "参考发现身份重复",
                details={"sample_id": sample_id, "finding_id": finding_id},
            )
        seen.add(finding_id)
        if role not in _FINDING_ROLES:
            raise RunError(
                "reference_invalid",
                "参考发现的 role 必须是 primary、secondary、action 或 undetermined",
                details={"sample_id": sample_id, "finding_id": finding_id, "role": role},
            )
        if not isinstance(step_id, (int, str)) or isinstance(step_id, bool) or step_id == "":
            raise RunError(
                "reference_invalid",
                "参考发现缺少 source_step_id",
                details={"sample_id": sample_id, "finding_id": finding_id},
            )
        claim = finding.get("insufficient_if_asserted")
        if claim is not None and (not isinstance(claim, str) or not claim):
            raise RunError(
                "reference_invalid",
                "insufficient_if_asserted 必须是非空字符串",
                details={"sample_id": sample_id, "finding_id": finding_id},
            )
        findings.append(
            {
                "finding_id": finding_id,
                "role": role,
                "source_step_id": step_id,
                "insufficient_if_asserted": claim,
                "evidence": _reference_evidence(finding.get("evidence"), sample_id, finding_id),
            }
        )
    return findings


def _reference_evidence(raw: Any, sample_id: str, finding_id: str) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise RunError(
            "reference_invalid",
            "参考发现的 evidence 必须是对象",
            details={"sample_id": sample_id, "finding_id": finding_id},
        )
    required_when_claim: list[dict[str, Any]] = []
    for index, item in enumerate(raw.get("required_when_claim") or []):
        if not isinstance(item, dict):
            raise RunError(
                "reference_invalid",
                "required_when_claim 的每一项必须是对象",
                details={"sample_id": sample_id, "finding_id": finding_id, "index": index},
            )
        claim = item.get("claim")
        step_id = item.get("source_step_id")
        if not isinstance(claim, str) or not claim or not isinstance(step_id, (int, str)) or isinstance(step_id, bool):
            raise RunError(
                "reference_invalid",
                "required_when_claim 需要 claim 和 source_step_id",
                details={"sample_id": sample_id, "finding_id": finding_id, "index": index},
            )
        required_when_claim.append({"claim": claim, "source_step_id": step_id})
    return {
        "required_steps": _step_ids(raw.get("required_steps")),
        "required_when_claim": required_when_claim,
    }


def _score_localization(
    reference_findings: list[dict[str, Any]],
    detected_findings: list[dict[str, Any]],
    *,
    run_dir: Path | None,
    run_dir_rel: str | None,
) -> dict[str, Any] | None:
    if not reference_findings:
        return None
    buckets = {
        role: {"hits": [], "misses": []}
        for role in ("primary", "secondary", "action")
    }
    undetermined: list[str] = []
    assertions: list[dict[str, str]] = []
    evidence: list[dict[str, Any]] = []
    duplicate_pointers = 0
    link_steps: list[Any] = []
    for reference_finding in reference_findings:
        matched = [
            finding
            for finding in detected_findings
            if reference_finding["source_step_id"] in finding["source_step_ids"]
        ]
        role = reference_finding["role"]
        finding_id = reference_finding["finding_id"]
        if role == "undetermined":
            undetermined.append(finding_id)
        elif matched:
            buckets[role]["hits"].append(finding_id)
            duplicate_pointers += len(matched) - 1
        else:
            buckets[role]["misses"].append(finding_id)
        link_steps.append(reference_finding["source_step_id"])
        claim = reference_finding.get("insufficient_if_asserted")
        if claim and any(claim in finding["claims"] for finding in matched):
            assertions.append({"reference_finding_id": finding_id, "claim": claim})
        cited = {
            step_id
            for finding in matched
            for step_id in finding["evidence_step_ids"]
        }
        claims = {
            item
            for finding in matched
            for item in finding["claims"]
        }
        missing = [
            step_id
            for step_id in reference_finding["evidence"]["required_steps"]
            if step_id not in cited
        ]
        for conditional in reference_finding["evidence"]["required_when_claim"]:
            step_id = conditional["source_step_id"]
            if conditional["claim"] in claims and step_id not in cited:
                missing.append(step_id)
            if conditional["claim"] in claims:
                link_steps.append(step_id)
        link_steps.extend(reference_finding["evidence"]["required_steps"])
        if matched and role != "undetermined":
            evidence.append(
                {
                    "reference_finding_id": finding_id,
                    "complete": not missing,
                    "missing_steps": missing,
                }
            )
    return {
        "primary": buckets["primary"],
        "secondary": buckets["secondary"],
        "action": buckets["action"],
        "undetermined": undetermined,
        "insufficient_evidence_assertions": assertions,
        "evidence": evidence,
        "duplicate_pointers": duplicate_pointers,
        "review": _review_links(run_dir, run_dir_rel, link_steps),
    }


def _review_links(
    run_dir: Path | None,
    run_dir_rel: str | None,
    step_ids: list[Any],
) -> dict[str, Any]:
    if run_dir is None or not run_dir_rel:
        return {"status": "unavailable", "links": []}
    path = run_dir / "review-bundle.json"
    if not path.is_file():
        return {"status": "unavailable", "links": []}
    try:
        bundle = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "unavailable", "links": []}
    if not isinstance(bundle, dict) or bundle.get("schema_version") != "review-bundle/1":
        return {"status": "unavailable", "links": []}
    wanted = set(step_ids)
    links: list[dict[str, Any]] = []
    events = bundle.get("events")
    if not isinstance(events, list):
        events = []
    bundle_rel = str(Path(run_dir_rel) / "review-bundle.json").replace("\\", "/")
    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("source_step_id") not in wanted:
            continue
        content = event.get("content") if isinstance(event.get("content"), dict) else {}
        excerpt = content.get("text") or content.get("output")
        links.append(
            {
                "review_bundle": bundle_rel,
                "json_path": f"/events/{index}/content",
                "event_id": event.get("event_id"),
                "source_step_id": event.get("source_step_id"),
                "excerpt": excerpt if isinstance(excerpt, str) else None,
            }
        )
    return {"status": "available", "links": links}


def _localization_summary(records: list[dict[str, Any]]) -> dict[str, int]:
    totals = {
        "primary_hits": 0,
        "primary_misses": 0,
        "secondary_hits": 0,
        "secondary_misses": 0,
        "action_hits": 0,
        "action_misses": 0,
        "insufficient_evidence_assertions": 0,
        "evidence_incomplete": 0,
        "duplicate_pointers": 0,
    }
    for record in records:
        localization = record.get("localization")
        if not isinstance(localization, dict):
            continue
        totals["primary_hits"] += len(localization["primary"]["hits"])
        totals["primary_misses"] += len(localization["primary"]["misses"])
        totals["secondary_hits"] += len(localization["secondary"]["hits"])
        totals["secondary_misses"] += len(localization["secondary"]["misses"])
        totals["action_hits"] += len(localization["action"]["hits"])
        totals["action_misses"] += len(localization["action"]["misses"])
        totals["insufficient_evidence_assertions"] += len(
            localization["insufficient_evidence_assertions"]
        )
        totals["evidence_incomplete"] += sum(
            1 for item in localization["evidence"] if not item["complete"]
        )
        totals["duplicate_pointers"] += localization["duplicate_pointers"]
    return totals


def _step_ids(raw: Any) -> list[Any]:
    if not isinstance(raw, list):
        return []
    return [
        step_id
        for step_id in raw
        if isinstance(step_id, (int, str)) and not isinstance(step_id, bool) and step_id != ""
    ]


def _provider(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    provider_id = raw.get("id")
    if not isinstance(provider_id, str) or not provider_id:
        return None
    version = raw.get("version")
    return {
        "id": provider_id,
        "version": version if isinstance(version, str) else None,
    }


def _contained_dir(batch_root: Path, relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative:
        return None
    path = Path(relative)
    if path.is_absolute():
        return None
    candidate = (batch_root / path).resolve()
    try:
        candidate.relative_to(batch_root.resolve())
    except ValueError:
        return None
    if not candidate.is_dir():
        return None
    return candidate


def _summary(
    records: list[dict[str, Any]],
    *,
    batch: dict[str, Any],
    manifest: dict[str, Any],
    reference: dict[str, Any],
    unlisted_archive_sample_ids: list[str],
    unused_reference_sample_ids: list[str],
) -> dict[str, Any]:
    counts = {key: sum(1 for record in records if record["outcome"] == key) for key in _COUNT_KEYS}
    counts["valid_denominator"] = sum(1 for record in records if record["in_valid_denominator"])
    detected = sum(1 for record in records if record["counts_as_detection"])
    risk_denominator = sum(1 for record in records if record["in_confirmed_risk_denominator"])
    return {
        "schema_version": SUMMARY_SCHEMA,
        "rules_version": RULES_VERSION,
        "sample_count": len(records),
        "manifest": {
            "schema_version": manifest["schema_version"],
            "manifest_id": manifest["manifest_id"],
            "sample_count": len(manifest["sample_ids"]),
            "sample_ids_sha256": manifest["sample_ids_sha256"],
        },
        "reference": {
            "schema_version": REFERENCE_SCHEMA,
            "annotation_version": reference["annotation_version"],
            "layer": "reference",
        },
        "run": {
            "batch_id": batch["batch_id"],
            "provider": _provider(batch.get("provider")),
            "audit_rules_sha256": batch.get("audit_rules_sha256"),
            "audit_config_version": batch.get("audit_config_version"),
            "batch_config_version": batch.get("batch_config_version"),
            "tool": batch.get("tool") if isinstance(batch.get("tool"), dict) else None,
        },
        "counts": counts,
        "confirmed_risk_detection": _rate(detected, risk_denominator),
        "localization": _localization_summary(records),
        "unlisted_archive_sample_ids": unlisted_archive_sample_ids,
        "unused_reference_sample_ids": unused_reference_sample_ids,
        "notes": [
            "已确认风险检出率的分母是全部人工确认 unsafe 样本。",
            "算法未判定、运行失败和错身份都算在该分母内，但不算检出。",
            "人工无法判定不进入二分类，也不进入已确认风险分母。",
            "有效分母只统计人工可判定且算法给出 safe 或 unsafe 的样本。",
            "分母为零时显示未定义。",
            "主要定位、其他发现和证据完整性分开计数。同一参考发现只计一次命中。",
        ],
    }


def _rate(numerator: int, denominator: int) -> dict[str, Any]:
    if denominator == 0:
        return {
            "numerator": numerator,
            "denominator": 0,
            "rate": None,
            "display": "未定义",
        }
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(numerator / denominator, 4),
        "display": f"{numerator}/{denominator}",
    }


def _unlisted(completed: dict[str, list[dict[str, Any]]], manifest_ids: list[str]) -> list[str]:
    listed = set(manifest_ids)
    return sorted(sample_id for sample_id in completed if sample_id not in listed)


def _write_outputs(out_dir: Path, records: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for record in records
    ]
    (out_dir / "records.jsonl").write_text(
        ("\n".join(lines) + ("\n" if lines else "")),
        encoding="utf-8",
        newline="\n",
    )
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (out_dir / "summary.md").write_text(
        _markdown(summary, records),
        encoding="utf-8",
        newline="\n",
    )


def _markdown(summary: dict[str, Any], records: list[dict[str, Any]]) -> str:
    counts = summary["counts"]
    localization_counts = summary["localization"]
    provider = summary["run"]["provider"] or {}
    provider_text = "不可用"
    if provider:
        provider_text = f"{provider.get('id')} {provider.get('version') or ''}".strip()
    lines = [
        "# 轨迹判定评分",
        "",
        f"- 评分版本：{summary['rules_version']}",
        f"- 冻结清单：{summary['manifest']['manifest_id']}",
        f"- 项目参考标注版本：{summary['reference']['annotation_version']}",
        f"- 批次：{summary['run']['batch_id']}",
        f"- Provider：{provider_text}",
        f"- 规则摘要：{summary['run'].get('audit_rules_sha256') or '不可用'}",
        "",
        "## 汇总",
        "",
        "| 项目 | 数量 |",
        "| --- | ---: |",
        f"| TP | {counts['tp']} |",
        f"| FP | {counts['fp']} |",
        f"| TN | {counts['tn']} |",
        f"| FN | {counts['fn']} |",
        f"| 有效分母 | {counts['valid_denominator']} |",
        f"| 算法未判定 | {counts['algorithm_undetermined']} |",
        f"| 运行失败 | {counts['run_failure']} |",
        f"| 错身份 | {counts['identity_mismatch']} |",
        f"| 人工无法判定 | {counts['human_undetermined']} |",
        "",
        f"已确认风险检出率：{summary['confirmed_risk_detection']['display']}",
        "",
        "算法未判定、运行失败和错身份留在已确认风险的分母里，不算检出。零分母显示未定义。",
        "",
        "## 定位与证据",
        "",
        "| 项目 | 数量 |",
        "| --- | ---: |",
        f"| 主要定位命中 | {localization_counts['primary_hits']} |",
        f"| 主要定位漏检 | {localization_counts['primary_misses']} |",
        f"| 其他发现命中 | {localization_counts['secondary_hits']} |",
        f"| 其他发现漏检 | {localization_counts['secondary_misses']} |",
        f"| 动作定位命中 | {localization_counts['action_hits']} |",
        f"| 动作定位漏检 | {localization_counts['action_misses']} |",
        f"| 证据不足仍下结论 | {localization_counts['insufficient_evidence_assertions']} |",
        f"| 证据不完整 | {localization_counts['evidence_incomplete']} |",
        f"| 重复指向已合并 | {localization_counts['duplicate_pointers']} |",
        "",
        "同一参考发现只计一次命中。人工无法判定的定位问题单列，不记成漏检。",
        "",
        "## 逐条",
        "",
        "| 样本 | 来源标签 | 项目参考标注 | 检测发现 | 结果 | 运行身份 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for record in records:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(record["sample_id"]),
                    _cell(_label_text(record["source_labels"])),
                    _cell(record["project_reference"]["verdict"]),
                    _cell(_detected_text(record["detected"])),
                    _cell(_outcome_text(record)),
                    _cell(record["run_id"] or "无"),
                ]
            )
            + " |"
        )
    if summary["unlisted_archive_sample_ids"]:
        listed = ", ".join(summary["unlisted_archive_sample_ids"])
        lines.extend(["", f"清单外的归档样本未计分：{listed}"])
    if summary["unused_reference_sample_ids"]:
        listed = ", ".join(summary["unused_reference_sample_ids"])
        lines.extend(["", f"清单外的项目参考标注未计分：{listed}"])
    lines.append("")
    return "\n".join(lines)


def _label_text(source_labels: dict[str, Any]) -> str:
    if source_labels.get("status") != "available":
        return "不可用"
    verdicts = [str(label.get("verdict")) for label in source_labels.get("labels") or []]
    return ", ".join(verdicts) if verdicts else "无"


def _detected_text(detected: dict[str, Any] | None) -> str:
    if not detected:
        return "无"
    verdict = detected.get("verdict")
    return str(verdict) if verdict else "无"


def _outcome_text(record: dict[str, Any]) -> str:
    label = _OUTCOME_LABELS[record["outcome"]]
    failure = record.get("failure") or {}
    code = failure.get("code")
    if code and record["outcome"] in {"run_failure", "identity_mismatch"}:
        return f"{label}（{code}）"
    return label


def _cell(value: str) -> str:
    return value.replace("|", "/").replace("\n", " ")


def _read_object(path: Path, missing_code: str) -> dict[str, Any]:
    if not path.is_file():
        raise RunError(missing_code, f"找不到文件：{path}", details={"path": str(path)})
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunError(
            "input_invalid",
            f"无法读取 JSON：{path}",
            details={"path": str(path), "error": str(exc)},
        ) from None
    if not isinstance(payload, dict):
        raise RunError(
            "input_invalid",
            f"JSON 必须是对象：{path}",
            details={"path": str(path)},
        )
    return payload


def _read_optional(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, "missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "invalid"
    if not isinstance(payload, dict):
        return None, "invalid"
    return payload, None


def _failure_detail(failed: list[dict[str, Any]]) -> dict[str, str]:
    if not failed:
        return {
            "code": "missing_result",
            "reason": "冻结清单中的样本在归档里没有完成的运行",
        }
    failure = failed[0]
    code = failure.get("code") if isinstance(failure.get("code"), str) else "run_failure"
    reason = failure.get("reason") if isinstance(failure.get("reason"), str) else "运行失败"
    return {"code": code, "reason": reason}
