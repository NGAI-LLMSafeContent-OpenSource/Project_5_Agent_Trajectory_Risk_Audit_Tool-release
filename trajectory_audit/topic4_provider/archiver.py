# SPDX-License-Identifier: Apache-2.0
"""Provider 运行归档。

保存输入副本、原始输出、命令事实、消毒后的配置摘要、Provider 身份与
失败事实。归档目录本身不写入凭据值。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .raw_loader import copy_file, save_raw_json, save_raw_text
from .sanitize import config_sha256, sanitize_config, diagnostic_secrets, redact_diagnostics
from .types import ProcessRunResult, ValidationIssue


def archive_provider_run(
    archive_dir: str | Path,
    *,
    input_path: str | Path,
    config: Mapping[str, Any] | None = None,
    config_path: str | Path | None = None,
    raw_output_path: str | Path | None = None,
    raw_output: Any | None = None,
    process: ProcessRunResult | None = None,
    provider_identity: Mapping[str, Any] | None = None,
    mapped: Mapping[str, Any] | None = None,
    validation_issues: list[ValidationIssue] | None = None,
    failure: Mapping[str, Any] | None = None,
    degraded: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """写入一次 Provider 运行的归档，返回 manifest。"""

    root = Path(archive_dir)
    root.mkdir(parents=True, exist_ok=True)
    secrets = diagnostic_secrets(config, config_path)

    archived_input = copy_file(input_path, root / "input-batch.json")

    sanitized_config = sanitize_config(config) if config is not None else None
    digest = config_sha256(config) if config is not None else None
    if sanitized_config is not None:
        save_raw_json(root / "config.sanitized.json", sanitized_config)

    raw_out_target = root / "raw-output.json"
    raw_saved: str | None = None
    if raw_output_path is not None and Path(raw_output_path).is_file():
        copy_file(raw_output_path, raw_out_target)
        raw_saved = str(raw_out_target)
    elif raw_output is not None:
        save_raw_json(raw_out_target, raw_output)
        raw_saved = str(raw_out_target)
    elif raw_output_path is not None:
        # 保留“缺失输出”的诊断痕迹
        save_raw_text(
            root / "raw-output.missing.txt",
            f"missing: {raw_output_path}\n",
        )

    if process is not None:
        save_raw_text(root / "stdout.txt", redact_diagnostics(process.stdout, secrets))
        save_raw_text(root / "stderr.txt", redact_diagnostics(process.stderr, secrets))

    if mapped is not None:
        save_raw_json(root / "mapped.json", mapped)

    if validation_issues:
        save_raw_json(
            root / "validation-issues.json",
            [
                {"path": issue.path, "code": issue.code, "message": issue.message}
                for issue in validation_issues
            ],
        )

    command_facts = None
    if process is not None:
        command_facts = {
            "argv": list(process.argv),
            "cwd": process.cwd,
            "exit_code": process.exit_code,
            "timed_out": process.timed_out,
            "started": process.started,
            "duration_seconds": process.duration_seconds,
            "error": process.error,
        }

    manifest: dict[str, Any] = {
        "schema_version": "topic4-provider-run-archive/1",
        "input": str(archived_input),
        "config_path": str(config_path) if config_path is not None else None,
        "config_sha256": digest,
        "raw_output": raw_saved,
        "command": command_facts,
        "provider": dict(provider_identity) if provider_identity else None,
        "failure": dict(failure) if failure else None,
        "degraded": dict(degraded) if degraded else None,
        "validation_issue_count": len(validation_issues or []),
    }
    if extra:
        manifest["extra"] = dict(extra)

    manifest = redact_diagnostics(manifest, secrets)
    save_raw_json(root / "manifest.json", manifest)
    return manifest


def load_archive_manifest(archive_dir: str | Path) -> dict[str, Any]:
    path = Path(archive_dir) / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))
