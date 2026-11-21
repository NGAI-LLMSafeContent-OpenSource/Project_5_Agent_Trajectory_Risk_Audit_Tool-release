#!/usr/bin/env bash
# 本机启动 Review Workbench 前端（Vite，/api 代理到 8765）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT/web"
if [[ ! -d node_modules ]]; then
  npm install
fi
exec npm run dev -- --host "${HOST:-127.0.0.1}"
