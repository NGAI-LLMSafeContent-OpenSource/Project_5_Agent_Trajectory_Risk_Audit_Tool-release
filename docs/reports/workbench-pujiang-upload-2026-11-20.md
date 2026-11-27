# #61：浦江 JSONL 上传与 176 验收

日期：2026-11-20（Asia/Shanghai）。来源与算法结果分开报告；本票未调整课题四算法或读取参考答案填结果。

## 实现与部署

- 实现提交：`c7e5b50`；严格 JSONL 校验修复：`ba390cb`。
- 176 部署代码提交：`a551996`。先前 `8bd8a0b` 曾用 Git bundle 快进并执行部署脚本 `--no-pull`；本轮在服务器 `git pull --ff-only` 后执行 `bash scripts/server/deploy-review-workbench.sh`。
- 地址：<http://platform.example.invalid:5861/>。健康检查：`provider.profile=finial-1.0`、`overrides_allowed=false`。
- 初始上传和再次上传复用 UploadPanel。OpenAI Messages（浦江）是默认首项，页面不提供范围控件，不传 limit/offset 即完整文件。ATBench / HINTBench 仍可填写处理条数和跳过条数。接口显式选择只要求正整数，不再把条数限制在 20。上传入口和顶栏「上传轨迹」都不再提供 Fixture 按钮。
- 切片前校验整个文件，拒绝重复身份、非法编码、非法 JSONL、空白行和无效消息。额外拒绝 Python JSON 默认接受的 NaN/Infinity/-Infinity。
- 保留上传原始字节、规范事件和完整消息；算法失败仍可查询与审阅。失败 Review Bundle 判定为 undetermined，检测发现与算法指标未提供，不把空列表展示成已检查无发现。
- #57 的 source_mapping 负责共享思考、工具返回及赛题步骤；系统约束保留消息原文定位，事件号不等于赛题步骤。来源映射只进入展示，不传给 Provider。

## 可重复验证

```bash
python -m unittest tests.test_workbench_messages -v
python -m unittest tests.test_workbench_api -v
python -m unittest discover -s tests
pyright --pythonpath /path/to/venv/bin/python workbench/api/app.py workbench/api/ingest.py workbench/api/messages_evidence.py trajectory_audit/pujiang.py tests/test_workbench_messages.py
cd workbench/web
npm test
npm run build
```

- 最终 176 全套 Python：384 tests，OK（2 skipped）。`a551996` 部署前在同一环境复跑，结果相同。
- React：10 files / 98 tests 通过；有既有 Radix 键盘测试的 React act 警告，无失败。`a551996` 部署前复跑同样为 98 通过。
- 浏览器核对 `a551996` 上线页：空态与顶栏弹层都以浦江为默认首项，没有范围控件，也没有 Fixture。切到 ATBench 后处理条数默认为 5、跳过条数为 0。420px 宽时顶栏弹层缩进视口。
- TypeScript、生产构建及本次 Python 变更 pyright：通过，0 类型错误。
- HTTP 回归覆盖完整公开五例、身份/原文/步骤映射、会话重开、30 条全量、显式子集范围、非法行（含未选中行）、算法失败保留证据。
- React 回归覆盖初始及再次上传、JSONL 默认不发送切片参数、显式大于 20 条子集、失败样本继续审阅与不显示无发现。
- 对审查发现的非标准 JSON 常量先观察 HTTP 200 的失败用例，再实现拒绝，三种常量均转为 HTTP 400。
- Windows 检出曾将历史 v2 冻结文件转换为 CRLF，导致字节摘要测试失败。恢复 Git LF 字节后本地 383 tests 通过；最终修复版在 Linux checkout 384 tests 通过。历史 corpus 内容未修改或重建。
- `npx -p @google/design.md designmd lint DESIGN.md`：0 errors，2 个镜像颜色未在组件 frontmatter 引用的警告。
- UI 静态审计对 `workbench/web/src` 使用[范围与原生控件归属配置](issue61-ui-audit-config.json)：0 findings；[JSON 结果](issue61-ui-audit.json)。全仓扫描同时包含历史静态文档/原型，不以其作为本次应用范围的通过声明。

## 双轴代码审查

### Standards

0 项发现。新增 Python 文件符合许可证与类型标注要求；参数变化同步文档；React 保持 Review Bundle 展示边界。

### Spec

初次 1 项 P2：JSON 解析接受非标准常量。`ba390cb` 修复并通过公开 API 回归；复审 0 项剩余发现。

## 真实浏览器五例

- Chrome 实际选择浦江格式和原始 `samples.jsonl`，通过上传按钮提交到同一会话；默认完整文件。
- 会话：`58842b4374a04699b6732c46fafba5fa`。
- 上传 SHA-256：`25d4c1fb7aacb6ee076dfdc076ef4304a0152fb5064cd6175a4c5eb1c4460a7c`，与 #57 固定来源一致。
- 远端归档：`/data/zhangxu/review-workbench/sessions/58842b4374a04699b6732c46fafba5fa/`，包含 upload.jsonl、source-batch.json、evidence/ 与 runs/。
- Provider 使用已部署的真实 topic4-audit 文件式命令；第一次运行期间 Jev 处于冷加载状态。接收/解析 **5/5**，算法 **0 success / 5 failure**，每条明确为 `required_provider_unavailable`，页面显示未判定而非 safe。
- 已在 Chrome 打开失败样本的过程页及系统约束原文抽屉；再次上传弹层可选择浦江并显示默认完整文件。第二次原生文件对话框的自动化交互不稳定，取消后改为同一 multipart API 复测，不宣称完成了第二次浏览器提交。
- 浏览器从根地址重开热启动会话并定位 `science_agent_bench_66`（6 步），再点击左侧 `p14` 切换成功（43 步），地址栏同步样本身份；可用事件与工具统计保留，判定仍明确为未判定。尝试调整窄窗口未能通过自动化改变实际窗口尺寸，本次不声明已完成窄窗口实测。
- Jev ready 后，通过同一 `POST /api/audit-sessions` 复测，会话 `5739cac897a242d69ffde191b32e8c76`：接收/解析 **5/5**，算法仍 **0 success / 5 failure**。stdout 明确课题一 success、课题二 failure、课题三 success；独立探测已配置 FilterGuard `/api/policy_conditioned_detection` 返回 **HTTP 404**。当前算法验收受课题二上游阻塞，不能把五例上传成功宣称为算法通过。
- 两次会话、十次真实 Provider 调用身份、错误、逐例原文相等检查和正式输入 Schema 校验保存在[机读证据](issue61-real-provider-sessions.json)。未改算法口径、未调整远程服务、未以其他模块或参考答案替代失败结果。

| trajectory_id | 消息原文数 | #57 赛题步骤数 | 两次算法运行 |
| --- | ---: | ---: | --- |
| s01 | 17 | 7 | failure / failure |
| p14 | 80 | 43 | failure / failure |
| s04 | 38 | 22 | failure / failure |
| science_agent_bench_66 | 11 | 6 | failure / failure |
| science_agent_bench_48 | 46 | 24 | failure / failure |

复测入口：[首次浏览器上传会话](http://platform.example.invalid:5861/?session_id=58842b4374a04699b6732c46fafba5fa&sample_id=s01)、[Jev ready 后复测会话](http://platform.example.invalid:5861/?session_id=5739cac897a242d69ffde191b32e8c76&sample_id=science_agent_bench_66)。

再次复现（不要把内部 mapping 或参考答案作为上传输入）：

```bash
curl -fsS --max-time 21600 -F adapter=openai-messages \
  -F trajectory=@acceptance/saima-risk-visualization-sample-v1/raw/samples.jsonl \
  http://platform.example.invalid:5861/api/audit-sessions -o session.json
```

返回 `ok=true` 表示会话已建立；必须逐条核对 `samples[].ok` 才能报告算法运行成功。
