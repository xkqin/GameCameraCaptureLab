from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "core" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from re9_pose_recorder.cli import app  # noqa: E402


if __name__ == "__main__":
    trajectory_json = (
        PROJECT_ROOT
        / "data"
        / "trajectory_exports"
        / "scene_2_true_keyframes_gain2p3_distance4_step4_singleanchor_smoke15_cluster3"
        / "scene_2_true_gain2p3_distance4_step4_singleanchor_smoke15_cluster3_low_to_high_ui.json"
    )
    trajectory_output_dir = (
        PROJECT_ROOT
        / "data"
        / "videos"
        / "trajectories"
        / "scene_2_true_keyframes_gain2p3_distance4_step4_singleanchor_smoke15_cluster3"
    )
    sys.argv = [
        sys.argv[0],
        "scan-stills-gui",
        "--obs-password",
        "123456",
        "--layers-config",
        str(PROJECT_ROOT / "core" / "configs" / "scene_2_no_lamp_scan_layers.yaml"),
        "--session-id",
        "scene_2_no_lamp",
        "--points-x",
        "5",
        "--points-z",
        "6",
        "--settle-seconds",
        "0.6",
        "--image-format",
        "jpg",
        "--image-width",
        "1920",
        "--image-height",
        "1080",
        "--image-quality",
        "100",
        "--trajectory-json",
        str(trajectory_json),
        "--trajectory-output-dir",
        str(trajectory_output_dir),
        "--trajectory-label",
        "scene_2 true keyframes gain2p3 distance4 step4 singleanchor smoke15 cluster3",
        "--trajectory-session-prefix",
        "scene_2_gain2p3_distance4_step4_singleanchor_smoke15_cluster3_traj",
    ]
    app()
