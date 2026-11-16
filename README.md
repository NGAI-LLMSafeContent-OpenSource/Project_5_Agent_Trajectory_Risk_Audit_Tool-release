# 课题五：Agent轨迹风险审计工具

发布单位：NGAI-LLMSafeContent。版本：1.0.0。发布日期：2026-10-27。

本发布包提供离线轨迹规范化、确定性规则审计、策略校验及HTML结果页。Python 3.11或更新版本即可运行，核心功能仅使用标准库，无需安装额外依赖、模型、Docker或API令牌。

## 安装和运行

克隆release/1.0分支后进入仓库根目录。下面命令均从根目录执行：

```bash
python3 -m trajectory_audit run --input fixtures/smoke/openai_messages_smoke.json --out runs --provider deterministic-baseline
python3 -m trajectory_audit batch --input fixtures/smoke/atbench_smoke.json --out runs --provider deterministic-baseline --adapter atbench
python3 -m trajectory_audit batch --input fixtures/smoke/hintbench_smoke.json --out runs --provider deterministic-baseline --adapter hintbench
```

使用Windows时可将python3替换为python。输出根目录下每次生成独立run或batch目录，包含输入清单、规范化轨迹、audit_result.json、metrics.json和index.html；直接在浏览器打开HTML即可审阅，不依赖服务器或外部CDN。

## 最小运行测试

```bash
python3 -m unittest discover -s tests -p test_trajectory_audit_run.py
python3 -m unittest discover -s tests -p test_trajectory_audit_sources.py
python3 -m unittest discover -s tests -p test_trajectory_audit_baseline_policy.py
```

共60项测试，覆盖实际CLI运行、输出完整性、三个输入适配器及策略规则。测试自行创建和清理临时输出，无需已有数据库、缓存或开发配置。

## 配置、样例与功能边界

默认使用deterministic-baseline，Judge关闭。可用`python3 -m trajectory_audit --help`及各子命令`--help`查看选项；策略示例见tests/fixtures/trajectory_audit/。`.env.example`是源仓保留的空凭据模板，核心离线审计不读取API密钥。

审计结果是规则检查，不代表语义模型判断。Judge接口可供另行集成，默认不运行。本包不包含Docker任务、模型权重、完整第三方数据集、跨课题服务、竞赛验收包、参考标注或私有语料；acceptance/reference等扩展需要自行获取相应合法输入，未作为本版本验收能力。

ATBench/HINTBench仅提供已有公开来源的固定冒烟子集，来源、版本和许可证保留在样例source字段及THIRD_PARTY_NOTICES.md中，不包含完整数据集。

## 版本和许可证

本分支为按白名单导出的干净发布历史。源提交、发布文件清单及SHA-256见RELEASE_MANIFEST.json与SHA256SUMS；原始开发历史不在本分支中。

项目使用Apache-2.0；保留LICENSE、原始NOTICE及各源文件SPDX标识。第三方归属见THIRD_PARTY_NOTICES.md，来源说明见docs/provenance.md。

## ModelScope 在线应用

部署适配与本地启动方式见 [deploy/README.md](deploy/README.md)。部署文件已纳入统一发布源码，在线服务需另外构建和启动。
