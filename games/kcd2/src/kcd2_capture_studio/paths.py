from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]

def _env_path(name: str, default: Path) -> Path:
    """Resolve an optional launcher-provided path without changing direct use."""
    raw = os.environ.get(name, "").strip()
    return Path(raw).expanduser().resolve() if raw else default


# The KCD2 UI can still be launched directly with its historical per-game
# folders.  The unified launcher supplies these variables to place the same
# backend under the repository-wide capture_data/kcd2 namespace.
ADAPTER_ROOT = _env_path("GAME_CAMERA_ADAPTER_ROOT", PROJECT_ROOT)
DATA_ROOT = _env_path(
    "GAME_CAMERA_DATA_ROOT",
    ADAPTER_ROOT / "capture_studio_data",
)
POINTS_DIR = DATA_ROOT / "scene_points"
PLANS_DIR = DATA_ROOT / "scan_plans"
STILLS_DIR = DATA_ROOT / "stills"
POSE_LOGS_DIR = DATA_ROOT / "pose_logs"
TRAJECTORIES_DIR = DATA_ROOT / "trajectories"
RUNS_DIR = DATA_ROOT / "runs"
BACKUPS_DIR = DATA_ROOT / "backups"
ANALYSIS_DIR = DATA_ROOT / "analysis"
REPORTS_DIR = DATA_ROOT / "reports"
MODELS_DIR = DATA_ROOT / "models"
SETTINGS_PATH = _env_path(
    "GAME_CAMERA_SETTINGS_PATH",
    ADAPTER_ROOT / "capture_studio_settings.json",
)
POSE_CONFIG_PATH = _env_path(
    "GAME_CAMERA_POSE_CONFIG_PATH",
    ADAPTER_ROOT / "pose_offsets.json",
)
CAMERA_TOOLS_DIR = _env_path(
    "GAME_CAMERA_TOOLS_DIR",
    ADAPTER_ROOT / "camera_tools",
)


def ensure_data_dirs() -> None:
    for path in (
        DATA_ROOT,
        POINTS_DIR,
        PLANS_DIR,
        STILLS_DIR,
        POSE_LOGS_DIR,
        TRAJECTORIES_DIR,
        RUNS_DIR,
        BACKUPS_DIR,
        ANALYSIS_DIR,
        REPORTS_DIR,
        MODELS_DIR,
        SETTINGS_PATH.parent,
    ):
        path.mkdir(parents=True, exist_ok=True)
