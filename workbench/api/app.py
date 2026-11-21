# SPDX-License-Identifier: Apache-2.0
"""Workbench FastAPI：上传原始轨迹并返回 Review Bundle（单条或小批量）。"""

from __future__ import annotations

import json
import hashlib
import os
import re
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from trajectory_audit import openai_messages
from trajectory_audit.artifacts import read_json, write_json
from trajectory_audit.batch import execute_batch
from trajectory_audit.errors import RunError
from trajectory_audit.pujiang import source_mapping
from trajectory_audit.race_items import race_aggregate, with_algorithm_items
from trajectory_audit.review_bundle import SCHEMA_VERSION, project_review_bundle_from_run_dir
from trajectory_audit.runner import execute_run
from workbench.api.source_trajectory import (
    UNREADABLE_REASON,
    project_source_trajectory,
    unavailable_source_trajectory,
)
from workbench.api.ingest import (
    DEFAULT_LIMIT,
    DEFAULT_OFFSET,
    IngestError,
    decode_trajectory_bytes,
    parse_limit_offset,
    prepare_upload_payload,
    prepare_messages_upload,
)
from workbench.api.messages_evidence import archive_messages_evidence
from workbench.api.submission import SubmissionUnavailable, build_submission

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ADAPTER = "atbench"
DEFAULT_PROVIDER = "topic4-cli"
DEFAULT_PROFILE = "strict-v1"
DEFAULT_TIMEOUT = 1800.0
PROVIDER_OVERRIDE_FIELDS = frozenset(
    {
        "topic4_command",
        "topic4_config",
        "topic4_config_path",
        "topic4_profile",
    }
)
DEFAULT_TOPIC4_CONFIG = (
    REPO_ROOT / "fixtures/topic4_provider/fake_unsafe_located_config.json"
)


def default_topic4_command() -> str:
    return f"{sys.executable} -m tools.fake_topic4_provider"


def ensure_repo_pythonpath() -> None:
    """假 topic4 provider 在隔离 cwd 下运行，需要仓库根在 PYTHONPATH 上。"""

    root = str(REPO_ROOT)
    current = os.environ.get("PYTHONPATH", "")
    parts = [part for part in current.split(os.pathsep) if part]
    if root not in parts:
        os.environ["PYTHONPATH"] = os.pathsep.join([root, *parts])


def _environment_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是 true 或 false")


def create_app(
    *,
    storage_root: Path | None = None,
    default_topic4_config: Path | None = None,
    topic4_command: str | None = None,
    default_topic4_profile: str | None = None,
    default_topic4_timeout: float | None = None,
    allow_provider_overrides: bool | None = None,
) -> FastAPI:
    ensure_repo_pythonpath()
    configured_store = os.environ.get("WORKBENCH_STORAGE_ROOT")
    store = (
        Path(storage_root)
        if storage_root is not None
        else Path(configured_store)
        if configured_store
        else Path(tempfile.mkdtemp(prefix="workbench-"))
    )
    store.mkdir(parents=True, exist_ok=True)
    configured_topic4_config = os.environ.get("TOPIC4_CONFIG")
    topic4_config = (
        Path(default_topic4_config)
        if default_topic4_config is not None
        else Path(configured_topic4_config)
        if configured_topic4_config
        else DEFAULT_TOPIC4_CONFIG
    )
    resolved_command = (
        topic4_command
        if topic4_command is not None
        else os.environ.get("TOPIC4_COMMAND") or default_topic4_command()
    )
    resolved_profile = (
        default_topic4_profile
        if default_topic4_profile is not None
        else os.environ.get("TOPIC4_PROFILE", DEFAULT_PROFILE)
    )
    resolved_timeout = (
        float(default_topic4_timeout)
        if default_topic4_timeout is not None
        else float(os.environ.get("TOPIC4_TIMEOUT", DEFAULT_TIMEOUT))
    )
    overrides_allowed = (
        allow_provider_overrides
        if allow_provider_overrides is not None
        else _environment_flag("WORKBENCH_ALLOW_PROVIDER_OVERRIDES", default=True)
    )

    app = FastAPI(title="Review Workbench API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.storage_root = store
    app.state.default_topic4_config = topic4_config
    app.state.default_topic4_command = resolved_command
    app.state.default_topic4_profile = resolved_profile
    app.state.default_topic4_timeout = resolved_timeout
    app.state.allow_provider_overrides = overrides_allowed

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "service": "review-workbench",
            "provider": {
                "id": DEFAULT_PROVIDER,
                "profile": app.state.default_topic4_profile,
                "overrides_allowed": app.state.allow_provider_overrides,
            },
        }

    @app.post("/api/audit-sessions")
    async def create_audit_session(
        request: Request,
        trajectory: UploadFile | None = File(None),
        adapter: str = Form(DEFAULT_ADAPTER),
        limit: str | None = Form(None),
        offset: str | None = Form(None),
        topic4_config: UploadFile | None = File(None),
        topic4_command: str | None = Form(None),
        topic4_profile: str | None = Form(None),
    ) -> JSONResponse:
        content_type = request.headers.get("content-type", "")
        trajectory_bytes: bytes | None = None
        adapter_id = adapter or DEFAULT_ADAPTER
        command = topic4_command or app.state.default_topic4_command
        profile = topic4_profile or app.state.default_topic4_profile
        config_path = Path(app.state.default_topic4_config)
        raw_limit: Any = limit
        raw_offset: Any = offset

        if "application/json" in content_type and trajectory is None:
            body = await request.json()
            if not isinstance(body, dict):
                return _error_response(
                    status_code=400,
                    code="invalid_request",
                    message="JSON 请求体必须是对象",
                )
            if (
                not app.state.allow_provider_overrides
                and PROVIDER_OVERRIDE_FIELDS.intersection(body)
            ):
                return _error_response(
                    status_code=400,
                    code="provider_override_disabled",
                    message="部署环境不允许请求覆盖 Provider 配置",
                )
            raw = body.get("trajectory")
            if raw is None:
                return _error_response(
                    status_code=400,
                    code="missing_trajectory",
                    message="缺少 trajectory 字段",
                )
            if isinstance(raw, (dict, list)):
                trajectory_bytes = json.dumps(raw, ensure_ascii=False).encode("utf-8")
            elif isinstance(raw, str):
                trajectory_bytes = raw.encode("utf-8")
            else:
                return _error_response(
                    status_code=400,
                    code="invalid_trajectory",
                    message="trajectory 必须是 JSON 对象、数组或字符串",
                )
            adapter_id = str(body.get("adapter") or adapter_id)
            if "limit" in body:
                raw_limit = body.get("limit")
            if "offset" in body:
                raw_offset = body.get("offset")
            if body.get("topic4_command"):
                command = str(body["topic4_command"])
            if body.get("topic4_profile"):
                profile = str(body["topic4_profile"])
            if body.get("topic4_config_path"):
                config_path = Path(str(body["topic4_config_path"]))
            elif isinstance(body.get("topic4_config"), dict):
                config_path = Path(app.state.storage_root) / f"config-{uuid.uuid4().hex}.json"
                config_path.write_text(
                    json.dumps(body["topic4_config"], ensure_ascii=False),
                    encoding="utf-8",
                )
        else:
            if trajectory is None:
                return _error_response(
                    status_code=400,
                    code="missing_trajectory",
                    message="缺少 trajectory 上传文件",
                )
            multipart_overrides = {
                "topic4_command": topic4_command,
                "topic4_config": topic4_config,
                "topic4_profile": topic4_profile,
            }
            if not app.state.allow_provider_overrides and any(
                multipart_overrides.get(name) is not None
                for name in PROVIDER_OVERRIDE_FIELDS
            ):
                return _error_response(
                    status_code=400,
                    code="provider_override_disabled",
                    message="部署环境不允许请求覆盖 Provider 配置",
                )
            trajectory_bytes = await trajectory.read()
            if topic4_config is not None:
                config_bytes = await topic4_config.read()
                config_path = Path(app.state.storage_root) / f"config-{uuid.uuid4().hex}.json"
                config_path.write_bytes(config_bytes)

        if not trajectory_bytes:
            return _error_response(
                status_code=400,
                code="empty_trajectory",
                message="轨迹内容为空",
            )

        try:
            if adapter_id == "openai-messages":
                prepared = prepare_messages_upload(trajectory_bytes, limit=raw_limit, offset=raw_offset)
            else:
                limit_i, offset_i = parse_limit_offset(limit=raw_limit, offset=raw_offset)
                decoded = decode_trajectory_bytes(trajectory_bytes)
                prepared = prepare_upload_payload(
                    decoded, adapter_id=adapter_id, limit=limit_i, offset=offset_i,
                )
        except IngestError as exc:
            return _error_response(
                status_code=400,
                code=exc.code,
                message=exc.message,
                details=exc.details,
            )

        session_id = uuid.uuid4().hex
        session_dir = Path(app.state.storage_root) / "sessions" / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        input_path = session_dir / prepared.filename
        if adapter_id == "openai-messages":
            (session_dir / "upload.jsonl").write_bytes(trajectory_bytes)
        input_path.write_text(
            json.dumps(prepared.payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        out_root = session_dir / "runs"
        provider_options = {
            "config": str(config_path),
            "command": command,
            "profile": profile,
            "timeout": app.state.default_topic4_timeout,
        }

        ensure_repo_pythonpath()
        try:
            if prepared.mode == "batch":
                return _run_batch_session(
                    session_id=session_id,
                    session_dir=session_dir,
                    input_path=input_path,
                    out_root=out_root,
                    adapter_id=adapter_id,
                    provider_options=provider_options,
                    selection=prepared.selection or {},
                )
            return _run_single_session(
                session_id=session_id,
                session_dir=session_dir,
                input_path=input_path,
                out_root=out_root,
                adapter_id=adapter_id,
                provider_options=provider_options,
            )
        except RunError as exc:
            status = 400 if exc.exit_code in {2, 3} else 502
            if exc.code == "topic4_provider_failed":
                status = 502
            return _error_response(
                status_code=status,
                code=exc.code,
                message=exc.message,
                details=exc.details,
            )
        except Exception as exc:  # noqa: BLE001
            return _error_response(
                status_code=500,
                code="internal_error",
                message=str(exc),
            )

    @app.get("/api/audit-sessions/{session_id}")
    def get_audit_session(session_id: str) -> JSONResponse:
        try:
            if re.fullmatch(r"[0-9a-f]{32}", session_id):
                sessions = (Path(app.state.storage_root) / "sessions").resolve()
                session_dir = sessions / session_id
                if session_dir.resolve() == session_dir:
                    archived = _read_archived_session(session_dir)
                    if archived is not None:
                        return JSONResponse(archived, headers={"Cache-Control": "no-store"})
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
            return _error_response(
                status_code=500,
                code="session_archive_unreadable",
                message="无法读取这次审计的归档",
            )
        return _error_response(
            status_code=404,
            code="session_not_found",
            message="找不到这次审计，请确认链接和工作台地址",
        )

    @app.get("/api/audit-sessions/{session_id}/submission.jsonl")
    def download_submission(session_id: str) -> Response:
        session_dir = _session_dir(app, session_id)
        if session_dir is None:
            return _error_response(status_code=404, code="session_not_found", message="找不到这次审计的归档")
        try:
            archived = _read_archived_session(session_dir)
            if archived is None:
                return _error_response(status_code=404, code="session_not_found", message="找不到这次审计的归档")
            mappings, issues = {}, []
            for sample in archived["samples"]:
                sample_id = sample["sample_id"]
                if sample.get("ok") is not True:
                    continue
                try:
                    source = _archived_sample_source(session_dir, sample_id)
                    if source is None or source[0] is None:
                        raise ValueError("原始轨迹未归档")
                    trajectory = _read_session_file(session_dir, source[0])
                    record = trajectory["source"]["record"]
                    normalized = openai_messages.normalize_record(
                        record["raw"], record_index=record["index"],
                        dataset_id=trajectory["source"]["dataset_id"],
                        dataset_version=trajectory["source"]["dataset_version"],
                        input_sha256=trajectory["sample"]["input_sha256"],
                        record_json_path=record["json_path"], record_sha256=record["sha256"], sample_id=sample_id,
                    )[0]
                    mappings[sample_id] = source_mapping(normalized, line_number=sample["bundle"]["source_mapping"]["line_number"])
                except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
                    issues.append({"sample_id": sample_id, "item": "archive", "reason": f"原文或映射无法读取：{exc}"})
            if issues:
                raise SubmissionUnavailable(issues)
            payload = build_submission(archived, mappings)
            digest = hashlib.sha256(payload).hexdigest()
            return Response(payload, media_type="application/x-ndjson; charset=utf-8", headers={
                "Content-Disposition": 'attachment; filename="submission.jsonl"',
                "Cache-Control": "no-store", "ETag": f'"{digest}"',
                "X-Audit-Session-ID": session_id,
            })
        except SubmissionUnavailable as exc:
            return _error_response(status_code=409, code="submission_unavailable", message=str(exc), details={"issues": exc.issues})
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
            return _error_response(status_code=500, code="session_archive_unreadable", message="无法读取这次审计的归档")

    @app.get("/api/audit-sessions/{session_id}/samples/{sample_id}/source-trajectory")
    def get_source_trajectory(session_id: str, sample_id: str) -> JSONResponse:
        session_dir = _session_dir(app, session_id)
        if session_dir is None:
            return _error_response(
                status_code=404,
                code="session_not_found",
                message="找不到这次审计，请确认链接和工作台地址",
            )
        try:
            archived = _archived_sample_source(session_dir, sample_id)
            if archived is None:
                return _error_response(
                    status_code=404,
                    code="sample_not_found",
                    message="这次审计中没有该样本的原始轨迹",
                )
            trajectory_path, source_format = archived
            if trajectory_path is None:
                return _source_trajectory_response(
                    unavailable_source_trajectory(
                        sample_id=sample_id, source_format=source_format
                    )
                )
            projected = project_source_trajectory(_read_session_file(session_dir, trajectory_path))
            if projected is None:
                return _source_trajectory_response(
                    unavailable_source_trajectory(
                        sample_id=sample_id, source_format=source_format
                    )
                )
            return _source_trajectory_response(
                {
                    "ok": True,
                    "sample_id": sample_id,
                    "source_format": projected["source_format"] or source_format,
                    "availability": {"status": "available"},
                    "steps": projected["steps"],
                }
            )
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError):
            return _source_trajectory_response(
                unavailable_source_trajectory(
                    sample_id=sample_id,
                    source_format=None,
                    reason=UNREADABLE_REASON,
                )
            )

    return app


def _source_trajectory_response(payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


def _session_dir(app: FastAPI, session_id: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f]{32}", session_id):
        return None
    sessions = (Path(app.state.storage_root) / "sessions").resolve()
    session_dir = sessions / session_id
    if session_dir.resolve() != session_dir or not session_dir.is_dir():
        return None
    return session_dir


def _archived_sample_source(
    session_dir: Path, sample_id: str
) -> tuple[Path | None, str | None] | None:
    """定位已成功归档的样本。文件缺失时路径为 None，样本不存在时返回 None。"""

    runs_dir = session_dir / "runs"
    for path in runs_dir.glob("run-*/review-bundle.json"):
        if not re.fullmatch(r"run-[0-9a-f]{16}", path.parent.name):
            continue
        bundle = _read_archived_bundle(session_dir, path)
        if bundle["sample"]["sample_id"] != sample_id:
            return None
        return _trajectory_beside(session_dir, path.parent, bundle)

    manifest_path = session_dir / "audit-session.json"
    if not manifest_path.is_file():
        return None
    manifest = _read_session_file(session_dir, manifest_path)
    batch_id = manifest.get("batch_id", "")
    if not re.fullmatch(r"batch-[0-9a-f]{16}", batch_id):
        raise ValueError("Invalid archived batch ID")
    for entry in manifest["samples"]:
        if entry.get("sample_id") != sample_id:
            continue
        if entry.get("trajectory_path"):
            return session_dir / entry["trajectory_path"], "openai_messages"
        if not entry.get("ok"):
            return None
        run_id = entry.get("run_id", "")
        if not re.fullmatch(r"run-[0-9a-f]{16}", run_id):
            raise ValueError("Invalid archived run ID")
        run_dir = runs_dir / batch_id / "samples" / run_id
        bundle = _read_archived_bundle(session_dir, run_dir / "review-bundle.json")
        return _trajectory_beside(session_dir, run_dir, bundle)
    return None


def _trajectory_beside(
    session_dir: Path, run_dir: Path, bundle: dict[str, Any]
) -> tuple[Path | None, str | None]:
    source_format = bundle.get("sample", {}).get("source_format")
    resolved_format = source_format if isinstance(source_format, str) else None
    trajectory_path = run_dir / "trajectory.json"
    resolved = trajectory_path.resolve()
    if not resolved.is_relative_to(session_dir.resolve()) or not resolved.is_file():
        return None, resolved_format
    return resolved, resolved_format


def _read_archived_session(session_dir: Path) -> dict[str, Any] | None:
    runs_dir = session_dir / "runs"
    # Old single uploads could use archive filenames. Read the actual run's bundle.
    for path in runs_dir.glob("run-*/review-bundle.json"):
        if re.fullmatch(r"run-[0-9a-f]{16}", path.parent.name):
            bundle = _read_archived_bundle(session_dir, path)
            return _single_session_payload(session_dir.name, path.parent.name, bundle)

    manifest_path = session_dir / "audit-session.json"
    if not manifest_path.is_file():
        return None
    manifest = _read_session_file(session_dir, manifest_path)
    batch_id = manifest.get("batch_id", "")
    if not re.fullmatch(r"batch-[0-9a-f]{16}", batch_id):
        raise ValueError("Invalid archived batch ID")
    batch_dir = runs_dir / batch_id
    if not (batch_dir / "batch.json").is_file():
        return None

    samples = []
    for entry in manifest["samples"]:
        sample = {"sample_id": entry["sample_id"], "ok": entry["ok"]}
        if entry["ok"]:
            run_id = entry["run_id"]
            if not re.fullmatch(r"run-[0-9a-f]{16}", run_id):
                raise ValueError("Invalid archived run ID")
            path = batch_dir / "samples" / run_id / "review-bundle.json"
            sample.update(run_id=run_id, bundle=_read_archived_bundle(session_dir, path))
        else:
            sample["error"] = entry["error"]
        if entry.get("evidence_bundle"):
            sample["bundle"] = _read_archived_bundle(session_dir, session_dir / entry["evidence_bundle"])
        samples.append(sample)
    return _batch_session_payload(session_dir.name, batch_id, manifest["selection"], samples)


def _read_session_file(session_dir: Path, path: Path) -> Any:
    resolved = path.resolve()
    if not resolved.is_relative_to(session_dir):
        raise ValueError("Archive file is outside its session")
    return read_json(resolved)


def _read_archived_bundle(session_dir: Path, path: Path) -> dict[str, Any]:
    bundle = _read_session_file(session_dir, path)
    if bundle["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported review bundle")
    return bundle


def _run_single_session(
    *,
    session_id: str,
    session_dir: Path,
    input_path: Path,
    out_root: Path,
    adapter_id: str,
    provider_options: dict[str, Any],
) -> JSONResponse:
    summary = execute_run(
        input_path=input_path,
        out_root=out_root,
        provider_id=DEFAULT_PROVIDER,
        adapter_id=adapter_id,
        provider_options=provider_options,
    )
    run_dir = Path(summary["run_dir"])
    bundle = project_review_bundle_from_run_dir(run_dir, write_artifact=True)
    write_json(session_dir / "review-bundle.json", bundle)
    return JSONResponse(_single_session_payload(session_id, summary["run_id"], bundle))


def _single_session_payload(
    session_id: str, run_id: str, bundle: dict[str, Any]
) -> dict[str, Any]:
    sample = {
        "sample_id": bundle["sample"]["sample_id"],
        "ok": True,
        "run_id": run_id,
        "bundle": bundle,
    }
    return {
        "ok": True,
        "session_id": session_id,
        "mode": "single",
        "run_id": run_id,
        "bundle": bundle,
        "samples": [sample],
    }


def _run_batch_session(
    *,
    session_id: str,
    session_dir: Path,
    input_path: Path,
    out_root: Path,
    adapter_id: str,
    provider_options: dict[str, Any],
    selection: dict[str, int],
) -> JSONResponse:
    evidence = {}
    if adapter_id == "openai-messages":
        evidence = archive_messages_evidence(session_dir, read_json(input_path), selection["offset"])
    try:
        summary = execute_batch(
            input_path=input_path,
            out_root=out_root,
            provider_id=DEFAULT_PROVIDER,
            adapter_id=adapter_id,
            provider_options=provider_options,
        )
    except RunError as exc:
        if not evidence:
            raise
        # Provider initialization can fail before the batch runner creates an archive.
        batch_id = "batch-" + hashlib.sha256(input_path.read_bytes()).hexdigest()[:16]
        failed_dir = out_root / batch_id
        failed_dir.mkdir(parents=True, exist_ok=True)
        summary = {"batch_id": batch_id, "batch_dir": str(failed_dir), "samples": [],
                   "failures": [{"sample_id": sample_id, "code": exc.code,
                                 "reason": exc.message, "details": exc.details} for sample_id in evidence]}
        write_json(failed_dir / "batch.json", {**summary, "status": "failed"})
    batch_dir = Path(summary["batch_dir"])
    samples: list[dict[str, Any]] = []

    for item in summary.get("samples") or []:
        run_rel = item.get("run_dir")
        if not run_rel:
            samples.append(
                {
                    "sample_id": item.get("sample_id"),
                    "ok": False,
                    "error": {
                        "code": "missing_run_dir",
                        "message": "批次样本缺少 run_dir",
                        "details": {},
                    },
                }
            )
            continue
        run_dir = batch_dir / run_rel
        bundle = project_review_bundle_from_run_dir(run_dir, write_artifact=True)
        samples.append(
            {
                "sample_id": item.get("sample_id") or bundle["sample"]["sample_id"],
                "ok": True,
                "run_id": item.get("run_id"),
                "bundle": bundle,
            }
        )

    for failure in summary.get("failures") or []:
        samples.append(
            {
                "sample_id": failure.get("sample_id"),
                "ok": False,
                "error": {
                    "code": failure.get("code") or "sample_failed",
                    "message": failure.get("reason") or "样本审计失败",
                    "details": failure.get("details") or {},
                },
            }
        )

    batch_id = summary["batch_id"]
    for sample in samples:
        retained = evidence.get(sample["sample_id"])
        if retained:
            if sample.get("bundle"):
                sample["bundle"]["source_mapping"] = retained["bundle"]["source_mapping"]
            else:
                sample["bundle"] = retained["bundle"]
            error = sample.get("error") or {}
            sample["bundle"]["race"] = with_algorithm_items(
                retained["bundle"]["race"],
                verdict=sample["bundle"]["verdict"]["value"],
                completed=bool(sample.get("ok")),
                failure=f"算法未完成：{error.get('code', 'sample_failed')}" if error else None,
            )
            sample["trajectory_path"] = retained["trajectory_path"]
            bundle_path = str(Path(retained["trajectory_path"]).parent / "review-bundle.json")
            sample["evidence_bundle"] = bundle_path
            write_json(session_dir / bundle_path, sample["bundle"])
    if evidence:
        order = {sample_id: i for i, sample_id in enumerate(evidence)}
        samples.sort(key=lambda sample: order[sample["sample_id"]])
    write_json(
        session_dir / "audit-session.json",
        {
            "ok": True,
            "session_id": session_id,
            "mode": "batch",
            "batch_id": batch_id,
            "selection": selection,
            "samples": [
                {
                    "sample_id": sample.get("sample_id"),
                    "ok": sample.get("ok"),
                    "run_id": sample.get("run_id"),
                    "error": sample.get("error"),
                    "has_bundle": bool(sample.get("bundle")),
                    **({"trajectory_path": sample["trajectory_path"], "evidence_bundle": sample["evidence_bundle"]}
                       if sample.get("trajectory_path") else {}),
                }
                for sample in samples
            ],
        },
    )
    return JSONResponse(_batch_session_payload(session_id, batch_id, selection, samples))


def _batch_session_payload(
    session_id: str, batch_id: str, selection: dict[str, int], samples: list[dict[str, Any]]
) -> dict[str, Any]:
    samples = [{key: value for key, value in sample.items() if key not in {"trajectory_path", "evidence_bundle"}}
               for sample in samples]
    response = {
        "ok": True,
        "session_id": session_id,
        "mode": "batch",
        "batch_id": batch_id,
        "selection": selection,
        "samples": samples,
    }
    races = {sample["sample_id"]: sample["bundle"]["race"] for sample in samples if "race" in (sample.get("bundle") or {})}
    if races:
        response["aggregate"] = race_aggregate(races)
    # Keep first successful bundle at top-level for older clients; frontend prefers samples[].
    first_ok = next((sample for sample in samples if sample.get("ok") and sample.get("bundle")), None)
    if first_ok is not None:
        response["bundle"] = first_ok["bundle"]
        response["run_id"] = first_ok.get("run_id")
    return response


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    payload: dict[str, Any] = {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
        },
    }
    return JSONResponse(status_code=status_code, content=payload)


app = create_app()


__all__ = [
    "DEFAULT_ADAPTER",
    "DEFAULT_LIMIT",
    "DEFAULT_OFFSET",
    "DEFAULT_PROFILE",
    "DEFAULT_PROVIDER",
    "DEFAULT_TIMEOUT",
    "PROVIDER_OVERRIDE_FIELDS",
    "app",
    "create_app",
    "default_topic4_command",
    "ensure_repo_pythonpath",
]
