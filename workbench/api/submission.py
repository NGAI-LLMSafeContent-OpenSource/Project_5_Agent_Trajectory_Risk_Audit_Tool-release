# SPDX-License-Identifier: Apache-2.0
"""Serialize the archived public race projection; never run or infer an analysis.

The Provider-to-race mapping for B3/C2/C3 awaits #60. Until it supplies explicitly
available items, exports fail closed. Fixed public projections can test assembly
without establishing a private Provider contract or using reference answers.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from trajectory_audit.model import sha256_json
from trajectory_audit.race_items import race_aggregate, race_facts

TEMPLATE = Path(__file__).resolve().parents[2] / "acceptance/saima-risk-visualization-sample-v1/raw/submission_template.json"
FACT_ITEMS = ("A1", "A2", "A3", "B1", "C1", "C4")
ALGORITHM_ITEMS = {"B2": "judgment", "B3": "risks", "C2": "markers", "C3": "failures"}
PRIVATE_FIELDS = {"event_id", "tool_call_index", "status", "pending", "characters", "source_pointer", "start", "end"}
RAW_FIELDS = {"arguments", "content", "text", "tool_call_id", "value"}


class SubmissionUnavailable(ValueError):
    def __init__(self, issues: list[dict[str, str]]) -> None:
        super().__init__("当前归档不能导出完整 submission.jsonl")
        self.issues = issues


def _public(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: deepcopy(v) if k in RAW_FIELDS else _public(v) for k, v in value.items() if k not in PRIVATE_FIELDS}
    if isinstance(value, list):
        return [_public(v) for v in value]
    return value


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def _field(record: dict, path: str) -> Any:
    """Resolve only concrete source fields, never eval or a fuzzy text search."""
    _require(bool(re.fullmatch(r"[a-zA-Z_]\w*(?:(?:\[\d+\])|(?:\.[a-zA-Z_]\w*))*", path)), "无效原文字段")
    value: Any = record
    for key, index in re.findall(r"([a-zA-Z_]\w*)|\[(\d+)\]", path):
        value = value[key] if key else value[int(index)]
    return value


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _texts(value: Any) -> list[str]:
    # A tool-call event points at an object; its arguments are source text,
    # not the JSON-escaped representation of that text inside the object.
    texts = [_text(value)]
    if isinstance(value, dict):
        texts.extend(text for child in value.values() for text in _texts(child))
    elif isinstance(value, list):
        texts.extend(text for child in value for text in _texts(child))
    return texts


def _location(entry: dict, mapping: dict) -> list[dict]:
    events = mapping["events"]
    if entry.get("event_id"):
        events = [e for e in events if e["event_id"] == entry["event_id"]]
        _require(len(events) == 1, "引用了不存在或不唯一的 event_id")
    _require("step_id" in entry or "message_index" in entry, "引用缺少步骤或消息位置")
    if "step_id" in entry:
        _require(type(entry["step_id"]) is int, "无效 step_id")
        events = [e for e in events if entry["step_id"] in e["step_ids"]]
    if "message_index" in entry:
        _require(type(entry["message_index"]) is int, "无效 message_index")
        events = [e for e in events if entry["message_index"] == e["message_index"]]
    _require(bool(events), "事件、步骤与消息位置不一致")
    return events


def _evidence(entry: dict, mapping: dict) -> None:
    quote = entry.get("quote")
    _require(isinstance(quote, str) and bool(quote), "证据缺少非空 quote")
    record = mapping["raw_record"]
    if "message_index" not in entry and "step_id" not in entry and entry.get("field"):
        # Metadata has no synthetic step or message index.
        _require(entry["field"].startswith("metadata."), "无位置证据仅允许 metadata 字段")
        _require(quote == _text(_field(record, entry["field"])), "metadata 证据与原文不一致")
        return
    events = _location(entry, mapping)
    if entry.get("field"):
        field = entry["field"]
        _require(any(field == e["source_field"] or field.startswith(e["source_field"] + ".")
                     or field.startswith(e["source_field"] + "[") for e in events), "证据字段不属于指定事件")
        texts = _texts(_field(record, field))
    else:
        texts = [text for e in events for text in _texts(_field(record, e["source_field"]))]
    _require(any(quote in text for text in texts), "quote 不属于指定事件的原文字段")


def _algorithm(race: dict, mapping: dict, verdict: str) -> None:
    judgment = {"safe": "无风险", "unsafe": "有风险"}.get(verdict)
    _require(judgment is not None, "verdict 为 undetermined 或未提供")
    _require(race["B2"]["judgment"] == judgment, "B2 与归档最终判定不一致")
    risks = race["B3"]["risks"]
    _require(isinstance(risks, list) and len(risks) == (1 if verdict == "unsafe" else 0),
             "B3 必须明确选定一个首次风险行为；无风险必须为 []")
    calls = {c["step_id"] for c in race["C1"]["calls"]}
    for key, field in (("B3", "risks"), ("C2", "markers"), ("C3", "failures")):
        rows = race[key][field]
        _require(isinstance(rows, list), f"{key}.{field} 必须是列表")
        for row in rows:
            events = _location(row, mapping)
            if key == "B3":
                _require(type(row.get("step_id")) is int, "首次行为缺少 step_id")
                _require(bool(row.get("event_id")), "首次行为必须由算法明确选定事件")
                _require(all(e["type"] in {"message", "thought", "tool_call"} for e in events), "首次行为不是 Agent 行为")
                _evidence({**row, "quote": row.get("risk_quote")}, mapping)
            else:
                _require(row.get("step_id") in calls, f"{key} 未指向工具调用")
            if key == "C2":
                previous = row.get("previous_step_id")
                _require(type(previous) is int and previous in calls and previous < row["step_id"], "冗余调用缺少有效的前一次调用")
            if key == "C3":
                _require(isinstance(row.get("error_type"), str) and bool(row["error_type"].strip()), "执行失败缺少错误类型")
            evidence = row.get("evidence")
            _require(isinstance(evidence, list) and bool(evidence), f"{key} 缺少支持证据")
            for item in evidence:
                _evidence(item, mapping)
            if key == "C3":
                _require(any(e["type"] == "tool_result" and row["step_id"] in e["step_ids"]
                             and item["quote"] in _text(_field(mapping["raw_record"], e["source_field"]))
                             for item in evidence if "step_id" in item or "message_index" in item
                             for e in _location(item, mapping)), "执行失败证据未定位对应返回")
    # C5 is a projection of C2-C4, not another detector. C4 issues are generated
    # by race_facts; C2/C3 must preserve the same position and evidence.
    expected = deepcopy(race_facts(mapping)["C5"]["issues"])
    for key, field in (("C2", "markers"), ("C3", "failures")):
        expected.extend({"source_subitem": key, **row} for row in race[key][field])
    actual = race["C5"]
    _require(not actual.get("pending"), "C5 仍有未完成分析")
    # Additional UI-only descriptions do not change the issue identity.
    def identity(row: dict) -> dict:
        return {k: v for k, v in _public(row).items() if k not in {"kind", "issue"}}
    _require([identity(row) for row in actual["issues"]] == [identity(row) for row in expected], "C5 与 C2—C4 的位置或证据不一致")


def build_submission(session: dict, mappings: dict[str, dict]) -> bytes:
    """Return all rows or structured diagnostics; never a partial submission."""
    templates = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    labels = {key: item["指标"] for row in templates for key, item in row["results"].items()}
    rows, issues, seen, races = [], [], set(), {}
    samples = session.get("samples") or []
    if not samples:
        raise SubmissionUnavailable([{"sample_id": "", "item": "session", "reason": "归档没有轨迹"}])
    for sample in samples:
        sample_id = sample.get("sample_id")
        try:
            _require(isinstance(sample_id, str) and bool(sample_id.strip()) and sample_id != "aggregate", "无效或保留的 trajectory_id")
            _require(sample_id not in seen, "trajectory_id 重复")
            seen.add(sample_id)
            _require(sample.get("ok") is True, (sample.get("error") or {}).get("message") or "算法失败或未完成")
            bundle = sample["bundle"]
            _require(bundle["sample"]["sample_id"] == sample_id, "样本与 Review Bundle 身份不一致")
            mapping = mappings[sample_id]
            raw = mapping["raw_record"]
            _require(raw.get("trajectory_id") == sample_id, "归档原文身份不一致")
            _require(bundle["sample"]["record"]["sha256"] == sha256_json(raw), "归档原文摘要不一致")
            _require(bundle["source_mapping"] == {k: mapping[k] for k in ("steps", "events", "line_number")}, "归档事件与步骤映射不一致")
            race = bundle["race"]
            facts = race_facts(mapping)
            for key in FACT_ITEMS:
                _require(race[key] == facts[key], f"{key} 与同源归档不一致或缺少必需字段")
            missing = False
            for key, field in ALGORITHM_ITEMS.items():
                item = race.get(key) or {}
                if item.get("status") != "available" or field not in item:
                    issues.append({"sample_id": sample_id, "item": key, "reason": item.get("reason") or "必需算法结果未完成或缺少字段"})
                    missing = True
            if missing:
                continue
            _algorithm(race, mapping, bundle["verdict"]["value"])
            results = {key: {"指标": labels[key], **_public(race[key])} for key in templates[0]["results"]}
            races[sample_id] = race
            rows.append({"trajectory_id": sample_id, "results": results})
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as exc:
            issues.append({"sample_id": str(sample_id or ""), "item": "archive", "reason": str(exc)})
    if issues:
        raise SubmissionUnavailable(issues)
    aggregate = race_aggregate(races)
    if session.get("aggregate") != aggregate:
        raise SubmissionUnavailable([{"sample_id": "aggregate", "item": "A4", "reason": "汇总与当前批次不一致"}])
    rows.append({"trajectory_id": "aggregate", "results": {"A4": {"指标": labels["A4"], **aggregate}}})
    return ("\n".join(json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) for row in rows) + "\n").encode("utf-8")
