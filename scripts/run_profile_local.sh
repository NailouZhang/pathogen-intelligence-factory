#!/usr/bin/env bash
set -Eeuo pipefail
[[ $# -ge 1 ]] || { echo "用法：$0 PROFILE_ID [OUTPUT_DIR] [--demo]" >&2; exit 2; }
PROFILE_ID="$1"; shift
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_PREFIX="${PIF_ENV_PREFIX:-$ROOT/.conda-env}"
OUTPUT_DIR="${1:-$ROOT/runtime/$PROFILE_ID}"
if [[ $# -gt 0 ]]; then shift; fi
[[ -x "$ENV_PREFIX/bin/python" ]] || { echo "缺少环境：$ENV_PREFIX；先运行 scripts/bootstrap_dev.sh" >&2; exit 1; }
mkdir -p "$OUTPUT_DIR" "$ROOT/runtime/shared"
export PIF_PROVIDER_STATE_FILE="${PIF_PROVIDER_STATE_FILE:-$ROOT/runtime/shared/provider_quota_daily.json}"
cd "$ROOT"
exec "$ENV_PREFIX/bin/python" -u scripts/run_daily.py \
  --profile "$PROFILE_ID" \
  --output-dir "$OUTPUT_DIR" \
  --state-dir "$OUTPUT_DIR/data/state" \
  "$@"
