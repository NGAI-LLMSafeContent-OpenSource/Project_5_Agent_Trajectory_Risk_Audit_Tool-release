# SPDX-License-Identifier: Apache-2.0
"""Retain deterministic messages evidence independently of Provider outcomes."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from trajectory_audit import openai_messages
from trajectory_audit.artifacts import write_json
from trajectory_audit.model import sha256_json
from trajectory_audit.pujiang import source_mapping
from trajectory_audit.race_items import race_facts
from trajectory_audit.review_bundle import project_review_bundle


def archive_messages_evidence(session_dir: Path, payload: dict[str, Any], offset: int) -> dict[str, dict[str, Any]]:
    """Archive source and a neutral Review Bundle before calling the algorithm."""
    evidence: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(payload["records"]):
        sample_id, _ = openai_messages.sample_identity(record)
        assert sample_id is not None
        digest = sha256_json(record)
        trajectory = openai_messages.normalize_record(
            record, record_index=index, dataset_id="openai-messages",
            dataset_version="workbench-upload", input_sha256=digest,
            record_json_path="", record_sha256=digest, sample_id=sample_id,
        )[0]
        data = trajectory.to_dict()
        # Messages are source units, not generated competition steps.
        data["source_steps"] = [
            {"json_path": f"messages[{i}]", "raw": message}
            for i, message in enumerate(record["messages"])
        ]
        run = {
            "run_id": "unavailable",
            "sample": {**data["sample"], "dataset": {"id": "openai-messages", "version": "workbench-upload"},
                       "record": {"index": index, "sha256": digest}, "sample_id_source": "declared"},
            "adapter": {"id": openai_messages.ADAPTER_ID, "version": openai_messages.ADAPTER_VERSION},
        }
        bundle = project_review_bundle(
            run=run, trajectory=data,
            audit_result={"verdict": {"value": "undetermined", "reason": "算法未完成，检测结果未提供"}},
            metrics={"events": {"total": len(trajectory.events), "by_type": dict(Counter(e.type for e in trajectory.events))},
                     "tools": {"calls": sum(e.type == "tool_call" for e in trajectory.events),
                               "results": sum(e.type == "tool_result" for e in trajectory.events)}},
        )
        mapping = source_mapping(trajectory, line_number=offset + index + 1)
        bundle["source_mapping"] = {key: mapping[key] for key in ("steps", "events", "line_number")}
        bundle["race"] = race_facts(mapping)
        bundle["algorithm_status"] = "failure"
        relative = f"evidence/record-{index:06d}/trajectory.json"
        (session_dir / relative).parent.mkdir(parents=True, exist_ok=True)
        write_json(session_dir / relative, data)
        evidence[sample_id] = {"bundle": bundle, "trajectory_path": relative}
    return evidence
