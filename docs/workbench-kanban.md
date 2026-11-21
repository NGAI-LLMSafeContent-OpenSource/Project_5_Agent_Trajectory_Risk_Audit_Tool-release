# Review Workbench 任务看板（本地）

- 规格：[#26](https://github.com/NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/issues/26)
- 方向票：[#11](https://github.com/NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/issues/11)
- 更新：人手改本文件状态；GitHub Issue 仍是权威验收清单

## 怎么读

- **可开工**：没有未完成的阻塞，或阻塞已全部进「完成」
- **进行中 / 评审 / 完成**：按实际挪动
- **#27 合完后**，#28、#29、#30 可以并行

## 可开工

- [ ] [#28](https://github.com/NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/issues/28) HINTBench 同一套 API/UI — #27 已完成
- [ ] [#30](https://github.com/NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/issues/30) 真实 topic4-cli — #27 已完成
- [ ] [#31](https://github.com/NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/issues/31) 176 同机部署 — 等 #30
### UI 改版（规格 [#33](https://github.com/NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/issues/33)）后续

- [ ] [#41](https://github.com/NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/issues/41) 8/8 截图对照人工验收 — 前置票已全部完成，待人工验收

## 进行中

（空）

## 阻塞中

（空）

## 评审

（空）

## 完成

- [x] [#27](https://github.com/NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/issues/27) ATBench 最小闭环（假 topic4 → 审阅包 → React 三页签）
- [x] [#29](https://github.com/NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/issues/29) Finding 跳转 + 工具展开（`8bbdb68`）
- [x] [#34](https://github.com/NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/issues/34) UI 改版 1/8：设计变量、应用骨架与空状态上传卡片（`ef4eeb3`、`f92da71`，本地未推送）
- [x] [#35](https://github.com/NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/issues/35) UI 改版 2/8：上传面板收起 + 顶栏弹层（`1f2854d`、`3142283`，本地未推送）
- [x] [#36](https://github.com/NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/issues/36) UI 改版 3/8：左侧样本栏（`a02753a`、`fed0f2b`，本地未推送）
- [x] [#37](https://github.com/NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/issues/37) UI 改版 4/8：样本头部卡片分层（`cae5c40`、`562722f`，本地未推送）
- [x] [#38](https://github.com/NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/issues/38) UI 改版 5/8：页签框架、未提供的维度汇总条与数据统计页签（`b078608`、`b6b4cc9`，本地未推送）
- [x] [#39](https://github.com/NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/issues/39) UI 改版 6/8：思维链页签改版（`e70508e`、`1bfee37`，本地未推送）
- [x] [#40](https://github.com/NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/issues/40) UI 改版 7/8：工具调用页签改版（`2f42786`、`dd382a5`，本地未推送）

## 依赖简图

```text
#27 最小闭环
 ├── #28 HINTBench        ⎫
 ├── #29 Finding/工具展开  ⎬ 可并行（#27 完成后）
 └── #30 真 topic4-cli    ⎭
       └── #31 176 部署

#34 UI 骨架
 ├── #35 上传弹层     ⎫
 ├── #36 样本栏       ⎬ 可并行
 ├── #37 头部卡片     ⎭
 └── #38 页签框架 + 汇总条
       ├── #39 思维链
       └── #40 工具调用
#35 #36 #37 #39 #40 ──► #41 人工验收
```

## 本阶段不做（备忘）

- #9 人工 reference 对照
- 盲评静态导出
- 在 `prototypes/review-workbench-v2` 接真数据（只作 UI 1:1 参考，正式用 React）
- 待另立 spec：**测试集总览页**与**原始轨迹抽屉**。UI 改版（视觉 + 布局）不含这两项，因为都卡在数据缺口：Review Bundle 是单样本投影，没有批次聚合（ADR-0002 规定聚合归 Python）；bundle 不内联原始记录（ADR-0002 否决）。需先定后端投影再做前端
