# 课题四 × 课题五：批量风险审计 Provider v1 合同

> 状态：已接受
>
> 冻结日期：2026-11-03
>
> Parent：GitHub Issue #1
>
> 决策记录：GitHub Issue #4
>
> 后续实现：GitHub Issue #5

## 1. 合同边界

课题五只通过外部文件式 CLI 调用课题四，不直接 import 课题四 Python 模块，也不依赖其内部模型服务：

```text
topic4-audit run \
  --input input-batch.json \
  --output audit-result.json \
  --config provider-config.json
```

- 输入使用 ADR-0001 已冻结的 `topic4-audit-input/1`，不含 Source Label、Project Reference Annotation、benchmark 答案或已有 Detected Finding。
- 输出使用本合同冻结的 `topic4-audit-result/1`。
- 配置由课题四拥有；课题五传递配置路径和凭据引用，但不解释课题四私有选项，也不保存凭据值。
- 课题五负责进程、总超时、Schema、身份关联、失败汇总、指标、血缘和展示投影；课题四负责风险判定、Finding、Evidence 和算法身份。

## 2. 顶层输出

```json
{
  "schema_version": "topic4-audit-result/1",
  "batch_id": "<input batch_id or null only when unreadable>",
  "provider_run_id": "<unique run id>",
  "status": "complete|partial|failed|rejected",
  "provider": {},
  "execution": {},
  "items": []
}
```

核心对象使用严格 Schema。未来新增的诊断、性能或组件私有数据必须放入相应对象的 `extensions`，不得用未知字段改变既有字段含义。当前已稳定提供且被正式 Schema 声明的可选字段可以保留原位置，不要求为了形式统一迁移。

## 3. 批次状态和 Item 联合

- `complete`：每个输入 item 都恰好有一个 success 输出；
- `partial`：每个输入 item 都恰好有一个输出，且 success 与 failure 同时存在；
- `failed`：每个输入 item 都恰好有一个 failure；
- `rejected`：未进入逐项处理，`items=[]`，并携带顶层 `rejection`。

除 rejected 外，输出必须按 `sample_id` 与输入严格一一对应并回显 `audit_input_sha256`。数组顺序没有语义；缺失、重复或未知样本身份均为合同错误。

```text
status=success  → 必须有 result，不得有 failure
status=failure  → 必须有 failure，不得有 result 或 verdict
```

## 4. Verdict 和 Findings

| 情形 | verdict | findings |
| --- | --- | --- |
| 确认存在风险 | `unsafe` | 至少一个 |
| 完成约定范围的评估并肯定未发现风险 | `safe` | 空 |
| 评估已运行但证据不足以肯定安全或不安全 | `undetermined` | 空 |

`no_findings` 不是独立 wire 状态，只是 findings 为空的描述；区分 safe 与 undetermined 必须读取 verdict。运行、输入、资源或组件失败不得伪装成任何 verdict。

Finding 的最低稳定字段为：

```json
{
  "finding_id": "fnd-...",
  "category": "task_incomplete",
  "mechanism": "contextual_trajectory_audit",
  "scope": "event|trajectory",
  "located": true,
  "origin_event_ids": ["evt-..."],
  "affected_event_ids": ["evt-..."],
  "evidence": [],
  "reason": "...",
  "detector_component_id": "topic4-semantic",
  "detector": {"rule": "trajectory-context"},
  "confidence": {
    "kind": "model_assertion",
    "calibrated_probability": false
  },
  "severity": "high"
}
```

- `finding_id` 在单个 Item 内唯一。
- `category` 必填且为单值 Provider-native Finding Type；它只在声明的 Provider/组件版本内解释，不冒充统一风险 taxonomy。
- `mechanism` 是可选单值 Provider-native 辅助类型。当前值混合检测方法、规则和触发条件，因此 v1 不承诺它是统一的风险形成机制。
- `detector_component_id` 必须引用 `provider.components[].id`；`detector.rule` 可选。
- `severity` 必填，取值为 `low`、`medium`、`high` 或 `unspecified`。
- `confidence.kind` 是非空机器字符串，`confidence.calibrated_probability` 是必填布尔值，`confidence.value` 可选。
- 当 `calibrated_probability=false` 时，课题五不得把 confidence 当作正确概率、统一阈值或排行榜分数。confidence 与 severity 默认不跨组件或 Provider 比较。

## 5. Finding 定位和 Evidence

已定位 Finding 使用：

```text
located=true
origin_event_ids 或 affected_event_ids 至少一侧非空
evidence 至少一项
```

同一事件可以同时是 origin 和 affected。origin 表示风险条件、约束、输入或失败事实从哪里可观察；affected 表示风险在何处体现，不强制 affected 只能是 assistant 事件。

Evidence 沿用课题四当前格式：

```json
{
  "event_id": "evt-0003",
  "field": "text",
  "source_pointer": "/trajectory/events/2/content/text",
  "excerpt": "exact source text",
  "start": 10,
  "end": 27
}
```

- `event_id` 必须存在于对应输入 Item；
- `source_pointer` 必须指向该事件 `content` 下的字符串叶子；
- `start`/`end` 是 Unicode code point 的半开区间 `[start,end)`；
- `excerpt` 必须精确等于该字符串区间的输入原文，不得翻译、改写或概括。

无法可靠定位时不得猜测，允许：

```json
{
  "scope": "trajectory",
  "located": false,
  "origin_event_ids": [],
  "affected_event_ids": [],
  "evidence": [],
  "reason": "说明风险判断及无法可靠定位的原因"
}
```

未定位 Finding 仍可使 verdict 为 unsafe，但必须在 reason 和 `checks.limitations` 中说明定位限制，展示层也必须显著呈现。

## 6. Checks

```json
{
  "coverage": {"status": "observed_only|partial"},
  "limitations": []
}
```

- `observed_only`：已评估输入中可观察的轨迹，不代表全局安全保证；
- `partial`：unsupported 或缺失内容限制了评估范围；
- `limitations`：记录部分覆盖、未定位 Finding 或其他结果限制。

课题四当前的 policy、trajectory、reasoning、facts、integrations、semantic 和 toolchain 等详细检查可以保留，但课题五最小互操作逻辑不得依赖这些 Provider 私有结构。

## 7. Provider Result Provenance

非 rejected 结果至少记录：

```json
{
  "provider": {
    "implementation": "topic4-toolchain-integrated",
    "version": "0.3.0",
    "revision": "<code or build revision>",
    "config_sha256": "<sanitized canonical config sha256>",
    "components": [
      {
        "id": "topic4-semantic",
        "version": "0.3.0",
        "revision": "<component revision>",
        "model": {
          "id": "topic4-jev-27b",
          "revision": "local-tested-20261030"
        },
        "preflight_status": "ready"
      }
    ]
  },
  "execution": {
    "started_at": "<RFC 3339 UTC>",
    "finished_at": "<RFC 3339 UTC>",
    "runtime": "<runtime version>",
    "platform": "<platform>",
    "device": "<actual device>"
  }
}
```

每个实际参与判定的组件都必须出现在 components 中：

- `id`、`revision` 和 `preflight_status` 必填；
- 有语义版本时填写 version，否则为 null；
- 非模型组件的 model 为 null；使用模型时 model.id 与 model.revision 必填；
- `preflight_status` 为 `ready`、`failed` 或 `not_run`；
- preflight 只描述运行前状态，逐项运行时错误由 Item failure 表达；
- 配置为必选的组件只要不是 ready，批次就不能成为 complete。

`config_sha256` 对不含凭据值的规范配置计算。API key、token、password 等值不得进入配置、输出、日志或持久化摘要输入。

Rejected 仍必须记录 provider_run_id、Provider 程序的 implementation/version/revision、结束时间和 rejection。尚未可靠加载的模型、配置摘要或组件身份允许缺失或明确 unavailable，不得伪造。只有输入无法解析出 batch identity 时，batch_id 才允许为 null。

## 8. Failure 和 Rejection

Item failure 与批次 rejection 复用同一机器结构：

```json
{
  "stage": "inference",
  "code": "model_timeout",
  "reason": "Model inference exceeded the item timeout.",
  "retryable": true,
  "details": {}
}
```

- `stage`、`code`、`reason` 和 `retryable` 必填；details 可选且不得包含凭据；
- stage/code 是稳定机器字符串，但 v1 不穷举未来所有组件错误；
- Adapter 不根据自然语言 reason 作程序决策；
- failure 作用于单个 Item，rejection 作用于尚未逐项处理的整个批次。

| 层级 | 表达方式 |
| --- | --- |
| transport/进程失败 | 非零退出；没有可信正式结果 |
| 批次拒绝 | 合法 `status=rejected` JSON；退出 0 |
| 单条失败 | Item `status=failure`；批次可为 partial 或 failed；退出 0 |

## 9. 退出码和原子发布

- 完整合法的 complete、partial、failed 或 rejected 结果均退出 0；
- 非零退出只表示进程或 transport 没有可靠完成，残缺文件不得作为正式结果；
- Provider 先在输出同目录写临时文件，完成写入和关闭后再原子替换正式目标；
- 只有原子替换成功后才能退出 0；失败时正式路径不得留下半截 JSON；
- stdout/stderr 只用于诊断，不承载语义结果，也不得输出凭据。

## 10. 兼容与扩展

- 支持 `/1` 的消费者拒绝未知主版本，例如 `/2`；
- `/1` 内可以在明确的 extensions 中新增不改变既有语义的可选数据；
- 删除字段、改变必填性、枚举含义、状态不变量、身份关联或 Evidence 解释必须升级主版本；
- 未知扩展不得改变核心字段含义。

## 11. Sidecar 和前端边界

课题四的 `.toolchain.json` 与 `.visualization.json` 是可选 sidecar，不属于 Provider v1 必需输出：

- 课题五正式 Adapter 只消费 `topic4-audit-result/1`；
- sidecar 缺失不导致正式运行失败；
- sidecar 变化不构成结果合同的破坏性变更；
- React 工作台只消费课题五的 `review-bundle/1`。

## 12. 当前实现对齐项

现有实现已经对齐 Schema 名、Item 联合、complete/partial/failed 汇总、verdict 基本不变量、精确 Evidence 校验、confidence/severity 输出和 sidecar 分离。

在 conformance suite 冻结前仍需：

1. 合法 partial/failed/rejected 统一退出 0；
2. 采用临时文件和原子替换；
3. validator 接受并校验自身生成的 rejected；
4. 正式 Schema 补齐 Finding 字段和跨字段不变量；
5. 增加 components、模型 revision 和 detector component 引用；
6. 增加 located 判别联合及未定位分支；
7. checks 增加稳定 coverage 与 limitations；
8. 核心 Schema 收紧，未来私有数据进入 extensions。

上述对齐完成并由 Issue #5 的外部 conformance suite 验证后，才视为实现满足 Provider v1。

## 13. 当前交付的显式兼容接入

2026-11-04，按使用者明确要求优先接通真实算法和正常 run/batch 展示路径，
新增 `finial-1.0` 接入 profile。该 profile 接受当前课题四版本 `1.0.0` 的实际输出形状，
随每次运行归档 profile、原始结果、实际模式与严格合同差异。`topic4-run` 和 conformance
继续默认执行前述严格合同。此补充只调整已交付字段的消费方式，尚未提供的模型与组件 revision 保持缺失。

- 保留 provider 顶层 model、preflight、code_sha256、effective_config_sha256。
- 保留 execution 的耗时、吞吐、输入摘要和 toolchain_runtime。
- 接受 checks 中 policy、trajectory、reasoning、facts、integrations、semantic、toolchain，
  接受 limitations 尚未提供，并保留其完整原始形状。
- 对没有 located 字段的原生 Finding，仍要求非空事件绑定和精确 Evidence，
  通过既有事件 ID、Pointer、excerpt 和 Unicode 区间校验后才映射为已定位。
- 接受 provider.components 与 detector_component_id 缺失，但 Finding 必须提供实际
  detector.name、version、rule。保留 severity、confidence 及 detector 全部原始信息。
- 接受 Evidence 的 kind 与 granularity，其他未知核心字段仍报错。
- 样本/批次身份、摘要、状态联合、unsafe/safe/undetermined 不变量继续严格校验。
- 非零退出仍作为调用失败保留原始输出，原子发布和退出码差异继续由课题四跟进。

此 profile 不代表课题四通过严格 conformance，也不替代完整模型模式的部署验证。
