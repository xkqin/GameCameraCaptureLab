from __future__ import annotations

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
