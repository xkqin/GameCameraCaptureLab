from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "capture_studio_data"
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
SETTINGS_PATH = PROJECT_ROOT / "capture_studio_settings.json"
POSE_CONFIG_PATH = PROJECT_ROOT / "pose_offsets.json"
CAMERA_TOOLS_DIR = PROJECT_ROOT / "camera_tools"


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
    ):
        path.mkdir(parents=True, exist_ok=True)
