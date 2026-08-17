#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "python3 was not found. Install Python 3.10+ and python3-venv first." >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  echo "git was not found. Install git first." >&2
  exit 1
fi

"${PYTHON_BIN}" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10+ is required.")
PY

"${PYTHON_BIN}" -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [[ ! -f core/configs/linux.local.yaml ]]; then
  cp core/configs/linux.yaml core/configs/linux.local.yaml
  echo "Created core/configs/linux.local.yaml. Edit its Steam/Proton and OBS paths before capture."
fi

echo
echo "Linux environment ready."
echo "Next:"
echo "  source .venv/bin/activate"
echo "  edit core/configs/linux.local.yaml with your Steam/Proton game path"
echo "  export RE9_CONFIG=core/configs/linux.local.yaml"
echo "  python -m re9_pose_recorder.cli setup-laion --config core/configs/linux.local.yaml"
