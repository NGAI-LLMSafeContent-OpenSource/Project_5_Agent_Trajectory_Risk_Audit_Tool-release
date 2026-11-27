# Review Workbench

可部署的审阅工作台：上传原始轨迹 → `topic4-cli`（本机默认假 Provider）→ `review-bundle/1` → React 三页签。

正式代码在本目录，不在 `prototypes/review-workbench-v2` 接真数据。展示缝见 `docs/adr/0004-review-workbench-app-and-bundle-seam.md`。

ATBench 与 HINTBench 共用同一 `POST /api/audit-sessions`、同一 `review-bundle/1` 合同与同一套三页签；上传时选择 adapter，前端不按数据集名称分支审阅布局。上传入口不再提供 Fixture 按钮，冻结样例仅供自动化测试。

## 上传形态

| 输入 | 行为 |
| --- | --- |
| OpenAI Messages（浦江）UTF-8 `.jsonl` | `adapter=openai-messages`；复用 #57 adapter，逐行完整校验，默认处理整个文件 |
| 单条 JSON 对象 | `mode=single`，走 `execute_run` |
| 原始记录数组（如 `.cache/datasets/atbench.json`） | 按 `limit`/`offset` 切片并包装为 `source-batch/1`，`mode=batch` |
| 已有 `source-batch/1` | 对 `records` 切片后走 `execute_batch` |

参数：

- `adapter`：`atbench` \| `hintbench` \| `openai-messages`
- `limit`：ATBench/HINTBench 默认 `5`，正整数，无条数上限；浦江不传即完整文件，显式选择仅要求正整数，不设 20 条上限
- `offset`：默认 `0`

ATBench/HINTBench 整库文件只审计切片内的小批量。浦江作为页面默认首项，上传完整文件，页面不提供范围选项；接收/解析总数、选中数、范围与算法成功/失败数分别展示。当前仍为同步处理，大文件需保持页面打开。

浦江在切片前校验所有行，拒绝重复 trajectory_id、非法 UTF-8、非法 JSON/空白行和无效 messages，不静默漏项。原始字节保存为会话目录下 `upload.jsonl`；确定性事件与消息原文保存在 `evidence/`，Provider 仍只读取既有无标签投影，不读取内部映射或参考答案。
算法失败不丢弃这些信息，页面显示未判定、检测结果未提供；会话重开与原始轨迹查询对失败样本仍有效。赛题步骤使用 #57 的 `source_mapping`，事件编号不充当赛题步骤。映射只进入 Review Bundle 展示，不进入模型输入。

## 重开和分享审计结果

上传完成后，工作台会将会话和当前样本写入地址栏。切换样本后，点击「分享样本链接」即可复制该样本的地址，也可以直接复制地址栏。刷新或打开同一链接会从服务器读取归档，定位到该样本。地址格式为 `/?session_id=<会话 ID>&sample_id=<样本 ID>`，省略 `sample_id` 时打开会话中的第一个样本。

`GET /api/audit-sessions/<session_id>` 读取 `WORKBENCH_STORAGE_ROOT/sessions/<session_id>/` 下的已有审阅包，响应结构与上传接口相同。以前上传的会话也可使用此链接，只要归档仍在。点击思维链或工具调用里的具体 evt 时，工作台再请求 `GET /api/audit-sessions/<session_id>/samples/<sample_id>/source-trajectory`，只返回该样本的原始轨迹步骤，不重新运行 Provider。归档里没有原文时，这个接口仍返回 200，并用 Evidence Availability 标明 `unavailable`。若只记得页面上的 `run_id`，可在服务器的归档目录内按该名称查找运行目录，然后取路径中紧跟 `sessions/` 的目录名作为 `session_id`。相同输入可能产生相同的 `run_id`，查到多个结果时应核对所属会话。

接收人需要能访问存放归档的工作台实例。工作台目前无登录，能访问工作台并持有链接的人可查看该会话。部署实例已设置持久化的 `WORKBENCH_STORAGE_ROOT`。本地开发如需跨服务重启保留链接，也应设置此变量，默认启动会使用新的临时目录。

### 下载赛题提交文件（#64）

工作台顶部的「导出 submission.jsonl」读取整个当前会话，不受样本选择或搜索筛选影响。
`GET /api/audit-sessions/<session_id>/submission.jsonl` 只读归档，不调用 Provider。
完整结果以 UTF-8 JSONL 下载：每条轨迹一行，最后一行 `aggregate` 只含 A4；
保留组织方模板的指标全名，去除展示层内部事件标识，原始工具参数和返回保持完整。
响应的 `X-Audit-Session-ID` 绑定会话，`ETag` 是下载字节的 SHA-256。

任何样本失败、判定未确定、必需项未提供、身份/摘要/步骤映射不一致或证据无效，
返回 HTTP 409 `submission_unavailable`，`error.details.issues[]` 列出 `sample_id`、
`item` 和 `reason`。错误响应没有附件文件名，也不返回部分提交。原文不可读时同样拒绝导出；
归档整体损坏返回 500，未知会话返回 404。页面保留失败详情，可关闭并重新尝试下载。

**当前集成边界：#60 尚未确认正式 B3/C2/C3 Provider 字段。** `with_algorithm_items`
仍将这些必需项设为 unavailable；现有真实运行因此会明确拒绝完整提交。
本功能提供下载/校验/组装基础设施，不能据此宣称 #64 全部验收或 #62 算法正确性通过。
后续在 #60 确认后，需将正式 Evidence（含身份、原文字段和区间）校验并投影到同一归档 `race`，
然后同时用于页面与下载；这里不读取 Provider 私有 sidecar，也不自行选择首次行为。

组装测试使用人工固定的公开展示投影：B2/B3/C2/C3 只有显式 `status=available`
且分别包含 `judgment`/`risks`/`markers`/`failures` 时才可继续；无发现必须为明确空列表。
风险行为必须有算法选定的事件和步骤、`risk_quote` 与证据；C2 指向前一次调用，
C3 的错误证据指向对应返回，C5 复用 C2—C4 的位置与证据。
这是工作台投影的组装测试接口，不是已经确认的课题四输出合同。
测试不读取组织方参考答案，也不将固定结果注入生产审计。

## 本机启动

先装一次依赖（仓库根目录）：

```bash
uv sync --group task-test
```

然后两个终端：

```bash
./workbench/run-api.sh    # http://127.0.0.1:8765
./workbench/run-web.sh    # http://127.0.0.1:5173  （/api 代理到 8765）
```

打开 <http://127.0.0.1:5173/>。可用 `PORT=9000 ./workbench/run-api.sh` 改端口。

默认 Provider 命令为 `python -m tools.fake_topic4_provider`，配置为 `fixtures/topic4_provider/fake_unsafe_located_config.json`，profile `strict-v1`。CI / 测试不依赖真模型或 GPU。

也可以在本机显式选择真实课题四；未设置这些变量时仍使用上述 fake：

```bash
export TOPIC4_COMMAND=/absolute/path/to/topic4-audit
export TOPIC4_CONFIG=/absolute/path/to/config/toolchain.json
export TOPIC4_PROFILE=finial-1.0
export TOPIC4_TIMEOUT=21600
./workbench/run-api.sh
```

真实 CLI 的启动失败、超时或合同校验失败会作为结构化 API 错误返回，不会伪装成 safe。若 CLI 依赖激活脚本，请让 `TOPIC4_COMMAND` 指向一个先准备环境、最后 `exec` CLI 的可执行 wrapper；不要把 `source ... && ...` 作为命令字符串传入。

样例原始轨迹：

- `fixtures/workbench/atbench_single_unsafe.json`（单条，adapter=`atbench`）
- `fixtures/workbench/hintbench_single_risky.json`（单条，adapter=`hintbench`）
- `fixtures/workbench/atbench_raw_array_tiny.json`（3 条原始数组）
- `fixtures/workbench/hintbench_raw_array_tiny.json`（2 条原始数组）
- `.cache/datasets/atbench.json` / `hintbench.json`（整库；配合 limit/offset）

## 测试

```bash
# Gate A
PYTHONPATH=. uv run --group task-test python -m unittest tests.test_workbench_api -v

# Gate B
cd workbench/web && npm test
```

冻结 UI fixture：

- `fixtures/workbench/review_bundle_atbench.json`
- `fixtures/workbench/review_bundle_hintbench.json`
- `fixtures/workbench/review_bundle_unlocated.json`（定位失败：`located=false`，空关联）

可由 `scripts/_freeze_review_bundle_fixture.py [atbench|hintbench|all]` 重新生成。定位失败 fixture 由 ATBench 审阅包派生，供 Gate B 核对失败态。

假 Provider 定位失败配置：`fixtures/topic4_provider/fake_unsafe_unlocated_config.json`。

## 176 部署

在 `platform.example.invalid` 上用 systemd 跑 API、用 nginx 提供页面，和内容安全治理平台同一类方式，不用 Docker。旧静态原型仍在 `5855`，本脚本不改它。

先把要上线的提交推到 `origin/main`，再在 176 的仓库里执行：

```bash
bash scripts/server/deploy-review-workbench.sh
```

脚本会 `git pull`、安装 Python 依赖、构建前端、重启 `llmsafe-review-workbench`，并重载 nginx。需要 sudo。默认地址是 <http://platform.example.invalid:5861/>。只看会改什么、不真正执行时加 `--dry-run`。日志用 `journalctl -u llmsafe-review-workbench -n 80 --no-pager`。

### 部署拓扑与 Provider

```text
浏览器
  → 176 nginx :5861
  → 176 Review Workbench API :8765
  → 176 trajectory_audit + topic4-audit
      ├→ FilterGuard 远程 HTTPS
      ├→ 176 回环 :18427 → SSH 反向隧道 → 182 Jev
      └→ 176 回环 :18428 → SSH 反向隧道 → 182 V7
```

工作台不直连模型。部署脚本固定使用 176 上的真实课题四：

- root：`/data/zhangxu/repos/Project_4-5_ContentToolRisk/课题五/finial`
- config：`config/toolchain.json`
- activation：`deployment/176/activate.sh`
- profile：`finial-1.0`
- sessions/runs：`/data/zhangxu/review-workbench`

路径可用 `TOPIC4_FINIAL_ROOT`、`TOPIC4_CONFIG`、`TOPIC4_ACTIVATE`、`WORKBENCH_STORAGE_ROOT` 覆盖。部署服务将 `WORKBENCH_ALLOW_PROVIDER_OVERRIDES=false`，因此公开请求不能替换命令、配置或 profile；本地开发服务仍允许显式覆盖。第一版不包含登录或多租户，只应开放在约定的内网范围。

部署前先确认三项依赖，再执行脚本：

```bash
curl -fsS --max-time 30 \
  'https://u165979-8749-15195365.westc.seetacloud.com:8443/api/policy_conditioned_detection' \
  -H 'Content-Type: application/json' \
  -d '{"task_id":"workbench-deploy-smoke","texts":["Have a nice day."]}'
curl -fsS http://127.0.0.1:18427/v1/models -H 'Authorization: Bearer local'
curl -fsS --max-time 600 http://127.0.0.1:18428/v1/risk-classification \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"0123456789abcdef0123456789abcdef","input_text":"","target_text":"Have a nice day.","model_name":"Safeguard","language":"en-US"}'
bash scripts/server/deploy-review-workbench.sh --dry-run
bash scripts/server/deploy-review-workbench.sh
```

健康检查必须显示真实 profile 且禁止请求覆盖：

```bash
curl -fsS http://127.0.0.1:5861/api/health
# provider.profile == "finial-1.0"
# provider.overrides_allowed == false
```

最后用开发样例完成一次真实上传；不要用 Blind Acceptance Batch 做部署调试：

```bash
curl -fsS --max-time 21600 \
  -F adapter=atbench \
  -F trajectory=@fixtures/workbench/atbench_single_unsafe.json \
  http://127.0.0.1:5861/api/audit-sessions \
  -o /tmp/review-workbench-acceptance.json

python - <<'PY'
import json
from pathlib import Path

result = json.loads(Path('/tmp/review-workbench-acceptance.json').read_text())
assert result['ok'] is True, result
assert result['bundle']['schema_version'] == 'review-bundle/1'
session_id = result['session_id']
session_dir = Path('/data/zhangxu/review-workbench/sessions') / session_id
audit_results = list(session_dir.glob('runs/run-*/audit_result.json'))
assert len(audit_results) == 1, audit_results
audit = json.loads(audit_results[0].read_text())
provider_result = audit['provider_result']
assert provider_result['profile'] == 'finial-1.0', provider_result
assert provider_result['provider_run_id'], provider_result
integrations = list(session_dir.glob('runs/run-*/topic4/integration.json'))
assert len(integrations) == 1, integrations
integration = json.loads(integrations[0].read_text())
assert integration['profile'] == 'finial-1.0', integration
print(session_id, result['run_id'], provider_result['provider_run_id'])
PY
```

验收时保留响应中的 `session_id`、`run_id`，以及 `/data/zhangxu/review-workbench/sessions/<session_id>/` 下的 Review Bundle、Topic 4 原始结果和 integration 归档。模型/隧道启动与完整工具链探测见 `docs/topic4-toolchain-176-acceptance.md`。
