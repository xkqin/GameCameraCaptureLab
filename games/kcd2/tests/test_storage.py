from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kcd2_capture_studio.models import Pose
from kcd2_capture_studio import storage


def sample_pose(x: float = 1.0) -> Pose:
    return Pose(
        captured_at="2026-07-28T00:00:00+08:00",
        pid=123,
        x=x,
        y=2.0,
        z=3.0,
        q0=0.0,
        q1=0.0,
        q2=0.0,
        q3=1.0,
        pitch_degrees=4.0,
        yaw_degrees=5.0,
        roll_degrees=6.0,
        fov_degrees=63.0,
    )


class PointStoreTests(unittest.TestCase):
    def test_append_load_and_backup_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            points_dir = root / "points"
            backups_dir = root / "backups"
            backups_dir.mkdir()
            with (
                patch.object(storage, "POINTS_DIR", points_dir),
                patch.object(storage, "BACKUPS_DIR", backups_dir),
            ):
                store = storage.PointStore("scene 01")
                first = store.append(sample_pose(), "corner")
                second = store.append(sample_pose(9.0), "door")
                self.assertEqual(first.index, 1)
                self.assertEqual(second.index, 2)
                loaded = store.load()
                self.assertEqual([point.label for point in loaded], ["corner", "door"])
                self.assertEqual(loaded[1].pose.x, 9.0)
                payload = json.loads(store.json_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["schema_version"], "camera-point-set/v1")
                self.assertEqual(payload["game_id"], "kcd2")
                self.assertEqual(payload["points"][0]["id"], "point_0001")
                self.assertEqual(payload["points"][1]["pose"]["position"]["x"], 9.0)
                self.assertEqual(payload["coordinate_system"]["meters_per_unit"], 1.0)
                self.assertEqual(payload["points"][1]["pose"]["position_m"]["x"], 9.0)

                csv_backup, json_backup = store.reset()
                self.assertIsNotNone(csv_backup)
                self.assertIsNotNone(json_backup)
                self.assertTrue(csv_backup.exists())
                self.assertTrue(json_backup.exists())
                self.assertEqual(store.load(), [])

    def test_trajectory_store_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            trajectories = Path(temp)
            with patch.object(storage, "TRAJECTORIES_DIR", trajectories):
                store = storage.TrajectoryStore("walk 01")
                frame = store.append_pose(sample_pose())
                loaded = store.load()
                self.assertEqual(frame.step, 0)
                self.assertEqual(len(loaded), 1)
                self.assertEqual(loaded[0].yaw_degrees, 5.0)
                payload = json.loads(store.json_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["trajectory_id"], "walk_01")
                self.assertEqual(payload["schema_version"], "camera-trajectory/v1")
                self.assertEqual(payload["game_id"], "kcd2")
                self.assertEqual(payload["keyframes"][0]["index"], 0)
                self.assertEqual(payload["keyframes"][0]["pose"]["rotation"]["yaw"], 5.0)
                self.assertEqual(payload["coordinate_system"]["position_unit"], "meters")
                self.assertEqual(payload["keyframes"][0]["pose"]["position_m"]["z"], 3.0)

                store.reset()
                self.assertFalse(store.json_path.exists())
                self.assertFalse(store.csv_path.exists())
                self.assertEqual(store.load(), [])


if __name__ == "__main__":
    unittest.main()
