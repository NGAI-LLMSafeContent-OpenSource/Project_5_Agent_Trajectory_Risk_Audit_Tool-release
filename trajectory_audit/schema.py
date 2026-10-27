# SPDX-License-Identifier: Apache-2.0
"""统一轨迹 Schema 校验与原始记录定位解析。

校验对象是**已序列化**的轨迹字典，而不是归一过程中的内部对象：下游消费者、
run 包和 run 包外部的复核者看到的都是这份结构，因此校验必须针对它。

本模块不读取数据集名称，也不按来源格式分支；来源差异只能体现在字段值中。
"""

from __future__ import annotations

import re
from typing import Any

from .model import (
    EVENT_TYPES,
    REFERENCE_STATUS_ABSENT,
    SOURCE_LABEL_SCOPES,
    SOURCE_LABEL_SCOPE_STEP,
    SOURCE_LABEL_SCOPE_TRAJECTORY,
)

TRAJECTORY_SCHEMA_VERSION = "trajectory/2"
SOURCE_LABEL_LAYER = "source"
REFERENCE_LAYERS = ("absent", "present")
VERDICTS = ("unsafe", "safe", "undetermined")

TOP_LEVEL_KEYS = (
    "schema_version",
    "sample",
    "source",
    "source_steps",
    "source_labels",
    "reference",
    "events",
    "unsupported",
)
SAMPLE_KEYS = (
    "sample_id",
    "source_format",
    "adapter",
    "input_sha256",
    "unmapped_top_level_fields",
)
SOURCE_KEYS = ("dataset_id", "dataset_version", "record")
RECORD_KEYS = ("index", "json_path", "sha256", "raw")
EVENT_KEYS = (
    "event_id",
    "sequence",
    "type",
    "actor",
    "source_step_id",
    "content",
    "source",
    "raw",
)
LOCATOR_KEYS = (
    "record_index",
    "json_path",
    "record_sha256",
    "dataset_id",
    "dataset_version",
)
STEP_KEYS = ("source_step_id", "source_index", "json_path", "role", "raw")
LABEL_KEYS = (
    "label_id",
    "layer",
    "scope",
    "source_step_id",
    "event_ids",
    "located",
    "verdict",
    "categories",
    "source_categories",
    "reason",
    "mapping",
    "raw",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EVENT_ID_RE = re.compile(r"^evt-(\d{4,})$")
_SEGMENT_RE = re.compile(r"^([^.\[\]]+)((?:\[\d+\])*)$")
_INDEX_RE = re.compile(r"\[(\d+)\]")


def validate_trajectory(payload: Any) -> list[dict[str, str]]:
    """返回机器可读的 Schema 问题列表；空列表表示通过 ``trajectory/2``。"""
    issues: list[dict[str, str]] = []

    def add(path: str, message: str) -> None:
        issues.append({"path": path, "message": message})

    if not isinstance(payload, dict):
        add("", "轨迹必须是 JSON 对象")
        return issues

    _check_keys(payload, TOP_LEVEL_KEYS, "", add)
    if payload.get("schema_version") != TRAJECTORY_SCHEMA_VERSION:
        add(
            "schema_version",
            f"schema_version 必须是 {TRAJECTORY_SCHEMA_VERSION!r}",
        )

    sample = payload.get("sample")
    dataset_id = None
    dataset_version = None
    if not isinstance(sample, dict):
        add("sample", "sample 必须是对象")
    else:
        _check_keys(sample, SAMPLE_KEYS, "sample", add)
        _check_non_empty_str(sample, "sample_id", "sample", add)
        _check_non_empty_str(sample, "source_format", "sample", add)
        _check_sha256(sample, "input_sha256", "sample", add)
        adapter = sample.get("adapter")
        if not isinstance(adapter, dict):
            add("sample.adapter", "adapter 必须是对象")
        else:
            _check_keys(adapter, ("id", "version"), "sample.adapter", add)
            _check_non_empty_str(adapter, "id", "sample.adapter", add)
            _check_non_empty_str(adapter, "version", "sample.adapter", add)
        unmapped = sample.get("unmapped_top_level_fields")
        if not isinstance(unmapped, list) or not all(isinstance(item, str) for item in unmapped):
            add("sample.unmapped_top_level_fields", "必须是字符串数组")

    source = payload.get("source")
    if not isinstance(source, dict):
        add("source", "source 必须是对象")
    else:
        required = ("dataset_id", "dataset_version", "record")
        for key in required:
            if key not in source:
                add(f"source.{key}", "缺少必需字段")
        for key in source:
            if key not in SOURCE_KEYS + ("tool_definitions",):
                add(f"source.{key}", "未知 source 字段")
        dataset_id = source.get("dataset_id")
        dataset_version = source.get("dataset_version")
        if not isinstance(dataset_id, str) or not dataset_id.strip():
            add("source.dataset_id", "必须是非空字符串")
        if not isinstance(dataset_version, str) or not dataset_version.strip():
            add("source.dataset_version", "必须是非空字符串")
        record = source.get("record")
        if not isinstance(record, dict):
            add("source.record", "record 必须是对象")
        else:
            _check_keys(record, RECORD_KEYS, "source.record", add)
            index = record.get("index")
            if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                add("source.record.index", "必须是非负整数")
            if not isinstance(record.get("json_path"), str):
                add("source.record.json_path", "必须是字符串")
            _check_sha256(record, "sha256", "source.record", add)
            if "raw" not in record:
                add("source.record.raw", "缺少原始记录")

    steps = payload.get("source_steps")
    step_ids: list[Any] = []
    if not isinstance(steps, list):
        add("source_steps", "source_steps 必须是数组")
        steps = []
    else:
        for position, step in enumerate(steps):
            path = f"source_steps[{position}]"
            if not isinstance(step, dict):
                add(path, "来源步骤必须是对象")
                continue
            _check_keys(step, STEP_KEYS, path, add)
            step_id = step.get("source_step_id")
            if step_id is not None and not _is_step_id(step_id):
                add(f"{path}.source_step_id", "必须是整数、非空字符串或 null")
            if step_id is not None:
                if step_id in step_ids:
                    add(f"{path}.source_step_id", f"来源步骤身份重复：{step_id!r}")
                step_ids.append(step_id)
            index = step.get("source_index")
            if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                add(f"{path}.source_index", "必须是非负整数")
            if not isinstance(step.get("json_path"), str):
                add(f"{path}.json_path", "必须是字符串")
            if not isinstance(step.get("role"), str):
                add(f"{path}.role", "必须是字符串")
            if "raw" not in step:
                add(f"{path}.raw", "缺少原始步骤内容")

    events = payload.get("events")
    events_by_id: dict[str, dict[str, Any]] = {}
    if not isinstance(events, list) or not events:
        add("events", "events 必须是非空数组")
        events = []
    else:
        for position, event in enumerate(events):
            path = f"events[{position}]"
            if not isinstance(event, dict):
                add(path, "事件必须是对象")
                continue
            _check_keys(event, EVENT_KEYS, path, add)
            event_id = event.get("event_id")
            if not isinstance(event_id, str) or not _EVENT_ID_RE.match(event_id):
                add(f"{path}.event_id", "必须是 evt-NNNN 形式的稳定身份")
            elif event_id in events_by_id:
                add(f"{path}.event_id", f"事件身份重复：{event_id}")
            else:
                events_by_id[event_id] = event
            if event.get("sequence") != position + 1:
                add(f"{path}.sequence", "必须是 1 起的连续事件顺序")
            if event.get("type") not in EVENT_TYPES:
                add(f"{path}.type", f"未知事件类型：{event.get('type')!r}")
            if not isinstance(event.get("actor"), str) or not event.get("actor"):
                add(f"{path}.actor", "必须是非空字符串")
            step_id = event.get("source_step_id")
            if step_id is not None:
                if not _is_step_id(step_id):
                    add(f"{path}.source_step_id", "必须是整数、非空字符串或 null")
                elif step_id not in step_ids:
                    add(f"{path}.source_step_id", f"引用了不存在的来源步骤：{step_id!r}")
            if not isinstance(event.get("content"), dict):
                add(f"{path}.content", "content 必须是对象")
            _check_locator(event, path, dataset_id, dataset_version, add)
            if "raw" not in event:
                add(f"{path}.raw", "缺少原始片段")

    labels = payload.get("source_labels")
    if not isinstance(labels, list):
        add("source_labels", "source_labels 必须是数组")
        labels = []
    else:
        seen_labels: set[str] = set()
        for position, label in enumerate(labels):
            path = f"source_labels[{position}]"
            if not isinstance(label, dict):
                add(path, "来源标签必须是对象")
                continue
            _check_keys(label, LABEL_KEYS, path, add)
            label_id = label.get("label_id")
            if not isinstance(label_id, str) or not label_id:
                add(f"{path}.label_id", "必须是非空字符串")
            elif label_id in seen_labels:
                add(f"{path}.label_id", f"标签身份重复：{label_id}")
            else:
                seen_labels.add(label_id)
            if label.get("layer") != SOURCE_LABEL_LAYER:
                add(f"{path}.layer", f"来源标签 layer 必须是 {SOURCE_LABEL_LAYER!r}")
            _check_source_label(label, path, step_ids, events_by_id, add)

    reference = payload.get("reference")
    if not isinstance(reference, dict):
        add("reference", "reference 必须是对象")
    else:
        for key in ("status", "labels", "note"):
            if key not in reference:
                add(f"reference.{key}", "缺少必需字段")
        if reference.get("status") not in REFERENCE_LAYERS:
            add("reference.status", f"未知 reference 状态：{reference.get('status')!r}")
        if not isinstance(reference.get("labels"), list):
            add("reference.labels", "必须是数组")
        elif reference.get("status") == REFERENCE_STATUS_ABSENT and reference["labels"]:
            add("reference.labels", "status=absent 时不得携带参考标注")
        if not isinstance(reference.get("note"), str):
            add("reference.note", "必须是字符串")

    unsupported = payload.get("unsupported")
    if not isinstance(unsupported, dict):
        add("unsupported", "unsupported 必须是对象")
    else:
        expected = [event_id for event_id, event in events_by_id.items() if event.get("type") == "unsupported_content"]
        if not isinstance(unsupported.get("count"), int):
            add("unsupported.count", "必须是整数")
        elif unsupported["count"] != len(expected):
            add("unsupported.count", f"应为 {len(expected)}，实际 {unsupported.get('count')}")
        if not isinstance(unsupported.get("event_ids"), list):
            add("unsupported.event_ids", "必须是数组")
        elif set(unsupported["event_ids"]) != set(expected):
            add("unsupported.event_ids", "必须与 unsupported_content 事件集合一致")

    return issues


def _check_source_label(
    label: dict[str, Any],
    path: str,
    step_ids: list[Any],
    events_by_id: dict[str, dict[str, Any]],
    add: Any,
) -> None:
    scope = label.get("scope")
    if scope not in SOURCE_LABEL_SCOPES:
        add(f"{path}.scope", f"未知作用范围：{scope!r}")
        return
    located = label.get("located")
    event_ids = label.get("event_ids")
    if not isinstance(located, bool):
        add(f"{path}.located", "必须是布尔值")
    if not isinstance(event_ids, list) or not all(isinstance(item, str) for item in event_ids):
        add(f"{path}.event_ids", "必须是字符串数组")
        event_ids = []
    step_id = label.get("source_step_id")
    if label.get("verdict") is not None and label.get("verdict") not in VERDICTS:
        add(f"{path}.verdict", f"未知来源判定：{label.get('verdict')!r}")
    for key in ("categories", "source_categories"):
        value = label.get(key)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            add(f"{path}.{key}", "必须是字符串数组")
    if label.get("reason") is not None and not isinstance(label.get("reason"), str):
        add(f"{path}.reason", "必须是字符串或 null")
    if not isinstance(label.get("mapping"), dict):
        add(f"{path}.mapping", "必须是对象")
    if "raw" not in label:
        add(f"{path}.raw", "缺少原始标签")

    if scope == SOURCE_LABEL_SCOPE_STEP:
        if located is not True:
            add(f"{path}.located", "来源步骤级标签必须 located=true")
        if step_id not in step_ids:
            add(f"{path}.source_step_id", f"引用了不存在的来源步骤：{step_id!r}")
        if not event_ids:
            add(f"{path}.event_ids", "已定位的来源步骤标签必须解析到至少一个事件")
        for event_id in event_ids:
            event = events_by_id.get(event_id)
            if event is None:
                add(f"{path}.event_ids", f"引用了不存在的事件：{event_id}")
            elif event.get("source_step_id") != step_id:
                add(f"{path}.event_ids", f"事件 {event_id} 的 source_step_id 与标签不一致")
    elif scope == SOURCE_LABEL_SCOPE_TRAJECTORY:
        if located is not False:
            add(f"{path}.located", "轨迹级标签必须 located=false")
        if step_id is not None:
            add(f"{path}.source_step_id", "轨迹级标签不得携带来源步骤身份")
        if event_ids:
            add(f"{path}.event_ids", "未定位的轨迹级标签不得解析事件")


def _check_locator(
    event: dict[str, Any],
    path: str,
    dataset_id: str | None,
    dataset_version: str | None,
    add: Any,
) -> None:
    locator = event.get("source")
    if not isinstance(locator, dict):
        add(f"{path}.source", "source 定位必须是对象")
        return
    _check_keys(locator, LOCATOR_KEYS, f"{path}.source", add)
    index = locator.get("record_index")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        add(f"{path}.source.record_index", "必须是非负整数")
    if not isinstance(locator.get("json_path"), str) or not locator.get("json_path"):
        add(f"{path}.source.json_path", "必须是非空字符串")
    _check_sha256(locator, "record_sha256", f"{path}.source", add)
    if locator.get("dataset_id") != dataset_id:
        add(f"{path}.source.dataset_id", "必须与轨迹级 dataset_id 一致")
    if locator.get("dataset_version") != dataset_version:
        add(f"{path}.source.dataset_version", "必须与轨迹级 dataset_version 一致")


def locate_json_path(record: Any, path: str) -> tuple[bool, Any]:
    """按适配器使用的点号加下标路径回读原始记录。

    返回 ``(是否存在, 值)``；路径不存在时第二个元素为 ``None``，因此调用方
    不会把「值为 null」误判成「路径不存在」。
    """
    if not path:
        return True, record
    current = record
    for segment in path.split("."):
        match = _SEGMENT_RE.match(segment)
        if match is None:
            return False, None
        name, indices = match.group(1), match.group(2)
        if not isinstance(current, dict) or name not in current:
            return False, None
        current = current[name]
        for raw_index in _INDEX_RE.findall(indices):
            index = int(raw_index)
            if not isinstance(current, list) or index >= len(current):
                return False, None
            current = current[index]
    return True, current


def path_resolves(record: Any, path: str) -> bool:
    return locate_json_path(record, path)[0]


def _check_keys(payload: dict[str, Any], expected: tuple[str, ...], prefix: str, add: Any) -> None:
    for key in expected:
        if key not in payload:
            add(_join(prefix, key), "缺少必需字段")
    for key in payload:
        if key not in expected:
            add(_join(prefix, key), "未知字段")


def _check_non_empty_str(payload: dict[str, Any], key: str, prefix: str, add: Any) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        add(_join(prefix, key), "必须是非空字符串")


def _check_sha256(payload: dict[str, Any], key: str, prefix: str, add: Any) -> None:
    value = payload.get(key)
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        add(_join(prefix, key), "必须是 64 位小写十六进制 SHA-256")


def _join(prefix: str, key: str) -> str:
    return f"{prefix}.{key}" if prefix else key


def _is_step_id(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, str) and bool(value)


__all__ = [
    "TRAJECTORY_SCHEMA_VERSION",
    "locate_json_path",
    "path_resolves",
    "validate_trajectory",
]
