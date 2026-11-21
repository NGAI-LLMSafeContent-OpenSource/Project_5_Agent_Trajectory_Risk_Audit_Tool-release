# 第三方代码与样例归属

自研派生代码按项目权利人授权采用Apache-2.0，原LICENSE、NOTICE及文件SPDX保留。本次发布包括现行Docker benchmark、任务fixture、轨迹审计与审阅工作台；依赖声明保持源仓原样，不再使用只含离线切片的依赖说明。

| 组件 | 来源 | 许可与处理 |
| --- | --- | --- |
| SeClaw上游工程 | https://github.com/seclaw-eval/seclaw-eval | Apache-2.0；保留LICENSE/NOTICE |
| ATBench冒烟子集 | https://huggingface.co/datasets/AI45Research/ATBench/tree/4476ef92ed8f85c8d58d8a5b9dfdf55aa7893138 | Apache-2.0；third_party/ATBench-LICENSE，原source元数据保留 |
| HINTBench冒烟子集 | https://github.com/Jiacheng-31/HINTBench/tree/1210cd54ebc9b0a1f3a9c4ff122705b7b3cb208b | Apache-2.0；third_party/HINTBench-LICENSE |
| Python/Node依赖与OpenClaw镜像 | pyproject.toml、uv.lock、benchmark/requirements.txt、workbench/web/package-lock.json及Docker配置 | 各发行包、镜像保留原许可；不将它们统一改标为Apache |
| 可选Gradio入口 | https://github.com/gradio-app/gradio | Apache-2.0；运行时依赖遵循各包许可 |

OpenClaw任务fixture为本地模拟环境，演练标记和截断凭据不用于外部服务认证。完整外部数据集、模型、冻结审计输入及参考标注需按各自获取条件与许可另行准备。

## 本次功能对齐说明

本次保留源仓的现行运行实现、依赖声明及原NOTICE/SPDX。自研代码Apache-2.0授权不替代第三方代码、Python/Node依赖、模型、数据集或服务的原许可。实际依赖以本仓原始pyproject/requirements/lock文件为准。未捆绑的权重、原始数据、知识库、第三方子模块与历史结果由使用者按原许可准备。
