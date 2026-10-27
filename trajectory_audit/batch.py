# SPDX-License-Identifier: Apache-2.0
"""批量运行入口：一个来源批次 → 每条轨迹独立 run 包 + 批次索引。

批量入口不按数据集名称分支；来源差异完全由 ``--adapter`` 选择的 adapter
负责。单条记录失败不会丢弃其他已完成结果，失败写入 ``failures.json``。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import __version__
from .adapters import (
    SOURCE_BATCH_SCHEMA_VERSION,
    DatasetRef,
    adapter_spec,
    batch_records,
    batch_source,
    get_adapter,
    validate_batch_input,
)
from .artifacts import claim_directory, now, read_input, remove_tree, write_json
from .errors import (
    STAGE_ARTIFACT_WRITE,
    STAGE_ADAPTER_NORMALIZATION,
    STAGE_SAMPLE_IDENTITY,
    ConversionError,
    RunError,
)
from .model import canonical_json, sha256_json
from .page import render_batch_page
from .policy import Policy
from .providers import resolve_provider
from .runner import AUDIT_CONFIG_VERSION, write_run_bundle

BATCH_CONFIG_VERSION = "0.2.0"
BATCH_RUN_SCHEMA = "batch-run/1"


def execute_batch(
    *,
    input_path: Path,
    out_root: Path,
    provider_id: str,
    adapter_id: str,
    policy: Policy | None = None,
    judge_name: str = "none",
    allow_fallback: bool = False,
) -> dict[str, Any]:
    """执行一个来源批次，逐样本写出 run 包并汇总失败。"""
    started_at = now()
    raw_bytes, payload = read_input(input_path)
    issues = validate_batch_input(payload, adapter_id)
    if issues:
        raise RunError(
            "input_invalid",
            "批次输入不符合 source-batch 契约",
            details={
                "path": str(input_path),
                "adapter": adapter_id,
                "issues": issues,
            },
        )

    adapter = get_adapter(adapter_id)
    spec = adapter_spec(adapter_id)
    source = batch_source(payload)
    records = batch_records(payload)
    input_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    dataset = DatasetRef(
        dataset_id=source["dataset_id"],
        dataset_version=source["dataset_version"],
    )

    provider, batch_degraded = resolve_provider(provider_id, allow_fallback=allow_fallback)
    identity = provider.provider_identity()
    rules_sha256 = provider.rules_digest()
    identity_material = {
        "adapter": {"id": spec.id, "version": spec.version},
        "dataset": {
            "id": dataset.dataset_id,
            "version": dataset.dataset_version,
        },
        "input_sha256": input_sha256,
        "provider": {
            "id": identity.id,
            "version": identity.version,
            "rules_sha256": rules_sha256,
        },
        "batch_config_version": BATCH_CONFIG_VERSION,
        "audit_config_version": AUDIT_CONFIG_VERSION,
    }
    if policy is not None:
        identity_material["policy"] = {
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "sha256": policy.source_sha256,
        }
    if judge_name != "none":
        identity_material["judge"] = {"name": judge_name}
    if batch_degraded is not None:
        identity_material["fallback"] = {
            "from_provider": batch_degraded.get("from_provider"),
            "mode": batch_degraded.get("mode"),
        }
    batch_id = _batch_identity(identity_material)
    batch_dir = out_root / batch_id
    claim_directory(
        batch_dir,
        completion_marker="batch.json",
        already_exists_code="batch_already_exists",
        incomplete_code="batch_incomplete",
        conflict_details={"batch_id": batch_id, "batch_dir": str(batch_dir)},
    )

    try:
        stored_input = f"input/{input_path.name}"
        input_record = {
            "original_path": str(input_path),
            "stored_path": stored_input,
            "sha256": input_sha256,
            "bytes": len(raw_bytes),
            "media_type": "application/json",
        }
        input_manifest = {
            "schema_version": "batch-input-manifest/1",
            "batch_id": batch_id,
            "adapter": spec.to_dict(),
            "source": source,
            "received_at": started_at,
            "files": [{"role": "source_batch", **input_record}],
            "records": {
                "total": len(records),
                "sha256": [sha256_json(record) for record in records],
            },
            "unmapped_batch_fields": sorted(
                set(payload) - {"schema_version", "source", "records"}
            ),
        }
        (batch_dir / "input").mkdir()
        (batch_dir / stored_input).write_bytes(raw_bytes)
        write_json(batch_dir / "input_manifest.json", input_manifest)

        sample_results: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        seen_sample_ids: set[str] = set()
        record_outcomes: dict[int, bool] = {}

        for record_index, record in enumerate(records):
            fallback_id, identity_source = adapter.sample_identity(record)
            fallback_sample_id = fallback_id or f"record-{record_index:04d}"
            if fallback_id is None:
                identity_source = "fallback"
            record_bytes = _record_bytes(record)
            record_sha256 = sha256_json(record)
            record_input_sha256 = hashlib.sha256(record_bytes).hexdigest()

            try:
                trajectories = adapter.normalize_record(
                    record,
                    record_index=record_index,
                    dataset_id=dataset.dataset_id,
                    dataset_version=dataset.dataset_version,
                    input_sha256=record_input_sha256,
                    record_json_path="",
                    record_sha256=record_sha256,
                    sample_id=fallback_sample_id,
                )
                if not trajectories:
                    raise ConversionError(
                        "unsupported_record",
                        "adapter 没有从记录中产生任何轨迹",
                        stage=STAGE_ADAPTER_NORMALIZATION,
                        details={"record_index": record_index},
                        sample_id=fallback_sample_id,
                    )

                for trajectory in trajectories:
                    if trajectory.sample_id in seen_sample_ids:
                        failures.append(
                            {
                                "sample_id": trajectory.sample_id,
                                "sample_id_source": identity_source,
                                "record_index": record_index,
                                "stage": STAGE_SAMPLE_IDENTITY,
                                "code": "duplicate_sample_id",
                                "reason": "同一批次内样本身份重复，拒绝静默覆盖",
                                "details": {"sample_id": trajectory.sample_id},
                            }
                        )
                        record_outcomes[record_index] = False
                        continue
                    seen_sample_ids.add(trajectory.sample_id)

                    result = write_run_bundle(
                        trajectory=trajectory,
                        out_root=batch_dir / "samples",
                        provider_id=provider_id,
                        input_bytes=record_bytes,
                        input_name=f"record-{record_index:03d}.json",
                        input_original_path=f"{input_path}#records[{record_index}]",
                        input_role="trajectory_record",
                        sample_id_source=identity_source,
                        started_at=started_at,
                        entry_point="python -m trajectory_audit batch",
                        batch_context={
                            "batch_id": batch_id,
                            "input_sha256": input_sha256,
                            "record_index": record_index,
                            "adapter_id": spec.id,
                        },
                        policy=policy,
                        judge_name=judge_name,
                        allow_fallback=allow_fallback,
                    )
                    run_dir = Path(result["run_dir"])
                    sample_results.append(
                        {
                            "sample_id": trajectory.sample_id,
                            "sample_id_source": identity_source,
                            "record_index": record_index,
                            "status": "complete",
                            "run_id": result["run_id"],
                            "run_dir": run_dir.relative_to(batch_dir).as_posix(),
                            "verdict": result["verdict"],
                            "check_status": result["check_status"],
                            "event_count": len(trajectory.events),
                            "source_steps": len(trajectory.source_steps),
                            "source_labels": len(trajectory.source_labels),
                            "located_source_labels": sum(
                                1 for label in trajectory.source_labels if label.located
                            ),
                        }
                    )
                    record_outcomes[record_index] = True
            except ConversionError as exc:
                sample_id = exc.sample_id or fallback_sample_id
                failures.append(
                    exc.to_failure(
                        sample_id=sample_id,
                        record_index=record_index,
                        sample_id_source=identity_source,
                    )
                )
                record_outcomes[record_index] = False
            except RunError as exc:
                failures.append(
                    {
                        "sample_id": fallback_sample_id,
                        "sample_id_source": identity_source,
                        "record_index": record_index,
                        "stage": STAGE_ARTIFACT_WRITE,
                        "code": exc.code,
                        "reason": exc.message,
                        "details": exc.details,
                    }
                )
                record_outcomes[record_index] = False

        status = "complete" if not failures else "partial"
        artifacts = {
            "batch": "batch.json",
            "input": stored_input,
            "input_manifest": "input_manifest.json",
            "failures": "failures.json",
            "samples": "samples/",
            "review_page": "index.html",
        }
        batch = {
            "schema_version": BATCH_RUN_SCHEMA,
            "batch_id": batch_id,
            "status": status,
            "created_at": started_at,
            "completed_at": now(),
            "tool": {
                "name": "trajectory_audit",
                "version": __version__,
                "entry_point": "python -m trajectory_audit batch",
            },
            "adapter": spec.to_dict(),
            "provider": identity.to_dict(),
            "audit_rules_sha256": rules_sha256,
            "batch_config_version": BATCH_CONFIG_VERSION,
            "audit_config_version": AUDIT_CONFIG_VERSION,
            "policy": policy.summary() if policy is not None else None,
            "judge": {"name": judge_name, "influences_verdict": False},
            "degraded": batch_degraded,
            "source": source,
            "input": dict(input_record),
            "records": {
                "total": len(records),
                "converted": sum(1 for ok in record_outcomes.values() if ok),
                "failed": sum(1 for ok in record_outcomes.values() if not ok),
                "samples_completed": len(sample_results),
                "samples_failed": len(failures),
            },
            "samples": sample_results,
            "failures": failures,
            "artifacts": artifacts,
        }
        write_json(
            batch_dir / "failures.json",
            {
                "schema_version": "batch-failures/1",
                "batch_id": batch_id,
                "failures": failures,
            },
        )
        (batch_dir / "index.html").write_text(
            render_batch_page(batch=batch),
            encoding="utf-8",
            newline="\n",
        )
        # batch.json 最后写入：它存在才表示本次批次已完成。
        write_json(batch_dir / "batch.json", batch)
    except BaseException:
        remove_tree(batch_dir)
        raise

    return {
        "status": status,
        "batch_id": batch_id,
        "batch_dir": str(batch_dir),
        "adapter": spec.to_dict(),
        "provider": {
            "id": identity.id,
            "version": identity.version,
            "selection": identity.selection,
        },
        "source": {
            "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.dataset_version,
        },
        "records": batch["records"],
        "samples": [
            {
                "sample_id": sample["sample_id"],
                "status": sample["status"],
                "run_id": sample["run_id"],
                "run_dir": sample["run_dir"],
                "verdict": sample["verdict"],
                "check_status": sample["check_status"],
            }
            for sample in sample_results
        ],
        "failures": failures,
        "artifacts": artifacts,
    }


def _record_bytes(record: Any) -> bytes:
    return (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _batch_identity(material: dict[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()
    return f"batch-{digest[:16]}"


__all__ = ["BATCH_CONFIG_VERSION", "SOURCE_BATCH_SCHEMA_VERSION", "execute_batch"]
