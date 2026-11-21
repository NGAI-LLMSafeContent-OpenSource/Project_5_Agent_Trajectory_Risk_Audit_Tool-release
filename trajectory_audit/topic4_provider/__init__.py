# SPDX-License-Identifier: Apache-2.0
"""文件式 Topic 4 Provider CLI Adapter。

课题五只通过外部进程与 JSON 文件交换调用课题四，不 import 其内部模块。
本包拆分 process runner、raw loader、contract validator、result mapper 与
run archiver，便于字段微调与独立测试。
"""

from __future__ import annotations

from .adapter import run_topic4_adapter
from .provider_module import PROVIDER_ID, register_provider_helpers

__all__ = [
    "PROVIDER_ID",
    "register_provider_helpers",
    "run_topic4_adapter",
]
