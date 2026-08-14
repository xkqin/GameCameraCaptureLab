from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "capture_data"
POINT_FILES_DIR = DATA_ROOT / "point_files"
ACTIVE_POINT_MAP_PATH = POINT_FILES_DIR / "current_point_map.json"
TRAJECTORY_FILES_DIR = DATA_ROOT / "trajectory_files"
CAPTURES_DIR = DATA_ROOT / "captures"
STATIC_CAPTURES_DIR = DATA_ROOT / "still_captures"
TRAJECTORY_CAPTURES_DIR = DATA_ROOT / "trajectory_captures"
LOGS_DIR = DATA_ROOT / "logs"
SETTINGS_PATH = PROJECT_ROOT / "settings.json"
NATIVE_DIR = PROJECT_ROOT / "native"
_STABLE_BRIDGE_PATH = NATIVE_DIR / "build_smooth_v4" / "Release" / "IgcsConnector.addon64"
_V6_BRIDGE_PATH = NATIVE_DIR / "build_smooth_v3" / "Release" / "IgcsConnector.addon64"
_V5_BRIDGE_PATH = NATIVE_DIR / "build_smooth_v2" / "Release" / "IgcsConnector.addon64"
_SMOOTH_BRIDGE_PATH = NATIVE_DIR / "build_smooth" / "Release" / "IgcsConnector.addon64"
_DEFAULT_BRIDGE_PATH = NATIVE_DIR / "build" / "Release" / "IgcsConnector.addon64"
# Prefer the latest stabilized player. Independent build directories avoid trying
# to overwrite a DLL that the running game has already loaded.
BRIDGE_PATH = next(
    (
        candidate
        for candidate in (
            _STABLE_BRIDGE_PATH,
            _V6_BRIDGE_PATH,
            _V5_BRIDGE_PATH,
            _SMOOTH_BRIDGE_PATH,
            _DEFAULT_BRIDGE_PATH,
        )
        if candidate.is_file()
    ),
    _STABLE_BRIDGE_PATH,
)
DEFAULT_UUU_DIR = Path(
    os.environ.get("BMW_UUU_DIR")
    or Path.home() / "Downloads" / "UUU_v5.8.21"
)


def ensure_directories() -> None:
    for directory in (
        DATA_ROOT,
        POINT_FILES_DIR,
        TRAJECTORY_FILES_DIR,
        CAPTURES_DIR,
        STATIC_CAPTURES_DIR,
        TRAJECTORY_CAPTURES_DIR,
        LOGS_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
