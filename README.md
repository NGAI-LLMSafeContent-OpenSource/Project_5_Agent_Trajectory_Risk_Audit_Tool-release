# SeClaw / Agent轨迹风险审计

提供Agent安全任务执行、轨迹规范化、Provider调用、评分归档和可交互审阅工作台。

发布单位：NGAI-LLMSafeContent。发布分支：`release/1.0`；更新日期：2026-11-21；自研代码许可：Apache-2.0，第三方保留原许可。

## 功能

- 150个OpenClaw任务及Docker批量执行、grader和可选Judge
- OpenAI Messages、ATBench、HINTBench适配及规则/课题四Provider
- corpus构建、score、baseline/source-label报告及审阅包
- React/FastAPI工作台、原文证据查看、归档重开与提交文件导出

## 安装与快速开始

Python ≥3.11、uv；工作台另需Node.js/npm，任务执行另需可用Docker及镜像，真实Provider/模型需自行配置。

从公开仓下载同一发布分支：

```bash
git clone --branch release/1.0 https://github.com/NGAI-LLMSafeContent-OpenSource/Project_5_Agent_Trajectory_Risk_Audit_Tool-release.git
cd Project_5_Agent_Trajectory_Risk_Audit_Tool-release
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell改用 .venv\Scripts\Activate.ps1
```

以下命令均以仓库根目录为起点，注释中的外部条件需先准备：

```bash
uv sync --group task-test
uv run python -m trajectory_audit run --input fixtures/smoke/openai_messages_smoke.json --out runs --provider deterministic-baseline
uv run python -m trajectory_audit corpus --help
uv run python -m trajectory_audit topic4-run --help
# 工作台：两个终端
./workbench/run-api.sh
./workbench/run-web.sh
```

## 使用与输出

工作台默认UI5173、API8765；上传轨迹后查看审计、证据和归档。默认假Provider只用于合同演示，真实课题四按 `workbench/README.md` 设置TOPIC4_COMMAND/CONFIG等变量。Docker任务执行按 `README_CN.md`、`docs/docker.md` 配置模型并运行 `scripts/batch_execute.sh --help`。源仓现行150个任务保留，历史工作台原型和预跑报告排除。

## 配置与外部依赖

task fixture中的演练凭据和截断密钥为本地mock输入，不能当真实凭据使用。真实模型、Docker权限、外部数据缓存、冻结语料输入和参考标注需自备。完整提交导出仍受原仓Provider字段未齐的限制，见KNOWN_ISSUES.md。

真实凭据只在自己的运行环境或平台Secrets中配置，不提交.env、用户上传、模型权重或运行结果。原仓文件中的内网服务地址经发布负责人确认按原样保留，使用者应在本地配置自己的可达服务。

## 验证与已知问题

完成对应依赖准备后运行：

```bash
python tools/release_check.py
```

检查包含发布文件完整性、原仓文件一致性与声明的最小测试。它不会自动下载模型或申请API，也不代表全部真实业务/准确率验收。保留的原仓错误、未运行项及外部依赖见 [KNOWN_ISSUES.md](KNOWN_ISSUES.md)。

## 发布版本与文件清单

本版本以原开发仓主分支提交 `8bc943ba697f4c613540c6b0644e7467cb3a767e` 为源。功能实现与对应原文件保持一致；README、许可说明和发布辅助材料单独登记。当前功能的必要源码、配置、界面与运行资源保留，明确弃用的原型、历史结果和开发缓存不捆绑。

- [逐文件发布清单](RELEASE_FILES.md)
- [原仓文件与Git对象对应关系](SOURCE_ALIGNMENT.json)
- [完整文件SHA-256及权限](RELEASE_MANIFEST.json)，校验表为SHA256SUMS

校验值按Git存储字节生成；若原.gitattributes规定检出换行转换，请用Git对象或发布检查脚本校验，避免将LF/CRLF误认为源码变化。各公开平台release/1.0使用同一个提交，平台生成的运行分支不属于此发布清单。

## 反馈、贡献与许可证

请通过 [公开仓Issues](https://github.com/NGAI-LLMSafeContent-OpenSource/Project_5_Agent_Trajectory_Risk_Audit_Tool-release/issues) 提交复现步骤、环境和日志摘要，勿附真实凭据或用户数据。代码问题由课题维护者在原开发仓处理，发布负责人同步审核后的版本；不要仅修一个平台造成分歧。

自研代码采用[Apache-2.0](LICENSE)。模型、数据集、外部服务和第三方代码遵循各自许可与使用条件，见[第三方说明](THIRD_PARTY_NOTICES.md)。


## 公开版本地址占位符

本发布版本将原部署环境的内网 IP 替换为 `example.invalid` 下的保留示例域名，保留原端口和接口路径。部署前请通过项目现有环境变量或配置项填写自己的服务地址；这些域名不是可访问的线上服务。

- `evaluation.example.invalid`：评价体系前端示例地址。
- `model-api.example.invalid`、`llm-api.example.invalid`：模型 API 地址。
- `platform.example.invalid`：治理平台或审计工作台地址。
- `proxy.example.invalid`：代理地址。
- `inference.example.invalid`：推理服务机器地址。

本次仅经授权替换地址字符串及配套测试中的同一地址，未修改接口路径、端口或业务算法。具体例外及文件校验记录见 `SOURCE_ALIGNMENT.json`、`RELEASE_FILES.md` 和 `RELEASE_MANIFEST.json`。
