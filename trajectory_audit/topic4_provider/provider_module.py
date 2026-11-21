# SPDX-License-Identifier: Apache-2.0
"""Topic4 CLI Provider 的显式注册辅助。

本 Provider 是批量文件式 Adapter，不强制塞进单轨迹 ``audit()`` 接口。
``register_provider_helpers`` 仅在调用方明确需要时，把身份写入
``providers.PROVIDERS`` 供 fallback 解析引用；默认不自动注册。
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

PROVIDER_ID = "topic4-cli"


def provider_identity() -> dict[str, Any]:
    return {
        "id": PROVIDER_ID,
        "kind": "external_cli_file_adapter",
        "interface": "topic4-audit-result/1",
        "transport": "subprocess+files",
    }


def register_provider_helpers(
    registry: dict[str, ModuleType],
    module: ModuleType,
) -> None:
    """把本模块登记到现有 ``PROVIDERS`` 映射，供显式选择/fallback 引用。"""

    registry[PROVIDER_ID] = module
