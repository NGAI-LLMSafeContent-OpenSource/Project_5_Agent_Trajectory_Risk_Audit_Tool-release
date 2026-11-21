# Topic 4 Provider Conformance Suite

本目录只通过**外部 CLI + 文件 + JSON**验证 Provider 行为，不 import
`trajectory_audit` 或 `topic4_audit` 内部包。课题四团队可用同一套 suite
验证其交付是否满足 Provider v1 合同（Issue #4 / ADR-0003）。

## 运行

在课题五仓库根目录：

```bash
# 用 fake provider 自检
PYTHONPATH=. python3 conformance/run_suite.py \
  --provider-command "python3 -m tools.fake_topic4_provider"

# 用真实 topic4-audit（示例）
PYTHONPATH=. python3 conformance/run_suite.py \
  --provider-command "/path/to/topic4-audit"
```

Windows / WSL：

```bash
wsl -d Ubuntu -- bash -lc \
  'cd /mnt/d/repos/LLMSafeContent/Project_5_Agent_Trajectory_Risk_Audit_Tool && \
   PYTHONPATH=. python3 conformance/run_suite.py \
   --provider-command "python3 -m tools.fake_topic4_provider"'
```

## Case 格式

`cases/*.json`：

```json
{
  "name": "complete_multi",
  "provider_config": {"scenario": "complete_multi"},
  "input": "fixtures/topic4_provider/minimal_input_batch.json",
  "timeout_seconds": 10,
  "expect": {
    "exit_code": 0,
    "output_present": true,
    "json_object": true,
    "status": "complete",
    "atomic_tmp_absent": true
  }
}
```

相对路径相对于仓库根目录。`start_fail` 等启动失败场景由 suite 用无效命令覆盖，不依赖 fake provider。

## 合同基线

- 退出 0 + 原子发布的合法 JSON = 语义结果（complete/partial/failed/rejected）
- 非零退出 = transport 失败；残缺文件不得当作正式结果
- Evidence 使用 Unicode code-point `[start,end)` 半开区间
