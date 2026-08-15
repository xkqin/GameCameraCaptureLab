from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import SETTINGS_PATH


DEFAULTS: dict[str, Any] = {
    "language": "zh",
    "bridge_endpoint": "",
    "capture_interval_sec": 0.12,
    "position_tolerance": 4.0,
    "angle_tolerance_degrees": 1.5,
    "fov_tolerance_degrees": 0.5,
    "move_pulse_sec": 0.035,
    "rotate_pulse_sec": 0.025,
    "max_move_seconds": 25.0,
    "native_feedback_timeout_sec": 0.5,
    "screenshot_format": "jpg",
    "obs_host": "127.0.0.1",
    "obs_port": 4455,
    "pose_log_hz": 30.0,
    "trajectory_playback_hz": 60.0,
    "trajectory_obs_restart_interval_sec": 30.0,
    "obs_restart_command": "",
    "obs_restart_wait_sec": 20.0,
    "scene_id": "scene_1",
    "autoload_trajectory": "",
    "always_on_top": False,
}


def load_settings(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    result = dict(DEFAULTS)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return result
    if isinstance(raw, dict):
        result.update(raw)
    return result


def save_settings(value: dict[str, Any], path: Path = SETTINGS_PATH) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
