# ADR-0004：审阅工作台改为可部署 API，展示缝仍为 Review Bundle

- 状态：已接受
- 日期：2026-11-05
- 取代：ADR-0002 中「仅静态离线包、禁止常驻服务 / 运行时 fetch」的交付约束；**保留** ADR-0002 中「React 只消费 Review Bundle、不重新推断风险」的 seam

## 背景

#11 方向调整为：用工程化 React 工作台接真实课题四算法，上传原始轨迹、运行审计并展示结果，以便打磨算法可见效果。1.2 评分表（统计 / 思维链 / 工具调用 / 多格式集成）仍约束信息架构，但本阶段不以盲评导出或 Project Reference Annotation 为硬验收。旧 ADR-0002 的 `file://` 自包含 HTML 交付与「上传 → 调 topic4-cli → 展示」产品形态冲突。

## 决策

1. **展示缝不变**：Python（或工作台 API 后端）将每次审计运行投影为 `review-bundle/1`。React + TypeScript 工作台只负责筛选、排序、导航、展开与格式化，不解析课题四私有结果形状，不补造缺失指标；字段缺失时按 Evidence Availability 显示未提供。Source Label 与 Detected Finding 仍分层；Project Reference Annotation 本阶段不作为硬依赖。
2. **交付形态改为可部署工作台**：提供小型 HTTP API（上传原始轨迹 → 调用 `trajectory_audit` + `topic4-cli` → 返回或落盘 Review Bundle）与 React 前端。本机与 176 部署同一套服务。工作台与课题四 Provider 同机；模型服务可在他机，经端口代理到达同机本地端口，由课题四访问——工作台不直连模型。
3. **第一刀来源格式**：至少 ATBench 与 HINTBench 两种原始轨迹经 adapter 进入同一 Review Bundle；OpenAI messages、受控执行（Docker）轨迹与浦江统一格式后续接入。前端不按数据集名称做布局分支。
4. **UI 参考**：`prototypes/review-workbench-v2` 仅作评分表三页签信息架构参考，不在该目录接真数据或「转正」为正式应用。
5. **明确后置**：盲评用静态导出 / 自包含 `index.html` 交卷包；不以 #9 人工参考标注为开工阻塞。

## 被否决的备选方案

- 在 `prototypes/review-workbench-v2` 内直接挂真实 audit-result：无法形成可部署产品，且绕过 Review Bundle seam。
- React 直接消费 run 包或课题四 `topic4-audit-result/1`：展示层与 provider 私有字段耦合，算法缺字段时易在前端偷偷补全。
- 维持 ADR-0002 纯静态离线包作为本阶段唯一交付：无法支撑「上传并真调算法」的打磨闭环。
- 本阶段就上多机鉴权与完整队列平台：过重；176 第一版同机 API + 端口代理即可。
