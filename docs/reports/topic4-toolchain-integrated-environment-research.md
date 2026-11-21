# 课题四 toolchain-integrated 环境与服务调研

日期为 2026-11-04。本报告静态核对当前 `课题五/finial` 交付的 README、配置、启动脚本和运行代码，未启动模型、安装依赖或请求外部服务。交付中的历史实测声明与本次已验证的内容分别说明。

## 服务和交付责任

`toolchain-integrated` 同时使用三方算法及完整轨迹语义审计。配置必须启用 topic1、topic2、topic3，三方预检任一失败会使样本返回 failure。正式目标配置是 `config/toolchain.json`。 [README](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/README.md:28) · [配置检查](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/runner.py:26) · [预检失败处理](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/toolchain.py:222)

| 参与方 | 运行形态 | 对方需要交付或启动的内容 |
| --- | --- | --- |
| 课题一 | 课题四进程加载交付中 vendored 课题一算法，通过 OpenAI 兼容 HTTP 接口调用模型 | 确认随包算法版本。当前实现复用课题四语义模型服务，无需再启动独立的课题一 HTTP 服务 |
| 课题二 | 课题四 HTTP 客户端请求 FilterGuard | 运行正式 FilterGuard 服务，提供可达 base URL 和接口版本。默认必须支持 `POST /api/policy_conditioned_detection` |
| 课题三 | 课题四导入 vendored `risk_algorithm`，由它自动启动本地 GPU worker，以 IPC 通信 | 交付已恢复且通过冻结 SHA256 检查的 `guard_v7` 权重，或交付授权 V7 adapter 与锁定基础模型供恢复。按当前代码无需单独启动课题三 HTTP 服务 |
| 课题四 | Provider CLI，加一个持续运行的语义模型 HTTP 服务 | 提供 Jev_Qwen3.8-27B 权重或可用的同版本模型端点、Python/CUDA 环境、配置和 GPU 资源 |
| 课题五 | 外部进程启动 Provider CLI | 准备输入、总超时、环境变量、部署配置和归档目录 |

课题一预检调用模型的 `/models` 列表并确认模型别名存在。它和语义审计使用相同的 `local-model.json` 及 `TOPIC4_MODEL_*` 覆盖项。课题二预检直接发送 benign 检测请求，代码没有单独请求 health endpoint。课题三 worker 使用 `sys.executable` 启动，继承当前 Python 环境及环境变量。 [课题一实现](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/toolchain.py:91) · [课题二预检](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/toolchain.py:130) · [FilterGuard HTTP 客户端](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/vendor/topic2/http_client.py:38) · [课题三 worker](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/vendor/topic3/risk_algorithm/pipeline.py:14)

FilterGuard 客户端也提供 `explicit_risk_screening`、`neuron_activation_detection`，但当前 toolchain 配置仅启用 `policy_conditioned_detection`。代码中的静态 base URL 只代表交付配置中的地址，本次没有验证其当前可达性或服务状态。需要课题二确认正式地址、网络访问要求、默认 policy、并发容量和服务可用时间。 [配置](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/config/toolchain.json:27) · [客户端方法](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/vendor/topic2/http_client.py:86)

## 环境和资源

按交付已测试的路线，在 Linux GPU 主机或配置好 GPU 支持的 Linux 环境部署。当前子进程实现使用 `pass_fds`、Unix 信号和进程组，启动脚本为 Bash，不能把这些脚本直接当作 Windows 原生部署步骤。此判断来自实现结构，本次没有验证 WSL GPU 兼容性。 [worker](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/vendor/topic3/risk_algorithm/pipeline.py:14) · [启动脚本](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/scripts/run_batch.sh:1)

交付的 `environment.yml` 指定 Python 3.11、Torch 2.11.0、vLLM 0.25.0、Transformers 5.14.1，另含 OpenAI、numpy、safetensors、ninja、requests、scikit-learn、tqdm、jsonschema。项目的 `pip install -e '.[toolchain,validation]'` 只补充项目及对应 extras，extras 没有安装 Torch/vLLM，需先建立模型运行环境。新机器仍需按交付要求核对驱动和 CUDA 兼容性。 [环境文件](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/environment.yml:1) · [extras](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/pyproject.toml:13) · [部署说明](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/README.md:59)

交付报告中的实测部署为语义模型使用一张 80GB A100，V7 使用另一张 40GB A100。这是其复现参考配置，文档未给出硬性最低显存结论。两个进程默认各使用可见 GPU 的大部分显存，初次复现可按分卡部署规划，具体卡型、数量和最低资源需课题四确认。默认语义服务使用 0.9 显存比例、32768 context、最多 8 序列，V7 默认显存比例为 0.9，可用 `RISK_GUARD_GPU_MEMORY_UTILIZATION` 调整预算。 [README 实测资源](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/README.md:110) · [语义服务参数](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/scripts/start_model.sh:7) · [V7 预算覆盖](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/vendor/topic3/risk_algorithm/settings.py:24)

V7 权重不在 Git 中。`prepare_topic3` 默认执行完整冻结权重校验并做 benign 推理。直接交付已恢复的 `guard_v7` 最容易复现，若仅有 adapter，则还需课题三授权的 `adapter_model.safetensors` 和锁定基础模型，按随包 `prepare_models.py` 恢复。当前 `en-US` 和 `zh-CN` 使用直通路线，默认配置为 `en-US`。民族语言另需 Hy-MT2-7B 或原算法要求的翻译凭据，交付未声称已验证该链路。 [权重交付要求](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/README.md:78) · [V7 预检](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/toolchain.py:146) · [恢复脚本](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/vendor/topic3/scripts/prepare_models.py:51) · [语言路线](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/vendor/topic3/config.json:7)

## 搭建顺序和命令模板

以下为待执行模板。所有路径、GPU 编号、URL 应替换为部署主机实际值，模型权重须预先取得。

先在部署主机建立环境。

```bash
cd /path/to/finial
conda env create -f environment.yml
conda activate topic4-toolchain
python -m pip install -e '.[toolchain,validation]'
export TOPIC4_CONDA_ENV=topic4-toolchain
```

环境文件创建的环境名是 `topic4-toolchain`，而两个 Bash 脚本默认使用 `vllm`，因此需要显式设置 `TOPIC4_CONDA_ENV`。 [环境名](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/environment.yml:1) · [服务启动](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/scripts/start_model.sh:7) · [批次启动](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/scripts/run_batch.sh:8)

终端 A 启动语义模型服务，保持运行。默认监听 `127.0.0.1:18427`，OpenAI base URL 为 `http://127.0.0.1:18427/v1`，模型别名 `topic4-jev-27b`。该回环地址要求 Provider CLI 与服务处于可访问同一回环网络的位置；跨主机部署需课题四提供可达端点及访问配置。 [启动参数](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/scripts/start_model.sh:1) · [连接配置](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/config/local-model.json:1)

```bash
cd /path/to/finial
export TOPIC4_CONDA_ENV=topic4-toolchain
export TOPIC4_MODEL_DIR=/absolute/path/to/Jev_Qwen3.8-27B
export TOPIC4_SEMANTIC_GPU=<语义模型GPU编号>
bash scripts/start_model.sh
```

终端 B 在 FilterGuard 已由课题二提供可用地址后，先校验 V7，再跑 Development Corpus 的小批次。`RISK_RUNTIME_DIR` 使用部署主机可写目录，且应放在 vendored 课题三目录之外。`run_batch.sh` 会设置 V7 的可见 GPU，并启动 CLI。输出必须使用全新路径。 [V7 设置](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/vendor/topic3/risk_algorithm/settings.py:31) · [校验脚本参数](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/vendor/topic3/scripts/check_models.py:28) · [批次脚本](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/scripts/run_batch.sh:1)

```bash
cd /path/to/finial
conda activate topic4-toolchain
export TOPIC4_CONDA_ENV=topic4-toolchain
export GUARD_MODEL_DIR=/absolute/path/to/restored/guard_v7
export TOPIC4_GUARD_GPU=<V7模型GPU编号>
export RISK_RUNTIME_DIR=/absolute/writable/path/to/model-runtime
export FILTERGUARD_BASE_URL=https://<课题二确认的服务地址>
export TOPIC4_MODEL_BASE_URL=http://127.0.0.1:18427/v1
export TOPIC4_MODEL_NAME=topic4-jev-27b
export TOPIC4_MODEL_API_KEY=local

python topic4_audit/vendor/topic3/scripts/check_models.py --only guard --full
bash scripts/run_batch.sh /absolute/path/to/development-subset.json \
  /absolute/path/to/new-run/audit-result.json
```

`TOPIC4_MODEL_API_KEY=local` 对应交付默认本地配置，真实端点如启用鉴权须通过环境变量填入实际凭据。原始配置副本可包含调用所需值，归档配置应继续使用课题五脱敏机制。小批次只取 Development Corpus，保持其已有样本身份和摘要。 [连接配置](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/config/local-model.json:1) · [语义连接](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/semantic.py:183)

当前预检会依次检查课题一模型列表、课题二 benign API 响应、课题三冻结权重与 benign 推理。正式批次输出中应核对 `provider.preflight` 三项成功、逐项结果中三方输出及语义结果。交付写出的 `.toolchain.json` 可用于这轮部署诊断，课题五正式 Adapter 仍按合同只消费主结果。 [预检流程](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/toolchain.py:79) · [sidecar 内容](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/cli.py:66) · [合同边界](/D:/repos/LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/docs/adr/0003-topic4-provider-result-and-transport-boundary.md:7)

## 课题五接入前的两个具体问题

课题五 Adapter 把顶层配置复制到临时工作目录。课题四按收到的配置文件所在目录解析 `semantic.config_path`、`semantic.cache_dir`、`toolchain.cache_dir`、`integrations.*.config_path/root`。因此把交付原始 `toolchain.json` 直接交给现有 Adapter，会把 `local-model.json` 和 `../topic4_audit/vendor/topic3` 等相对路径解析到错误位置。离线配置没有这些依赖，已有离线冒烟无法覆盖此问题。 [课题五复制逻辑](/D:/repos/LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/trajectory_audit/topic4_provider/adapter.py:98) · [课题四路径解析](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/cli.py:21)

部署时可先在独立配置副本中将上述路径全部改为 CLI 执行主机上的绝对路径，或者后续修改 Adapter 保留配置路径语义。本次研究没有修改实现和课题四交付。课题五直接调用 CLI 时也需继承 `CUDA_VISIBLE_DEVICES`、`GUARD_MODEL_DIR`、模型与 FilterGuard 连接变量。`TOPIC4_GUARD_GPU` 由 `run_batch.sh` 转成 `CUDA_VISIBLE_DEVICES`，直接运行 CLI 不会自动执行这段脚本。 [批次脚本](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/scripts/run_batch.sh:4)

课题五 `topic4-run` 默认总超时是 600 秒，课题四配置的语义请求超时为 300 秒，V7 默认启动超时为 360 秒，且完整批次还包含权重校验、预检、多个模型请求。应以小批次的冷启动耗时决定正式总超时。缓存默认启用，重新发起模型推理需要把两个 cache_dir 设为 null 或新目录并记录配置 revision，缓存命中耗时无法代表冷启动。 [课题五超时](/D:/repos/LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/trajectory_audit/cli.py:410) · [配置](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/config/toolchain.json:7) · [V7 超时](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/topic4_audit/vendor/topic3/risk_algorithm/settings.py:43) · [缓存说明](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/README.md:182)

## 当前证据和需要确认的事项

课题四 README 声明完整工具链曾在 80 条 Development Corpus 上实测，三方均参与。这是交付方已有的运行记录，本次没有独立复跑。课题五目前已复跑的真实冒烟是 `supplemental-offline` 的 1 条临时子集，完整 `toolchain-integrated` 环境未在本次部署验证。正式接入仍受既有 Provider v1 合同差异约束，环境跑通后要继续用严格校验和 conformance 核对结果。 [交付实测声明](/D:/repos/LLMSafeContent/Project_4-5_ContentToolRisk/课题五/finial/README.md:4) · [课题五接入差异](/D:/repos/LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool/docs/reports/topic4-provider-v1-integration-gap.md:19)

向课题四确认冻结配置 revision、模型 revision、可用 GPU 主机、运行环境和负责人，以及是否能直接提供已恢复 V7 与语义模型服务。向课题二确认正式 FilterGuard 地址、默认 policy 和访问方式。若 V7 权重尚未包含在受控交付中，再向课题三确认授权 adapter、锁定基础模型、恢复产物和 SHA256 清单。课题一目前只需确认随包算法版本及所用语义模型配置。以上内容应落实成可复现命令和非敏感配置副本。
