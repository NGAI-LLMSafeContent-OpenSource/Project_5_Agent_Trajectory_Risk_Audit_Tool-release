# Topic4 Provider v1 接入差异报告

- 日期：2026-11-03
- 基线：Issue #4 / `docs/topic4-provider-v1-meeting-draft.md` / ADR-0001 / ADR-0003
- 实现：Issue #5（本仓库正式文件式 Adapter + fake provider + conformance suite）
- 课题四交付（只读）：`Project_4-5_ContentToolRisk/课题五/finial/`

## 1. Development Corpus 身份核对

| 项 | 值 |
| --- | --- |
| 路径 | `corpus/topic4-provider-corpus-v1/development/input-batch.json` |
| `batch_id` | `audit-input-5fb4394b7634fb5c` |
| item 数 | **80**（未变） |
| 首条 | `sample-02f11d422648b69e` / `44fd3bf29118540a2bae4fddc73263983a06fc65964e4703356ae8e813914025` |
| 末条 | `sample-fcc50cc1710ccf9f` / `d563f103b24479c92df99d3082cf62cf5ece31b344ab183de0ddc52e2e896e2f` |
| digest 列表摘要 | `72ae02462d5bb973eaaccbd13b971484bbd7658a175379e3baa4cb0f898b61bf` |

完整 `sample_id` + `audit_input_sha256` 列表见：
`docs/reports/topic4-provider-corpus-v1-development-digests.json`。

## 2. 真实 CLI 离线冒烟

在 WSL 临时目录对 **1 条子集副本** 调用：

```text
python3 -m topic4_audit run \
  --input <tmp>/input-batch.json \
  --output <tmp>/audit-result.json \
  --config <tmp>/offline.json   # 复制自 finial/config/offline.json
```

| 项 | 结果 |
| --- | --- |
| 环境 | WSL Ubuntu；`PYTHONPATH=<finial>`；未改课题四仓库 |
| 配置 | `config/offline.json`（`mode=supplemental-offline`） |
| 退出码 | **0** |
| 输出 `status` | `complete` |
| 输出文件 | 已写出；同时写出 `.visualization.json` / `.toolchain.json` sidecar |
| 对本仓库 validator | **17** 项合同问题（见下） |
| 模型/toolchain | 离线 supplemental 路径可用；完整在线 toolchain 未在本次强制跑通 |

结论：外部 CLI/file seam **可调用**；当前结果 JSON **尚未满足** Provider v1 严格 Schema。

## 3. 冻结 demo 结果对本仓库 validator

对 `finial/demo/input-batch.json` + `finial/demo/output/audit-result.json`：

| 统计 | 值 |
| --- | --- |
| 问题总数 | 844 |
| `unknown_core_field` | 570 |
| `bad_located` | 193 |
| `bad_limitations` | 80 |
| `missing_components` | 1 |
| Finding 含 `located` | 否 |
| Finding 含 `detector_component_id` | 否 |
| `provider.components` | 缺失 |

## 4. 差异分类与 Owner

图例：

- **compatible**：已符合 v1，可直接消费
- **topic5 mapping**：课题五侧投影/容忍即可，不阻塞课题四
- **needs topic4 change**：课题四必须改交付
- **suggest contract revision**：合同可能过严或字段归属需再议
- **needs joint decision**：双方共同决定后再改 #4 / Schema / Adapter

| 差异 | 分类 | Owner | 说明 / 后续 |
| --- | --- | --- | --- |
| Schema 名 `topic4-audit-result/1`、Item success/failure XOR、complete/partial/failed 汇总、verdict 基本枚举、Evidence 精确 excerpt 能力 | compatible | — | 方向已对齐；字段完备性仍不足 |
| sidecar `.toolchain.json` / `.visualization.json` 可选、正式 Adapter 不消费 | compatible | Topic5 | ADR / 合同第 11 节；本 Adapter 忽略 sidecar |
| Development Corpus 80 条身份稳定 | compatible | Topic5 | 已记录 digest 列表 |
| 离线/supplemental 可跑通 1 条子集 | compatible | Topic4 | 证明 CLI seam 可用 |
| Provider-native `category` / `mechanism` / `confidence` / `severity` 映射为 Detected Finding，不混 Source Label / Reference | topic5 mapping | Topic5 | Adapter mapper 已按此实现 |
| `checks` 中 policy/trajectory/reasoning/facts/integrations/semantic/toolchain 等私有块 | suggest contract revision / needs topic4 change | Joint → 倾向 Topic4 迁入 `checks.extensions` | 合同要求核心对象严格 Schema；当前大量未知核心字段 |
| `provider.model` / `preflight` / `code_sha256` / `effective_config_sha256` | needs topic4 change | Topic4 | 应改为 `components[]` + `extensions`；顶层 model 字符串不符合 v1 |
| `execution` 性能字段（elapsed/cpu/latency/...）与 `toolchain_runtime` | suggest contract revision | Joint | 可保留在 `execution.extensions`；否则 Topic4 删除或合同扩表白名单 |
| 缺少 `provider.components[]` 与 Finding `detector_component_id` | needs topic4 change | Topic4 | 合同第 7/4 节硬要求 |
| Finding 缺少 `located` 判别联合；unlocated 分支与 limitations | needs topic4 change | Topic4 | demo 193 处 `bad_located` |
| `checks.limitations` 缺失/非字符串数组；coverage/limitations 稳定形状 | needs topic4 change | Topic4 | demo 80 处；合同第 6 节 |
| Evidence 额外字段 `kind` / `granularity` | needs topic4 change 或 suggest contract revision | Joint | 严格 Schema 下应进 `evidence.extensions` 或合同扩表白名单 |
| 合法 partial/failed/rejected **统一退出 0**；当前 `complete→0`，否则 `1`；rejected 路径 `2` | needs topic4 change | Topic4 | ADR-0003；`cli.py` `return 0 if status=='complete' else 1` |
| rejected 结构缺 `stage`/`retryable`；自身 validator 不接受自产 rejected | needs topic4 change | Topic4 | 合同第 8 节；会议草稿第 12 节对齐项 |
| 原子发布（同目录临时文件 + replace）；当前 `open('x')` 独占创建 | needs topic4 change | Topic4 | ADR-0003；非原子时退出 0 不可信 |
| stdout 打印状态摘要 | compatible / topic5 mapping | Topic5 | 仅诊断；Adapter 不把 stdout 当语义 |
| 是否允许 checks/provider/execution 的“已稳定可选字段”留在原位而不进 extensions | needs joint decision | Joint | 合同第 2 节允许“已稳定提供且被正式 Schema 声明的可选字段”；需双方列出白名单后回写 #4 |
| Blind Acceptance 是否可跑 | needs joint decision | Joint | 本 Issue **不运行**；需 Provider/合同/conformance 冻结后另开 |

## 5. 本仓库已交付（Issue #5）

- `trajectory_audit/topic4_provider/`：process_runner / raw_loader / validator / mapper / archiver / adapter
- `tools/fake_topic4_provider/`：可控场景 + 原子写 + 退出 0 语义结果
- `conformance/`：外部 CLI suite（不 import 双方内部包）
- CLI：`python -m trajectory_audit topic4-run --input --config --command --out --timeout --allow-fallback`
- 正式失败默认不 fallback；`--allow-fallback` 记录 `degraded` + 原始失败
- 归档配置消毒：`api_key`/`token`/`password`/`secret` 等值为 `[REDACTED]`

## 6. 复现命令

```bash
# 单元测试
wsl -d Ubuntu -- bash -lc \
  'cd /mnt/d/repos/LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool && \
   PYTHONPATH=. python3 -m unittest discover -s tests -p "test_topic4_provider_*.py" -v'

# Conformance（fake provider）
wsl -d Ubuntu -- bash -lc \
  'cd /mnt/d/repos/LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool && \
   PYTHONPATH=. python3 conformance/run_suite.py \
   --provider-command "python3 -m tools.fake_topic4_provider"'

# Development Corpus 冒烟（身份核对 + 可选真实 CLI）
wsl -d Ubuntu -- bash -lc \
  'cd /mnt/d/repos/LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool && \
   PYTHONPATH=. python3 -m unittest tests.test_topic4_provider_corpus_smoke -v'
```


## 7. 建议下一步

1. Topic4：按 conformance suite 与本表改造退出码、原子发布、components/located/limitations、rejected 形状，并把私有块迁入 `extensions`。
2. Joint：开会冻结“允许留在核心对象的可选字段白名单”，回写 #4 与 Schema 后再放宽 Topic5 validator。
3. Topic5：白名单冻结前，**不以**兼容猜测放宽正式 validator；继续用 fake provider 保 Adapter 路径绿灯。
4. 双方 conformance 全绿后，再冻结配置 revision，进入 Blind Acceptance。

## 8. 2026-11-04 正常运行入口接入验证

`run` 与 `batch --provider topic4-cli` 已接入同一个外部文件式 Adapter。
当前交付通过合同第 13 节声明的 `finial-1.0` profile 消费，独立 `topic4-run`
和 conformance 仍使用严格合同。原始输入与输出留档，原生 Finding 保留在
`provider_native`，完整检查与运行血缘保留在 `provider_result`。

真实复现使用 WSL、仓库 `.venv/bin/python` 和课题四 `config/offline.json`。
普通入口实际生成 `runs/run-6b739855e1aaf634/index.html` 及标准运行包，
verdict 为 unsafe，实际 Provider 为 topic4-supplemental-offline，未 fallback。
使用者可按 `docs/trajectory-audit.md` 的环境变量配置后运行普通命令。
完整 toolchain-integrated 模型和三方服务尚未在本机联调验证。

验证结果如下。

- 新增 CLI 入口回归测试 8 项通过，包含真实离线单条/多条、原生 Finding 保留、
  Policy 展示、错配 Evidence 拒绝、失败留档、fallback 后严格重试、凭据诊断脱敏和非法 UTF-8 留档。
- 原 Topic4 Adapter 专项测试 35 项通过，包含 fake conformance 和开发集身份/真实 CLI 冒烟。
- 全仓 330 项测试中 328 项通过，2 项冻结 Corpus 重建错误。
  两项错误已在修改前 `0a8d667` 的临时只读代码快照重现。
  它们涉及 `8d43cad` 修正 Complete 语义后与冻结 v1 内容不一致，本次保留冻结文件。
- Ruff 的 F 类检查、Python 编译检查和 git diff 空白检查通过。仓库未配置专用静态类型检查器。

Standards 与 Spec 两轴审查发现并修复了 runtime fallback 的身份与规则摘要、
批次降级汇总、Policy 展示、detector 类型、诊断凭据脱敏和非法编码失败归档问题。
复查没有剩余阻塞发现。

后续责任及状态如下。

| 后续 | Owner | 当前状态 |
| --- | --- | --- |
| 语义模型、FilterGuard 与 V7 的完整模式部署及真实联调 | Topic4 提供部署资料，Topic5 集成验证 | 待服务与权重确认，衔接 Issue #12 的预检与演练 |
| 本报告第 4 节列出的严格合同输出差异 | Topic4 修改，Joint 决定合同可选字段 | 当前通过显式 profile 接入，严格 conformance 仍待对齐 |
| 冻结 v1 与 Complete 语义修正后的重建差异 | Topic5 | 保持 v1 不变，新版本及验收时间另行决定 |

完整模式联调与严格合同通过须有独立证据，不能由本次离线运行替代。
