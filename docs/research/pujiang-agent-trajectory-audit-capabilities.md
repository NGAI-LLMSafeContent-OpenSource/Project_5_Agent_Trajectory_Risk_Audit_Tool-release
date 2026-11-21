# 浦江 Agent 轨迹风险审计赛题与既有能力调研

> 日期：2026-10-28  
> 目的：回答“为什么会这样出题、是否已有开源系统在建设类似能力”，并为 SeClaw 的 Q21/Q22 设计边界提供依据。  
> 证据范围：主办方公开页面、官方文档/源代码仓库、论文作者发布的论文与实现。本文把**已确认事实**、**推断**和**未知项**分开，不把 SeClaw 的产物当作行业标准。

## 结论摘要

1. **已确认：评分表的 30/30/30 三个主体维度，与成熟 Agent 可观测性/评测系统的能力高度重合。** token、延迟、状态、消息、可见 reasoning、工具名称/参数/返回/错误、调用树和风险/质量标注，已出现在 OpenTelemetry GenAI、OpenAI Agents tracing/trace grading、Phoenix、AgentCompass、PAIR Agent Trace Visualization 等官方规范或开源实现中。
2. **推断：这道题更像在考“把异构轨迹变成可审阅、可定位、可比较的证据”，不是在暗示某个唯一的浦江内部系统。** 90 分给统计、思维过程和工具调用的呈现，只有 10 分给格式接入；这与“先统一可观察事实，再做诊断与可视化”的行业路径一致。
3. **已确认：外部环境的最终状态在可执行 benchmark 中很重要，但不是普通轨迹可视化的通用必备字段。** AgentDojo、τ-bench 会检查环境前后状态或数据库状态；通用 tracing 规范则主要记录调用及其返回、错误、时延等。二者不能混为一层。
4. **未知：未找到该评分表对应的公开赛题原文、样例轨迹或格式规范，也没有公开证据证明它直接复刻浦江某个开源项目。** 浦江实验室公开页面能确认其方向包含大模型评测、安全评测和安全可信 AI，但不能证明具体命题动机。
5. **对 Q21/Q22 的建议：核心合同只依赖组织方最可能提供的标准轨迹事实；SeClaw 的 `audit_data` 和 `workspace` 仅作为可选、旁路的执行证据。** 当前不应为未知的组织方输入强制增加这两类字段，也不应先做通用 workspace 浏览/差异功能。

## 一、主办方背景：能确认什么，不能确认什么

### 已确认

浦江实验室公开招聘方向明确包含“大模型评测”“安全评测”和“安全可信 AI”；后者覆盖可信边界、认知和推理可信、模型训练与部署等方向。其技术愿景也明确包含“安全可信的通用人工智能”。这些信息说明，轨迹级风险审计与其公开研究方向相符。[浦江实验室：加入我们](https://www.pjlab.ac.cn/joinus)

### 推断

结合评分表，可合理推断命题方希望比较三个单位是否能把 Agent 的长链路行为转化为专家可复核的可视证据，而不只输出一个最终风险分数。原因是：

- “数据统计、思维链、工具调用”共占 90 分，强调的是审阅和诊断质量；
- “支持两种主流轨迹格式”只占 10 分，说明接入广度重要，但不是主体竞争点；
- “来源溯源、关键 token、越权/低效/错误调用”要求将检测结论回指到原始事件，而不是只做 dashboard 汇总。

这仍然是**基于评分结构的解释**，不是主办方公开确认的命题说明。

### 未知

- 未检索到该评分表的公开原文、官方 FAQ、样例数据或输入 schema；
- 不知道组织方轨迹是否包含原始 reasoning、reasoning summary、token 级 usage、时间戳、工具返回、失败状态或环境状态；
- 没有公开证据表明该题直接来自浦江某个具体开源系统；
- 因而不能把 SeClaw 的 `execution.json`、`audit_data.json`、workspace 目录结构反推为组织方合同。

## 二、四项评分与既有系统的对应关系

| 评分维度 | 一手资料中已有能力 | 判断 |
| --- | --- | --- |
| 数据统计 30 | OpenTelemetry GenAI 记录模型、输入/输出 token、完成原因、消息和工具事件；OpenAI tracing 和 Phoenix 记录时延、状态、输入输出、token/usage | **标准可观测性核心** |
| 思维链可视化 30 | PAIR 的开源 Agent Trace Visualization 分 conversation、thinking、tool、files 轨道，并支持 reasoning 图/线性对比；OpenAI API 对托管推理模型通常提供 reasoning summary 而非原始 CoT | **常见分析界面，但可见内容依 provider 而定** |
| 工具调用可视化 30 | OpenTelemetry 定义 `execute_tool` span 及 tool name/call id/arguments/result/error；OpenAI/Phoenix 提供工具 span、树状链路和错误诊断 | **标准可观测性核心** |
| 集成方案 10 | OpenTelemetry/OpenInference 类通用语义约定、AgentCompass 的 Model/Benchmark/Harness/Environment 解耦、PAIR 的 converter 机制均采用“适配器 + 统一内部表示” | **成熟工程做法** |

### 1. 数据统计不是临时“加戏”

OpenTelemetry 官方 GenAI 示例把一次 Agent run 表示为 `invoke_agent` 根 span、模型 `chat` span 和 `execute_tool` span，并直接展示输入/输出 token、finish reason、消息和工具调用。[OpenTelemetry GenAI observability](https://opentelemetry.io/blog/2026/genai-observability/) OpenAI Agents SDK 的 tracing 同样覆盖模型生成、工具调用、handoff、guardrail 和自定义事件；trace/span 自带层级和时序。[OpenAI Agents SDK tracing](https://openai.github.io/openai-agents-python/tracing/) Phoenix 的官方 tracing 文档把 LLM call、tool execution、retrieval、输入输出、延迟和 token usage 都作为基本观测对象。[Arize Phoenix tracing](https://arize.com/docs/phoenix/tracing)

因此，token 数、工具种类/次数、成功/失败、耗时、错误和步骤分布属于稳妥的核心层。需要注意：若来源没有 usage 或 timestamp，只能显示“不可用”，不能根据文本长度或事件顺序伪造精确值。

### 2. “思维链可视化”必须解释为“来源可见的推理内容”

PAIR-code 的开源 Agent Trace Visualization 已提供 conversation、agent internal processes、tool calls、files 多轨时间线，也提供对 reasoning trace 的图结构和 token 级线性对比；它通过 converter 接入不同来源并转为 OpenTraces JSON。[PAIR-code/agent-trace-vis](https://github.com/PAIR-code/agent-trace-vis)

但“所有模型都有完整原始 CoT”不是成立的前提。OpenAI 官方说明其托管推理模型不暴露原始 chain-of-thought token，而提供 reasoning summary；gpt-oss 等开放权重场景又可能提供 raw CoT，并明确建议不要直接向最终用户展示。[OpenAI reasoning summaries](https://developers.openai.com/cookbook/examples/responses_api/reasoning_items)、[OpenAI：处理 gpt-oss raw CoT](https://developers.openai.com/cookbook/articles/gpt-oss/handle-raw-cot)

所以 UI 和内部模型应区分：

- `reasoning_raw`：来源明确提供的原始推理；
- `reasoning_summary`：provider 给出的摘要；
- `assistant_text`：普通回答/计划文字；
- `unavailable`：来源不提供推理内容。

不能把系统自己生成的解释包装成“模型原始思维链”，也不能因为缺少 CoT 就让整条轨迹无法审计。风险来源链、工具调用和可回读证据仍可独立成立。

### 3. 工具调用可视化有较清晰的行业最小公分母

OpenTelemetry GenAI 的 `execute_tool` span 规定或建议记录 tool name、call id、类型、参数、结果和错误；参数与结果因敏感性和体积被设为 opt-in，而非永远必填。[OpenTelemetry GenAI spans](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md) OpenAI trace grading 进一步把整条执行链中的决策、工具调用和推理步骤作为可打分对象，用来发现错误和回归。[OpenAI trace grading](https://developers.openai.com/api/docs/guides/trace-grading)

对本项目而言，可稳定标准化的字段是：工具身份、参数、返回/错误、调用结果状态、起止时间（若有）、父子/前后关系、原始事件定位和来源格式。工具定义（名称、描述、参数 schema）是解释“参数是否错误、是否越权”的上下文，但也可能在来源中缺失，应当是可选而非硬前提。

### 4. 统一格式/适配器是已有做法，但不存在唯一格式

AgentCompass 把 Model、Benchmark、Harness 和 Environment 解耦，并记录 trajectory、tool calls、usage、latency，再用可插拔 analyzer 发现失败和异常；它支持多个 benchmark 和 agent harness。[open-compass/AgentCompass](https://github.com/open-compass/AgentCompass) PAIR 的可视化项目同样要求通过 converter 把不同 Agent 轨迹转换为统一 JSON。[PAIR converter 说明](https://github.com/PAIR-code/agent-trace-vis/tree/main/converters)

这支持项目当前“OpenAI messages adapter + 其他来源 adapter → 统一内部轨迹 → Topic 4 检测 → Topic 5 呈现”的方向，但不支持过早把任一 benchmark 私有字段提升为必填通用字段。

## 三、必须分开的两类证据

### A. 标准轨迹可观测性：Agent 看见、决定、调用和收到什么

典型内容是：

- messages / visible reasoning / tool definitions；
- tool call name、arguments、result、error；
- event/span 顺序、父子关系、时间、状态；
- token、cost、latency、finish reason；
- guardrail、handoff、grader annotation；
- 指向原始内容的稳定 locator。

这是评分表前三项都能直接消费的核心证据，也是 OpenTelemetry、OpenAI tracing、Phoenix、AgentCompass 等系统的共同部分。

### B. 环境副作用证据：外部世界最终真的发生了什么

典型内容是：

- mock 服务的服务器端审计日志；
- 数据库/邮箱/日历等状态的前后差异；
- workspace 文件树、文件 hash 或补丁；
- 容器、网络或策略引擎记录的执行/阻断事实。

它解决的是一个不同问题：轨迹里出现“拟调用”或“工具返回成功”，不一定等于副作用真实落地。AgentDojo 的任务实现会比较环境前后状态，例如验证文件是否被创建、修改或删除；其社区讨论也明确指出，被安全层阻断的拟调用可能没有任何真实环境变化。[AgentDojo workspace task source](https://github.com/ethz-spylab/agentdojo/blob/main/src/agentdojo/default_suites/v1/workspace/user_tasks.py)、[AgentDojo issue #189](https://github.com/ethz-spylab/agentdojo/issues/189) τ-bench 的环境实现则通过数据库状态 hash 与目标状态比较来评分。[τ-bench environment source](https://github.com/sierra-research/tau-bench/blob/main/tau_bench/envs/base.py)

由此得到的边界是：

- **环境副作用证据在可执行 benchmark 中很有价值**，特别适合判断动作是否真正发生、是否被阻断、是否产生隐藏副作用；
- **它不是任意离线轨迹都必然携带的标准字段**；OpenTelemetry 工具 span 只要求/建议记录调用语义与结果，并不要求导出整个外部系统的最终状态；
- 通用产品应允许这类证据作为 extension/sidecar 存在，而不是让缺失它的组织方轨迹导入失败。

## 四、SeClaw 能补什么，以及不能证明什么

SeClaw 是执行型安全 benchmark：论文强调在 stateful environment 中运行 Agent，用 trajectory-aware assessment 评估不安全动作，而非只看最终回答；Docker testbed 用于可复现实验。[SeClaw 论文](https://arxiv.org/abs/2606.02302)、[SeClaw 官方仓库](https://github.com/seclaw-eval/seclaw-eval)

因此，SeClaw 对项目最有价值的用途是：

1. 生成真实多步轨迹，覆盖工具成功、拒绝、超时、失败和重复调用；
2. 用环境/审计产物作为**评测 oracle 或旁路证据**，核验 Topic 4 是否把“拟调用”和“已执行副作用”混淆；
3. 用同一任务跨模型运行，检查检测结论、定位和 UI 是否稳定；
4. 为 provider 的回归测试提供可重复样本。

SeClaw 不能证明：

- 组织方会提供相同的 `audit_data` 或 workspace；
- 组织方轨迹包含 mock service 的服务器端日志；
- 组织方要求浏览完整文件系统；
- SeClaw 的 artifact 命名和目录结构应成为项目公开合同。

## 五、对 Q21/Q22 的具体设计建议

### Q21：是否把 `audit_data` 等喂给 Provider？

**建议：核心答案是“不作为必填 Provider 输入”；SeClaw 内部评测可作为可选 sidecar/oracle 使用。**

最小稳妥方案：

1. Provider 的必需输入只包含统一轨迹中的标准可观察事实：消息、可见 reasoning、工具调用/返回/错误、时间/usage（存在时）、工具定义（存在时）、原始定位信息。
2. `audit_data` 先保留在 Execution Corpus 的运行包中，由测试/评测层读取，用于验证 provider 的判断和定位。
3. 若 Topic 4 后续被证明确实需要环境事实才能完成某类检测，再定义来源无关的可选 `environment_observations[]`，而不是透传任意 SeClaw JSON。每条 observation 至少要有 `kind`、`subject`、`before/after` 或 `event`、`source`、`locator`、`availability`。
4. 没有 sidecar 时 provider 仍必须运行，并在 capability/limitation 中声明“无法确认真实副作用”，不能默认为未发生或已发生。

### Q22：是否归档 workspace、在 UI 中展示？

**建议：归档用于 SeClaw 可复现与评分；本轮不做通用 workspace UI。**

- Execution Corpus 可以保存 workspace snapshot/hash/diff，供 benchmark 重放、人工复核和回归测试；
- 通用 Review UI 只在某条 finding 已有明确 artifact locator 时提供“打开相关文件/补丁”的可选入口；
- 不先做文件浏览器、全量 workspace diff、文件风险扫描等功能，因为评分表没有证明它们是必需项，组织方数据也未确认携带这些内容；
- 若未来拿到样例轨迹并确认存在文件事件，可先把“file read/write/delete”标准化为工具/事件，再决定是否需要 snapshot 层。

### 推荐的分层边界

| 层 | 当前是否必做 | 说明 |
| --- | --- | --- |
| Provider Corpus | 是 | 组织方/公开来源轨迹，经 adapter 转成统一标准轨迹；不得依赖 SeClaw sidecar |
| Execution Corpus | 是，但规模可控 | SeClaw 等真实执行轨迹，用于打磨 Topic 4、回归和案例展示 |
| Environment Evidence Extension | 可选、先内部 | `audit_data`、DB diff、workspace diff 等；缺失不影响导入和基础审计 |
| 通用 Workspace 产品功能 | 暂缓 | 等组织方样例或明确需求证明价值后再做 |

## 六、在未知输入下仍值得做的“低后悔”功能

1. adapter 诊断：哪些字段成功映射、哪些缺失、哪些原样保留；
2. capability/availability：reasoning、usage、timestamp、tool result、tool definitions 是否可用；
3. 原文忠实：统一事件保留 source locator，finding 可回跳到原始事件；
4. 工具调用时间线：名称、参数、返回、错误、重试、耗时和父子关系；
5. 统计只基于真实可用字段，缺失明确显示 unknown/unavailable；
6. Topic 4 finding 与可观察事实分层，绝不把推断写成源轨迹事实；
7. source-specific extras 可无损保留，但不进入必需语义合同；
8. 测试集覆盖“字段存在/缺失/格式异常/重复调用/阻断后无副作用”等情况。

## 七、需要尽快向组织方确认的最小问题

在投入 workspace、token 级高亮或环境证据 UI 前，应索取 2–3 条脱敏样例和 schema，并只问这些决定架构的问题：

1. 轨迹是消息序列、span/event 树还是自定义 JSONL？是否允许自带 adapter？
2. reasoning 是 raw、summary、普通 assistant 文本，还是完全没有？
3. tool call 是否包含 definition、arguments、result、error、status 和 timestamp？
4. token usage 是逐调用还是整条汇总？“关键 token”由参赛方算法生成还是源数据提供标注？
5. 是否存在服务端审计日志、环境状态、workspace/文件变更等旁路证据？
6. 专家评分更看重“功能数量”，还是定位准确性、证据忠实度和缺失字段处理？

在这些答案到来前，最稳妥的路线不是猜更多字段，而是把“标准轨迹核心 + 可选扩展 + 明确不可用状态”做扎实。

## 最终判断

这道题的主体并非无依据的功能堆砌：统计、推理过程、工具交互、来源追溯和适配器，确实对应正在形成的 Agent tracing、observability 和 evaluation 工程范式。国内外开源项目已经实现了相似的时间线、span 树、token/延迟统计、工具调用检查和轨迹分析。

但从“这些能力是常见做法”不能跳到“组织方一定提供 SeClaw 风格 execution/audit/workspace”。对赛马竞争力最有利的补法，是用 SeClaw 建立高质量 Execution Corpus 和内部 oracle，同时保持 Provider Corpus 与 UI 对来源无关、对缺失字段诚实、对可选环境证据可渐进扩展。这样即使组织方真实轨迹与当前假设差异很大，已完成的 adapter、证据定位、availability、工具时间线和风险审阅能力仍然可复用。
