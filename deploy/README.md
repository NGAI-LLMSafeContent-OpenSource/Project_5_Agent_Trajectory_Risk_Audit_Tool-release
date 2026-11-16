# ModelScope 部署

运行类型 gradio，入口 app.py，Gradio 版本与 requirements.txt 中一致，使用可用的免费 CPU 资源。应用监听 0.0.0.0:7860，不需要 GPU、模型或模型 API 密钥。

本地：`python -m pip install -r requirements.txt`，然后 `python app.py`。访问 http://127.0.0.1:7860，选择一个公开样例或上传最大 4 MiB 的 JSON，选择正确的单条/批量模式和适配器，点击开始审计并下载 ZIP；解压后打开 index.html。

审计复用原 CLI，固定使用 deterministic-baseline，Judge 关闭；结果为规则检查，不代表模型语义判断。网页提供的报告为临时下载，生成文件会定期清理，不作为永久存储。每个请求使用独立工作目录，输入轨迹不会当作命令执行。

先更新 GitHub release/1.0 并同步各平台，再将 Studio 设置为 gradio、选择与 requirements.txt 一致的 SDK 版本和 Python 3.11+ 基础镜像，触发部署并检查运行日志、上传、审计及报告下载。
