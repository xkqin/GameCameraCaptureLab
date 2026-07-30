#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

PYTHON_BIN="${PYTHON_BIN:-${PROJECT_ROOT}/.venv/bin/python}"
if [[ -n "${RE9_CONFIG:-}" ]]; then
  CONFIG_PATH="${RE9_CONFIG}"
elif [[ -f configs/linux.local.yaml ]]; then
  CONFIG_PATH="configs/linux.local.yaml"
else
  CONFIG_PATH="configs/linux.yaml"
fi

export PYTHONPATH="${PROJECT_ROOT}/src:${PYTHONPATH:-}"

ARGS=(
  -m re9_pose_recorder.cli scan-stills-gui
  --config "${CONFIG_PATH}"
  --obs-password "${OBS_PASSWORD:-123456}"
  --session-id "${SESSION_ID:-scene_linux}"
  --settle-seconds "${SETTLE_SECONDS:-0.6}"
  --image-format "${IMAGE_FORMAT:-jpg}"
  --image-width "${IMAGE_WIDTH:-1920}"
  --image-height "${IMAGE_HEIGHT:-1080}"
  --image-quality "${IMAGE_QUALITY:-100}"
)

if [[ -n "${POSE_PLAN_CONFIG:-}" ]]; then
  ARGS+=(--pose-plan-config "${POSE_PLAN_CONFIG}")
elif [[ -n "${LAYERS_CONFIG:-}" ]]; then
  ARGS+=(--layers-config "${LAYERS_CONFIG}")
else
  ARGS+=(--layers-config configs/scene01_scan_layers.yaml)
fi

if [[ -n "${TRAJECTORY_SET:-}" ]]; then
  ARGS+=(--trajectory-set "${TRAJECTORY_SET}")
fi
if [[ -n "${TRAJECTORY_JSON:-}" ]]; then
  ARGS+=(--trajectory-json "${TRAJECTORY_JSON}")
fi
if [[ -n "${TRAJECTORY_OUTPUT_DIR:-}" ]]; then
  ARGS+=(--trajectory-output-dir "${TRAJECTORY_OUTPUT_DIR}")
fi
if [[ -n "${TRAJECTORY_LABEL:-}" ]]; then
  ARGS+=(--trajectory-label "${TRAJECTORY_LABEL}")
fi
if [[ -n "${TRAJECTORY_SESSION_PREFIX:-}" ]]; then
  ARGS+=(--trajectory-session-prefix "${TRAJECTORY_SESSION_PREFIX}")
fi

"${PYTHON_BIN}" "${ARGS[@]}" "$@"
