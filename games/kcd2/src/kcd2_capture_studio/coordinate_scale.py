"""KCD2 metric scale supplied by the dataset owner: 1 game unit = 1 metre."""

from __future__ import annotations

from pathlib import Path
import sys


try:
    from game_camera_capture_lab.coordinates import game_coordinate_scale
except ModuleNotFoundError:
    _core_src = Path(__file__).resolve().parents[4] / "core" / "src"
    if str(_core_src) not in sys.path:
        sys.path.insert(0, str(_core_src))
    from game_camera_capture_lab.coordinates import game_coordinate_scale


COORDINATE_SCALE = game_coordinate_scale("kcd2")
