# SPDX-License-Identifier: Apache-2.0
"""保守策略（policy）输入：加载、校验与逐样本绑定。

policy 是调用方提供的授权声明，不是来源数据集的真值，也不参与 reference
标注。它只回答"这次任务被允许做什么"，不回答"实际发生了什么"。

设计约束：

- 六类 policy 场景各自独立，同一份 policy 文档可同时声明其中若干项。
- 未声明的项一律视为"无依据"，对应检查保持 ``undetermined``，绝不默认放行。
- policy 文件必须显式携带 ``policy_id`` / ``policy_version`` / ``scope``，
  缺失即拒绝加载，避免隐式默认值被当成授权。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

POLICY_SCHEMA_VERSION = "audit-policy/1"

#: policy 纵切场景到检查项的映射。未授权工具是 ``authorized_tools`` 检查的
#: violation 状态，因此不再单列一个重复检查项。
POLICY_SCENARIOS: dict[str, str] = {
    "allowed": "authorized_tools",
    "argument_out_of_bounds": "argument_bounds",
    "missing_human_confirmation": "human_confirmation",
    "out_of_user_scope": "user_scope",
    "no_policy": "policy_availability",
}

#: 参数比较方式。只接受确定性的字面比较，不做类型强制转换。
_ARGUMENT_COMPARATORS = frozenset(
    {
        "equals",
        "not_equals",
        "in",
        "not_in",
        "contains",
        "not_contains",
        "matches",
        "minimum",
        "maximum",
        "minimum_length",
        "maximum_length",
    }
)


class PolicyError(ValueError):
    """policy 文档不合法。"""


@dataclass(frozen=True)
class PolicyToolRule:
    """单个工具名的参数约束。"""

    tool_name: str
    allowed: bool = True
    allowed_arguments: list[str] = field(default_factory=list)
    constraints: list[dict[str, Any]] = field(default_factory=list)
    requires_confirmation: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "allowed": self.allowed,
            "allowed_arguments": list(self.allowed_arguments),
            "constraints": [dict(item) for item in self.constraints],
            "requires_confirmation": self.requires_confirmation,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class Policy:
    """一份可审计的授权声明。"""

    policy_id: str
    policy_version: str
    scope: str
    description: str = ""
    sample_ids: list[str] = field(default_factory=list)
    tools: list[PolicyToolRule] = field(default_factory=list)
    task_terms: list[str] = field(default_factory=list)
    out_of_scope_terms: list[str] = field(default_factory=list)
    user_request: str = ""
    confirmation_events: list[str] = field(default_factory=list)
    source_path: str = ""
    source_sha256: str = ""

    def tool_rule(self, tool_name: str | None) -> PolicyToolRule | None:
        if not tool_name:
            return None
        for rule in self.tools:
            if rule.tool_name == tool_name:
                return rule
        return None

    def declared_tool_names(self) -> list[str]:
        return [rule.tool_name for rule in self.tools]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": POLICY_SCHEMA_VERSION,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "scope": self.scope,
            "description": self.description,
            "sample_ids": list(self.sample_ids),
            "tools": [rule.to_dict() for rule in self.tools],
            "task_terms": list(self.task_terms),
            "out_of_scope_terms": list(self.out_of_scope_terms),
            "user_request": self.user_request,
            "confirmation_events": list(self.confirmation_events),
            "source": {"path": self.source_path, "sha256": self.source_sha256},
        }

    def summary(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "scope": self.scope,
            "declared_tools": self.declared_tool_names(),
            "has_task_scope": bool(self.user_request or self.task_terms),
            "out_of_scope_terms": list(self.out_of_scope_terms),
            "has_confirmation_policy": bool(
                self.confirmation_events
                or any(rule.requires_confirmation for rule in self.tools)
            ),
            "checks_enabled": sorted(POLICY_SCENARIOS.values()),
            "source": {"path": self.source_path, "sha256": self.source_sha256},
        }


def is_policy_document(payload: Any) -> bool:
    """判断一份 JSON 是否是 policy 文档（相对 batch 清单/样本文件）。"""

    return (
        isinstance(payload, dict)
        and payload.get("kind") == "audit-policy"
        and "policy_id" in payload
        and "tools" in payload
    )


def load_policy(path: str | Path) -> Policy:
    file_path = Path(path)
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise PolicyError(f"无法读取 policy 文件 {file_path}: {error}") from error
    except json.JSONDecodeError as error:
        raise PolicyError(f"policy 文件不是合法 JSON {file_path}: {error}") from error
    sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()
    return policy_from_dict(payload, source_path=str(file_path), source_sha256=sha256)


def policy_from_dict(
    payload: Any,
    *,
    source_path: str = "",
    source_sha256: str = "",
) -> Policy:
    if not isinstance(payload, dict):
        raise PolicyError("policy 文档必须是 JSON 对象")
    if payload.get("kind") != "audit-policy":
        raise PolicyError("policy 文档缺少 kind: audit-policy")
    if payload.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise PolicyError(
            f"policy schema_version 必须是 {POLICY_SCHEMA_VERSION}，"
            f"实际为 {payload.get('schema_version')!r}"
        )

    policy_id = _require_str(payload, "policy_id")
    policy_version = _require_str(payload, "policy_version")
    scope = _require_str(payload, "scope")
    if scope != "trajectory":
        raise PolicyError(f"policy scope 目前只支持 trajectory，实际为 {scope!r}")

    raw_tools = payload.get("tools", [])
    if not isinstance(raw_tools, list):
        raise PolicyError("policy.tools 必须是数组")
    tools = [_parse_tool(item, index) for index, item in enumerate(raw_tools)]
    names = [rule.tool_name for rule in tools]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise PolicyError(f"policy.tools 存在重复工具名: {duplicates}")

    sample_ids = _optional_str_list(payload, "sample_ids")
    task_terms = _optional_str_list(payload, "task_terms")
    out_of_scope_terms = _optional_str_list(payload, "out_of_scope_terms")
    confirmation_events = _optional_str_list(payload, "confirmation_events")
    user_request = payload.get("user_request", "")
    if not isinstance(user_request, str):
        raise PolicyError("policy.user_request 必须是字符串")

    return Policy(
        policy_id=policy_id,
        policy_version=policy_version,
        scope=scope,
        description=str(payload.get("description", "")),
        sample_ids=sample_ids,
        tools=tools,
        task_terms=task_terms,
        out_of_scope_terms=out_of_scope_terms,
        user_request=user_request,
        confirmation_events=confirmation_events,
        source_path=source_path,
        source_sha256=source_sha256,
    )


def _parse_tool(payload: Any, index: int) -> PolicyToolRule:
    if not isinstance(payload, dict):
        raise PolicyError(f"policy.tools[{index}] 必须是对象")
    tool_name = _require_str(payload, "tool_name", prefix=f"tools[{index}].")
    allowed = payload.get("allowed", True)
    if not isinstance(allowed, bool):
        raise PolicyError(f"policy.tools[{index}].allowed 必须是布尔值")
    requires_confirmation = payload.get("requires_confirmation", False)
    if not isinstance(requires_confirmation, bool):
        raise PolicyError(f"policy.tools[{index}].requires_confirmation 必须是布尔值")
    allowed_arguments = _optional_str_list(payload, "allowed_arguments", prefix=f"tools[{index}].")

    raw_constraints = payload.get("constraints", [])
    if not isinstance(raw_constraints, list):
        raise PolicyError(f"policy.tools[{index}].constraints 必须是数组")
    constraints = []
    for position, item in enumerate(raw_constraints):
        if not isinstance(item, dict):
            raise PolicyError(f"policy.tools[{index}].constraints[{position}] 必须是对象")
        argument = _require_str(item, "argument", prefix=f"tools[{index}].constraints[{position}].")
        comparator = _require_str(
            item, "comparator", prefix=f"tools[{index}].constraints[{position}]."
        )
        if comparator not in _ARGUMENT_COMPARATORS:
            raise PolicyError(
                f"policy.tools[{index}].constraints[{position}].comparator 不支持: {comparator!r}"
            )
        if "value" not in item:
            raise PolicyError(
                f"policy.tools[{index}].constraints[{position}] 缺少 value"
            )
        constraints.append(
            {
                "argument": argument,
                "comparator": comparator,
                "value": item["value"],
                "notes": str(item.get("notes", "")),
            }
        )

    return PolicyToolRule(
        tool_name=tool_name,
        allowed=allowed,
        allowed_arguments=allowed_arguments,
        constraints=constraints,
        requires_confirmation=requires_confirmation,
        notes=str(payload.get("notes", "")),
    )


def _require_str(payload: dict[str, Any], key: str, *, prefix: str = "") -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PolicyError(f"policy.{prefix}{key} 必须是非空字符串")
    return value


def _optional_str_list(
    payload: dict[str, Any], key: str, *, prefix: str = ""
) -> list[str]:
    value = payload.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise PolicyError(f"policy.{prefix}{key} 必须是字符串数组")
    return list(value)
