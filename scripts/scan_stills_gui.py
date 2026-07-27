from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from re9_pose_recorder.cli import app  # noqa: E402


if __name__ == "__main__":
    sys.argv = [
        sys.argv[0],
        "scan-stills-gui",
        "--obs-password",
        "123456",
        "--layers-config",
        "configs/still_scan_layers.yaml",
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
    ]
    app()

