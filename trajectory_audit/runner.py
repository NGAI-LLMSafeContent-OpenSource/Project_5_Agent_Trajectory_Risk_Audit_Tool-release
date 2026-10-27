# SPDX-License-Identifier: Apache-2.0
"""单条轨迹运行入口：输入 → 归一轨迹 → 审计 → 不可变 run 包。

批量入口复用 ``write_run_bundle``，因此单条与批量产生相同形态的 run 包。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import __version__
from .adapters import DatasetRef, OPENAI_MESSAGES_ADAPTER, get_adapter
from .artifacts import claim_directory, now, read_input, read_json, remove_tree, write_json
from .errors import ConversionError, RunError
from .metrics import compute_metrics
from .model import AuditResult, NormalizedTrajectory, canonical_json, sha256_json
from .page import render_review_page
from .policy import Policy
from .providers import resolve_provider

AUDIT_CONFIG_VERSION = "0.2.0"


def execute_run(
    *,
    input_path: Path,
    out_root: Path,
    provider_id: str,
    adapter_id: str = OPENAI_MESSAGES_ADAPTER,
    policy: Policy | None = None,
    judge_name: str = "none",
    allow_fallback: bool = False,
) -> dict[str, Any]:
    """执行一次单条轨迹审计运行并写出完整 run 包。"""
    started_at = now()
    raw_bytes, payload = read_input(input_path)
    adapter = get_adapter(adapter_id)
    issues = adapter.validate_record(payload)
    if issues:
        raise RunError(
            "input_invalid",
            f"输入不符合 {adapter.SOURCE_FORMAT} 轨迹契约",
            details={
                "path": str(input_path),
                "adapter": adapter.ADAPTER_ID,
                "issues": issues,
            },
        )

    input_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    record_sha256 = sha256_json(payload)
    sample_id, sample_id_source = adapter.sample_identity(payload)
    if not sample_id:
        sample_id, sample_id_source = input_path.stem, "derived"

    dataset = DatasetRef(
        dataset_id=adapter.ADAPTER_ID,
        dataset_version=adapter.ADAPTER_VERSION,
    )
    try:
        trajectories = adapter.normalize_record(
            payload,
            record_index=0,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.dataset_version,
            input_sha256=input_sha256,
            record_json_path="",
            record_sha256=record_sha256,
            sample_id=sample_id,
        )
    except ConversionError as exc:
        raise _conversion_run_error(exc, input_path) from None

    if len(trajectories) != 1:
        raise RunError(
            "input_has_multiple_samples",
            "单条 run 入口只接受一条轨迹；含多条轨迹的输入请使用 batch 入口",
            details={
                "path": str(input_path),
                "adapter": adapter.ADAPTER_ID,
                "sample_count": len(trajectories),
            },
        )

    return write_run_bundle(
        trajectory=trajectories[0],
        out_root=out_root,
        provider_id=provider_id,
        input_bytes=raw_bytes,
        input_name=input_path.name,
        input_original_path=str(input_path),
        input_role="trajectory_input",
        sample_id_source=sample_id_source,
        started_at=started_at,
        policy=policy,
        judge_name=judge_name,
        allow_fallback=allow_fallback,
    )


def write_run_bundle(
    *,
    trajectory: NormalizedTrajectory,
    out_root: Path,
    provider_id: str,
    input_bytes: bytes,
    input_name: str,
    input_original_path: str,
    input_role: str,
    sample_id_source: str,
    started_at: str,
    batch_context: dict[str, Any] | None = None,
    entry_point: str = "python -m trajectory_audit run",
    policy: Policy | None = None,
    judge_name: str = "none",
    allow_fallback: bool = False,
) -> dict[str, Any]:
    """把一条归一轨迹写成不可变 run 包；单条与批量共用。"""
    provider, degraded = resolve_provider(provider_id, allow_fallback=allow_fallback)
    identity = provider.provider_identity()
    rules_sha256 = provider.rules_digest()
    identity_material = {
        "sample_id": trajectory.sample_id,
        "input_sha256": trajectory.input_sha256,
        "record_sha256": trajectory.record_sha256,
        "dataset": {
            "id": trajectory.dataset_id,
            "version": trajectory.dataset_version,
        },
        "provider": {
            "id": identity.id,
            "version": identity.version,
            "rules_sha256": rules_sha256,
        },
        "adapter": {
            "id": trajectory.adapter_id,
            "version": trajectory.adapter_version,
        },
        "audit_config_version": AUDIT_CONFIG_VERSION,
    }
    if policy is not None:
        identity_material["policy"] = {
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "sha256": policy.source_sha256,
            "sample_ids": list(policy.sample_ids),
        }
    if judge_name != "none":
        identity_material["judge"] = {"name": judge_name}
    if degraded is not None:
        identity_material["fallback"] = {
            "from_provider": degraded.get("from_provider"),
            "mode": degraded.get("mode"),
        }
    run_id = _run_identity(identity_material)
    run_dir = out_root / run_id
    claim_directory(
        run_dir,
        completion_marker="run.json",
        already_exists_code="run_already_exists",
        incomplete_code="run_incomplete",
        conflict_details={"run_id": run_id, "run_dir": str(run_dir)},
    )

    try:
        provider_output = provider.audit(
            trajectory,
            policy=policy,
            judge_name=judge_name,
            degraded=degraded,
        )
        audit_result = AuditResult(
            sample_id=trajectory.sample_id,
            run_id=run_id,
            provider=identity,
            verdict=provider_output.verdict,
            checks=provider_output.checks,
            findings=provider_output.findings,
            evaluated_at=now(),
            facts=provider_output.facts,
            policy=provider_output.policy,
            judge=provider_output.judge,
            degraded=provider_output.degraded,
        )
        metrics = compute_metrics(trajectory, audit_result)
        trajectory_dict = trajectory.to_dict()
        audit_dict = audit_result.to_dict()

        stored_path = f"input/{input_name}"
        artifacts = {
            "run": "run.json",
            "input": stored_path,
            "input_manifest": "input_manifest.json",
            "trajectory": "trajectory.json",
            "audit_result": "audit_result.json",
            "metrics": "metrics.json",
            "review_page": "index.html",
        }
        if policy is not None:
            artifacts["policy"] = "policy.json"
        input_record = {
            "original_path": input_original_path,
            "stored_path": stored_path,
            "sha256": hashlib.sha256(input_bytes).hexdigest(),
            "bytes": len(input_bytes),
            "media_type": "application/json",
        }
        source_context: dict[str, Any] = {
            "dataset_id": trajectory.dataset_id,
            "dataset_version": trajectory.dataset_version,
            "record_index": trajectory.record_index,
            "record_json_path": trajectory.record_json_path,
            "record_sha256": trajectory.record_sha256,
            "adapter_id": trajectory.adapter_id,
            "adapter_version": trajectory.adapter_version,
        }
        if batch_context is not None:
            source_context["batch"] = batch_context
        input_manifest = {
            "schema_version": "input-manifest/1",
            "sample_id": trajectory.sample_id,
            "sample_id_source": sample_id_source,
            "source_format": trajectory.source_format,
            "source": source_context,
            "received_at": started_at,
            "files": [{"role": input_role, **input_record}],
            "unmapped_top_level_fields": trajectory.unmapped_top_level_fields,
        }
        run = {
            "schema_version": "run/1",
            "run_id": run_id,
            "status": "complete",
            "created_at": started_at,
            "completed_at": now(),
            "tool": {
                "name": "trajectory_audit",
                "version": __version__,
                "entry_point": entry_point,
            },
            "sample": {
                "sample_id": trajectory.sample_id,
                "sample_id_source": sample_id_source,
                "source_format": trajectory.source_format,
                "dataset": {
                    "id": trajectory.dataset_id,
                    "version": trajectory.dataset_version,
                },
                "record": {
                    "index": trajectory.record_index,
                    "sha256": trajectory.record_sha256,
                },
            },
            "provider": identity.to_dict(),
            "audit_rules_sha256": rules_sha256,
            "adapter": {
                "id": trajectory.adapter_id,
                "version": trajectory.adapter_version,
            },
            "audit_config_version": AUDIT_CONFIG_VERSION,
            "input": dict(input_record),
            "artifacts": artifacts,
            "policy": policy.summary() if policy is not None else None,
            "judge": {
                "name": judge_name,
                "status": (provider_output.judge or {}).get("status"),
                "influences_verdict": False,
            },
            "degraded": degraded,
        }
        if batch_context is not None:
            run["batch"] = dict(batch_context)

        (run_dir / "input").mkdir()
        (run_dir / stored_path).write_bytes(input_bytes)
        write_json(run_dir / "input_manifest.json", input_manifest)
        write_json(run_dir / "trajectory.json", trajectory_dict)
        write_json(run_dir / "audit_result.json", audit_dict)
        write_json(run_dir / "metrics.json", metrics)
        if policy is not None:
            write_json(run_dir / "policy.json", policy.to_dict())
        # 页面从已落盘的同一份结构化结果渲染，避免展示层与机器结果脱钩。
        (run_dir / "index.html").write_text(
            render_review_page(
                run=run,
                trajectory=read_json(run_dir / "trajectory.json"),
                audit_result=read_json(run_dir / "audit_result.json"),
                metrics=read_json(run_dir / "metrics.json"),
            ),
            encoding="utf-8",
            newline="\n",
        )
        # run.json 最后写入：它存在且 status=complete 才表示本次 run 已完成。
        write_json(run_dir / "run.json", run)
    except BaseException:
        remove_tree(run_dir)
        raise

    return {
        "status": "ok",
        "run_id": run_id,
        "run_dir": str(run_dir),
        "sample_id": trajectory.sample_id,
        "verdict": audit_result.verdict.value,
        "check_status": audit_result.checks.status,
        "provider": {
            "id": identity.id,
            "version": identity.version,
            "selection": identity.selection,
        },
        "adapter": {
            "id": trajectory.adapter_id,
            "version": trajectory.adapter_version,
        },
        "source": {
            "dataset_id": trajectory.dataset_id,
            "dataset_version": trajectory.dataset_version,
            "record_index": trajectory.record_index,
            "record_sha256": trajectory.record_sha256,
        },
        "artifacts": artifacts,
    }


def _conversion_run_error(exc: ConversionError, input_path: Path) -> RunError:
    return RunError(
        exc.code,
        exc.message,
        details={
            **exc.details,
            "stage": exc.stage,
            "path": str(input_path),
        },
    )


def _run_identity(material: dict[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()
    return f"run-{digest[:16]}"


__all__ = [
    "AUDIT_CONFIG_VERSION",
    "execute_run",
    "write_run_bundle",
]
