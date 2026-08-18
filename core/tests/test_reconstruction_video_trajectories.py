from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

from re9_pose_recorder.trajectory_replay import load_replay_trajectory


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRAJECTORY_ROOT = PROJECT_ROOT / "data" / "reconstruction_video_trajectories"


class ReconstructionVideoTrajectoryTests(unittest.TestCase):
    def test_scene_3_video_routes_are_detailed_continuous_and_ui_loadable(self) -> None:
        expectations = {
            "scene_3.1": {"segments": 11, "positions": 4_856},
            "scene_3.2": {"segments": 23, "positions": 7_527},
        }
        for scene_id, expected in expectations.items():
            with self.subTest(scene_id=scene_id):
                path = TRAJECTORY_ROOT / f"{scene_id}_reconstruction_video_trajectories.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["logical_trajectory_count"], 7)
                self.assertEqual(payload["trajectory_count"], expected["segments"])
                self.assertEqual(payload["source"]["source_position_count"], expected["positions"])
                self.assertEqual(
                    [route["pitch_deg"] for route in payload["logical_trajectories"]],
                    [0.0, -10.0, -25.0, -40.0, -55.0, -82.0, -82.0],
                )

                loaded = load_replay_trajectory(path, trajectory_index=1)
                self.assertGreater(len(loaded.keyframes), 2)

                by_logical: dict[str, list[dict]] = {}
                for segment in payload["trajectories"]:
                    self.assertLessEqual(segment["duration_sec"], 180.0)
                    self.assertAlmostEqual(
                        segment["path_length_game_units"],
                        segment["duration_sec"] * 4.0,
                        places=5,
                    )
                    frames = segment["keyframes"]
                    self.assertEqual(frames[0]["time_sec"], 0.0)
                    self.assertTrue(
                        all(right["time_sec"] > left["time_sec"] for left, right in zip(frames, frames[1:]))
                    )
                    self.assertLessEqual(
                        max(frame["distance_from_previous"] for frame in frames),
                        2.0,
                    )
                    by_logical.setdefault(segment["logical_trajectory_id"], []).append(segment)

                self.assertEqual(len(by_logical), 7)
                for segments in by_logical.values():
                    segments.sort(key=lambda item: item["segment_index"])
                    for previous, current in zip(segments, segments[1:]):
                        previous_end = previous["keyframes"][-1]
                        current_start = current["keyframes"][0]
                        for field in ("x", "y", "z", "yaw", "pitch", "route_time_sec"):
                            self.assertTrue(
                                math.isclose(previous_end[field], current_start[field], abs_tol=1e-9),
                                msg=f"{previous['trajectory_id']} -> {current['trajectory_id']} differs at {field}",
                            )


if __name__ == "__main__":
    unittest.main()
