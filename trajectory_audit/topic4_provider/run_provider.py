# SPDX-License-Identifier: Apache-2.0
"""Topic 4 provider behind the normal run/batch entry points."""

import json
import math
import os
from pathlib import Path
import shlex
import sys
import tempfile
from typing import Any

from .. import baseline
from ..artifacts import write_json
from ..audit_input import build_batch, canonical_item
from ..errors import RunError
from ..model import (
    Checks,
    Evidence,
    Finding,
    ProviderIdentity,
    ProviderOutput,
    Verdict,
    sha256_json,
)
from .adapter import run_topic4_adapter
from .finial import PROFILE, deployment_config
from .sanitize import sanitize_config
from .validator import validate_result

PROVIDER_ID = "topic4-cli"


def create_provider(options: dict[str, Any] | None = None):
    return Topic4Provider(options or {})


class Topic4Provider:
    def __init__(self, options: dict[str, Any]):
        root = Path(
            os.environ.get(
                "TOPIC4_FINIAL_ROOT",
                str(
                    Path(__file__).resolve().parents[2].parent
                    / "Project_4-5_ContentToolRisk"
                    / "课题五"
                    / "finial"
                ),
            )
        )
        config_path = (
            options.get("config")
            or os.environ.get("TOPIC4_CONFIG")
            or root / "config/toolchain.json"
        )
        self.config_path = Path(config_path).resolve()
        try:
            self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
            if not isinstance(self.config, dict):
                raise ValueError("configuration must be an object")
        except (OSError, ValueError) as exc:
            raise RunError(
                "topic4_config_unavailable",
                f"无法读取课题四配置：{self.config_path}",
                details={"reason": str(exc)},
            ) from exc
        command = options.get("command") or os.environ.get("TOPIC4_COMMAND")
        try:
            self.command = (
                shlex.split(command)
                if command
                else [sys.executable, str(root / "topic4-audit")]
            )
            timeout = options.get("timeout")
            self.timeout = float(
                timeout
                if timeout is not None
                else os.environ.get("TOPIC4_TIMEOUT", "1800")
            )
        except ValueError as exc:
            raise RunError("invalid_topic4_options", "课题四命令或超时无效") from exc
        self.profile = options.get("profile") or os.environ.get(
            "TOPIC4_PROFILE", PROFILE
        )
        if (
            not self.command
            or not math.isfinite(self.timeout)
            or self.timeout <= 0
            or self.profile not in (PROFILE, "strict-v1")
        ):
            raise RunError("invalid_topic4_options", "课题四命令、超时或 profile 无效")
        self.deployed = (
            deployment_config(self.config, self.config_path)
            if self.profile == PROFILE
            else self.config
        )

    def provider_identity(self):
        return ProviderIdentity(
            kind="external_cli",
            id=PROVIDER_ID,
            version="1.0.0",
            selection="explicit",
            implementation="trajectory_audit.topic4_provider.run_provider",
            description=f"Topic 4 CLI ({self.profile}, mode={self.config.get('mode', 'provider-configured')})",
        )

    def rules_digest(self):
        references = {}
        for block in [
            self.deployed.get("semantic", {}),
            *self.deployed.get("integrations", {}).values(),
        ]:
            reference = block.get("config_path")
            if reference and Path(reference).is_file():
                references[reference] = sanitize_config(
                    json.loads(Path(reference).read_text(encoding="utf-8"))
                )
        return sha256_json(
            {
                "adapter": "topic4-run-provider/1",
                "command": self.command,
                "config": sanitize_config(self.deployed),
                "references": references,
                "profile": self.profile,
                "timeout": self.timeout,
                "environment": {
                    key: os.environ.get(key)
                    for key in (
                        "TOPIC4_MODEL_BASE_URL",
                        "TOPIC4_MODEL_NAME",
                        "FILTERGUARD_BASE_URL",
                        "GUARD_MODEL_DIR",
                        "CUDA_VISIBLE_DEVICES",
                        "RISK_GUARD_GPU_MEMORY_UTILIZATION",
                    )
                },
            }
        )

    def audit(
        self,
        trajectory,
        *,
        policy=None,
        judge_name="none",
        degraded=None,
        archive_dir: Path,
        allow_fallback=False,
    ):
        if judge_name != "none":
            raise RunError(
                "unsupported_topic4_judge",
                "topic4-cli 使用课题四配置中的语义模型，请使用 --judge none",
            )
        bound = policy is not None and (
            not policy.sample_ids or trajectory.sample_id in policy.sample_ids
        )
        effective_policy = policy if bound else None
        batch = build_batch([canonical_item(trajectory, effective_policy)])
        with tempfile.TemporaryDirectory(prefix="topic5-input-") as tmp:
            input_path = Path(tmp) / "input-batch.json"
            write_json(input_path, batch)
            outcome = run_topic4_adapter(
                input_path=input_path,
                config_path=self.config_path,
                command=self.command,
                timeout=self.timeout,
                archive_dir=archive_dir,
                validation_profile=self.profile,
            )
        raw = outcome.raw_result
        if raw is not None:
            strict_issues = validate_result(batch, raw)
            write_json(
                archive_dir / "strict-contract-issues.json",
                [vars(issue) for issue in strict_issues],
            )
        write_json(
            archive_dir / "integration.json",
            {
                "profile": self.profile,
                "mode": self.config.get("mode"),
                "effective_config": sanitize_config(self.deployed),
            },
        )
        failure = outcome.failure
        if outcome.ok and raw is not None:
            if raw.get("status") == "rejected":
                failure = raw.get("rejection")
            elif raw["items"][0]["status"] == "failure":
                failure = raw["items"][0]["failure"]
        if failure is not None:
            details = {"original_failure": failure, "archive_dir": str(archive_dir)}
            if not allow_fallback:
                raise RunError(
                    "topic4_provider_failed",
                    "课题四调用或检测失败，详见原始运行归档",
                    details=details,
                )
            degraded = {
                "mode": "explicit_fallback",
                "from_provider": PROVIDER_ID,
                "to_provider": baseline.PROVIDER_ID,
                "original_failure": failure,
                "retained": True,
                "reason": "显式允许在课题四失败后运行 baseline",
            }
            write_json(archive_dir / "fallback.json", degraded)
            return baseline.audit(
                trajectory, policy=policy, judge_name=judge_name, degraded=degraded
            )
        row = raw["items"][0]
        body = row["result"]
        findings = []
        for native in body["findings"]:
            evidence = [
                Evidence(
                    kind=entry.get("kind", "event"),
                    event_id=entry["event_id"],
                    field_path="content."
                    + entry["source_pointer"]
                    .split("/content/", 1)[1]
                    .replace("/", "."),
                    excerpt=entry["excerpt"],
                    json_path=entry["source_pointer"],
                    located=True,
                )
                for entry in native["evidence"]
            ]
            detector = native.get("detector") or {}
            findings.append(
                Finding(
                    finding_id=native["finding_id"],
                    categories=[native["category"]],
                    scope=native["scope"],
                    origin_event_ids=native["origin_event_ids"],
                    affected_event_ids=native["affected_event_ids"],
                    located=native.get("located", bool(evidence)),
                    evidence=evidence,
                    reason=native["reason"],
                    rule_id=detector.get("rule", native.get("mechanism", "topic4")),
                    rule_version=detector.get(
                        "version", raw["provider"].get("version", "unknown")
                    ),
                    basis="provider_native",
                    provider_native=native,
                )
            )
        return ProviderOutput(
            findings=findings,
            verdict=Verdict(
                body["verdict"],
                "topic4_provider",
                "算法原始判定，详见算法结果与原始归档。",
            ),
            checks=Checks(
                "findings" if findings else "no_findings",
                len(findings),
                [f.rule_id for f in findings],
            ),
            policy={
                "document": policy.summary() if policy is not None else None,
                "status": "submitted_to_provider"
                if bound
                else "not_bound"
                if policy is not None
                else "absent",
                "binding": {
                    "bound": bound,
                    "reason": "已按样本范围传给课题四"
                    if bound
                    else "Policy 未提供或未绑定当前样本",
                },
                "checks": [],
            },
            provider_result={
                "profile": self.profile,
                "provider_run_id": raw["provider_run_id"],
                "sample_id": row["sample_id"],
                "audit_input_sha256": row["audit_input_sha256"],
                "provider": raw["provider"],
                "execution": raw["execution"],
                "checks": body["checks"],
                "provenance_limitations": [
                    "组件及模型 revision 以原始输出为准，未提供的字段保持缺失。"
                ]
                if self.profile == PROFILE
                else [],
            },
        )
