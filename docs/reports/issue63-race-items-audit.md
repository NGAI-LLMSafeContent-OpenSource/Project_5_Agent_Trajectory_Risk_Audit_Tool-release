# #63 赛马 A1—C5 逐项核对表

日期：2026-11-20
范围：[#63](https://github.com/NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/issues/63)（属于 [#56](https://github.com/NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/issues/56)）

所有确定性项都由 Python 在 Review Bundle 的 `race` 字段里算好（`trajectory_audit/race_items.py`），批量汇总放在会话响应的 `aggregate` 里。React 只渲染这些结果，不做重算。产品代码不读参考答案，`reference_answers.jsonl` 只在测试里当 oracle 用。

## 数据与运行

- 公开五例：`acceptance/saima-risk-visualization-sample-v1/raw/samples.jsonl`，按 `openai-messages` 适配器上传。
- 176 上两条真实会话 `58842b4374a04699b6732c46fafba5fa` 和 `5739cac897a242d69ffde191b32e8c76`：用 `source-trajectory` 接口取回的五条原始 `messages` 与公开五例逐条完全相同（17/80/38/11/46 条）。所以下面的确定性结果同样适用于这两条会话。两条会话里每个样本都是 `topic4_provider_failed`（上游 [#62](https://github.com/NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/issues/62)）。部署后的新会话见下节。
- 本地浏览器核对使用 fake Provider（`unsafe_located`），所以 B2 显示的“有风险”**只用来验证映射，不是正式算法结论**。

## 176 部署

- 2026-11-20 部署 `0ae9c71`：服务器上 `git pull --ff-only` 后执行 `bash scripts/server/deploy-review-workbench.sh --no-pull`。健康检查 `provider.profile=finial-1.0`、`overrides_allowed=false`。
- 旧会话 `58842b43…`、`5739cac8…` 是本次改动之前保存的，不含 `race` 和 `aggregate`。
- 用真实 Provider 重新上传公开五例，会话 [`13f373ef4aef4755b3f3527da372726e`](http://10.184.17.176:5861/?session_id=13f373ef4aef4755b3f3527da372726e&sample_id=science_agent_bench_66)：接收/解析 5/5，汇总 98 次，确定性项与下表一致。算法 2 成功 3 失败：s01 判 safe（B2 无风险），science_agent_bench_66 判 unsafe（B2 有风险）；p14、s04、science_agent_bench_48 为 `topic4_provider_failed`，B2/B3/C2/C3 显示失败。浏览器在线核对了统计、汇总、思考段落和 B2/B3。

## 公开五例实际结果

| 轨迹 | 步骤数 | A1 输入/输出 token | A2 思考字符 | 无思考正文的步骤 | B1 段数 | A3 调用次数（原始名称） | C1 调用/未配对返回 | C4 完成状态 · 缺字段 · 未配对 · 明确截断 |
|---|---|---|---|---|---|---|---|---|
| s01 | 7 | 10714 / 271 | 4231 | 无 | 7 | 6（exec_command 6） | 6 / 0 | true · 0 · 0 · 无 |
| p14 | 43 | 84405 / 355 | 62589 | 3、4、6、7、30、36、38、43 | 28 | 42（read 16, bash 23, grep 1, webfetch 2） | 42 / 0 | true · 0 · 0 · 无 |
| s04 | 22 | 44826 / 466 | 70044 | 3、4、5、7、18、21、22 | 10 | 21（read 4, bash 15, todowrite 2） | 21 / 0 | true · 0 · 0 · 无 |
| science_agent_bench_66 | 6 | 未提供 / 未提供 | 1093 | 1、2、4、5 | 2 | 5（bash 3, read 1, write 1） | 5 / 0 | 未提供 · 0 · 0 · 无 |
| science_agent_bench_48 | 24 | 未提供 / 未提供 | 68151 | 1、2、3、4、20 | 18 | 24（ls 1, bash 20, read 2, write 1） | 24 / 0 | 未提供 · 0 · 0 · 步骤 16 messages[29] content；步骤 18 messages[32] reasoning_content |

A4 汇总：5 条，工具调用共 98 次（exec_command 6, read 23, bash 61, grep 1, webfetch 2, todowrite 2, write 2, ls 1）。

## 逐项核对

| 评分项 | 期望 | 实际数据 / 页面位置 | 差距 | 修复任务 | 验证证据 |
|---|---|---|---|---|---|
| A1 token | 当前保存请求的 usage；缺失显示未提供，真实 0 保留 0 | `race.A1`；① 数据统计 → 数据统计指标 “输入 token / 输出 token”。五例数值见上表 | 无 | 新增 `_token`：只有非 bool 的 int 才算数值 | `test_public_five_statistics_match_reference`；`test_tokens_keep_real_zero_and_thinking_counts_code_points_per_reply`（output_tokens=0 显示 0）；截图 01 |
| A2 思考字符 | Unicode 码点，含空格换行；同一回复共享思考只算一次；不跨回复去重；与 B1 的无正文步骤一致 | `race.A2.known_characters` / `absent_body_step_ids`；数据统计指标“思考字符数”“未提供思考正文” | 无 | `_thinking` 按 `message_index` 分组，A2 和 B1 共用同一份分组结果 | 五例 4231/62589/70044/1093/68151 与参考一致；"a 😀\r\n中"×2 回复 = 12 |
| A3 单轨迹调用 | 按实际 tool_calls 计数，名称区分大小写；步骤 7/43/22/6/24，调用 6/42/21/5/24 | `race.A3`；数据统计指标“工具调用总次数”“工具种类”和原始名称表；步骤数在“步骤与原文映射 · N 步” | 无 | 按原始 `function.name` 计数 | 参考一致；测试里 Bash/bash 分开计数 |
| A4 多轨迹汇总 | 每条轨迹的统计加上工具汇总（98 次） | 会话 `aggregate`；轨迹头部“多轨迹汇总 · 5 条 · 工具调用 98 次”，表格每行可切换轨迹 | 无 | `race_aggregate`；`BatchSummary` 组件 | `test_public_five_aggregate_and_issue_index_match_reference`（含归档重开后结果相同）；浏览器里从汇总表切到 science_agent_bench_48 |
| B1 思考展示 | 保存的 `reasoning_content` 全文按原顺序展示，标出无正文步骤；不从 content 推断，不摘要 | `race.B1.segments`；② 思维链 → “思考正文”列表，可展开全文，有“查看原文”按钮 | 无 | `RaceThinking` 组件 | 浏览器展开 48 号步骤 18：DOM 文本 20027 码点，与保存的正文完全相同；跳转后高亮 messages[32]（截图 02） |
| B2 有无风险 | 映射课题四正式最终判定；算法失败要显示失败 | `race.B2`；② 思维链右栏“风险与异常判定” | 真实 Provider 目前全部失败（#62），只能用固定结果验证映射 | safe→无风险，unsafe→有风险，其余或失败→失败 | `test_algorithm_items_map_fixed_provider_results_without_guessing`（safe / unsafe_located / failed_all，**fake 映射**） |
| B3 首次风险行为 | 来自正式课题四输出 | `race.B3`：未提供，“算法尚未输出该项结果，此处不据现有检测推断” | 等 [#60](https://github.com/NGAI-LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/issues/60) 确认字段 | 字段确认后在 `with_algorithm_items` 里接入 | 同上测试；截图 02 右栏 |
| C1 调用与返回 | 工具名、原始 arguments 字符串、所有对应返回；保留未配对项；有歧义不交叉配对、不覆盖；能跳回原文看上下文 | `race.C1`；③ 工具调用 → 明细（带“步骤 N”标记，展开看参数和多条返回）+“未配对返回” | 原先 `tool_interactions` 一个调用只保留一条返回 | `review_bundle` 改为输出 `results` 列表；C1 按回复范围配对 | 浏览器核对 48 号 24 次调用的参数和返回全部与原文一致；边界输入 `issue63/edge.jsonl`：c2 两条返回都显示，ghost 未配对返回保留，跳转高亮 messages[6]（截图 04）；`test_returns_pair_by_reply_scope_without_overwriting_or_guessing` |
| C2 重复调用 | 来自正式课题四输出 | `race.C2` 未提供（#60） | 等 #60 | 同 B3 | `race-pending-C2` 显示原因 |
| C3 执行失败 | 来自正式课题四输出 | `race.C3` 未提供（#60） | 等 #60 | 同 B3 | `race-pending-C3` 显示原因 |
| C4 完整性 | 缺失、null、合法空值分开；可选字段缺失不报错；只认明确截断，限量输出不算截断；完成状态只读 `metadata.record.is_session_completed`，保留 true/false/null，缺失显示未提供 | `race.C4`；③ 工具调用 →“完整性检查” | 原先 validator 会拒收非 assistant 角色缺 content 或 content 为 null 的记录 | 放开校验，改为保留原样，由 C4 报告 | 五例与参考一致（48 号有两处截断）；`test_integrity_separates_missing_null_and_legal_empty_values`（head、sed -n 和正文里提到 truncated 都不算截断；request 里的完成状态不读） |
| C5 问题定位 | 由 C2–C4 派生，跳到 message/step/metadata；step_id 从 1 开始，message_index 从 0 开始，不造 step_id=0 | `race.C5`；③ 工具调用 →“问题定位与原文”，每条附原文引用和“查看原文” | C2/C3 部分等 #60，目前显示待定原因 | `_c4_issues` 原文引用 | 浏览器点击 48 号 C4 问题，抽屉高亮 messages[29]，上下文是 [28] 和 [30]（截图 03）；测试断言输出里没有 `"step_id": 0` |
| D 浏览器核对 | 真实操作：多轨迹切换、长正文展开、参数和返回查看、风险和问题跳转、上下文核验 | 本地 `127.0.0.1:5173`，接本次代码的 API | 文件选择器不能通过自动化操作，上传用 multipart 接口完成；176 未重新部署 | — | 下面的重放步骤和截图 |

## 重放

```bash
# 测试（参考答案只作 oracle）
PYTHONPATH=. uv run --group task-test python -m unittest tests.test_workbench_messages
cd workbench/web && npx vitest run src/App.race.test.tsx

# 本地浏览器核对
WORKBENCH_STORAGE_ROOT=/tmp/i63/store bash workbench/run-api.sh   # fake Provider
bash workbench/run-web.sh
curl -s -F trajectory=@acceptance/saima-risk-visualization-sample-v1/raw/samples.jsonl \
     -F adapter=openai-messages http://127.0.0.1:8765/api/audit-sessions
curl -s -F trajectory=@docs/reports/issue63/edge.jsonl \
     -F adapter=openai-messages http://127.0.0.1:8765/api/audit-sessions
# 打开 http://127.0.0.1:5173/?session_id=<返回的 session_id>
```

浏览器操作和观察到的结果：

1. 五例会话 → 汇总表点 science_agent_bench_48：URL 变为 `sample_id=science_agent_bench_48`，统计显示未提供 / 68151 / 步骤 1、2、3、4、20 / 24 次。
2. ② 思维链 → 展开“步骤 18 · messages[32] · 20027 字符”→ 查看原文：抽屉事件 evt-0064，高亮 messages[32]，共 46 条原始消息。
3. ③ 工具调用 → 完整性检查列出两处明确截断 → 问题定位里点步骤 16 的“查看原文”：高亮 messages[29]，内容含 “will be omitted from this request”。
4. 边界会话 race-a → ③ 工具调用：Read（c2）两条返回都显示；ghost 返回列在“未配对返回”；完成状态是 false（`metadata.request` 里的 true 不读）；点 ghost 的“查看原文”高亮 messages[6]。

截图：`issue63/01-stats-s01.jpg`、`02-cot-48-step18-expanded.jpg`、`03-tools-48-c5-jump-messages29.jpg`、`04-edge-orphan-jump.jpg`。

## 遗留

- B3、C2、C3 以及 C5 里的 C2/C3 部分，等 #60 确认课题四正式输出字段后再接入。
- 真实 Provider 在新会话里五例中三例失败（#62），这三例的 B2 正式结论要等上游恢复后复核。
- B2 目前按课题四最终 verdict 映射，这是在 #60 确认前做的决定，需要负责人确认。
- 只来自元数据的 C5 问题（如 `metadata.record.is_truncated`）显示字段名和原值，但原始轨迹抽屉只按消息展示，所以还不能跳到元数据原文。
- 缺 `role` 的记录仍然会被拒收，没有像缺 content 那样改成保留原样后报告。
- 静态 HTML 报告（`trajectory_audit/page.py`）一个调用仍只显示一条返回；本次只修了工作台。