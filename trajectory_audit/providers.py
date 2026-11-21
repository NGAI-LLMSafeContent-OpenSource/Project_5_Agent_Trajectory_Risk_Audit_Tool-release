# SPDX-License-Identifier: Apache-2.0
"""审计 provider 注册表与显式 fallback 策略。

支持确定性 baseline 和通过外部 CLI 调用的课题四 Provider。

失败处理是 fail-closed 的：请求的 provider 不存在时默认直接失败，保留原始
失败；只有调用方显式允许 fallback，才会运行 baseline，并在结果中写入
``degraded`` 记录，同时保留原失败原因。fallback 永远不会静默发生。
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

from . import baseline
from .errors import RunError
from .topic4_provider import run_provider

PROVIDERS: dict[str, ModuleType] = {baseline.PROVIDER_ID: baseline, run_provider.PROVIDER_ID: run_provider}

#: 显式 fallback 时使用的 provider。只允许显式开关，不允许自动降级。
FALLBACK_PROVIDER_ID = baseline.PROVIDER_ID

FALLBACK_MODE = "explicit_fallback"


def get_provider(provider_id: str) -> ModuleType:
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        raise RunError(
            "unknown_provider",
            f"未知 provider：{provider_id}",
            details={"requested": provider_id, "available": sorted(PROVIDERS)},
        )
    return provider


def resolve_provider(
    provider_id: str, *, allow_fallback: bool = False,
    provider_options: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    """解析 provider，返回 ``(provider, degraded)``。

    ``degraded`` 非空表示本次运行是显式 fallback，调用方必须把它原样写入
    run 包，并保留 ``original_failure``。
    """

    provider = PROVIDERS.get(provider_id)
    if provider is not None:
        if provider_id == run_provider.PROVIDER_ID:
            return run_provider.create_provider(provider_options), None
        return provider, None
    if not allow_fallback:
        raise RunError(
            "unknown_provider",
            (
                f"未知 provider：{provider_id}；默认保留失败。"
                "如需使用 baseline 请显式允许 fallback。"
            ),
            details={
                "requested": provider_id,
                "available": sorted(PROVIDERS),
                "fallback_provider": FALLBACK_PROVIDER_ID,
                "allow_fallback": False,
            },
        )
    original = {
        "code": "unknown_provider",
        "message": f"请求的 provider {provider_id!r} 不可用",
        "requested_provider": provider_id,
    }
    return PROVIDERS[FALLBACK_PROVIDER_ID], {
        "mode": FALLBACK_MODE,
        "from_provider": provider_id,
        "to_provider": FALLBACK_PROVIDER_ID,
        "reason": "调用方显式允许 fallback，改由确定性 baseline 审计",
        "original_failure": original,
        "retained": True,
    }
