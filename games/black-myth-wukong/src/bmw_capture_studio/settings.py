from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import DEFAULT_UUU_DIR, SETTINGS_PATH


DEFAULTS: dict[str, Any] = {
    "uuu_dir": str(DEFAULT_UUU_DIR),
    "capture_interval_sec": 0.35,
    "position_tolerance": 4.0,
    "angle_tolerance_degrees": 1.5,
    "fov_tolerance_degrees": 0.5,
    "move_pulse_sec": 0.035,
    "rotate_pulse_sec": 0.025,
    "max_move_seconds": 25.0,
    "screenshot_format": "png",
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
