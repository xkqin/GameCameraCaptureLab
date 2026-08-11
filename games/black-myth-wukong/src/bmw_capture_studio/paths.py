from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "capture_data"
POINT_FILES_DIR = DATA_ROOT / "point_files"
TRAJECTORY_FILES_DIR = DATA_ROOT / "trajectory_files"
CAPTURES_DIR = DATA_ROOT / "captures"
LOGS_DIR = DATA_ROOT / "logs"
SETTINGS_PATH = PROJECT_ROOT / "settings.json"
NATIVE_DIR = PROJECT_ROOT / "native"
BRIDGE_PATH = NATIVE_DIR / "build" / "Release" / "IgcsConnector.addon64"
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
        LOGS_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
