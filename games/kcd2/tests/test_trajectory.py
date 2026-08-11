from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from kcd2_capture_studio.trajectory import TrajectoryService


class DummyBackend:
    pass


class TrajectoryParseTests(unittest.TestCase):
    def test_load_external_re9_style_json(self) -> None:
        payload = {
            "trajectories": [
                {
                    "keyframes": [
                        {
                            "x": 1,
                            "y": 2,
                            "z": 3,
                            "yaw": 45,
                            "pitch": -10,
                            "roll": 2,
                            "fov": 70,
                        },
                        {
                            "time_sec": 1.25,
                            "x": 4,
                            "y": 5,
                            "z": 6,
                            "yaw_degrees": 90,
                            "pitch_degrees": 5,
                            "roll_degrees": 0,
                            "fov_degrees": 63,
                        },
                    ]
                }
            ]
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "trajectory.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            service = TrajectoryService(DummyBackend(), "parse_test")
            frames = service.load_external_json(path)
        self.assertEqual(len(frames), 2)
        self.assertEqual(service.last_import_path, path.resolve())
        self.assertEqual(service.last_import_trajectory_id, "trajectory")
        self.assertEqual(frames[0].yaw_degrees, 45.0)
        self.assertEqual(frames[0].time_sec, 0.0)
        self.assertEqual(frames[1].time_sec, 1.25)
        self.assertEqual(frames[1].x, 4.0)

    def test_generic_radian_angles_are_converted_to_degrees(self) -> None:
        payload = {
            "coordinate_system": {"angle_unit": "radians"},
            "keyframes": [
                {
                    "x": 1,
                    "y": 2,
                    "z": 3,
                    "yaw": 3.141592653589793,
                    "pitch": 0.7853981633974483,
                    "roll": 0,
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "trajectory.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            frames = TrajectoryService(
                DummyBackend(), "radian_test"
            ).load_external_json(path)
        self.assertAlmostEqual(frames[0].yaw_degrees, 180.0)
        self.assertAlmostEqual(frames[0].pitch_degrees, 45.0)


if __name__ == "__main__":
    unittest.main()
