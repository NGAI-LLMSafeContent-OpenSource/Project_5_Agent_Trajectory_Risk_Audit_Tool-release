# SPDX-License-Identifier: Apache-2.0
"""ModelScope web entry for the existing offline audit CLI."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

os.environ.setdefault("GRADIO_TEMP_DIR", tempfile.mkdtemp(prefix="trajectory-audit-ui-"))
ROOT = Path(__file__).resolve().parent
OUTPUTS = Path(os.environ.get("AUDIT_DOWNLOAD_DIR", str(Path(tempfile.gettempdir()) / "trajectory-audit-downloads")))
MAX_BYTES = 4 * 1024 * 1024


def audit_file(filename, mode="run", adapter="openai-messages"):
    if mode not in {"run", "batch"} or adapter not in {"openai-messages", "atbench", "hintbench"}:
        raise ValueError("无效的运行模式或输入格式")
    if not filename:
        raise ValueError("请先上传 JSON 文件")
    source = Path(filename)
    if not source.is_file() or source.stat().st_size > MAX_BYTES:
        raise ValueError("文件不存在或超过 4 MiB")
    # Bounded read also covers a file growing after stat().
    with source.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("文件超过 4 MiB")
    json.loads(raw)
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    for old in OUTPUTS.glob("*.zip"):
        try:
            if old.stat().st_mtime < time.time() - 3600:
                old.unlink()
        except FileNotFoundError:
            pass
    with tempfile.TemporaryDirectory(prefix="trajectory-audit-") as temp:
        temp = Path(temp)
        input_path = temp / "trajectory.json"
        input_path.write_bytes(raw)
        command = [sys.executable, "-m", "trajectory_audit", mode, "--input", str(input_path), "--out", str(temp / "runs"), "--provider", "deterministic-baseline", "--adapter", adapter]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
        if result.returncode not in {0, 4}:
            raise ValueError("输入审计失败，请确认 JSON 契约和输入格式一致")
        summary = json.loads(result.stdout)
        # Do not display temporary server paths in the product UI.
        summary = {k: v for k, v in summary.items() if not k.endswith(("_dir", "_path"))}
        audits = [json.loads(p.read_text()) for p in sorted((temp / "runs").rglob("audit_result.json"))]
        archive = shutil.make_archive(str(OUTPUTS / uuid.uuid4().hex), "zip", temp / "runs")
        return {"summary": summary, "audit_results": audits}, archive


def build_app():
    import gradio as gr
    def submit(filename, mode, adapter):
        try:
            return audit_file(filename, mode, adapter)
        except (ValueError, OSError, subprocess.TimeoutExpired, RecursionError):
            raise gr.Error("无法完成审计：请检查文件格式、大小以及单条/批量模式。") from None
    with gr.Blocks(title="Agent 轨迹风险审计", delete_cache=(60, 3600)) as demo:
        gr.Markdown("# Agent 轨迹风险审计\n上传 JSON，执行离线规则审计并下载完整报告。使用 deterministic-baseline，不调用模型 API，结果不代表语义模型判断。")
        upload = gr.File(label="轨迹 JSON（最大 4 MiB）", file_types=[".json"], type="filepath")
        mode = gr.Radio([("单条轨迹", "run"), ("批量轨迹", "batch")], value="run", label="运行模式")
        adapter = gr.Dropdown(["openai-messages", "atbench", "hintbench"], value="openai-messages", label="输入格式")
        button = gr.Button("开始审计", variant="primary")
        output = gr.JSON(label="审计结果")
        download = gr.File(label="下载报告 ZIP（解压后打开 index.html）")
        gr.Examples(examples=[[str(ROOT / "fixtures/smoke/openai_messages_smoke.json"), "run", "openai-messages"], [str(ROOT / "fixtures/smoke/atbench_smoke.json"), "batch", "atbench"], [str(ROOT / "fixtures/smoke/hintbench_smoke.json"), "batch", "hintbench"]], inputs=[upload, mode, adapter])
        button.click(submit, inputs=[upload, mode, adapter], outputs=[output, download], api_name="audit")
    return demo.queue(max_size=8, default_concurrency_limit=1)


if __name__ == "__main__":
    build_app().launch(server_name="0.0.0.0", server_port=int(os.environ.get("PORT", "7860")), share=False, show_error=False, max_file_size="4mb")
