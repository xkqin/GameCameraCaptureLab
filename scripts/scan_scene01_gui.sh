#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

PYTHON_BIN="${PYTHON_BIN:-${PROJECT_ROOT}/.venv/bin/python}"
CONFIG_PATH="${RE9_CONFIG:-core/configs/linux.local.yaml}"
if [[ ! -f "${CONFIG_PATH}" ]]; then
  CONFIG_PATH="core/configs/linux.yaml"
fi

export PYTHONPATH="${PROJECT_ROOT}/core/src:${PYTHONPATH:-}"

"${PYTHON_BIN}" -m re9_pose_recorder.cli scan-stills-gui \
  --config "${CONFIG_PATH}" \
  --obs-password "${OBS_PASSWORD:-123456}" \
  --layers-config core/configs/scene01_scan_layers.yaml \
  --session-id scene_1 \
  --settle-seconds 0.6 \
  --image-format jpg \
  --image-width 1920 \
  --image-height 1080 \
  --image-quality 100
