# SPDX-License-Identifier: Apache-2.0
"""Topic 4 CLI Adapter 编排入口。

复制输入/配置到隔离工作目录，启动外部 CLI，加载原始输出，校验合同，
映射 Detected Finding，并归档运行事实。正式 Topic4 失败默认不 fallback；
仅当 ``allow_fallback=True`` 时记录 degraded 与原始失败。
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .archiver import archive_provider_run
from .mapper import map_topic4_result
from .process_runner import run_command
from .raw_loader import RawLoadError, load_raw_json
from .sanitize import config_sha256, sanitize_config, diagnostic_secrets, redact_diagnostics
from .types import AdapterOutcome, ProcessRunResult, ValidationIssue
from .validator import validate_result
from .finial import PROFILE, deployment_config, validate_finial_result

FallbackRunner = Callable[[dict[str, Any]], dict[str, Any]]


def run_topic4_adapter(
    *,
    input_path: str | Path,
    config_path: str | Path,
    command: Sequence[str],
    archive_dir: str | Path | None = None,
    timeout: float | None = 600.0,
    allow_fallback: bool = False,
    fallback_runner: FallbackRunner | None = None,
    work_root: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    validation_profile: str = "strict-v1",
) -> AdapterOutcome:
    """运行文件式 Topic4 Provider 并返回编排结果。

    ``command`` 是调用 Provider 的 argv 前缀，例如
    ``["python3", "-m", "tools.fake_topic4_provider"]`` 或
    ``["/path/to/topic4-audit"]``。Adapter 会追加
    ``run --input <work/input.json> --output <work/output.json> --config <work/config.json>``。
    """

    if validation_profile not in ("strict-v1", PROFILE):
        raise ValueError(f"Unknown Topic 4 validation profile: {validation_profile}")
    source_input = Path(input_path).resolve()
    source_config = Path(config_path).resolve()
    if not source_input.is_file():
        return AdapterOutcome(
            ok=False,
            kind="transport",
            input_path=str(source_input),
            output_path=None,
            archive_dir=str(archive_dir) if archive_dir else None,
            failure={
                "stage": "adapter",
                "code": "missing_input",
                "reason": f"输入文件不存在：{source_input}",
                "retryable": False,
            },
        )
    if not source_config.is_file():
        return AdapterOutcome(
            ok=False,
            kind="transport",
            input_path=str(source_input),
            output_path=None,
            archive_dir=str(archive_dir) if archive_dir else None,
            failure={
                "stage": "adapter",
                "code": "missing_config",
                "reason": f"配置文件不存在：{source_config}",
                "retryable": False,
            },
        )

    try:
        input_batch = json.loads(source_input.read_text(encoding="utf-8"))
        config = json.loads(source_config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return AdapterOutcome(
            ok=False,
            kind="transport",
            input_path=str(source_input),
            output_path=None,
            archive_dir=str(archive_dir) if archive_dir else None,
            failure={
                "stage": "adapter",
                "code": "unreadable_input_or_config",
                "reason": str(exc),
                "retryable": False,
            },
        )

    if work_root is None:
        workdir = Path(tempfile.mkdtemp(prefix="topic4-provider-"))
        cleanup_work = True
    else:
        workdir = Path(work_root)
        workdir.mkdir(parents=True, exist_ok=True)
        cleanup_work = False

    work_input = workdir / "input-batch.json"
    work_config = workdir / "provider-config.json"
    work_output = workdir / "audit-result.json"
    shutil.copyfile(source_input, work_input)
    # 工作目录内仍可放原始配置供 CLI 使用；归档只保存消毒副本。
    if validation_profile == PROFILE:
        work_config.write_text(
            json.dumps(deployment_config(config, source_config), ensure_ascii=False),
            encoding="utf-8",
        )
    else:
        shutil.copyfile(source_config, work_config)

    argv = [
        *[str(part) for part in command],
        "run",
        "--input",
        str(work_input),
        "--output",
        str(work_output),
        "--config",
        str(work_config),
    ]
    process = run_command(argv, cwd=workdir, timeout=timeout, env=env)
    secrets = diagnostic_secrets(config, source_config, env)
    process = ProcessRunResult(**redact_diagnostics(vars(process), secrets))

    outcome = _interpret_process(
        input_batch=input_batch,
        config=config,
        process=process,
        work_output=work_output,
        source_input=source_input,
        source_config=source_config,
        archive_dir=Path(archive_dir) if archive_dir else None,
        allow_fallback=allow_fallback,
        fallback_runner=fallback_runner,
        validation_profile=validation_profile,
    )

    if cleanup_work:
        shutil.rmtree(workdir, ignore_errors=True)
    return outcome


def _interpret_process(
    *,
    input_batch: dict[str, Any],
    config: Mapping[str, Any],
    process: ProcessRunResult,
    work_output: Path,
    source_input: Path,
    source_config: Path,
    archive_dir: Path | None,
    allow_fallback: bool,
    fallback_runner: FallbackRunner | None,
    validation_profile: str,
) -> AdapterOutcome:
    sanitized = sanitize_config(config)
    digest = config_sha256(config)

    def _archive(
        *,
        raw_result: Any | None,
        mapped: dict[str, Any] | None,
        issues: list[ValidationIssue] | None,
        failure: dict[str, Any] | None,
        degraded: dict[str, Any] | None,
        provider_identity: Mapping[str, Any] | None,
        kind: str,
    ) -> dict[str, Any] | None:
        if archive_dir is None:
            return None
        return archive_provider_run(
            archive_dir,
            input_path=source_input,
            config=config,
            config_path=source_config,
            raw_output_path=work_output if work_output.exists() else None,
            raw_output=raw_result if isinstance(raw_result, dict) else None,
            process=process,
            provider_identity=provider_identity,
            mapped=mapped,
            validation_issues=issues,
            failure=failure,
            degraded=degraded,
            extra={"kind": kind, "config_sha256": digest},
        )

    if not process.started or process.timed_out or process.exit_code != 0:
        code = "command_not_started"
        reason = process.error or "命令未能启动"
        if process.timed_out:
            code = "timeout"
            reason = f"Provider 总超时（{process.duration_seconds:.1f}s）"
        elif process.started and process.exit_code != 0:
            code = "nonzero_exit"
            reason = f"Provider 非零退出：{process.exit_code}"
        failure = {
            "stage": "transport",
            "code": code,
            "reason": reason,
            "retryable": process.timed_out,
            "details": {
                "exit_code": process.exit_code,
                "timed_out": process.timed_out,
                "started": process.started,
            },
        }
        # 非零退出时文件只作诊断，不当正式语义结果。
        diagnostic: Any | None = None
        if work_output.is_file():
            try:
                diagnostic = load_raw_json(work_output)
            except RawLoadError:
                diagnostic = None
        return _maybe_fallback(
            input_batch=input_batch,
            failure=failure,
            process=process,
            source_input=source_input,
            archive_dir=archive_dir,
            allow_fallback=allow_fallback,
            fallback_runner=fallback_runner,
            archive_fn=lambda **kwargs: _archive(
                raw_result=diagnostic,
                mapped=None,
                issues=None,
                failure=failure,
                degraded=kwargs.get("degraded"),
                provider_identity=None,
                kind=kwargs.get("kind", "transport"),
            ),
            kind="transport",
            sanitized_config=sanitized,
        )

    try:
        raw_result = load_raw_json(work_output)
    except RawLoadError as exc:
        failure = {
            "stage": "transport",
            "code": exc.code,
            "reason": exc.message,
            "retryable": False,
            "details": {"path": exc.path},
        }
        return _maybe_fallback(
            input_batch=input_batch,
            failure=failure,
            process=process,
            source_input=source_input,
            archive_dir=archive_dir,
            allow_fallback=allow_fallback,
            fallback_runner=fallback_runner,
            archive_fn=lambda **kwargs: _archive(
                raw_result=None,
                mapped=None,
                issues=None,
                failure=failure,
                degraded=kwargs.get("degraded"),
                provider_identity=None,
                kind=kwargs.get("kind", "transport"),
            ),
            kind="transport",
            sanitized_config=sanitized,
        )

    if not isinstance(raw_result, dict):
        failure = {
            "stage": "contract",
            "code": "result_not_object",
            "reason": "输出根节点不是 JSON 对象",
            "retryable": False,
        }
        issues = [
            ValidationIssue("", "result_not_object", "输出根节点不是 JSON 对象")
        ]
        manifest = _archive(
            raw_result=raw_result,
            mapped=None,
            issues=issues,
            failure=failure,
            degraded=None,
            provider_identity=None,
            kind="contract",
        )
        return AdapterOutcome(
            ok=False,
            kind="contract",
            input_path=str(source_input),
            output_path=str(work_output),
            archive_dir=str(archive_dir) if archive_dir else None,
            process=process,
            raw_result=None,
            validation_issues=issues,
            failure=failure,
            archive_manifest=manifest,
        )

    validator = validate_finial_result if validation_profile == PROFILE else validate_result
    issues = validator(input_batch, raw_result)
    if issues:
        failure = {
            "stage": "contract",
            "code": "contract_validation_failed",
            "reason": f"合同校验失败（{len(issues)} 项）",
            "retryable": False,
            "details": {
                "issues": [
                    {"path": i.path, "code": i.code, "message": i.message}
                    for i in issues[:20]
                ]
            },
        }
        return _maybe_fallback(
            input_batch=input_batch,
            failure=failure,
            process=process,
            source_input=source_input,
            archive_dir=archive_dir,
            allow_fallback=allow_fallback,
            fallback_runner=fallback_runner,
            archive_fn=lambda **kwargs: _archive(
                raw_result=raw_result,
                mapped=None,
                issues=issues,
                failure=failure,
                degraded=kwargs.get("degraded"),
                provider_identity=raw_result.get("provider")
                if isinstance(raw_result, dict)
                else None,
                kind=kwargs.get("kind", "contract"),
            ),
            kind="contract",
            sanitized_config=sanitized,
            raw_result=raw_result,
            validation_issues=issues,
        )

    mapped = map_topic4_result(raw_result)
    provider_identity = raw_result.get("provider")
    if isinstance(provider_identity, dict):
        provider_identity = {
            **provider_identity,
            "config_sha256": provider_identity.get("config_sha256") or digest,
        }
    manifest = _archive(
        raw_result=raw_result,
        mapped=mapped,
        issues=None,
        failure=None,
        degraded=None,
        provider_identity=provider_identity
        if isinstance(provider_identity, dict)
        else None,
        kind="semantic",
    )
    return AdapterOutcome(
        ok=True,
        kind="semantic",
        input_path=str(source_input),
        output_path=str(work_output),
        archive_dir=str(archive_dir) if archive_dir else None,
        process=process,
        raw_result=raw_result,
        mapped=mapped,
        archive_manifest=manifest,
    )


def _maybe_fallback(
    *,
    input_batch: dict[str, Any],
    failure: dict[str, Any],
    process: ProcessRunResult,
    source_input: Path,
    archive_dir: Path | None,
    allow_fallback: bool,
    fallback_runner: FallbackRunner | None,
    archive_fn,
    kind: str,
    sanitized_config: Any,
    raw_result: dict[str, Any] | None = None,
    validation_issues: list[ValidationIssue] | None = None,
) -> AdapterOutcome:
    if not allow_fallback:
        manifest = archive_fn(degraded=None, kind=kind)
        return AdapterOutcome(
            ok=False,
            kind=kind,
            input_path=str(source_input),
            output_path=None,
            archive_dir=str(archive_dir) if archive_dir else None,
            process=process,
            raw_result=raw_result,
            validation_issues=validation_issues or [],
            failure=failure,
            archive_manifest=manifest,
        )

    if fallback_runner is None:
        manifest = archive_fn(degraded=None, kind=kind)
        return AdapterOutcome(
            ok=False,
            kind=kind,
            input_path=str(source_input),
            output_path=None,
            archive_dir=str(archive_dir) if archive_dir else None,
            process=process,
            raw_result=raw_result,
            validation_issues=validation_issues or [],
            failure={
                **failure,
                "details": {
                    **(failure.get("details") or {}),
                    "allow_fallback": True,
                    "fallback_error": "fallback_runner_not_provided",
                },
            },
            archive_manifest=manifest,
        )

    degraded = {
        "mode": "explicit_fallback",
        "from_provider": "topic4-cli",
        "to_provider": "deterministic-baseline",
        "reason": "调用方显式允许 fallback，改由确定性 baseline 审计",
        "original_failure": failure,
        "retained": True,
        "degraded": True,
    }
    try:
        mapped = fallback_runner(input_batch)
    except Exception as exc:  # noqa: BLE001 - 显式记录 fallback 自身失败
        degraded["fallback_error"] = str(exc)
        manifest = archive_fn(degraded=degraded, kind="fallback")
        return AdapterOutcome(
            ok=False,
            kind="fallback",
            input_path=str(source_input),
            output_path=None,
            archive_dir=str(archive_dir) if archive_dir else None,
            process=process,
            raw_result=raw_result,
            validation_issues=validation_issues or [],
            failure=failure,
            degraded=degraded,
            archive_manifest=manifest,
        )

    if isinstance(mapped, dict):
        mapped = {**mapped, "degraded": degraded}
    manifest = archive_fn(degraded=degraded, kind="fallback")
    return AdapterOutcome(
        ok=True,
        kind="fallback",
        input_path=str(source_input),
        output_path=None,
        archive_dir=str(archive_dir) if archive_dir else None,
        process=process,
        raw_result=raw_result,
        validation_issues=validation_issues or [],
        mapped=mapped if isinstance(mapped, dict) else {"result": mapped},
        failure=failure,
        degraded=degraded,
        archive_manifest=manifest,
    )
