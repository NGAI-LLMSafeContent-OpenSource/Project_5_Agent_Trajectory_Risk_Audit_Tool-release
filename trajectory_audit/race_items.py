# SPDX-License-Identifier: Apache-2.0
"""Deterministic competition items computed from one source record and its race steps.

Inputs are the ``source_mapping`` record from :mod:`trajectory_audit.pujiang`; the
organizer's reference answers are never read. Algorithm items are only mapped from
the Provider result and never inferred here.
"""

from __future__ import annotations

import json
import re
from typing import Any

NOT_PROVIDED = "未提供"
JUDGMENT = {"safe": "无风险", "unsafe": "有风险"}
CONTRACT_PENDING = "算法尚未输出该项结果，此处不据现有检测推断"
NO_RETURN_ISSUE = "未找到可确定的对应返回"
# Only markers written by the recording system count; limiting output (head, sed -n) or prose
# mentioning truncation is not a record truncation.
TRUNCATION_MARKERS = (
    re.compile(r"(?:\.\.\.)?\[(?:output )?truncated(?: \d+ \w+)?\]", re.IGNORECASE),
    re.compile(r"\[\.\.\. ?\d+ (?:more )?\w+ (?:truncated|omitted)\]", re.IGNORECASE),
    re.compile(r"\[[^\[\]\n]{0,160}\bwill be omitted from this request\.?\]"),
)
UNMATCHED_ISSUE = {
    "ambiguous_reply_call_id": "同一回复内调用 ID 重复，无法确定对应调用",
    "no_call_id": "缺少 tool_call_id",
    "no_call_in_reply": "所属回复内没有该 ID 的调用",
}


def race_facts(mapping: dict[str, Any]) -> dict[str, Any]:
    record = mapping["raw_record"]
    messages = record["messages"]
    segments, absent = _thinking(mapping, messages)
    counts: dict[str, int] = {}
    for step in mapping["steps"]:
        if step["tool_call_index"] is not None:
            name = _function(_call(messages, step)).get("name")
            key = name if isinstance(name, str) else ""
            counts[key] = counts.get(key, 0) + 1
    return {
        "A1": {"input_tokens": _token(record, "input_tokens"), "output_tokens": _token(record, "output_tokens")},
        "A2": {"known_characters": sum(segment["characters"] for segment in segments),
               "absent_body_step_ids": absent},
        "A3": {"tool_calls": sum(counts.values()), "tool_kinds": len(counts), "tool_counts": counts},
        "B1": {"segments": segments, "absent_body_step_ids": absent},
        "C1": (c1 := _calls(mapping, messages)),
        "C4": (c4 := {
            "missing_fields": _missing_fields(mapping, messages),
            "unmatched": [
                *({"message_index": call["message_index"], "step_id": call["step_id"],
                   "event_id": call["event_id"], **_present(call, "tool_call_id"), "issue": NO_RETURN_ISSUE}
                  for call in c1["calls"] if not call["returns"]),
                *({key: item[key] for key in ("message_index", "event_id", "tool_call_id", "issue") if key in item}
                  for item in c1["unmatched"]),
            ],
            "truncations": _truncations(mapping, record),
            "completion": _completion(record),
        }),
        "C5": {"issues": _c4_issues(c4, c1, messages)},
    }


def with_algorithm_items(race: dict[str, Any], *, verdict: str, completed: bool, failure: str | None = None) -> dict[str, Any]:
    """Map only the Provider's final verdict; unconfirmed items stay unavailable, failures never become findings."""
    if not completed or verdict not in JUDGMENT:
        reason = failure or "算法未给出有风险或无风险的判定"
        items = {item: {"status": "failure", "reason": reason} for item in ("B2", "B3", "C2", "C3")}
    else:
        items = {"B2": {"status": "available", "judgment": JUDGMENT[verdict]},
                 **{item: {"status": "unavailable", "reason": CONTRACT_PENDING} for item in ("B3", "C2", "C3")}}
    return {**race, **items, "C5": {
        "issues": race["C5"]["issues"],
        "pending": [{"source_subitem": item, **items[item]} for item in ("C2", "C3")],
    }}


def _source_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _c4_issues(c4: dict[str, Any], c1: dict[str, Any], messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each C4 problem with the exact source text that shows it."""
    calls = {call["event_id"]: call for call in c1["calls"]}

    def container(entry: dict[str, Any]) -> Any:
        message = messages[entry["message_index"]]
        match = re.match(r"tool_calls\[(\d+)\]", entry.get("field", ""))
        return message["tool_calls"][int(match[1])] if match else message

    def quote(kind: str, entry: dict[str, Any]) -> str:
        if kind == "truncations":
            return entry["quote"]
        if kind == "missing_fields":
            return _source_text(container(entry))
        if entry["issue"] == NO_RETURN_ISSUE:
            call = calls[entry["event_id"]]
            return _source_text(messages[call["message_index"]]["tool_calls"][call["tool_call_index"]])
        return _source_text(messages[entry["message_index"]].get("content"))

    issues = []
    for kind in ("missing_fields", "unmatched", "truncations"):
        for entry in c4[kind]:
            if "message_index" not in entry:
                issues.append({"source_subitem": "C4", "kind": kind, "field": entry["field"], "event_id": None,
                               "evidence": [{"field": entry["field"], "quote": _source_text(entry["value"])}]})
                continue
            location = {key: entry[key] for key in ("step_id", "message_index") if key in entry}
            issues.append({"source_subitem": "C4", "kind": kind, **location, "event_id": entry["event_id"],
                           **({"field": entry["field"]} if "field" in entry else {}),
                           **({"issue": entry["issue"]} if "issue" in entry else {}),
                           "evidence": [{**location, "quote": quote(kind, entry)}]})
    return issues


def race_aggregate(races: dict[str, dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for race in races.values():
        for name, count in race["A3"]["tool_counts"].items():
            counts[name] = counts.get(name, 0) + count
    return {"trajectory_ids": list(races), "tool_calls": sum(counts.values()), "tool_counts": counts}


def _message_location(mapping: dict[str, Any], message_index: int) -> dict[str, Any]:
    rows = [row for row in mapping["events"] if row["message_index"] == message_index]
    step_ids = sorted({step_id for row in rows for step_id in row["step_ids"]})
    return {**({"step_id": step_ids[0]} if step_ids else {}), "message_index": message_index,
            "event_id": rows[0]["event_id"] if rows else None}


def _value_issue(container: dict[str, Any], key: str, *, empty_is_issue: bool) -> str | None:
    if key not in container:
        return "缺失"
    if container[key] is None:
        return "为 null"
    if empty_is_issue and container[key] == "":
        return "为空字符串"
    return None


def _missing_fields(mapping: dict[str, Any], messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Required OpenAI Message fields; legal empty values and optional record fields are not issues."""
    issues: list[dict[str, Any]] = []
    call_steps = {(step["message_index"], step["tool_call_index"]): step for step in mapping["steps"]}
    call_events = _call_events(mapping)

    def add(index: int, field: str, issue: str | None, call_index: int | None = None) -> None:
        if not issue:
            return
        location = _message_location(mapping, index)
        step = call_steps.get((index, call_index))
        if step is not None:
            location = {"step_id": step["step_id"], "message_index": index,
                        "event_id": _call_event_id(step, call_events)}
        issues.append({**location, "field": field, "issue": issue})

    for index, message in enumerate(messages):
        role = message.get("role")
        # An assistant reply may omit content or set it to null when it only calls tools.
        if role != "assistant":
            add(index, "content", _value_issue(message, "content", empty_is_issue=False))
        if role == "tool":
            add(index, "tool_call_id", _value_issue(message, "tool_call_id", empty_is_issue=True))
        for call_index, call in enumerate(message.get("tool_calls") or []):
            prefix = f"tool_calls[{call_index}]"
            call = call if isinstance(call, dict) else {}
            add(index, f"{prefix}.id", _value_issue(call, "id", empty_is_issue=True), call_index)
            if not isinstance(call.get("function"), dict):
                add(index, f"{prefix}.function", _value_issue(call, "function", empty_is_issue=False) or "不是对象",
                    call_index)
                continue
            function = call["function"]
            add(index, f"{prefix}.function.name", _value_issue(function, "name", empty_is_issue=True), call_index)
            add(index, f"{prefix}.function.arguments",
                _value_issue(function, "arguments", empty_is_issue=False), call_index)
    return issues


def _call_events(mapping: dict[str, Any]) -> set[str]:
    return {row["event_id"] for row in mapping["events"] if row["type"] == "tool_call"}


def _call_event_id(step: dict[str, Any], call_events: set[str]) -> str:
    return next(event_id for event_id in step["event_ids"] if event_id in call_events)


def _texts(message: dict[str, Any]) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    if isinstance(message.get("reasoning_content"), str):
        fields.append(("reasoning_content", message["reasoning_content"]))
    content = message.get("content")
    if isinstance(content, str):
        fields.append(("content", content))
    elif isinstance(content, list):
        fields.extend((f"content[{i}].text", part["text"]) for i, part in enumerate(content)
                      if isinstance(part, dict) and isinstance(part.get("text"), str))
    return fields


def _truncations(mapping: dict[str, Any], record: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for index, message in enumerate(record["messages"]):
        for field, text in _texts(message):
            for match in sorted((m for pattern in TRUNCATION_MARKERS for m in pattern.finditer(text)),
                                key=lambda m: m.start()):
                found.append({**_message_location(mapping, index), "field": field, "quote": match.group(0)})
    metadata = record.get("metadata")
    flags = metadata.get("record") if isinstance(metadata, dict) else None
    if isinstance(flags, dict) and flags.get("is_truncated") is True:
        found.append({"field": "metadata.record.is_truncated", "value": True})
    return found


def _completion(record: dict[str, Any]) -> Any:
    metadata = record.get("metadata")
    flags = metadata.get("record") if isinstance(metadata, dict) else None
    if isinstance(flags, dict) and "is_session_completed" in flags:
        return flags["is_session_completed"]
    return NOT_PROVIDED


def _calls(mapping: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
    results = [row for row in mapping["events"] if row["type"] == "tool_result"]
    call_events = _call_events(mapping)
    calls = []
    for step in mapping["steps"]:
        if step["tool_call_index"] is None:
            continue
        call = _call(messages, step)
        function = _function(call)
        event_id = _call_event_id(step, call_events)
        calls.append({
            "step_id": step["step_id"], "message_index": step["message_index"],
            "tool_call_index": step["tool_call_index"], "event_id": event_id,
            **_present(call, "id", as_key="tool_call_id"),
            "tool_name": function.get("name"), "arguments": function.get("arguments"),
            "returns": [{"message_index": row["message_index"], "event_id": row["event_id"],
                         "content": messages[row["message_index"]].get("content")}
                        for row in results if row["call_event_id"] == event_id],
        })
    unmatched = [{"message_index": row["message_index"], "event_id": row["event_id"],
                  **_present(messages[row["message_index"]], "tool_call_id"),
                  "issue": UNMATCHED_ISSUE[row["unresolved_reason"]],
                  "content": messages[row["message_index"]].get("content")}
                 for row in results if row["link_status"] != "linked"]
    return {"calls": calls, "unmatched": unmatched}


def _present(source: dict[str, Any], key: str, *, as_key: str | None = None) -> dict[str, Any]:
    """Copy a field only when the source has it, so a missing value stays distinct from null."""
    return {as_key or key: source[key]} if key in source else {}


def _token(record: dict[str, Any], key: str) -> int | str:
    usage = record.get("usage")
    value = usage.get(key) if isinstance(usage, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else NOT_PROVIDED


def _call(messages: list[dict[str, Any]], step: dict[str, Any]) -> dict[str, Any]:
    call = messages[step["message_index"]]["tool_calls"][step["tool_call_index"]]
    return call if isinstance(call, dict) else {}


def _function(call: dict[str, Any]) -> dict[str, Any]:
    function = call.get("function")
    return function if isinstance(function, dict) else {}


def _thinking(mapping: dict[str, Any], messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[int]]:
    """One segment per reply: a shared body is counted once, identical bodies in other replies are not merged."""
    by_message: dict[int, list[int]] = {}
    for step in mapping["steps"]:
        by_message.setdefault(step["message_index"], []).append(step["step_id"])
    thought_events = {row["message_index"]: row["event_id"] for row in mapping["events"] if row["type"] == "thought"}
    segments: list[dict[str, Any]] = []
    absent: list[int] = []
    for message_index, step_ids in by_message.items():
        text = messages[message_index].get("reasoning_content")
        if isinstance(text, str) and text:
            segments.append({"step_id": step_ids[0], "step_ids": step_ids, "message_index": message_index,
                             "event_id": thought_events.get(message_index), "text": text, "characters": len(text)})
        else:
            absent.extend(step_ids)
    return segments, absent


__all__ = ["NOT_PROVIDED", "race_aggregate", "race_facts", "with_algorithm_items"]
