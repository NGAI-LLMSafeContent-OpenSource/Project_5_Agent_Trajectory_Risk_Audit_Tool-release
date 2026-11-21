# Related Agent Trajectory Safety Benchmarks

本文档汇总与本项目相关的 Agent 轨迹安全评测基准及其官方访问入口。

## ATBench

ATBench（A Diverse and Realistic Agent Trajectory Benchmark for Safety Evaluation and Diagnosis）面向长时程、工具调用型 Agent 的轨迹级安全评估与风险诊断。

- [项目主页](https://www.yuli-cs.net/papers/atbench/)
- [GitHub 仓库](https://github.com/LiYu0524/ATbench)
- [Hugging Face 数据集](https://huggingface.co/datasets/AI45Research/ATBench)
- [arXiv 论文](https://arxiv.org/abs/2604.02022)
- [论文 PDF](https://arxiv.org/pdf/2604.02022)

可以使用 Hugging Face `datasets` 直接加载当前 ATBench 或兼容保留的 ATBench500：

```python
from datasets import load_dataset

atbench = load_dataset("AI45Research/ATBench", "ATBench", split="test")
atbench500 = load_dataset("AI45Research/ATBench", "ATBench500", split="test")
```

## HINTBench

HINTBench（Horizon-agent Intrinsic Non-attack Trajectory Benchmark）面向良性、非攻击条件下，由 Agent 内部失效引起的长时程轨迹风险审计。

- [GitHub 仓库](https://github.com/Jiacheng-31/HINTBench)
- [数据目录](https://github.com/Jiacheng-31/HINTBench/tree/main/data)
- [arXiv 论文](https://arxiv.org/abs/2604.13954)
- [论文 PDF](https://arxiv.org/pdf/2604.13954)
- [OpenReview 论文](https://openreview.net/forum?id=YSCiOXc1ij)

HINTBench 的数据和评测代码目前可从官方 GitHub 仓库获取，数据位于仓库的 `data/` 目录。

## Scope

这些链接指向外部项目，不属于本仓库发布的 SeClaw-Bench 数据。有关本仓库数据集的结构和使用方式，请参阅 [SeClaw-Bench Dataset Guide](data.md)。
