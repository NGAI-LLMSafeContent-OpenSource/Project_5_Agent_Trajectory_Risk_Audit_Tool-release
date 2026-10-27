# SPDX-License-Identifier: Apache-2.0
"""固定版本的数据集来源清单。

数据集身份只允许出现在来源侧模块（本文件、``adapters.py`` 与各 adapter）；
``runner``、``batch``、``metrics``、``page``、``baseline`` 等下游模块不得读取
本文件，也不得按数据集名称分支。

SHA-256 与字节数在 H1 验证中会被重新计算，因此这里的记录是可核对的期望值，
而不是「相信本地文件」的声明。
"""

from __future__ import annotations

from typing import Any

PINNED_DATASETS: tuple[dict[str, Any], ...] = (
    {
        "dataset_id": "atbench",
        "adapter_id": "atbench",
        "dataset_version": (
            "AI45Research/ATBench@4476ef92ed8f85c8d58d8a5b9dfdf55aa7893138/ATBench/test.json"
        ),
        "upstream": "https://huggingface.co/datasets/AI45Research/ATBench",
        "upstream_commit": "4476ef92ed8f85c8d58d8a5b9dfdf55aa7893138",
        "url": (
            "https://huggingface.co/datasets/AI45Research/ATBench/resolve/"
            "4476ef92ed8f85c8d58d8a5b9dfdf55aa7893138/ATBench/test.json"
        ),
        "license": "Apache-2.0",
        "sha256": "80c534b5f3517c872b528a2e6b64e34495206968edd184f4aed563d39f0cca09",
        "bytes": 18335079,
        "cache_name": "atbench.json",
        "layout": "json_array",
        "record_identity": "record index 与来源 id 字段",
    },
    {
        "dataset_id": "hintbench",
        "adapter_id": "hintbench",
        "dataset_version": (
            "Jiacheng-31/HINTBench@1210cd54ebc9b0a1f3a9c4ff122705b7b3cb208b/"
            "data/hintbench.json"
        ),
        "upstream": "https://github.com/Jiacheng-31/HINTBench",
        "upstream_commit": "1210cd54ebc9b0a1f3a9c4ff122705b7b3cb208b",
        "url": (
            "https://raw.githubusercontent.com/Jiacheng-31/HINTBench/"
            "1210cd54ebc9b0a1f3a9c4ff122705b7b3cb208b/data/hintbench.json"
        ),
        "license": "Apache-2.0",
        "sha256": "87b33d3941be49cc40e6b38e1faec3cb420fd3483369eff68821e43a4db62e44",
        "bytes": 12502100,
        "cache_name": "hintbench.json",
        "layout": "json_array",
        "record_identity": "record index 与来源 task_id 字段",
    },
)

BY_DATASET: dict[str, dict[str, Any]] = {
    entry["dataset_id"]: entry for entry in PINNED_DATASETS
}


# 验收集的冻结策略。分层与抽样只读来源标签，不读 detected 结果。
ACCEPTANCE_POLICIES: dict[str, dict[str, Any]] = {
    "acceptance-v1": {
        "seed": 20261023,
        "algorithm": "stratified-largest-remainder/1",
        "selection_basis": "source labels only; detected results never influence selection",
        "risk_layers": {
            "atbench": {
                "safe": {"field": "label", "true_value": 0},
                "unsafe": {"field": "label", "true_value": 1},
            },
            "hintbench": {
                "safe": {"field": "is_risky", "true_value": False},
                "risky": {"field": "is_risky", "true_value": True},
            },
        },
        "stratification": {
            "atbench": {
                "safe": {"layer": {"field": "label", "true_value": 0}, "field": "failure_mode"},
                "unsafe": {"layer": {"field": "label", "true_value": 1}, "field": "risk_source"},
            },
            "hintbench": {
                "safe": {
                    "layer": {"field": "is_risky", "true_value": False},
                    "field": "env",
                },
                "risky": {
                    "layer": {"field": "is_risky", "true_value": True},
                    "kind": "primary_category",
                },
            },
        },
        "quotas": {
            "atbench": {"total": 24, "strata": {"safe": 8, "unsafe": 16}},
            "hintbench": {"total": 24, "strata": {"safe": 6, "risky": 18}},
        },
    }
}


def pinned(dataset_id: str) -> dict[str, Any]:
    entry = BY_DATASET.get(dataset_id)
    if entry is None:
        raise KeyError(f"未固定的数据集：{dataset_id}")
    return entry


__all__ = ["ACCEPTANCE_POLICIES", "BY_DATASET", "PINNED_DATASETS", "pinned"]
