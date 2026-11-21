# 课题四真实 toolchain 输出：本地源码与历史运行核对

日期：2026-11-19。本文只读核对本地课题四源码及已有运行档，不执行模型，不登录服务器。176 本次五例的新运行由主任务单独记录；本文不构成新运行成功证据。

## 核对基线

- 相邻仓库：`D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk`，当前 HEAD `8f881430aa1a5115622402cbcb2a4b10cbc1185e`。HEAD 仅标识仓库提交；本文未据此声称工作区与远端一致。
- 实现目录：该仓库 `课题五/finial/topic4_audit/`。
- 历史运行：该仓库 `docs/evidence/full80-issue8/full80-pair-RxPsnCwo/round-2/full80-0r3mqwq7/result.json`。其记录 `provider.implementation=topic4-toolchain-integrated`、80 项、`status=complete`、开始日期 2026-11-05。18 safe、62 unsafe。该产物属于旧 80 条开发输入，不是本次赛马五例。

## 主要结论

真实实现已经在主结果文件返回 verdict、findings、事件引用和精确 Evidence；不用为获得这些信息先实现假 Provider。现有输出能够承载普通风险发现，但尚没有显式“首次风险行为”字段，也没有独立、完整的 C2 调用对和 C3 错误类型列表。对五例实际可复用多少，应以新运行结果核对，不能仅依据字段名断言。

| 赛题要求 | 已有主结果字段 | 可复用与缺口 |
| --- | --- | --- |
| B2 有无风险 | `items[].result.verdict` | 可读取 safe/unsafe；undetermined 不能直接转换成“无风险”。此外现有 findings 包含低效执行，非纯安全风险，需核对赛题 B2 口径。 |
| B3 首次风险行为 | `findings[].origin_event_ids/affected_event_ids/evidence/reason` | 已有行为与来源事件及原文；没有明确选定的 first-risk 事件。不能把 origin 最小值当作首次行为，也不能根据 findings 数组顺序声称首次。 |
| C2 冗余调用 | `findings[].category=inefficient_execution`；规则机制 `duplicate-successful-call` | 有冗余识别能力，但通用语义发现的 origin 可为返回/约束，affected 可多个；不存在专用 `previous_call_event_id`。不能将所有 inefficient_execution 机械转换为某一调用对。 |
| C3 执行失败 | `checks.facts[]` 中 `kind=tool_failure`、`event_id`（返回事件）、`call_event_id` | 已有失败候选定位，但 fact 没有 error_type 或 evidence。检测规则对错误形态的覆盖有限，不等于已满足赛题排除条件和完整失败清单。 |

## 主结果的生成与语义

1. [runner.py](../../../Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/runner.py#L58) 的 `run`：先执行 supplemental `analyze` 和 `structured_failures`，再合入 semantic findings，最后合入三方 toolchain。第 104 行使用“已有任意 findings 则 unsafe，否则沿用语义 verdict”。
2. [semantic.py](../../../Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/semantic.py#L54) 的模型输出约定是 verdict/summary/findings；每条 finding 有 category、origin_event_ids、affected_event_ids、证据及理由，无首次行为字段。第 123 行起 `normalize` 校验事件并生成 Evidence；Evidence 含 event_id、field、source_pointer、excerpt、start、end 和 granularity。
3. semantic 支持 `exact-quote` 和 `event-context`。后者将相关事件的文本字段全文附为上下文证据，并非模型选出的精细行为片段；主输出 evidence 并不总可不加选择地作为最终 risk_quote。
4. [toolchain.py](../../../Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/toolchain.py#L259) 的 `merge` 将三方结果放进 `checks.integrations`，结构、来源内容等级及复核信息放进 `checks.toolchain`。内容分类和价值低分并不独立直接变成行为风险。

## C2 的现有能力边界

[engine.py](../../../Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/engine.py#L120) 的 supplemental 规则默认 `repeat_threshold=3`，配置校验不允许小于 3。按工具名和解码参数签名找重复，累计到阈值时检查此前有成功返回且无插入用户消息，产生 `inefficient_execution / duplicate-successful-call`；origin 是首个相同调用，affected 是后续相同调用。该规则不能据此承诺识别样例 s04 的两次调用冗余，也未完整实现必要复核、状态变化等排除条件。semantic 提示词会识别有害冗余并排除失败重试及用户要求的重复，但输出仍是通用 finding。

历史 80 条产物实际含 3 条 `inefficient_execution`。检查到一条语义发现，其 origin 是失败返回事件，affected 有两个工具调用，说明通用 origin/affected 不保证就是 C2 所需的“前一次调用/当前调用”二元关系。

## C3 的现有能力边界

[engine.py](../../../Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/engine.py#L56) 的 `failure_status` 匹配明确 declared_status（failed/failure/error/timeout/denied），或返回文本中的 JSON 状态/非空 error。它不会仅凭任意普通文本中的 timeout/error 字样就认定失败，因而是否覆盖赛题的 shell 退出码、内部命令超时需新运行验证。

第 152 行把失败记录成 `checks.facts`，形状为：

```json
{"kind":"tool_failure","event_id":"evt-0006","call_event_id":"evt-0005","is_finding":false}
```

历史产物有 24 条此类 facts。它们是工具失败事实，不自动代表安全风险。

[supplemental.py](../../../Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/supplemental.py#L8) 会将部分 JSON 返回里的权限拒绝、无效参数提升为风险 finding，但这不是完整 C3 失败分类器，不能用它替代所有 C3 输出。

## 主结果与 sidecar 的区别

[cli.py](../../../Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/cli.py#L52) 默认产生三份文件：

- `--output`：`topic4-audit-result/1` 主结果，成功项 result 实际只有 verdict/checks/findings 三类字段；历史产物没有 race_audit 扩展。
- `.visualization.json`：图节点、调用返回关联、token 线索、发现链；`tool_interactions[].results[].observed_failure` 位于此文件。生成逻辑见 [engine.py 第 187 行](../../../Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/engine.py#L187)。
- `.toolchain.json`：运行预检和主结果 checks 的诊断投影，不是另一套赛题算法答案。

现有 ADR-0003 不允许课题五依赖 sidecar 作为正式集成接口。如果确实需要 sidecar 中已有信息，应先判断主结果是否已有同等字段；必要时将选定结构纳入公共结果合同，不能直接绕过当前接口边界。

## 对后续集成的建议

先看 176 本次五例真实主结果，复用 verdict、findings 和 Evidence。重点核对三处：首次行为是否能无歧义获得、冗余是否明确到调用对、失败是否覆盖且有准确错误类型。若不足，再最小化补充公共字段；无需预先重做整个输出外层或为假 Provider 建立独立交付里程碑。
