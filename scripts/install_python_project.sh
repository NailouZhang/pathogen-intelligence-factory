#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PIF_PYTHON_BIN:-python}"
INSTALL_MODE="${1:-editable}"
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_NO_INPUT=1
export PIP_DEFAULT_TIMEOUT="${PIF_PIP_TIMEOUT_SECONDS:-60}"

case "$INSTALL_MODE" in
  editable|regular) ;;
  *)
    echo "用法：PIF_PYTHON_BIN=/path/to/python bash scripts/install_python_project.sh [editable|regular]" >&2
    exit 2
    ;;
esac

command -v "$PYTHON_BIN" >/dev/null 2>&1 || {
  echo "[失败] 找不到Python解释器：$PYTHON_BIN" >&2
  exit 1
}

"$PYTHON_BIN" -m ensurepip --upgrade >/dev/null 2>&1 || true
"$PYTHON_BIN" -m pip install --upgrade -r "$ROOT/requirements-build.txt"

"$PYTHON_BIN" - <<'PY'
import importlib
import sys

for module in ("setuptools", "setuptools.build_meta", "wheel"):
    try:
        imported = importlib.import_module(module)
    except Exception as exc:
        raise SystemExit(f"[失败] 构建后端不可用：{module}: {exc}") from exc
    version = getattr(imported, "__version__", "available")
    print(f"[构建工具] {module}={version}")
print(f"[构建工具] python={sys.executable}")
PY

"$PYTHON_BIN" -m pip install -r "$ROOT/requirements.txt"

if [[ "$INSTALL_MODE" == editable ]]; then
  "$PYTHON_BIN" -m pip install --no-build-isolation --no-deps -e "$ROOT"
else
  "$PYTHON_BIN" -m pip install --no-build-isolation --no-deps "$ROOT"
fi

"$PYTHON_BIN" - <<'PY'
from importlib import metadata
from pathlib import Path
import pifactory

print("[安装完成] package=pathogen-intelligence-factory")
print("[安装完成] version=", metadata.version("pathogen-intelligence-factory"))
print("[安装完成] import=", Path(pifactory.__file__).resolve())
PY
