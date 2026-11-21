#!/usr/bin/env bash
# 本机启动 Review Workbench API（默认假 topic4 provider）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
PORT="${PORT:-8765}"
HOST="${HOST:-127.0.0.1}"
exec uv run --group task-test uvicorn workbench.api.app:app --reload --host "$HOST" --port "$PORT"
