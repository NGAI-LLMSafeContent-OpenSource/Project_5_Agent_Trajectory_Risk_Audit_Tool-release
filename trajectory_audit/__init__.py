# SPDX-License-Identifier: Apache-2.0
"""Agent 轨迹风险审计最小纵切。

当前实现范围：OpenAI messages 输入校验、统一事件归一化、显式选择的最小
确定性 baseline、不可变 run 包与离线审阅页面，以及冻结的 `acceptance-v1`
验收集与 H1 数据契约验证。
"""

__version__ = "0.1.0"
