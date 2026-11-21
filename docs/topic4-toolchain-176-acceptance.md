# 课题四完整工具链 176 验收手册

本手册用于从 176 运行课题四交付 CLI，验证课题一、课题二、课题三及轨迹语义审计。176 负责编排，Jev 和 V7 在 182 运行，经 SSH 隧道暴露在 176 的回环端口。FilterGuard 由课题二维护，通过远程 HTTPS 调用。

## 在 182 启动 Jev 和 V7

182 已安装模型服务和对应隧道的 systemd 用户单元，并启用了开机启动与用户 linger。服务可在 SSH 退出后继续运行。需要手动启动时，可从有 SSH 权限的机器执行：

```bash
ssh zx@10.184.17.182 'systemctl --user start topic4-jev topic4-jev-tunnel topic4-v7 topic4-v7-tunnel'
```

### 命令各部分的含义

- `ssh zx@10.184.17.182` 以 `zx` 身份登录 182，并在 182 执行引号里的命令。
- `systemctl` 向 systemd 发出服务管理指令。`--user` 选择当前用户的 systemd 管理器，这里是 `zx` 的管理器，通常无需 `sudo`。
- `start` 提交本次启动任务，对已运行的单元可重复执行。`restart` 会先停止再启动，可能打断正在执行的推理。`enable` 配置自动启动，`enable --now` 同时配置自动启动并立即启动。
- 后面的四个名称对应四个 `.service` 单元，可省略后缀。单独启动 Jev 时指定 `topic4-jev topic4-jev-tunnel`，单独启动 V7 时指定 `topic4-v7 topic4-v7-tunnel`。

`zx` 已配置 `Linger=yes`，因此其用户管理器可以在开机时启动，并在退出 SSH 后继续运行。四个单元均已启用自动启动。

### 单元定义与请求路径

182 上实际加载的定义在 `/home/zx/.config/systemd/user/`。每个文件的 `ExecStart=` 指定 systemd 要启动的命令。

| 单元文件 | 启动的进程与作用 |
| --- | --- |
| `topic4-jev.service` | 执行 Jev 的 `serve.sh`，使用 GPU 0 提供 `topic4-jev-27b`，监听 182 的 `127.0.0.1:18427`。课题一和轨迹语义审计共用该模型。 |
| `topic4-jev-tunnel.service` | 执行 `ssh -R`，将 176 的 `127.0.0.1:18427` 转发到 182 的 `127.0.0.1:18427`。 |
| `topic4-v7.service` | 执行 V7 的 `serve_v7.sh`，使用 GPU 1 提供课题三风险分类，监听 182 的 `127.0.0.1:18428`。 |
| `topic4-v7-tunnel.service` | 执行 `ssh -R`，将 176 的 `127.0.0.1:18428` 转发到 182 的 `127.0.0.1:18428`。 |

启动脚本分别位于 182 的 `/data/zhangxu/topic4-jev/repo/课题五/finial/deployment/182/serve.sh` 和 `/data/zhangxu/topic4-v7/repo/课题五/finial/deployment/182/serve_v7.sh`。脚本使用已安装的环境和权重。

两条 SSH 连接都由 182 发起到 176。176 上的 CLI 请求本机回环端口，SSH 隧道再把请求送到 182 上对应的模型。FilterGuard 则由 176 直接访问远程 HTTPS 接口。

### 查看定义与验证启动

用 `cat` 查看 systemd 实际加载的单元定义，用 `status` 查看运行状态。

```bash
ssh zx@10.184.17.182 'systemctl --user cat topic4-jev topic4-jev-tunnel topic4-v7 topic4-v7-tunnel'
ssh zx@10.184.17.182 'systemctl --user status topic4-jev topic4-jev-tunnel topic4-v7 topic4-v7-tunnel --no-pager'
```

模型与隧道之间未配置启动顺序依赖，这条 `start` 命令会一次提交四个启动任务。`active` 表示 systemd 跟踪的单元正在运行。启动后回到 176，用下一节的模型 `curl` 验证隧道和实际推理。

## 逐个检查依赖服务

先登录 `ssh zhangxu@10.184.17.176`，在 176 执行下列命令。三个探测都提交正常文本，直接检查实际检测或生成响应，无需激活 Python 环境。

### 课题二 FilterGuard

```bash
curl -fsS --max-time 30 \
  'https://u165979-8749-15195365.westc.seetacloud.com:8443/api/policy_conditioned_detection' \
  -H 'Content-Type: application/json' \
  -d '{"task_id":"curl-smoke","texts":["Have a nice day."]}'
```

预期 `status` 为 `success`，`api_name` 为 `policy_conditioned_detection`，`task_id` 与请求一致，`data.results` 中有一条结果。

### 课题三 V7

```bash
curl -fsS --max-time 600 \
  http://127.0.0.1:18428/v1/risk-classification \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"0123456789abcdef0123456789abcdef","input_text":"","target_text":"Have a nice day.","model_name":"Safeguard","language":"en-US"}'
```

预期 `status` 为 `success`，`request_id` 与请求一致，`data.risk_level.name` 为 `safe`。服务重启后的首次请求可能包含模型加载时间。

### Jev

```bash
curl -fsS --max-time 30 \
  http://127.0.0.1:18427/v1/chat/completions \
  -H 'Authorization: Bearer local' \
  -H 'Content-Type: application/json' \
  -d '{"model":"topic4-jev-27b","messages":[{"role":"user","content":"Reply with OK."}],"max_tokens":16,"chat_template_kwargs":{"enable_thinking":false}}'
```

预期 `model` 为 `topic4-jev-27b`，`choices[0].message.content` 非空。`local` 是当前部署的本地占位值。

## 直接运行正式 CLI

继续在 176 执行。下列命令使用 #1 指定的入口和默认配置，并为每次运行创建独立输出目录。

```bash
cd /data/zhangxu/repos/Project_4-5_ContentToolRisk/课题五/finial
source deployment/176/activate.sh
export CUDA_VISIBLE_DEVICES=""

run_dir=$(mktemp -d /data/zhangxu/topic4-176/runs/cli-XXXXXXXX)
printf '输出目录 %s\n' "$run_dir"
python -m topic4_audit run \
  --input demo/input-batch.json \
  --output "$run_dir/audit-result.json" \
  --config config/toolchain.json &&
python -m topic4_audit validate \
  --input demo/input-batch.json \
  --result "$run_dir/audit-result.json"
```

检查主结果 `audit-result.json` 和逐组件诊断 `audit-result.toolchain.json`。批次应为 `complete`，80 条样本身份应匹配且逐条为 `success`，validator 应通过。默认配置允许缓存命中。 [正式入口与部署规格](https://github.com/NGAI-LLMSafeContent/Project_4-5_ContentToolRisk/issues/1)

## 关闭缓存的验收与复跑

沿用上面的工作目录和环境。下面的脚本仍调用同一个正式 CLI，另外关闭响应缓存、设定并发、调用 validator，并保存逐条参与记录、有效配置和文件摘要。

```bash
python deployment/176/verify_full.py \
  --output-root /data/zhangxu/topic4-176/runs \
  --workers 2 --total-timeout 21600
```

脚本打印本轮目录。以退出码 0、`summary.json` 为 `complete`、80 条逐条成功且四个组件真实参与为通过条件。主结果位于 `result.json`，诊断位于 `result.toolchain.json`。需要连续做两轮独立验收时，运行 `bash deployment/176/run_full_twice.sh`，核对 `pair-summary.json` 为 `complete`、`driver-exit.txt` 为 `0`。两轮应有相同的输入及配置摘要和不同的 `provider_run_id`。

已归档的两轮各 80 条均成功，validator 均通过，分别耗时约 13 分 25 秒、12 分 46 秒。验收范围是开发集部署链路和独立复跑。 [双轮验收记录](https://github.com/NGAI-LLMSafeContent/Project_4-5_ContentToolRisk/issues/8#issuecomment-6661896698)
