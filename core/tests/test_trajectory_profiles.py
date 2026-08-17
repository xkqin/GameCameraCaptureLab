from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from re9_pose_recorder.config import AppConfig
from re9_pose_recorder.still_scan_gui import (
    SCENE_2_AGAIN_13000_OUTPUT_SUBDIR,
    SCENE_2_AGAIN_13000_TRAJECTORY_JSON,
    SCENE_3_1_15000_OUTPUT_SUBDIR,
    SCENE_3_1_15000_TRAJECTORY_JSON,
    SCENE_3_2_13000_OUTPUT_SUBDIR,
    SCENE_3_2_13000_TRAJECTORY_JSON,
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

    def test_scene_3_profiles_use_dedicated_capture_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            default_root = Path(temp_dir) / "old-captures"
            scene_3_root = Path(temp_dir) / "scene-3-captures"
            config = AppConfig(
                raw={
                    "trajectory": {
                        "capture_root": str(default_root),
                        "capture_roots": {"scene_3": str(scene_3_root)},
                    }
                },
                path=Path(temp_dir) / "config.yaml",
            )
            profiles = configured_trajectory_sets(config)

        scene_2_profile = profiles[
            "scene_2_again_true_gain2_distance4_step4_singleanchor_balanced_fast64_13000"
        ]
        scene_3_1_profile = profiles[
            "scene_3_1_true_gain2p5_distance10_step8_singlemax_globaloverlap90_fast64_15000"
        ]
        scene_3_2_profile = profiles[
            "scene_3_2_true_gain2p5_distance10_step8_singlemax_globaloverlap90_fast64_13000"
        ]

        self.assertEqual(
            scene_2_profile["output_dir"],
            default_root / SCENE_2_AGAIN_13000_OUTPUT_SUBDIR,
        )
        self.assertEqual(scene_3_1_profile["json"], SCENE_3_1_15000_TRAJECTORY_JSON)
        self.assertEqual(
            scene_3_1_profile["output_dir"],
            scene_3_root / SCENE_3_1_15000_OUTPUT_SUBDIR,
        )
        self.assertEqual(scene_3_2_profile["json"], SCENE_3_2_13000_TRAJECTORY_JSON)
        self.assertEqual(
            scene_3_2_profile["output_dir"],
            scene_3_root / SCENE_3_2_13000_OUTPUT_SUBDIR,
        )
        self.assertIs(scene_3_1_profile["trust_run_state"], True)
        self.assertIs(scene_3_2_profile["trust_run_state"], True)


if __name__ == "__main__":
    unittest.main()
