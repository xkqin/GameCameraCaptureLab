from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from re9_pose_recorder.config import AppConfig
from re9_pose_recorder.still_scan_gui import (
    SCENE_2_AGAIN_13000_OUTPUT_SUBDIR,
    SCENE_2_AGAIN_13000_TRAJECTORY_JSON,
    configured_trajectory_sets,
)


class TrajectoryProfileTests(unittest.TestCase):
    def test_scene_2_again_profile_uses_configured_capture_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            capture_root = Path(temp_dir) / "captures"
            config = AppConfig(
                raw={"trajectory": {"capture_root": str(capture_root)}},
                path=Path(temp_dir) / "config.yaml",
            )
            profiles = configured_trajectory_sets(config)

        profile = profiles[
            "scene_2_again_true_gain2_distance4_step4_singleanchor_balanced_fast64_13000"
        ]
        self.assertEqual(profile["json"], SCENE_2_AGAIN_13000_TRAJECTORY_JSON)
        self.assertEqual(profile["output_dir"], capture_root / SCENE_2_AGAIN_13000_OUTPUT_SUBDIR)
        self.assertIs(profile["trust_run_state"], True)


if __name__ == "__main__":
    unittest.main()
