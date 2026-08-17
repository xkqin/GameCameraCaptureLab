from __future__ import annotations

import os
from pathlib import Path


DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = Path(
    os.environ.get("GAME_CAMERA_ADAPTER_ROOT", str(DEFAULT_PROJECT_ROOT))
).expanduser().resolve()
REPOSITORY_ROOT = DEFAULT_PROJECT_ROOT.parents[1]
DATA_ROOT = Path(
    os.environ.get("GAME_CAMERA_DATA_ROOT", str(PROJECT_ROOT / "capture_data"))
).expanduser().resolve()
POINT_FILES_DIR = DATA_ROOT / "point_files"
ACTIVE_POINT_MAP_PATH = POINT_FILES_DIR / "current_point_map.json"
TRAJECTORY_FILES_DIR = DATA_ROOT / "trajectory_files"
CAPTURES_DIR = DATA_ROOT / "captures"
STATIC_CAPTURES_DIR = DATA_ROOT / "still_captures"
TRAJECTORY_CAPTURES_DIR = DATA_ROOT / "trajectory_captures"
LOGS_DIR = DATA_ROOT / "logs"
SETTINGS_PATH = Path(
    os.environ.get("GAME_CAMERA_SETTINGS_PATH", str(PROJECT_ROOT / "settings.json"))
).expanduser().resolve()
NATIVE_DIR = Path(
    os.environ.get("UE_CAMERA_NATIVE_DIR", str(DEFAULT_PROJECT_ROOT / "native"))
).expanduser().resolve()
LEGACY_BRIDGE_PATH = (
    NATIVE_DIR / "build_standalone_v1" / "Release" / "BmwCameraBridge.dll"
)
LEGACY_INJECTOR_PATH = (
    NATIVE_DIR / "build_standalone_v1" / "Release" / "BmwCameraInjector.exe"
)
UE_RUNTIME_PATH = (
    NATIVE_DIR / "build_standalone_v1" / "Release" / "UeCameraRuntime.dll"
)
UE_INJECTOR_PATH = (
    NATIVE_DIR / "build_standalone_v1" / "Release" / "UeCameraInjector.exe"
)
NATIVE_BUILD_SCRIPT_PATH = NATIVE_DIR / "build_standalone.ps1"
UE_PROFILE_DIR = REPOSITORY_ROOT / "core" / "runtime" / "ue-camera-runtime" / "profiles"
ACTIVE_RUNTIME_CONFIG_PATH = LOGS_DIR / "ue_camera_active_profile.json"
PREFLIGHT_DIAGNOSTIC_PATH = LOGS_DIR / "ue_camera_preflight_latest.json"
# Use the profile-driven runtime when it has been built; keep the legacy names
# as a compatibility fallback for older working trees.
BRIDGE_PATH = UE_RUNTIME_PATH if UE_RUNTIME_PATH.is_file() else LEGACY_BRIDGE_PATH
INJECTOR_PATH = UE_INJECTOR_PATH if UE_INJECTOR_PATH.is_file() else LEGACY_INJECTOR_PATH


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
