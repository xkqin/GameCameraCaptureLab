#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

PYTHON_BIN="${PYTHON_BIN:-${PROJECT_ROOT}/.venv/bin/python}"
export PYTHONPATH="${PROJECT_ROOT}/core/src:${PYTHONPATH:-}"

"${PYTHON_BIN}" scripts/analyze_stutter_all.py "$@"
