#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA_SH="${CONDA_SH:-/home/stone/20T/DataBase/SoftwaresEnsembel/MiniConda/etc/profile.d/conda.sh}"
ENV_PREFIX="${PIF_ENV_PREFIX:-$ROOT/.conda-env}"

[[ -f "$CONDA_SH" ]] || { echo "[失败] 缺少 Conda 初始化脚本：$CONDA_SH" >&2; exit 1; }
[[ -x "$ENV_PREFIX/bin/python" ]] || { echo "[失败] 缺少环境：$ENV_PREFIX；先运行 scripts/bootstrap_dev.sh" >&2; exit 1; }
command -v git >/dev/null 2>&1 || { echo "[失败] 缺少 git" >&2; exit 1; }
command -v gh >/dev/null 2>&1 || { echo "[失败] 缺少 gh" >&2; exit 1; }

cd "$ROOT"
"$ENV_PREFIX/bin/python" scripts/validate_all_profiles.py
"$ENV_PREFIX/bin/python" scripts/audit_query_coverage.py --output /tmp/pif-query-coverage.json
"$ENV_PREFIX/bin/python" -m compileall -q src scripts tests
"$ENV_PREFIX/bin/python" -m pytest -q
printf '[完成] 公开仓本地检查通过\n仓库：%s\nPython：%s\n' "$ROOT" "$ENV_PREFIX/bin/python"
