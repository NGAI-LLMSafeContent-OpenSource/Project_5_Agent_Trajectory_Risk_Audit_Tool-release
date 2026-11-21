# SPDX-License-Identifier: Apache-2.0
"""把上传体识别为单条 / 批次，并按 limit/offset 切片包装。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from trajectory_audit.adapters import SOURCE_BATCH_SCHEMA_VERSION
from trajectory_audit.errors import RunError
from trajectory_audit.pujiang import parse_jsonl

DEFAULT_LIMIT = 5
MIN_LIMIT = 1
DEFAULT_OFFSET = 0


class IngestError(ValueError):
    """上传体无法切片或包装时的结构化错误。"""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class PreparedInput:
    """准备好写入磁盘并交给 runner/batch 的输入。"""

    mode: str  # "single" | "batch"
    payload: dict[str, Any] | list[Any] | Any
    selection: dict[str, int] | None
    filename: str


def prepare_messages_upload(raw: bytes, *, limit: Any = None, offset: Any = None) -> PreparedInput:
    """Validate every JSONL line and select the complete file unless requested otherwise."""
    try:
        rows = parse_jsonl(raw)
    except RunError as exc:
        raise IngestError(exc.code, exc.message, details=exc.details) from exc
    total = len(rows)
    # The full-file default has no legacy 5/20-record cap.
    if limit in (None, "") and offset in (None, ""):
        limit_i, offset_i = total, 0
    else:
        try:
            limit_i = total if limit in (None, "") else int(limit)
            offset_i = 0 if offset in (None, "") else int(offset)
            if str(limit_i) != str(limit) and limit not in (None, ""):
                raise ValueError
            if str(offset_i) != str(offset) and offset not in (None, ""):
                raise ValueError
            if limit_i < 1 or offset_i < 0:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise IngestError("invalid_selection", "Limit 必须是正整数，Offset 必须是非负整数") from exc
    return _prepare_raw_array(
        [row["record"] for row in rows], adapter_id="openai-messages", limit=limit_i, offset=offset_i,
    )


def parse_limit_offset(
    *,
    limit: Any = None,
    offset: Any = None,
) -> tuple[int, int]:
    """解析并校验 limit/offset；越界抛 IngestError。"""

    resolved_limit = DEFAULT_LIMIT if limit is None or limit == "" else limit
    resolved_offset = DEFAULT_OFFSET if offset is None or offset == "" else offset
    try:
        limit_i = int(resolved_limit)
        offset_i = int(resolved_offset)
    except (TypeError, ValueError) as exc:
        raise IngestError(
            "invalid_selection",
            "limit 与 offset 必须是整数",
            details={"limit": limit, "offset": offset},
        ) from exc
    if limit_i < MIN_LIMIT:
        raise IngestError(
            "limit_out_of_range",
            f"limit 必须大于或等于 {MIN_LIMIT}",
            details={"limit": limit_i, "min": MIN_LIMIT},
        )
    if offset_i < 0:
        raise IngestError(
            "offset_out_of_range",
            "offset 不能为负数",
            details={"offset": offset_i},
        )
    return limit_i, offset_i


def prepare_upload_payload(
    payload: Any,
    *,
    adapter_id: str,
    limit: int,
    offset: int,
) -> PreparedInput:
    """识别单条对象、原始数组或 source-batch/1，并按需切片包装。"""

    if isinstance(payload, list):
        return _prepare_raw_array(payload, adapter_id=adapter_id, limit=limit, offset=offset)
    if isinstance(payload, dict) and payload.get("schema_version") == SOURCE_BATCH_SCHEMA_VERSION:
        return _prepare_source_batch(payload, adapter_id=adapter_id, limit=limit, offset=offset)
    if isinstance(payload, dict):
        return PreparedInput(
            mode="single",
            payload=payload,
            selection=None,
            filename="trajectory.json",
        )
    raise IngestError(
        "invalid_trajectory",
        "trajectory 必须是 JSON 对象、原始记录数组或 source-batch/1",
        details={"type": type(payload).__name__},
    )


def decode_trajectory_bytes(raw: bytes) -> Any:
    """解析上传字节为 JSON。"""

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IngestError("invalid_encoding", "轨迹文件必须是 UTF-8 JSON") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise IngestError("invalid_json", f"JSON 解析失败：{exc.msg}") from exc


def wrap_raw_records(records: list[Any], *, adapter_id: str) -> dict[str, Any]:
    """把原始记录数组包装为最小 source-batch/1。"""

    return {
        "schema_version": SOURCE_BATCH_SCHEMA_VERSION,
        "source": {
            "dataset_id": adapter_id,
            "dataset_version": "workbench-upload",
            "adapter_id": adapter_id,
            "record_selection": "workbench upload slice",
        },
        "records": records,
    }


def _prepare_raw_array(
    records: list[Any],
    *,
    adapter_id: str,
    limit: int,
    offset: int,
) -> PreparedInput:
    total = len(records)
    if total == 0:
        raise IngestError("empty_trajectory", "原始记录数组为空")
    selected = records[offset : offset + limit]
    if not selected:
        raise IngestError(
            "empty_selection",
            "offset/limit 切片后没有记录",
            details={"offset": offset, "limit": limit, "total_records": total},
        )
    batch = wrap_raw_records(selected, adapter_id=adapter_id)
    return PreparedInput(
        mode="batch",
        payload=batch,
        selection={
            "offset": offset,
            "limit": limit,
            "total_records": total,
            "selected": len(selected),
        },
        filename="source-batch.json",
    )


def _prepare_source_batch(
    payload: dict[str, Any],
    *,
    adapter_id: str,
    limit: int,
    offset: int,
) -> PreparedInput:
    records = payload.get("records")
    if not isinstance(records, list):
        raise IngestError("invalid_batch", "source-batch/1 的 records 必须是数组")
    total = len(records)
    if total == 0:
        raise IngestError("empty_trajectory", "source-batch/1 的 records 为空")
    selected = records[offset : offset + limit]
    if not selected:
        raise IngestError(
            "empty_selection",
            "offset/limit 切片后没有记录",
            details={"offset": offset, "limit": limit, "total_records": total},
        )
    source = dict(payload.get("source") or {})
    if not source.get("dataset_id"):
        source["dataset_id"] = adapter_id
    if not source.get("dataset_version"):
        source["dataset_version"] = "workbench-upload"
    source.setdefault("adapter_id", adapter_id)
    batch = {
        **payload,
        "schema_version": SOURCE_BATCH_SCHEMA_VERSION,
        "source": source,
        "records": selected,
    }
    return PreparedInput(
        mode="batch",
        payload=batch,
        selection={
            "offset": offset,
            "limit": limit,
            "total_records": total,
            "selected": len(selected),
        },
        filename="source-batch.json",
    )


__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_OFFSET",
    "IngestError",
    "MIN_LIMIT",
    "PreparedInput",
    "decode_trajectory_bytes",
    "parse_limit_offset",
    "prepare_upload_payload",
    "wrap_raw_records",
]
