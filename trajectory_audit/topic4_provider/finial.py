# SPDX-License-Identifier: Apache-2.0
"""Documented compatibility with the read-only Topic 4 finial 1.0 delivery.

Only a validation view is adjusted. The original result remains the archive
and mapping source. Missing model/component revisions are never invented.
"""

import copy
import os
from pathlib import Path
from typing import Any

from .types import ValidationIssue
from .validator import validate_result

PROFILE = "finial-1.0"


def deployment_config(config: dict[str, Any], source: Path) -> dict[str, Any]:
    """Keep relative config references meaningful after copying to a workdir."""
    deployed = copy.deepcopy(config)
    blocks = [deployed.get("semantic", {}), deployed.get("toolchain", {})]
    blocks.extend(deployed.get("integrations", {}).values())
    for block in blocks:
        for key in ("config_path", "cache_dir", "root"):
            value = block.get(key)
            if isinstance(value, str):
                expanded = os.path.expandvars(os.path.expanduser(value))
                block[key] = str((source.resolve().parent / expanded).resolve())
    return deployed


def validate_finial_result(batch: dict[str, Any], raw: Any) -> list[ValidationIssue]:
    if not isinstance(raw, dict):
        return validate_result(batch, raw)
    view = copy.deepcopy(raw)
    if not isinstance(view.get("items"), list):
        return [ValidationIssue("items", "bad_items", "items must be an array")]
    provider = view.get("provider")
    if not isinstance(provider, dict) or provider.get("version") != "1.0.0":
        return [
            ValidationIssue(
                "provider.version",
                "unsupported_delivery",
                "finial-1.0 requires Topic 4 version 1.0.0",
            )
        ]
    for key in ("model", "preflight", "code_sha256", "effective_config_sha256"):
        provider.pop(key, None)
    execution = view.get("execution")
    if isinstance(execution, dict):
        for key in (
            "elapsed_seconds",
            "cpu_seconds",
            "input_content_sha256",
            "toolchain_runtime",
            "latency_ms",
            "samples_per_second",
        ):
            execution.pop(key, None)
    waived: set[tuple[str, str]] = set()
    if "components" not in provider:
        waived.add(("provider.components", "missing_components"))
    for index, item in enumerate(view.get("items", [])):
        if not isinstance(item, dict) or not isinstance(item.get("result"), dict):
            continue
        body = item["result"]
        prefix = f"items[{index}].result"
        checks = body.get("checks")
        if isinstance(checks, dict):
            for key in (
                "policy",
                "trajectory",
                "reasoning",
                "facts",
                "integrations",
                "semantic",
                "toolchain",
            ):
                checks.pop(key, None)
            if "limitations" not in checks:
                waived.add((f"{prefix}.checks.limitations", "bad_limitations"))
        findings = body.get("findings")
        if not isinstance(findings, list):
            return [
                ValidationIssue(
                    f"{prefix}.findings", "bad_findings", "findings must be an array"
                )
            ]
        for findex, finding in enumerate(findings):
            if not isinstance(finding, dict):
                continue
            path = f"{prefix}.findings[{findex}]"
            # The delivery always binds Findings to events and exact evidence.
            # Setting this on the validation view makes all strict evidence and
            # origin/affected checks run, including for malformed native results.
            finding.setdefault("located", True)
            if "detector_component_id" not in finding:
                waived.add(
                    (f"{path}.detector_component_id", "bad_detector_component_id")
                )
            detector = finding.get("detector")
            if not isinstance(detector, dict) or not all(
                isinstance(detector.get(k), str) and detector[k]
                for k in ("name", "version", "rule")
            ):
                return [
                    ValidationIssue(
                        f"{path}.detector",
                        "bad_native_detector",
                        "Native detector name, version and rule are required",
                    )
                ]
            entries = finding.get("evidence")
            if not isinstance(entries, list):
                return [
                    ValidationIssue(
                        f"{path}.evidence", "bad_evidence", "evidence must be an array"
                    )
                ]
            for evidence in entries:
                if isinstance(evidence, dict):
                    evidence.pop("kind", None)
                    evidence.pop("granularity", None)
    return [
        issue
        for issue in validate_result(batch, view)
        if (issue.path, issue.code) not in waived
    ]
