#!/usr/bin/env bash
# 启动拼豆图纸生成器（macOS / Linux）
# 用法：./scripts/start.sh [--reload]
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "未找到 uv，请先安装：https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
fi

uv sync --quiet
exec uv run python run.py "$@"
