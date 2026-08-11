from __future__ import annotations

import json
from typing import Any

from .paths import SETTINGS_PATH


DEFAULT_SETTINGS: dict[str, Any] = {
    "scene_id": "new_scene",
    "target_point_count": 8,
    "grid": {"x": 5, "y": 5, "z": 3},
    "pose_logger_hz": 30.0,
    "trajectory": {
        "duration": 8.0,
        "hz": 20.0,
        "xy_scale": 12.0,
    },
    "obs": {
        "host": "127.0.0.1",
        "port": 4455,
        "source": "",
        "image_format": "jpg",
        "width": 1920,
        "height": 1080,
        "quality": 100,
    },
}


def load_settings() -> dict[str, Any]:
    merged = json.loads(json.dumps(DEFAULT_SETTINGS))
    if not SETTINGS_PATH.exists():
        return merged
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return merged
    _deep_update(merged, raw)
    return merged


def save_settings(settings: dict[str, Any]) -> None:
    sanitized = json.loads(json.dumps(settings))
    sanitized.get("obs", {}).pop("password", None)
    SETTINGS_PATH.write_text(
        json.dumps(sanitized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _deep_update(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value
