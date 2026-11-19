#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ -z "${CONDA_PREFIX:-}" ]]; then
  echo "请先手动激活项目 conda 环境（Python 3.12），再运行本脚本。" >&2
  exit 1
fi
if [[ -f .env ]]; then
  set -a
  source .env
  set +a
fi
exec python -m scripts.launcher admin
