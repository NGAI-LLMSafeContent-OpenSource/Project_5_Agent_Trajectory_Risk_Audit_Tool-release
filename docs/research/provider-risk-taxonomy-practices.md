# Provider 风险分类与轨迹标注实践调研

> 日期：2026-10-26  
> 问题：Topic 4 Provider v1 是否必须输出一套正式、版本化的风险 taxonomy，还是可以先只输出 verdict、finding、事件定位与证据？

## 结论

目前没有一套可直接作为本项目统一标准的行业级 Agent 风险 taxonomy。权威框架和轨迹 benchmark 使用的分类目标不同：NIST 管理风险，MITRE/OWASP 描述威胁，ATBench 诊断风险来源、失败模式与现实伤害，HINTBench 定位内生错误机制。因此，不宜在 Provider v1 输入中要求 `requested_taxonomy`，也不宜在双方尚无分类规则与标注指南时强迫 Topic 4 输出一个声称统一的 taxonomy。

但完全没有机器可分组的检测类型，会削弱赛马方案明确要求的“风险类别分布”和“危险思维模式”展示，也使不同运行无法稳定比较。建议 v1 采用渐进式做法：

1. 输入不包含 `requested_taxonomy`。
2. `verdict`、finding、origin/affected event、evidence 和 reason 是 v1 必需输出。
3. Provider 可先输出可选的、provider-native `finding_type` / `mechanisms`；这些值只在同一 provider 实现版本内解释，不冒充项目统一风险 taxonomy。
4. Topic 5 可以展示来源类别分布与 provider-native 检测类型分布，但必须分层命名，不能把 Source Label 当作 Detected Finding。
5. 等双方形成分类定义、消歧顺序、标注指南和映射验证后，再引入正式 `taxonomy.id/version` 与必填 `categories`。

如果赛马现场必须对未知、无来源标签的组织方轨迹展示“检测风险类别分布”，则至少需要 provider 输出稳定的 `finding_type`；只有自由文本 reason 时无法诚实地产生这一分布。

## 一手资料发现

### 1. NIST：风险不是单一类别，分类会随证据演进

NIST 的 Generative AI Profile 将风险定义为事件发生可能性与后果幅度的组合，并指出风险会因生命周期阶段、系统范围和来源等多个维度变化；文件还明确未来修订会随经验性证据增加风险和建议措施。这支持“风险类型、严重性和证据确定性应分开”，不支持假定存在一套冻结的通用 Agent 分类。[NIST AI 600-1, pp. 5–6](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)

NIST 的对抗机器学习 taxonomy 旨在建立攻击术语的共同语言，按攻击生命周期、目标、能力和知识组织；它是安全攻击分类，不等于行为结果、现实伤害或审计严重性分类。[NIST AI 100-2e2025](https://www.nist.gov/publications/adversarial-machine-learning-taxonomy-and-terminology-attacks-and-mitigations-0)

### 2. MITRE ATLAS 与 OWASP：适合描述威胁机制，不是轨迹裁决标签的唯一标准

MITRE ATLAS 是基于真实攻击观察和红队演示的、持续演化的 AI 对抗战术与技术知识库，并明确以 tactics、techniques、mitigations 和 case studies 组织内容。这类标识很适合作为 mechanism 或外部 crosswalk，但不能直接替代“这条轨迹是否 unsafe、风险发生在哪一步”的项目裁决。[MITRE ATLAS](https://atlas.mitre.org/)

OWASP Top 10 for Agentic Applications 是面向 Agent 应用安全的优先风险清单，目标是提供可操作的防护起点。OWASP 的 Agentic Threats Navigator 又按 reasoning、memory、tools、identity、human oversight 和 multi-agent 等攻击面组织威胁。两者说明行业会按用途维护多个分类视角，而不是共享唯一标签空间。[OWASP Agentic Top 10](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)、[OWASP Agentic Threats Navigator](https://genai.owasp.org/resource/owasp-gen-ai-security-project-agentic-threats-navigator/)

### 3. ATBench：把检测和三维诊断分开

ATBench 以 `risk source`、`failure mode`、`real-world harm` 三个维度组织风险，并分别报告二元检测和细粒度诊断结果。其官方项目页的排行榜也分别列出 Accuracy/Precision/Recall/F1 与 R.S./F.M./R.H.，说明“是否危险”与“危险属于什么类型”是不同能力，分类诊断不应成为输出基本可用性的前置条件。[ATBench 论文](https://arxiv.org/abs/2604.02022)、[ATBench 官方仓库](https://github.com/LiYu0524/ATbench)

### 4. HINTBench：位置、类型和严重性明确分离

HINTBench 明确拆成三个任务：轨迹级风险检测、五类 constraint 的粗粒度风险步骤定位、十二种 subtype 的细粒度定位。论文显示模型在二元检测上明显强于“定位 + 类型复现”，因此只用分类能力作为审计是否成功的门槛会掩盖定位能力。[HINTBench 论文 §4](https://arxiv.org/abs/2604.13954)

HINTBench 的人工标注依赖完整定义、例子、消歧决策过程和受训评估者。其标签按风险步骤上的直接错误机制确定，而不是按下游后果或严重性确定；分类优先顺序也明确“不表示严重性排名”。这说明正式 taxonomy 需要标注指南和消歧规则，不能只列几个枚举值。[HINTBench annotation guidelines](https://arxiv.org/abs/2604.13954)、[HINTBench 官方仓库](https://github.com/Jiacheng-31/HINTBench)

## 与赛马评分要求的关系

图示评分中，“数据统计指标可视化”和“工具调用可视化质量”不依赖统一风险 taxonomy；token、调用次数、工具类型、参数、返回、错误位置和追溯关系都可以直接由轨迹事实产生。

“思维链可视化质量”列出了风险样本来源溯源链、关键 token、风险类别分布和危险思维模式。这里至少存在四个不同概念：

| 概念 | 含义 | v1 来源 |
| --- | --- | --- |
| Verdict | 轨迹是否 unsafe/safe/undetermined | Provider 必需 |
| Evidence | 哪些输入原文支撑 finding | Provider 必需 |
| Mechanism / Finding Type | 风险如何形成或是哪种直接错误 | Provider-native，可先不映射统一 taxonomy |
| Severity | 影响大小或优先级 | 暂缓；不能由类别名称自动推出 |

若 Provider v1 不输出任何机器可分组字段，Topic 5 只能展示来源类别分布，或对自由文本做二次语义分类。前者不能代表检测结果，后者会让 Topic 5 越界承担 Topic 4 的语义判断。因此，赛马方案与“至少有稳定 finding type”密切相关，但与“现在就冻结完整统一 taxonomy”并不等价。

## 建议合同演进

### Provider v1

- 删除输入 `requested_taxonomy`。
- 必填：`verdict`、`findings[].reason`、事件定位、可回读 evidence、detector/provider 版本。
- 推荐必填一个 provider-native `finding_type`；若 Topic 4 暂时做不到，则允许 `null` 并在输出 limitations 中声明，页面不显示“Detected Category Distribution”。
- 可选：`mechanisms`、`provider_labels`。
- 暂缓：统一 `taxonomy.id/version`、跨 provider category comparison、severity。

### 后续 taxonomy 版本

只有在具备下列材料后再升级合同：

1. 类别定义及正反例；
2. 多个类别同时适用时的消歧/多标签规则；
3. category、mechanism、harm、severity 的关系；
4. ATBench/HINTBench/OWASP/MITRE 等来源映射及无法映射策略；
5. 固定标注集上的一致性或至少单人复核证据；
6. taxonomy owner 和版本变更规则。

这条路线保留赛马展示需要的结构化分组能力，同时避免在缺少依据时伪造一套“行业统一分类”。
