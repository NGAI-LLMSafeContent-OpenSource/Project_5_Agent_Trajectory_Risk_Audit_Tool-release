# 第三方组件与数据来源

本版本仅发布轨迹审计最小包，不发布Docker benchmark、模型或完整数据集。核心代码仅使用Python标准库；Python解释器遵循PSF许可证，由使用者安装，不包含在发布包中。原开发pyproject里的PyYAML/httpx/openai/python-dotenv用于本包之外的benchmark，已从本发布依赖中移除。

| 内容 | 原始来源与固定版本 | 许可证 | 本包处理 |
|---|---|---|---|
| 上游工程来源及归属 | https://github.com/seclaw-eval/seclaw-eval ，a3722ca6a754585c26a41e7b9f076fce0a19340f | Apache-2.0 | 保留原始LICENSE和NOTICE；不包含上游Docker任务 |
| 轨迹审计派生源码 | NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool ，f45dec0b0fc2505882a39c0c9169c352c9e65f45 | Apache-2.0（源文件SPDX） | 保留SPDX；README和依赖清单按发布范围重写 |
| ATBench冒烟子集 | https://huggingface.co/datasets/AI45Research/ATBench/tree/4476ef92ed8f85c8d58d8a5b9dfdf55aa7893138 | Apache-2.0（固定版本数据卡） | 只保留原smoke子集及source元数据；许可证见third_party/ATBench-LICENSE |
| HINTBench冒烟子集 | https://github.com/Jiacheng-31/HINTBench/tree/1210cd54ebc9b0a1f3a9c4ff122705b7b3cb208b | Apache-2.0（固定版本LICENSE） | 只保留原smoke子集及source元数据；许可证见third_party/HINTBench-LICENSE |

数据样例保持源仓原文及来源元数据，未重标注。发布README、依赖清单及来源说明发生编辑；其余纳入的源码、测试与样例取自固定源Git提交。发布包不自动下载或执行样例描述中的工具操作。
