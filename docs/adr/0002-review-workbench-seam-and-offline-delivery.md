# ADR-0002：冻结审阅工作台 seam 与离线交付方式

- 状态：部分被取代 — 离线静态交付约束见 [ADR-0004](0004-review-workbench-app-and-bundle-seam.md)；Review Bundle 展示缝仍有效并由 ADR-0004 重申
- 日期：2026-10-28
- 修订：2026-11-05

## 背景

赛马审阅工作台 v1 以专家逐样本审阅为主，同时要让评审者在几分钟内看懂批次优势；评分覆盖数据统计、可观察推理与风险溯源、工具调用以及多种轨迹格式集成。现有 Python renderer 直接理解 `run/1`、`trajectory/2`、`audit-result/3` 和 `metrics/2`，若 React 继续消费这些内部文件或重做语义关联，展示层会与审计实现紧耦合。

## 决策

Python 为每个不可变审计批次生成一个 `review-bundle/1` 展示投影：它包含批次摘要、逐样本状态与失败、规范化可观察事件、已关联的工具交互、指标及 availability、finding 与 Evidence Span、产物血缘，并保持 Source Label、Project Reference Annotation 和 Detected Finding 严格分层。Python 拥有事件规范化、工具关联、finding 定位、批次聚合和审计语义；React + TypeScript + Vite 工作台只负责筛选、排序、导航、展开与格式化，不重新推断风险或补造缺失指标。

v1 采用一个批次、一个 Detected Result 的审阅模型，不做课题四与 deterministic baseline 的横向比较；合同允许任何明确声明 `provider.id`、版本与配置修订的 provider 生成 bundle，正式交付只展示课题四结果。工作台统一展示可观察推理、工具调用、工具返回和状态变化，并将课题四提供的越权、低效或错误行为 finding 覆盖到对应事件；来源未提供 token 统计或 provider 未提供检测类型时明确显示 unavailable，不用估算值或 Source Label 代替。

交付物同时包含自包含 `index.html` 与内容一致的 `review-bundle.json`。HTML 内联构建后的 React、CSS 和完整 bundle，可通过 `file://` 双击运行，不使用 CDN、`fetch`、动态 import、外部字体或运行时网络请求；独立 JSON 用于 Schema 校验和自动测试，两者以 bundle 摘要证明一致。至少使用 OpenAI messages 与一种 benchmark adapter 完成端到端验收，以证明多个来源格式经统一轨迹进入同一 Review Bundle，而不是让前端按来源格式分支。

## 被否决的备选方案

- 让 React 直接消费四套内部 run 文件：会把内部 schema 和审计关联逻辑泄漏到展示层。
- 把完整 `trajectory/2` 原样内联：raw record、event raw 与 source raw 重复，增加单文件体积且暴露调试级结构。
- 依赖旁路 JSON 的运行时 `fetch`：在 `file://` 下不可靠，也破坏双击即用约束。
- 将 baseline 与课题四结果并排：赛马是多个项目组各自交付，不是本项目内的多 provider 对比。
