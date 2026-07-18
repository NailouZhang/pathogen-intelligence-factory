#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA_SH="${CONDA_SH:-/home/stone/20T/DataBase/SoftwaresEnsembel/MiniConda/etc/profile.d/conda.sh}"
ENV_PREFIX="${PIF_ENV_PREFIX:-$ROOT/.conda-env}"
[[ -f "$CONDA_SH" ]] || { echo "Missing conda.sh: $CONDA_SH" >&2; exit 1; }
# shellcheck disable=SC1090
source "$CONDA_SH"
if [[ ! -x "$ENV_PREFIX/bin/python" ]]; then
  conda create --prefix "$ENV_PREFIX" python=3.12 pip -y
fi
"$ENV_PREFIX/bin/python" -m pip install --upgrade pip setuptools wheel
"$ENV_PREFIX/bin/python" -m pip install -r "$ROOT/requirements.txt"
"$ENV_PREFIX/bin/python" -m pip install --no-build-isolation --no-deps -e "$ROOT"
"$ENV_PREFIX/bin/python" -c 'import pifactory, pathlib; print("Installed package:", pathlib.Path(pifactory.__file__).resolve())'
echo "Ready: $ENV_PREFIX/bin/python"
echo "Test: $ENV_PREFIX/bin/python -m pytest -q"
