from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kcd2_capture_studio.models import Pose
from kcd2_capture_studio.scan_capture import AutomatedStillScan, load_scan_samples
from kcd2_capture_studio import scan_capture


def observed_pose(target) -> Pose:
    return Pose(
        captured_at="2026-07-28T00:00:00+08:00",
        pid=123,
        x=target.x,
        y=target.y,
        z=target.z,
        q0=0,
        q1=0,
        q2=0,
        q3=1,
        pitch_degrees=target.pitch_degrees,
        yaw_degrees=target.yaw_degrees,
        roll_degrees=target.roll_degrees,
        fov_degrees=target.fov_degrees,
    )


class FakeController:
    def __init__(self) -> None:
        self.backend = self
        self.current = observed_pose(
            type(
                "Target",
                (),
                {
                    "x": 0,
                    "y": 0,
                    "z": 0,
                    "pitch_degrees": 0,
                    "yaw_degrees": 0,
                    "roll_degrees": 0,
                    "fov_degrees": 63,
                },
            )()
        )
        self.restored = False

    def move_to(self, target, strict=True):
        self.current = observed_pose(target)
        return {
            "target": target.as_dict(),
            "observed": self.current.as_dict(),
            "error": {
                "x": 0,
                "y": 0,
                "z": 0,
                "position": 0,
                "yaw_degrees": 0,
                "pitch_degrees": 0,
                "roll_degrees": 0,
                "fov_degrees": 0,
            },
            "reached": True,
        }

    def pose(self):
        return self.current

    def restore_start(self):
        self.restored = True
        return {"restored": True}


class FakeOBS:
    def save_screenshot(self, path, **kwargs):
        target = Path(path)
        target.write_bytes(b"fake-image")
        return "Program"


class ScanCaptureTests(unittest.TestCase):
    def _plan(self, root: Path) -> Path:
        samples = root / "samples.csv"
        fieldnames = [
            "sample_index",
            "point_index",
            "pattern",
            "x",
            "y",
            "z",
            "yaw_degrees",
            "pitch_degrees",
            "fov_degrees",
        ]
        with samples.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(
                [
                    {
                        "sample_index": 1,
                        "point_index": 1,
                        "pattern": "middle",
                        "x": 1,
                        "y": 2,
                        "z": 3,
                        "yaw_degrees": 0,
                        "pitch_degrees": 0,
                        "fov_degrees": 63,
                    },
                    {
                        "sample_index": 2,
                        "point_index": 1,
                        "pattern": "upper",
                        "x": 1,
                        "y": 2,
                        "z": 3,
                        "yaw_degrees": 60,
                        "pitch_degrees": 45,
                        "fov_degrees": 63,
                    },
                ]
            )
        manifest = root / "scene_plan.json"
        manifest.write_text(
            json.dumps(
                {
                    "scene_id": "scene",
                    "image_count": 2,
                    "samples_csv": str(samples),
                }
            ),
            encoding="utf-8",
        )
        return manifest

    def test_load_and_execute_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self._plan(root)
            raw, samples = load_scan_samples(manifest)
            self.assertEqual(raw["image_count"], 2)
            self.assertEqual(len(samples), 2)

            output = root / "output"
            controller = FakeController()
            runner = AutomatedStillScan(controller, FakeOBS())
            with patch.object(scan_capture, "STILLS_DIR", output):
                result = runner.run(
                    manifest,
                    scene_id="scene",
                    source_name="",
                    image_format="jpg",
                    width=640,
                    height=360,
                    quality=90,
                    settle_seconds=0,
                )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["completed_count"], 2)
            self.assertTrue(controller.restored)
            with Path(result["samples_csv"]).open(
                "r", newline="", encoding="utf-8-sig"
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(Path(row["image_path"]).exists() for row in rows))


if __name__ == "__main__":
    unittest.main()
