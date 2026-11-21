# SPDX-License-Identifier: Apache-2.0
"""来源侧抽样共用的纯函数。

``acceptance``（Blind Acceptance Batch）与 ``development``（Development Corpus）
用不同的分层与随机数配方，但有两件事必须完全一致，否则同一批来源会在两个集合
里得到互相矛盾的解释：

- 风险层匹配的严格类型比较（``False`` 不等于 ``0``）；
- 配额按候选数分配时的最大余数法。

两者都不含随机性，因此可以在两个集合之间共享而不改变各自的抽样结果。
"""

from __future__ import annotations

from typing import Any, Mapping


def matches_layer(record: Any, layer: Mapping[str, Any]) -> bool:
    """按严格类型比较风险层，避免 ``False == 0`` 之类的隐式相等。"""
    if not isinstance(record, dict):
        return False
    value = record.get(layer["field"])
    expected = layer.get("true_value")
    return type(value) is type(expected) and value == expected


def allocate_counts(sizes: Mapping[str, int], quota: int) -> dict[str, int]:
    """最大余数法分配配额，保证总数等于 ``quota`` 且每层不超过候选数。"""
    total = sum(sizes.values())
    if total == 0 or quota <= 0:
        return {}
    quotas: dict[str, int] = {}
    fractions: list[tuple[float, int, str]] = []
    for key, size in sizes.items():
        exact = quota * size / total
        floor = int(exact)
        quotas[key] = floor
        fractions.append((exact - floor, size, key))
    remaining = quota - sum(quotas.values())
    fractions.sort(key=lambda item: (-item[0], -item[1], item[2]))
    for _, _, key in fractions[:remaining]:
        quotas[key] += 1
    return {key: count for key, count in quotas.items() if count > 0}


__all__ = ["allocate_counts", "matches_layer"]
