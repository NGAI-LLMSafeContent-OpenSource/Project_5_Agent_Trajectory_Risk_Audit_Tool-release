# 已知问题与验收边界

对应原仓提交：`8bc943ba697f4c613540c6b0644e7467cb3a767e`。本分支不修正业务源码；以下问题不作为“已修复”发布。

## 外部运行条件

task fixture中的演练凭据和截断密钥为本地mock输入，不能当真实凭据使用。真实模型、Docker权限、外部数据缓存、冻结语料输入和参考标注需自备。完整提交导出仍受原仓Provider字段未齐的限制，见KNOWN_ISSUES.md。

发布检查仅覆盖release_checks.json中声明的文件一致性、规则/协议、基础HTTP或构建测试；真实模型、训练、GPU、受限数据、原始知识库和跨课题服务未配置的路径不计作真实端到端通过。

## 原仓状态与保留问题

- 工作台原实现尚未确认全部正式B3/C2/C3 Provider字段；必需项不足时提交导出会返回409，而不是伪造完整submission。默认fake Provider仅验证合同，真实Provider和Docker执行需要对应外部条件。

## 问题处理

请将错误与复现条件提交至公开仓Issues，由原仓维护者处理。发布分支仅同步审核后的源码和说明，其他平台不单独修改实现。

## 外部验收数据

`tests/test_workbench_messages.py` 需要 `acceptance/saima-risk-visualization-sample-v1/raw/samples.jsonl` 与 `acceptance/saima-risk-visualization-sample-v1/raw/reference_answers.jsonl`。它们是完整样本与参考标注，不属于运行代码最小包；该项标为 external_dependency_not_run。工作台本身不读取参考答案。导出所需的 `acceptance/saima-risk-visualization-sample-v1/raw/submission_template.json` 已原样发布。
