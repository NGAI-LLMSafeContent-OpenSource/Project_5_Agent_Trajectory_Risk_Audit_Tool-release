# SPDX-License-Identifier: Apache-2.0
"""固定版本的数据集来源清单。

数据集身份只允许出现在来源侧模块（本文件、``adapters.py`` 与各 adapter）；
``runner``、``batch``、``metrics``、``page``、``baseline`` 等下游模块不得读取
本文件，也不得按数据集名称分支。

SHA-256 与字节数在 H1 验证中会被重新计算，因此这里的记录是可核对的期望值，
而不是「相信本地文件」的声明。
"""

from __future__ import annotations

import copy
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


#: Development Corpus 的冻结策略。与 `acceptance-v1` 一样，分层、配额和覆盖
#: 维度只读来源标签与来源元数据，从不读取 detected 结果；因此规则实现无法
#: 反向影响样本选择。覆盖维度里的每个取值都是显式声明：只要某个声明值在本
#: 地固定版本中没有任何候选记录，构建就必须失败，而不是静默生成不完整 corpus。
DEVELOPMENT_CORPUS_POLICIES: dict[str, dict[str, Any]] = {
    "topic4-provider-corpus-v1": {
        "seed": 20261027,
        "algorithm": "coverage-first-distribution-fill/1",
        "selection_basis": "source labels and source metadata only; detected results never influence selection",
        "isolated_from": "acceptance-v1",
        "sources": {
            "atbench": {
                "quotas": {"safe": 16, "unsafe": 32},
                "risk_layers": {
                    "safe": {"field": "label", "true_value": 0},
                    "unsafe": {"field": "label", "true_value": 1},
                },
                "distribution": {"safe": "risk_source", "unsafe": "risk_source"},
                "coverage": [
                    {
                        "id": "atbench.benign",
                        "strata": ["safe"],
                        "kind": "field",
                        "field": "risk_source",
                        "values": ["benign"],
                        "detail": "普通安全样本：来源判定为 benign，没有任何风险诱因",
                    },
                    {
                        "id": "atbench.risk_inducing_safe",
                        "strata": ["safe"],
                        "kind": "field",
                        "field": "risk_source",
                        "values": [
                            "corrupted_tool_feedback",
                            "direct_prompt_injection",
                            "inherent_agent_failures",
                            "indirect_prompt_injection",
                            "malicious_tool_execution",
                            "malicious_user_instruction_or_jailbreak",
                            "tool_description_injection",
                            "unreliable_or_misinformation",
                        ],
                        "detail": "困难 safe 反例：存在风险诱因但来源判定仍为 safe",
                    },
                    {
                        "id": "atbench.unsafe_risk_source",
                        "strata": ["unsafe"],
                        "kind": "field",
                        "field": "risk_source",
                        "values": [
                            "corrupted_tool_feedback",
                            "direct_prompt_injection",
                            "inherent_agent_failures",
                            "indirect_prompt_injection",
                            "malicious_tool_execution",
                            "malicious_user_instruction_or_jailbreak",
                            "tool_description_injection",
                            "unreliable_or_misinformation",
                        ],
                        "detail": "ATBench 全部 8 类风险来源",
                    },
                    {
                        "id": "atbench.unsafe_failure_mode",
                        "strata": ["unsafe"],
                        "kind": "field",
                        "field": "failure_mode",
                        "values": [
                            "choosing_malicious_tool",
                            "failure_to_validate_tool_outputs",
                            "flawed_planning_or_reasoning",
                            "generation_of_harmful_offensive_content",
                            "generation_of_malicious_executables",
                            "incorrect_tool_parameters",
                            "inefficient_or_wasteful_execution",
                            "insecure_interaction_or_execution",
                            "instruction_for_harmful_illegal_activity",
                            "procedural_deviation_or_inaction",
                            "provide_inaccurate_misleading_or_unverified_information",
                            "tool_misuse_in_specific_context",
                            "unauthorized_information_disclosure",
                            "unconfirmed_or_over_privileged_action",
                        ],
                        "detail": "ATBench 全部 14 类失败模式",
                    },
                ],
            },
            "hintbench": {
                "quotas": {"safe": 8, "risky": 24},
                "risk_layers": {
                    "safe": {"field": "is_risky", "true_value": False},
                    "risky": {"field": "is_risky", "true_value": True},
                },
                "distribution": {"safe": "env", "risky": "primary_category"},
                "coverage": [
                    {
                        "id": "hintbench.constraint",
                        "strata": ["risky"],
                        "kind": "categories",
                        "values": [
                            "Capability Constraint",
                            "Factual Constraint",
                            "Goal Constraint",
                            "Procedural Constraint",
                            "State Constraint",
                        ],
                        "detail": "HINTBench 五类约束；一条记录可同时覆盖多类",
                    },
                    {
                        "id": "hintbench.safe_env",
                        "strata": ["safe"],
                        "kind": "field",
                        "field": "env",
                        "values": [
                            "banking",
                            "commerceOperations",
                            "documentForensics",
                            "publicTransit",
                            "socialMediaAnalytics",
                            "travel",
                        ],
                        "detail": "代表性 safe 环境",
                    },
                ],
            },
        },
    }
}

# v2：与 v1 选样策略相同；因 ATBench Complete 投影变更需新版本冻结，禁止覆盖 v1。
DEVELOPMENT_CORPUS_POLICIES["topic4-provider-corpus-v2"] = copy.deepcopy(
    DEVELOPMENT_CORPUS_POLICIES["topic4-provider-corpus-v1"]
)



def pinned(dataset_id: str) -> dict[str, Any]:
    entry = BY_DATASET.get(dataset_id)
    if entry is None:
        raise KeyError(f"未固定的数据集：{dataset_id}")
    return entry


__all__ = [
    "ACCEPTANCE_POLICIES",
    "BY_DATASET",
    "DEVELOPMENT_CORPUS_POLICIES",
    "PINNED_DATASETS",
    "pinned",
]
