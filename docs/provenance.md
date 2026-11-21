# 上游来源、许可证归属与受控同步

本仓库是 SeClaw 的私有派生工程，用于课题五的 Agent 轨迹风险审计最小原型。本文件记录固定上游起点、许可证与归属，以及 `origin` / `upstream` 的受控同步约定。

## 固定上游起点

| 项 | 记录 |
| --- | --- |
| 上游项目 | SeClaw（SpecSynth-ClawBench / SeClaw-Bench） |
| 上游仓库 | https://github.com/seclaw-eval/seclaw-eval |
| 固定起点提交 | `a3722ca6a754585c26a41e7b9f076fce0a19340f`（`Add SeClaw project page`，2026-07-13） |
| 派生仓库 | `NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool`（Private，Team4-平台与应用验证） |
| 导入方式 | 组织私有仓库 mirror/import，不使用公开仓库的 GitHub fork |
| 导入日期 | 2026-10-23 |
| 许可证 | Apache License 2.0，见 [`LICENSE`](../LICENSE) |
| 归属 | 见 [`NOTICE`](../NOTICE)；SeClaw 论文引用保留在 README |

起点提交在当前 `main` 历史中可核对：

```bash
git merge-base --is-ancestor a3722ca6a754585c26a41e7b9f076fce0a19340f HEAD && echo "baseline ok"
git log -1 --format="%H %ad %s" --date=short a3722ca6a754585c26a41e7b9f076fce0a19340f
```

若未来需要更换固定起点，必须新增一条记录并说明原因，不得静默改写本表。

## 为什么不使用 GitHub fork

GitHub 公开仓库的 fork 不能单独改为私有，而赛前材料需要保密。因此采用组织内新建私有仓库、再通过 mirror/import 引入 SeClaw 的方式：

- 私有仓库只需要满足上游 Apache-2.0 的许可证与归属要求，不需要公开派生关系。
- 上游更新通过人工评估后合并，而不是由 fork 同步机制自动拉取。

## 远程约定

| 远程 | 地址 | 用途 | 权限 |
| --- | --- | --- | --- |
| `origin` | 组织私有仓库 `NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool` | 唯一推送目标 | 读写 |
| `upstream` | `https://github.com/seclaw-eval/seclaw-eval` | 只读，用于人工评估上游更新 | 只读 |

首次需要评估上游更新时注册只读远程：

```bash
git remote add upstream https://github.com/seclaw-eval/seclaw-eval.git
git remote -v
```

约定：

- 只向 `origin` 推送；`upstream` 不配置 push URL，也不作为 `main` 的上游跟踪分支。
- 所有改动经 Pull Request 合并，评审通过后再合入 `main`，不直接向 `main` 提交。
- 当前仓库为 Private + GitHub Free 计划，平台不提供分支保护（保护规则 API 返回 403 `Upgrade to GitHub Pro or make this repository public`）。因此上一条是团队约定，靠评审流程而非平台强制；升级到支持保护的计划后应立即启用，并在此处更新说明。
- 禁止 `git push origin --mirror`、禁止在 `main` 上直接 `git merge upstream/main` 后强推、禁止自动定时同步。

## 受控同步流程

1. 在独立分支上获取上游并阅读差异：

   ```bash
   git fetch upstream
   git log --oneline --no-merges upstream/main ^HEAD
   git diff --stat HEAD upstream/main
   ```

2. 人工评估是否需要同步，重点判断：
   - 是否影响 `tasks/`、`benchmark/`、`scripts/` 等被复用的 SeClaw 骨架；
   - 是否与课题五新增的 `trajectory_audit/`、`fixtures/`、审计文档和测试冲突；
   - 是否改变许可证、NOTICE 归属或数据集版本。

3. 只把评估通过的改动放入 PR。PR 描述记录上游提交 SHA、差异摘要、验证命令与结果。
4. 合并后在下表追加记录。
5. 若上游许可证或归属发生变化，暂停同步并先处理合规问题。

## 已评估上游提交

| 评估日期 | 上游提交 | 处理 | 说明 |
| --- | --- | --- | --- |
| 2026-10-23 | `a3722ca6a754585c26a41e7b9f076fce0a19340f` | 作为派生起点导入 | 该提交是当前 `main` 的祖先；导入后课题五改动位于其之后 |

## 许可证与归属要求

- 保留 [`LICENSE`](../LICENSE)（Apache License 2.0）与 [`NOTICE`](../NOTICE)，不改写上游归属段落。
- 新增源文件使用 `SPDX-License-Identifier: Apache-2.0` 标识。
- 本工程对外交付或演示时，说明其派生自 SeClaw，并保留上游论文与数据集引用。
- 删除或替换上游文件时，在 PR 中说明理由并保留归属说明。
