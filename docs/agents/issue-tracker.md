# Issue Tracker：GitHub

本仓库的需求与规格记录在 GitHub Issues 中。所有操作使用 `gh` CLI。

## 约定

- **创建 Issue**：`gh issue create --title "..." --body "..."`
- **读取 Issue**：`gh issue view <编号> --comments`，同时获取标签；需要筛选评论时使用 `jq`
- **列出 Issue**：使用 `gh issue list --state open --json number,title,body,labels,comments`，并根据需要添加 `--label`、`--state` 和 `--jq`
- **评论**：`gh issue comment <编号> --body "..."`
- **添加或移除标签**：`gh issue edit <编号> --add-label "..."` / `--remove-label "..."`
- **关闭**：`gh issue close <编号> --comment "..."`

在仓库克隆目录中运行时，由 `gh` 根据 `git remote -v` 自动确定 GitHub 仓库。

## 是否将 Pull Request 作为 triage 请求入口

**否。**

如需将外部 Pull Request 视为功能请求，可将此项改为“是”。`triage` skill 会读取该配置。

启用后，Pull Request 使用与 Issue 相同的标签和状态：

- **读取 PR**：`gh pr view <编号> --comments`
- **读取差异**：`gh pr diff <编号>`
- **评论、添加标签或关闭**：使用 `gh pr comment`、`gh pr edit` 和 `gh pr close`

GitHub 的 Issue 和 Pull Request 共用编号空间。遇到 `#42` 等引用时，先运行 `gh pr view 42`，失败后再运行 `gh issue view 42`。

## 当 skill 要求“发布到 issue tracker”时

创建一个 GitHub Issue。

## 当 skill 要求“获取相关 ticket”时

运行 `gh issue view <编号> --comments`。

## Wayfinding 操作

`wayfinder` 使用一个 Issue 作为地图，并使用子 Issue 记录任务。

- **地图**：创建带有 `wayfinder:map` 标签的 Issue，用于记录 Notes、Decisions-so-far 和 Fog
- **子任务**：优先使用 GitHub Sub-issues；不可用时，在地图正文中添加任务清单，并在子 Issue 顶部写入 `Part of #<地图编号>`
- **任务类型标签**：`wayfinder:research`、`wayfinder:prototype`、`wayfinder:grilling` 或 `wayfinder:task`
- **阻塞关系**：优先使用 GitHub 原生 Issue Dependencies；不可用时，在子 Issue 顶部使用 `Blocked by: #<编号>`
- **可执行任务**：从地图的开放子任务中排除仍有开放阻塞项或已有负责人者，按地图顺序选择第一个
- **领取任务**：`gh issue edit <编号> --add-assignee @me`
- **完成任务**：先评论答案，再关闭 Issue，最后在地图的 Decisions-so-far 中追加上下文链接
