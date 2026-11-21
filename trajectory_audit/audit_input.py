# SPDX-License-Identifier: Apache-2.0
"""课题四审计输入：白名单投影、Exchange Envelope 与 Schema 校验。

`audit-trajectory/1` 是一条 Canonical Audit Item 的轨迹部分：只包含规范事件、
允许的 tool definitions 与 coverage。`topic4-audit-input/1` 是课题四与课题五
之间的 Exchange Envelope，把一条或多条 Canonical Audit Item 与稳定样本身份、
``audit_input_sha256`` 和显式 policy 一起承载。

投影是**白名单**而不是删除列表：来源标签、项目参考标注、来源定位、原始记录、
数据集身份、来源 reason 和既有 Detected Finding 都没有进入这里的代码路径。
新增来源字段不会自动出现在输出中。

``audit_input_sha256`` 只覆盖 provider 可见的 trajectory 与 policy；修改 Source
Label、Project Reference Annotation 或其他禁止字段不会改变摘要。
"""

from __future__ import annotations

import copy
import re
from typing import Any, Callable, Sequence

from .model import EVENT_TYPES, NormalizedTrajectory, sha256_json

#: 校验问题的统一出口：``(path, message)``，path 为空串表示顶层。
IssueSink = Callable[[str, str], None]

AUDIT_TRAJECTORY_SCHEMA = "audit-trajectory/1"
AUDIT_INPUT_SCHEMA = "topic4-audit-input/1"
DEFAULT_BATCH_ID_PREFIX = "audit-input"

#: 每种事件类型允许进入 provider 输入的 content 字段。未列出的字段一律不投影，
#: 因此事件级 ``source`` / ``raw`` 以及任何新增来源字段都没有进入路径。
EVENT_CONTENT_KEYS: dict[str, tuple[str, ...]] = {
    "message": ("role", "text", "parts"),
    "thought": ("text",),
    "tool_call": (
        "call_id",
        "tool",
        "arguments_raw",
        "arguments",
        "arguments_status",
        "arguments_error",
    ),
    "tool_result": (
        "call_id",
        "call_event_id",
        "link_status",
        "link_basis",
        "declared_status",
        "declared_status_raw",
        "output",
        "output_format",
    ),
    "state_change": ("state", "basis", "declaration"),
    "unsupported_content": ("part_type", "reason", "message_role"),
}

#: 每种事件类型 content 中必须出现的字段，用于校验必填字段关系。
EVENT_CONTENT_REQUIRED: dict[str, tuple[str, ...]] = {
    "message": ("role", "text", "parts"),
    "thought": ("text",),
    "tool_call": ("tool", "arguments_status"),
    "tool_result": ("link_status", "declared_status"),
    "state_change": ("state",),
    "unsupported_content": ("reason",),
}

#: 工具定义允许进入 provider 输入的字段。来源侧的 `_source` 等标注不是工具
#: 语义的一部分，不进入输入；未知新增字段同样被丢弃。
TOOL_DEFINITION_KEYS = ("type", "function", "name", "description", "parameters", "strict")
TOOL_FUNCTION_KEYS = ("name", "description", "parameters", "strict")

#: policy 文档允许进入 provider 输入的字段。``source``（本地路径与文件哈希）
#: 属于课题五的来源血缘，不进入课题四的授权语义。这里刻意与
#: ``Policy.to_dict()`` 保持独立：新增字段必须显式改白名单，不能随模型自动泄漏。
POLICY_KEYS = (
    "schema_version",
    "policy_id",
    "policy_version",
    "scope",
    "description",
    "sample_ids",
    "tools",
    "task_terms",
    "out_of_scope_terms",
    "user_request",
    "confirmation_events",
)
POLICY_SCHEMA_VERSION = "audit-policy/1"
POLICY_SCOPE = "trajectory"
POLICY_TOOL_KEYS = (
    "tool_name",
    "allowed",
    "allowed_arguments",
    "constraints",
    "requires_confirmation",
    "notes",
)
POLICY_CONSTRAINT_KEYS = ("argument", "comparator", "value", "notes")

INPUT_KEYS = ("schema_version", "batch_id", "items")
ITEM_KEYS = ("sample_id", "audit_input_sha256", "trajectory", "policy")
TRAJECTORY_KEYS = ("schema_version", "events", "tool_definitions", "coverage")
EVENT_KEYS = ("event_id", "sequence", "type", "actor", "source_step_id", "content")
COVERAGE_KEYS = (
    "event_count",
    "event_types",
    "unsupported_event_ids",
    "source_step_ids",
    "tool_definition_count",
)

_EVENT_ID_RE = re.compile(r"^evt-(\d{4,})$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# 白名单投影
# ---------------------------------------------------------------------------


def project_event(event: Any) -> dict[str, Any]:
    """把一条统一事件投影为 ``audit-trajectory/1`` 事件。"""
    allowed = EVENT_CONTENT_KEYS.get(event.type)
    if allowed is None:
        # 未知类型没有白名单，content 投影为空对象；类型本身交给 Schema 校验。
        content: dict[str, Any] = {}
    else:
        content = {
            key: copy.deepcopy(event.content[key])
            for key in allowed
            if key in event.content
        }
    if event.type == "message":
        content["parts"] = _project_parts(content.get("parts"))
    return {
        "event_id": event.event_id,
        "sequence": event.sequence,
        "type": event.type,
        "actor": event.actor,
        "source_step_id": event.source_step_id,
        "content": content,
    }


def _project_parts(parts: Any) -> list[dict[str, str]]:
    """只保留文本片段；未知片段类型不进入 message content。"""
    if not isinstance(parts, list):
        return []
    projected: list[dict[str, str]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type")
        text = part.get("text")
        if isinstance(part_type, str) and isinstance(text, str):
            projected.append({"type": part_type, "text": text})
    return projected


def project_tool_definitions(value: Any) -> list[dict[str, Any]]:
    """把来源工具定义投影为数组；缺失、空串或 null 一律为空数组。

    非对象的条目没有可投影的工具语义字段，直接丢弃而不是原样转写，避免一段
    自由文本借 ``tool_definitions`` 绕过白名单。
    """
    if value is None:
        return []
    if isinstance(value, str) and not value.strip():
        return []
    items = value if isinstance(value, list) else [value]
    return [
        _project_tool_definition(item)
        for item in items
        if isinstance(item, dict)
    ]


def _project_tool_definition(item: dict[str, Any]) -> dict[str, Any]:
    projected = {
        key: copy.deepcopy(item[key])
        for key in TOOL_DEFINITION_KEYS
        if key in item
    }
    function = item.get("function")
    if isinstance(function, dict):
        projected["function"] = {
            key: copy.deepcopy(function[key])
            for key in TOOL_FUNCTION_KEYS
            if key in function
        }
    else:
        # ``function`` 只在对象形态下才有工具语义；自由文本不原样转写。
        projected.pop("function", None)
    return projected


def project_policy(policy: Any) -> dict[str, Any] | None:
    """把 policy 投影为 provider 可见字段；``None`` 保持显式 null。"""
    if policy is None:
        return None
    payload = policy.to_dict() if hasattr(policy, "to_dict") else policy
    if not isinstance(payload, dict):
        return None
    projected = {
        key: copy.deepcopy(payload[key])
        for key in POLICY_KEYS
        if key in payload
    }
    if isinstance(projected.get("tools"), list):
        projected["tools"] = [
            _project_policy_tool(rule)
            for rule in projected["tools"]
            if isinstance(rule, dict)
        ]
    else:
        # 非数组的 ``tools`` 没有可投影语义，不作为自由文本转写。
        projected.pop("tools", None)
    return projected


def _project_policy_tool(rule: dict[str, Any]) -> dict[str, Any]:
    projected = {
        key: copy.deepcopy(rule[key])
        for key in POLICY_TOOL_KEYS
        if key in rule
    }
    if isinstance(projected.get("constraints"), list):
        projected["constraints"] = [
            {
                key: copy.deepcopy(constraint[key])
                for key in POLICY_CONSTRAINT_KEYS
                if key in constraint
            }
            for constraint in projected["constraints"]
            if isinstance(constraint, dict)
        ]
    return projected


def project_trajectory(trajectory: NormalizedTrajectory) -> dict[str, Any]:
    """把一条统一轨迹投影为 ``audit-trajectory/1``。"""
    events = [project_event(event) for event in trajectory.events]
    definitions = project_tool_definitions(trajectory.tool_definitions)
    return {
        "schema_version": AUDIT_TRAJECTORY_SCHEMA,
        "events": events,
        "tool_definitions": definitions,
        "coverage": _coverage(events, definitions),
    }


def _coverage(
    events: list[dict[str, Any]], tool_definitions: list[Any]
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    source_step_ids: list[Any] = []
    for event in events:
        event_type = event["type"]
        counts[event_type] = counts.get(event_type, 0) + 1
        step_id = event["source_step_id"]
        if step_id is not None and step_id not in source_step_ids:
            source_step_ids.append(step_id)
    return {
        "event_count": len(events),
        "event_types": dict(sorted(counts.items())),
        "unsupported_event_ids": [
            event["event_id"]
            for event in events
            if event["type"] == "unsupported_content"
        ],
        "source_step_ids": source_step_ids,
        "tool_definition_count": len(tool_definitions),
    }


def audit_input_sha256(
    trajectory: dict[str, Any], policy: dict[str, Any] | None
) -> str:
    """只对 provider 可见的 trajectory 与 policy 取摘要。"""
    return sha256_json({"trajectory": trajectory, "policy": policy})


def canonical_item(
    trajectory: NormalizedTrajectory,
    policy: Any = None,
) -> dict[str, Any]:
    """构造一条 Canonical Audit Item（``topic4-audit-input/1`` 的 item）。"""
    projected_trajectory = project_trajectory(trajectory)
    projected_policy = project_policy(policy)
    return {
        "sample_id": trajectory.sample_id,
        "audit_input_sha256": audit_input_sha256(
            projected_trajectory, projected_policy
        ),
        "trajectory": projected_trajectory,
        "policy": projected_policy,
    }


def batch_identity(items: Sequence[dict[str, Any]]) -> str:
    """由样本身份与摘要派生稳定 ``batch_id``。"""
    material = {
        "schema_version": AUDIT_INPUT_SCHEMA,
        "items": [
            {
                "sample_id": item["sample_id"],
                "audit_input_sha256": item["audit_input_sha256"],
            }
            for item in items
        ],
    }
    return f"{DEFAULT_BATCH_ID_PREFIX}-{sha256_json(material)[:16]}"


def build_batch(items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """把一组 Canonical Audit Item 包装成 ``topic4-audit-input/1`` 批次。"""
    materialized = list(items)
    return {
        "schema_version": AUDIT_INPUT_SCHEMA,
        "batch_id": batch_identity(materialized),
        "items": materialized,
    }


# ---------------------------------------------------------------------------
# Schema 校验
# ---------------------------------------------------------------------------


def _issue_sink(issues: list[dict[str, str]], prefix: str = "") -> IssueSink:
    def add(relative: str, message: str) -> None:
        issues.append({"path": _prefix(prefix, relative), "message": message})

    return add


def validate_audit_trajectory(payload: Any) -> list[dict[str, str]]:
    """返回机器可读问题列表；空列表表示通过 ``audit-trajectory/1``。"""
    issues: list[dict[str, str]] = []
    add = _issue_sink(issues)

    if not isinstance(payload, dict):
        add("", "审计轨迹必须是 JSON 对象")
        return issues

    _check_keys(payload, TRAJECTORY_KEYS, "", add)
    if payload.get("schema_version") != AUDIT_TRAJECTORY_SCHEMA:
        add("schema_version", f"schema_version 必须是 {AUDIT_TRAJECTORY_SCHEMA!r}")

    events = payload.get("events")
    events_by_id: dict[str, dict[str, Any]] = {}
    computed_counts: dict[str, int] = {}
    computed_steps: list[Any] = []
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
                add(f"{path}.sequence", "必须是 1 起的连续且严格递增事件顺序")
            event_type = event.get("type")
            if event_type not in EVENT_TYPES:
                add(f"{path}.type", f"未知事件类型：{event_type!r}")
            else:
                computed_counts[event_type] = computed_counts.get(event_type, 0) + 1
            if not isinstance(event.get("actor"), str) or not event.get("actor"):
                add(f"{path}.actor", "必须是非空字符串")
            step_id = event.get("source_step_id")
            if step_id is not None and not _is_step_id(step_id):
                add(f"{path}.source_step_id", "必须是整数、非空字符串或 null")
            elif step_id is not None and step_id not in computed_steps:
                computed_steps.append(step_id)
            _check_event_content(event, path, event_type, add)

    definitions = payload.get("tool_definitions")
    if not isinstance(definitions, list):
        add("tool_definitions", "tool_definitions 必须是数组；缺失时规范化为空数组")
    else:
        _check_tool_definitions(definitions, add)

    coverage = payload.get("coverage")
    if not isinstance(coverage, dict):
        add("coverage", "coverage 必须是对象")
        coverage = {}
    else:
        _check_keys(coverage, COVERAGE_KEYS, "coverage", add)
        unsupported = [
            event_id
            for event_id, event in events_by_id.items()
            if event.get("type") == "unsupported_content"
        ]
        count = coverage.get("event_count")
        if not isinstance(count, int) or isinstance(count, bool):
            add("coverage.event_count", "必须是整数")
        elif count != len(events):
            add(
                "coverage.event_count",
                f"应为 {len(events)}，实际 {count!r}",
            )
        event_types = coverage.get("event_types")
        if not isinstance(event_types, dict):
            add("coverage.event_types", "必须是对象")
        else:
            for key, value in event_types.items():
                if key not in EVENT_TYPES:
                    add("coverage.event_types", f"未知事件类型：{key!r}")
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    add(f"coverage.event_types.{key}", "必须是非负整数")
            if event_types != dict(sorted(computed_counts.items())):
                add("coverage.event_types", "必须与 events 的事件类型计数一致")
        unsupported_ids = coverage.get("unsupported_event_ids")
        if not isinstance(unsupported_ids, list) or not all(
            isinstance(item, str) for item in unsupported_ids
        ):
            add("coverage.unsupported_event_ids", "必须是字符串数组")
            unsupported_ids = []
        for event_id in unsupported_ids:
            event = events_by_id.get(event_id)
            if event is None:
                add(
                    "coverage.unsupported_event_ids",
                    f"引用了不存在的事件：{event_id!r}",
                )
            elif event.get("type") != "unsupported_content":
                add(
                    "coverage.unsupported_event_ids",
                    f"事件 {event_id!r} 不是 unsupported_content",
                )
        if set(unsupported_ids) != set(unsupported):
            add("coverage.unsupported_event_ids", "必须与 unsupported_content 事件集合一致")
        source_step_ids = coverage.get("source_step_ids")
        if not isinstance(source_step_ids, list):
            add("coverage.source_step_ids", "必须是数组")
        else:
            for step_id in source_step_ids:
                if not _is_step_id(step_id):
                    add("coverage.source_step_ids", f"非法的来源步骤身份：{step_id!r}")
                elif step_id not in computed_steps:
                    add(
                        "coverage.source_step_ids",
                        f"引用了不存在的来源步骤：{step_id!r}",
                    )
            if source_step_ids != computed_steps:
                add("coverage.source_step_ids", "必须与 events 的来源步骤身份一致")
        tool_count = coverage.get("tool_definition_count")
        if not isinstance(tool_count, int) or isinstance(tool_count, bool):
            add("coverage.tool_definition_count", "必须是整数")
        elif isinstance(definitions, list) and tool_count != len(definitions):
            add(
                "coverage.tool_definition_count",
                f"应为 {len(definitions)}，实际 {tool_count!r}",
            )

    return issues


def validate_audit_input(payload: Any) -> list[dict[str, str]]:
    """返回机器可读问题列表；空列表表示通过 ``topic4-audit-input/1``。"""
    issues: list[dict[str, str]] = []
    add = _issue_sink(issues)

    if not isinstance(payload, dict):
        add("", "审计输入必须是 JSON 对象")
        return issues

    _check_keys(payload, INPUT_KEYS, "", add)
    if payload.get("schema_version") != AUDIT_INPUT_SCHEMA:
        add("schema_version", f"schema_version 必须是 {AUDIT_INPUT_SCHEMA!r}")
    batch_id = payload.get("batch_id")
    if not isinstance(batch_id, str) or not batch_id.strip():
        add("batch_id", "必须是非空字符串")
    elif any(character.isspace() for character in batch_id):
        add("batch_id", "不得包含空白字符")

    items = payload.get("items")
    if not isinstance(items, list) or not items:
        add("items", "items 必须是非空数组")
        return issues

    seen_samples: set[str] = set()
    for position, item in enumerate(items):
        path = f"items[{position}]"
        if not isinstance(item, dict):
            add(path, "item 必须是对象")
            continue
        _check_keys(item, ITEM_KEYS, path, add)
        sample_id = item.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id.strip():
            add(f"{path}.sample_id", "必须是非空字符串")
        elif sample_id in seen_samples:
            add(f"{path}.sample_id", f"样本身份重复：{sample_id!r}")
        else:
            seen_samples.add(sample_id)
        declared = item.get("audit_input_sha256")
        if not isinstance(declared, str) or not _SHA256_RE.match(declared):
            add(
                f"{path}.audit_input_sha256",
                "必须是 64 位小写十六进制 SHA-256",
            )
        trajectory = item.get("trajectory")
        trajectory_issues = validate_audit_trajectory(trajectory)
        for issue in trajectory_issues:
            issues.append(
                {
                    "path": _prefix(f"{path}.trajectory", issue["path"]),
                    "message": issue["message"],
                }
            )
        if "policy" not in item:
            add(f"{path}.policy", "缺少必需字段；无 policy 时必须是显式 null")
        policy = item.get("policy")
        for issue in _validate_policy(policy, f"{path}.policy"):
            issues.append(issue)
        if isinstance(trajectory, dict) and isinstance(declared, str) and _SHA256_RE.match(
            declared
        ):
            expected = audit_input_sha256(trajectory, policy)
            if expected != declared:
                add(
                    f"{path}.audit_input_sha256",
                    "与 provider 可见 trajectory/policy 重新计算的摘要不一致",
                )

    if (
        isinstance(batch_id, str)
        and batch_id
        and all(
            isinstance(item, dict)
            and isinstance(item.get("sample_id"), str)
            and isinstance(item.get("audit_input_sha256"), str)
            for item in items
        )
    ):
        if batch_id != batch_identity(items):
            add("batch_id", "必须由 sample_id 与 audit_input_sha256 派生")

    return issues


def _validate_policy(policy: Any, path: str) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    add = _issue_sink(issues, path)

    if policy is None:
        return issues
    if not isinstance(policy, dict):
        add("", "policy 必须是对象或显式 null")
        return issues
    for key in ("schema_version", "policy_id", "policy_version", "scope"):
        if key not in policy:
            add(key, "缺少必需字段")
    for key in policy:
        if key not in POLICY_KEYS:
            add(key, "未知字段：不在 provider 可见 policy 白名单内")
    if policy.get("schema_version") != POLICY_SCHEMA_VERSION:
        add("schema_version", f"必须是 {POLICY_SCHEMA_VERSION!r}")
    for key in ("policy_id", "policy_version"):
        value = policy.get(key)
        if not isinstance(value, str) or not value.strip():
            add(key, "必须是非空字符串")
    if policy.get("scope") != POLICY_SCOPE:
        add("scope", f"必须是 {POLICY_SCOPE!r}")
    for key in ("description", "user_request"):
        value = policy.get(key, "")
        if not isinstance(value, str):
            add(key, "必须是字符串")
    for key in ("sample_ids", "task_terms", "out_of_scope_terms", "confirmation_events"):
        value = policy.get(key, [])
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            add(key, "必须是字符串数组")
    tools = policy.get("tools", [])
    if not isinstance(tools, list) or any(not isinstance(item, dict) for item in tools):
        add("tools", "必须是对象数组")
    else:
        for position, rule in enumerate(tools):
            _validate_tool_rule(rule, f"tools[{position}]", add)
    return issues


def _validate_tool_rule(
    rule: dict[str, Any], prefix: str, add: IssueSink
) -> None:
    for key in rule:
        if key not in POLICY_TOOL_KEYS:
            add(_prefix(prefix, key), "未知字段：不在 policy 工具规则白名单内")
    tool_name = rule.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        add(_prefix(prefix, "tool_name"), "必须是非空字符串")
    for key in ("allowed", "requires_confirmation"):
        if key in rule and not isinstance(rule[key], bool):
            add(_prefix(prefix, key), "必须是布尔值")
    if "notes" in rule and not isinstance(rule["notes"], str):
        add(_prefix(prefix, "notes"), "必须是字符串")
    arguments = rule.get("allowed_arguments", [])
    if not isinstance(arguments, list) or any(
        not isinstance(item, str) for item in arguments
    ):
        add(_prefix(prefix, "allowed_arguments"), "必须是字符串数组")
    constraints = rule.get("constraints", [])
    if not isinstance(constraints, list):
        add(_prefix(prefix, "constraints"), "必须是数组")
        return
    for position, constraint in enumerate(constraints):
        constraint_prefix = _prefix(prefix, f"constraints[{position}]")
        if not isinstance(constraint, dict):
            add(constraint_prefix, "必须是对象")
            continue
        for key in constraint:
            if key not in POLICY_CONSTRAINT_KEYS:
                add(
                    _prefix(constraint_prefix, key),
                    "未知字段：不在参数约束白名单内",
                )
        for key in ("argument", "comparator"):
            value = constraint.get(key)
            if not isinstance(value, str) or not value.strip():
                add(_prefix(constraint_prefix, key), "必须是非空字符串")
        if "value" not in constraint:
            add(_prefix(constraint_prefix, "value"), "缺少必需字段")


def _check_tool_definitions(definitions: list[Any], add: IssueSink) -> None:
    for position, item in enumerate(definitions):
        path = f"tool_definitions[{position}]"
        if not isinstance(item, dict):
            add(path, "工具定义必须是对象；自由文本没有可投影的工具语义")
            continue
        for key in item:
            if key not in TOOL_DEFINITION_KEYS:
                add(f"{path}.{key}", "未知字段：不在工具定义白名单内")
        function = item.get("function")
        if function is not None:
            if not isinstance(function, dict):
                add(f"{path}.function", "必须是对象")
                continue
            for key in function:
                if key not in TOOL_FUNCTION_KEYS:
                    add(f"{path}.function.{key}", "未知字段：不在工具定义白名单内")


def _check_event_content(
    event: dict[str, Any],
    path: str,
    event_type: Any,
    add: IssueSink,
) -> None:
    content = event.get("content")
    if not isinstance(content, dict):
        add(f"{path}.content", "content 必须是对象")
        return
    allowed = EVENT_CONTENT_KEYS.get(event_type)
    if allowed is None:
        add(f"{path}.content", "未知事件类型没有允许的 content 字段")
        return
    for key in content:
        if key not in allowed:
            add(f"{path}.content.{key}", "未知字段：不在该事件类型的白名单内")
    required = EVENT_CONTENT_REQUIRED[event_type]
    for key in required:
        if key not in content:
            add(f"{path}.content.{key}", "缺少必需字段")
    if event_type == "message":
        parts = content.get("parts")
        if not isinstance(parts, list):
            add(f"{path}.content.parts", "必须是数组")
        else:
            for position, part in enumerate(parts):
                part_path = f"{path}.content.parts[{position}]"
                if not isinstance(part, dict):
                    add(part_path, "文本片段必须是对象")
                    continue
                if set(part) != {"type", "text"}:
                    add(part_path, "只允许 type 与 text 字段")
                elif not isinstance(part.get("type"), str) or not isinstance(
                    part.get("text"), str
                ):
                    add(part_path, "type 与 text 必须是字符串")


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _check_keys(
    payload: dict[str, Any], expected: tuple[str, ...], prefix: str, add: IssueSink
) -> None:
    for key in expected:
        if key not in payload:
            add(_prefix(prefix, key), "缺少必需字段")
    for key in payload:
        if key not in expected:
            add(
                _prefix(prefix, key),
                "未知字段：不在白名单内，不得进入 provider 输入",
            )


def _prefix(prefix: str, key: str) -> str:
    if not key:
        return prefix
    return f"{prefix}.{key}" if prefix else key


def _is_step_id(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, str) and bool(value)


__all__ = [
    "AUDIT_INPUT_SCHEMA",
    "AUDIT_TRAJECTORY_SCHEMA",
    "COVERAGE_KEYS",
    "EVENT_CONTENT_KEYS",
    "EVENT_KEYS",
    "INPUT_KEYS",
    "ITEM_KEYS",
    "POLICY_KEYS",
    "TOOL_DEFINITION_KEYS",
    "TOOL_FUNCTION_KEYS",
    "TRAJECTORY_KEYS",
    "audit_input_sha256",
    "batch_identity",
    "build_batch",
    "canonical_item",
    "project_event",
    "project_policy",
    "project_tool_definitions",
    "project_trajectory",
    "validate_audit_input",
    "validate_audit_trajectory",
]
