# SPDX-License-Identifier: Apache-2.0
"""可插拔的语义 Judge 接口。

确定性审计（baseline + policy）不依赖任何模型，可以完全离线复现。语义
Judge 是可选的第二意见，默认关闭：

- 默认 provider ``none`` 直接返回 ``disabled``，不产生任何判定影响；
- Judge 的输出永远是"建议"，记录在 ``audit_result["judge"]``，不写进
  ``findings``，也不改变 ``verdict``——除非调用方显式把它纳入判定；
- 未注册的 judge 名称立即报错，避免悄悄降级成"看起来跑过 judge"。

Judge 实现必须接受关键字参数 ``trajectory`` / ``policy`` / ``findings`` 并
返回 :class:`JudgeResult`。测试可以注册确定性的桩实现，不需要网络。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

JUDGE_SCHEMA_VERSION = "audit-judge/1"

#: Judge 可能返回的状态。``disabled`` 表示没有运行，``abstain`` 表示运行了但
#: 不给意见，``revise`` 表示建议人工复核，``agree`` 表示同意确定性结论。
JUDGE_STATUSES = frozenset({"disabled", "abstain", "agree", "revise"})


class JudgeError(ValueError):
    """Judge 配置或输出不合法。"""


@dataclass(frozen=True)
class JudgeResult:
    status: str
    reason: str
    provider: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": JUDGE_SCHEMA_VERSION,
            "status": self.status,
            "influences_verdict": False,
            "reason": self.reason,
            "provider": self.provider,
            "notes": list(self.notes),
        }


class SemanticJudge(Protocol):
    def evaluate(
        self,
        *,
        trajectory: dict[str, Any],
        policy: Any,
        findings: list[Any],
    ) -> JudgeResult:
        """``trajectory`` 与 ``policy`` 都是 provider 可见的白名单投影。

        ``trajectory`` 只包含 ``audit-trajectory/1`` 的 events、
        tool_definitions 与 coverage；``policy`` 只包含
        ``audit-policy/1`` 的授权字段，无 policy 时为 ``None``。二者都不含
        来源标签、项目参考标注、事件 source/raw、数据集身份、policy 本地
        来源路径或既有 Detected Finding；Judge 不得要求完整 ``trajectory/2``。
        """
        ...


class NullJudge:
    """默认实现：明确表示未运行语义判断。"""

    identity = {
        "id": "none",
        "version": "1",
        "kind": "null",
        "description": "语义 Judge 未启用；判定只来自确定性规则与 policy",
    }

    def evaluate(
        self,
        *,
        trajectory: dict[str, Any],
        policy: Any,
        findings: list[Any],
    ) -> JudgeResult:
        return JudgeResult(
            status="disabled",
            reason="未启用语义 Judge，本 run 的判定只来自确定性规则与 policy",
            provider=dict(self.identity),
        )


JudgeFactory = Callable[[], SemanticJudge]

_REGISTRY: dict[str, JudgeFactory] = {"none": NullJudge}


def register_judge(name: str, factory: JudgeFactory) -> None:
    if not name:
        raise JudgeError("judge 名称不能为空")
    _REGISTRY[name] = factory


def available_judges() -> list[str]:
    return sorted(_REGISTRY)


def resolve_judge(name: str | None) -> SemanticJudge:
    key = (name or "none").strip() or "none"
    factory = _REGISTRY.get(key)
    if factory is None:
        raise JudgeError(
            f"未注册的 judge: {key!r}（可用: {', '.join(available_judges())}）"
        )
    return factory()


def run_judge(
    judge: SemanticJudge,
    *,
    trajectory: dict[str, Any],
    policy: Any,
    findings: list[Any],
) -> dict[str, Any]:
    """运行 judge 并校验输出，任何异常都收敛为显式的 ``error`` 记录。"""

    try:
        result = judge.evaluate(trajectory=trajectory, policy=policy, findings=findings)
    except Exception as error:  # noqa: BLE001 - judge 失败不能影响确定性审计
        return {
            "schema_version": JUDGE_SCHEMA_VERSION,
            "status": "error",
            "influences_verdict": False,
            "reason": f"judge 执行失败: {type(error).__name__}: {error}",
            "provider": {},
            "notes": [],
        }
    if not isinstance(result, JudgeResult):
        raise JudgeError("judge 返回值必须是 JudgeResult")
    if result.status not in JUDGE_STATUSES:
        raise JudgeError(
            f"judge status 非法: {result.status!r}（允许: {sorted(JUDGE_STATUSES)}）"
        )
    return result.to_dict()
