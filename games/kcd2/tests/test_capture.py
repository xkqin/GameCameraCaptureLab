from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest

from kcd2_capture_studio.capture import capture_rgb_depth_sample
from kcd2_capture_studio.models import Pose


class FakeBackend:
    def pose(self) -> Pose:
        return Pose(
            captured_at="2026-08-17T00:00:00+08:00",
            pid=123,
            x=1.0,
            y=2.0,
            z=3.0,
            q0=0.0,
            q1=0.0,
            q2=0.0,
            q3=1.0,
            pitch_degrees=5.0,
            yaw_degrees=10.0,
            roll_degrees=0.0,
            fov_degrees=63.0,
        )


class FakeOBS:
    def save_screenshot(self, path, **_kwargs):
        Path(path).write_bytes(b"rgb")
        return "Program"


class FakeDepthBridge:
    def begin_capture(self):
        return SimpleNamespace(request_id="request")

    def wait_capture(self, _ticket, output_dir, *, timeout):
        self.timeout = timeout
        depth = Path(output_dir) / "depth.npy"
        preview = Path(output_dir) / "depth_preview.png"
        depth.write_bytes(b"npy")
        preview.write_bytes(b"png")
        return {
            "status": "completed",
            "captured_unix_ns": time.time_ns(),
            "depth_path": str(depth),
            "preview_path": str(preview),
            "depth_space": "raw_device_depth",
            "metric_depth": False,
        }

    def cancel(self, _ticket):
        raise AssertionError("successful capture should not be cancelled")


class StaticCaptureTests(unittest.TestCase):
    def test_rgb_pose_depth_sample_has_auditable_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bridge = FakeDepthBridge()
            result = capture_rgb_depth_sample(
                FakeBackend(),
                FakeOBS(),
                bridge,
                sample_dir=root,
                source_name="",
                image_format="jpg",
                width=1920,
                height=1080,
                quality=100,
                depth_enabled=True,
                depth_timeout=3.5,
                metadata={"scene_id": "scene", "sample_index": 1},
            )
            self.assertEqual(bridge.timeout, 3.5)
            self.assertTrue(result["image_path"].exists())
            self.assertTrue(Path(result["depth"]["depth_path"]).exists())
            payload = json.loads(
                Path(result["sample_metadata_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(payload["schema_version"], "camera-static-sample/v1")
            self.assertEqual(payload["coordinate_system"]["meters_per_unit"], 1.0)
            self.assertEqual(payload["pose"]["before"]["position_m"]["x"], 1.0)
            self.assertTrue(payload["pose"]["camera_static"])
            self.assertEqual(payload["depth"]["depth_space"], "raw_device_depth")
            self.assertFalse(payload["depth"]["metric_depth"])
            self.assertEqual(
                payload["synchronization"]["status"],
                "static_camera_best_effort",
            )


if __name__ == "__main__":
    unittest.main()
