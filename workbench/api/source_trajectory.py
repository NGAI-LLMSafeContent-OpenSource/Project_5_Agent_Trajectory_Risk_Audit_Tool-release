# SPDX-License-Identifier: Apache-2.0
"""把归档轨迹投影成当前样本的原始轨迹。

投影只保留来源步骤的字段、值和顺序，并把规范事件已有的来源路径
归到对应步骤。样本级来源标签和其它外层元数据不进入这份投影。
"""

from __future__ import annotations

from typing import Any

UNAVAILABLE_REASON = "原始轨迹不可用"
UNREADABLE_REASON = "原始轨迹读取失败"


def project_source_trajectory(trajectory: dict[str, Any]) -> dict[str, Any] | None:
    """返回可展示的原始轨迹；归档里没有完整步骤原文时返回 None。"""

    steps = trajectory.get("source_steps")
    if not isinstance(steps, list) or not steps:
        return None
    projected_steps: list[dict[str, Any]] = []
    step_paths: list[str] = []
    for step in steps:
        if not isinstance(step, dict) or "raw" not in step or step.get("raw") is None:
            return None
        json_path = step.get("json_path")
        path = json_path if isinstance(json_path, str) else ""
        step_paths.append(path)
        projected_steps.append({"json_path": path, "raw": step["raw"], "event_ids": []})

    events = trajectory.get("events")
    if not isinstance(events, list):
        events = []
    for event in events:
        event_id, event_path = _event_source_path(event)
        if event_id is None or event_path is None:
            continue
        step_index = _matching_step(step_paths, event_path)
        if step_index is None:
            continue
        projected_steps[step_index]["event_ids"].append(event_id)

    sample = trajectory.get("sample") if isinstance(trajectory.get("sample"), dict) else {}
    source_format = sample.get("source_format")
    return {
        "source_format": source_format if isinstance(source_format, str) else None,
        "steps": projected_steps,
    }


def unavailable_source_trajectory(
    *,
    sample_id: str,
    source_format: str | None,
    reason: str = UNAVAILABLE_REASON,
) -> dict[str, Any]:
    return {
        "ok": True,
        "sample_id": sample_id,
        "source_format": source_format,
        "availability": {"status": "unavailable", "reason": reason},
        "steps": [],
    }


def _event_source_path(event: Any) -> tuple[str | None, str | None]:
    if not isinstance(event, dict):
        return None, None
    event_id = event.get("event_id")
    source = event.get("source")
    json_path = source.get("json_path") if isinstance(source, dict) else None
    if not isinstance(event_id, str) or not event_id:
        return None, None
    if not isinstance(json_path, str) or not json_path:
        return event_id, None
    return event_id, json_path


def _matching_step(step_paths: list[str], event_path: str) -> int | None:
    matches = [
        index
        for index, step_path in enumerate(step_paths)
        if step_path and _path_covers(step_path, event_path)
    ]
    if not matches:
        return None
    best_length = max(len(step_paths[index]) for index in matches)
    best = [index for index in matches if len(step_paths[index]) == best_length]
    if len(best) != 1:
        return None
    return best[0]


def _path_covers(step_path: str, event_path: str) -> bool:
    return (
        event_path == step_path
        or event_path.startswith(f"{step_path}.")
        or event_path.startswith(f"{step_path}[")
    )
