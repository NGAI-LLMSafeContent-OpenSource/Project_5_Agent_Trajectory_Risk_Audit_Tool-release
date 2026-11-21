#!/usr/bin/env bash
# 在 WSL 中：准备 1 条子集，并用课题五 Adapter 调用真实 Topic4 CLI（offline）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOPIC4_ROOT="${TOPIC4_FINIAL_ROOT:-$ROOT/../Project_4-5_ContentToolRisk/课题五/finial}"
PY="${ROOT}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3)"
fi

cd "$ROOT"
"$PY" scripts/prepare_topic4_real_debug.py --topic4-root "$TOPIC4_ROOT"

# Adapter 会再追加: run --input ... --output ... --config ...
# 这里 --command 必须是「可执行入口前缀」，用绝对路径避免 FileNotFoundError。
COMMAND="$PY ${TOPIC4_ROOT}/topic4-audit"

export PYTHONPATH="${TOPIC4_ROOT}${PYTHONPATH:+:$PYTHONPATH}"

mkdir -p runs/topic4-real-debug-archive
"$PY" -m trajectory_audit topic4-run \
  --input fixtures/topic4_provider/real_offline_one_item_input.json \
  --config fixtures/topic4_provider/real_offline_config.json \
  --command "$COMMAND" \
  --out runs/topic4-real-debug-archive \
  --timeout 120

echo
echo "归档目录: $ROOT/runs/topic4-real-debug-archive"
ls -la runs/topic4-real-debug-archive || true
