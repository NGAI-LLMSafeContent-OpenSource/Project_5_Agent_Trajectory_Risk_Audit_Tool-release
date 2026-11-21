# ADR-0001：冻结课题四审计输入 interface

- 状态：已接受
- 日期：2026-10-27
- 关联：Issue #15（生成无标签 Provider 输入并切断 Judge 标签泄漏）

## 背景

课题四需要在不理解 ATBench、HINTBench 原始格式的前提下消费 Agent 轨迹。课题五已有的
`trajectory/2` 是**内部**统一轨迹：它同时承载 Source Label、Project Reference
Annotation、来源定位、原始记录和 Detected Finding。直接把 `trajectory/2` 交给课题四会
把标签、参考结论和 benchmark 身份一并泄漏，使风险识别效果失去意义。

同时，课题四与课题五之间的执行 interface 必须是**文件式 Provider CLI**：课题四的
provider 实现可以在内部按需调用在线模型服务，但课题五不直接依赖该服务接口。输入文件的
合同因此需要独立冻结，不能随课题四内部实现变化。

## 决策

### 1. `audit-trajectory/1` 使用白名单投影，而不是删除列表

Canonical Audit Item 的轨迹部分只包含：

- `events`：`event_id`、`sequence`、`type`、`actor`、可选 `source_step_id`，以及该事件
  类型允许的 `content`；
- `tool_definitions`：只保留 `name`、`description`、`parameters`、`type`、`function`、
  `strict` 等工具语义字段；非对象条目没有可投影语义，构建时丢弃、校验时拒绝；
- `coverage`：事件数、事件类型计数、unsupported 事件身份、来源步骤身份和工具定义数。

`source`、`raw`、`source_steps`、`source_labels`、`reference`、数据集身份、完整原始记录、
来源 reason、人工决定和 Detected Finding 都没有进入投影的代码路径。白名单之外的新增来源
字段不会自动出现在输入中；缺失的 tool definitions 与 unsupported 事件集合规范化为空数组。

选择白名单而不是"序列化完整轨迹后删除已知字段"，是因为删除列表无法证明新增来源字段不会
泄漏；白名单把默认值固定为"不暴露"。

### 2. `topic4-audit-input/1` 是 Exchange Envelope

顶层只有 `schema_version`、稳定 `batch_id` 和非空 `items`。每条 item 显式包含
`sample_id`、`audit_input_sha256`、`trajectory` 与 `policy`。

`batch_id` 由 `sample_id` 与 `audit_input_sha256` 的规范化列表派生；同一来源、同一
policy 重复构建得到字节一致的输入。`audit_input_sha256` 只覆盖 provider 可见的
trajectory 与 policy：修改 Source Label、Project Reference Annotation、原始记录或其他
禁止字段不会改变摘要，修改事件内容或 policy 一定改变摘要。

### 3. 输入不请求 taxonomy

`topic4-audit-input/1` 不包含 `requested_taxonomy`。课题四当前不需要一套尚不存在的统一
风险 taxonomy、severity 或 confidence；Provider-native Finding Type 可以在后续合同里
先为 null 并声明 limitation。统一 taxonomy 属于之后的独立决策，不能阻塞输入交付。
调研依据见 `docs/research/provider-risk-taxonomy-practices.md`。

### 4. `policy=null` 表示"没有额外授权依据"

每条 item 的 `policy` 字段必须显式出现；没有 policy 时为 `null`。`null` **不表示全部
动作均获授权**：policy 检查保持 `no_basis`，判定不会因此变成 `safe`。真实开发与盲测
轨迹的 policy 均为 `null`；policy 纵切由独立的 synthetic fixture 验证。

### 5. 现有可插拔 Judge 复用同一投影

确定性 baseline 调用语义 Judge 时，传入的 `trajectory` 参数就是同一个
`audit-trajectory/1` 投影，不再是 `NormalizedTrajectory.to_dict()` 的完整内容；
`policy` 参数同样是 `audit-policy/1` 白名单投影，不再携带 `source` 路径与文件哈希。
Judge 因此和未来的课题四 provider 看到同一份白名单内容，不会成为绕过标签隔离的第二条
通道。

### 6. CLI 内部在线调用不改变文件式外部 seam

课题四对课题五提供的 interface 仍是：

```bash
topic4-audit run --input <input-batch.json> --output <result.json> --config <config.json>
```

课题四 CLI 内部可以按需启动在线模型服务；该 endpoint 不是课题五直接依赖的 interface。
输入与结果通过文件交换，因此线上服务、模型或 prompt 的变化不会改变课题五的文件合同。

### 7. Development Corpus 与 Blind Acceptance Batch 必须隔离

两个子集使用**同一** Canonical Audit Item 投影与 `topic4-audit-input/1` Envelope，但
批次、样本身份和用途隔离：

- Development Corpus 供课题四反复查看和运行，可以围绕它调整实现；
- Blind Acceptance Batch 在 provider、model、prompt 和 config revision 冻结后首次正式
  运行，保留 `acceptance-v1` 的冻结身份，不用于调试；
- 两者的 sample identity 与 record identity 零重叠；正式 provider 与 deterministic
  baseline 各自产生独立 run，禁止以 baseline 静默替代正式 provider 失败。

混用会让效果报告失去"首次盲测"的意义，因此隔离是 interface 的一部分，而不是流程建议。

## 影响

- 任何需要向 provider 增加可见字段的变更，都必须显式修改白名单并提升 Schema 版本，
  不能依赖"来源恰好没有该字段"。
- 输入的可复现性由 `batch_id`、`audit_input_sha256` 与逐文件摘要保证；同版本内容漂移
  会被生成或校验命令检出。
- 课题四的结果合同、失败语义与 Provider Contract Test Suite 仍由后续 ticket 冻结；本
  ADR 只冻结输入 interface。

## 被否决的备选方案

- **直接传 `trajectory/2`**：标签、reference、来源定位和原始记录全部暴露，否决。
- **序列化后删除已知禁止字段**：新增来源字段默认泄漏，无法证明闭合，否决。
- **输入携带 `requested_taxonomy`**：统一 taxonomy 尚未冻结，会把不存在的分类体系强加给
  课题四，否决。
- **把 Development Corpus 与 Blind Acceptance Batch 合成一个批次**：破坏盲测隔离，
  否决。
