from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest

from bmw_capture_studio.capture_runner import CaptureRunner
from bmw_capture_studio.models import CameraPose, CapturePoint


def pose(x: float = 100.0) -> CameraPose:
    return CameraPose(
        x=x,
        y=200.0,
        z=300.0,
        yaw_degrees=10.0,
        pitch_degrees=5.0,
        roll_degrees=0.0,
        fov_degrees=63.0,
    )


class FakeBridge:
    def __init__(self) -> None:
        self.current = pose(0.0)

    def read_pose(self) -> CameraPose:
        return self.current


class FakeMover:
    def __init__(self, bridge: FakeBridge) -> None:
        self.bridge = bridge

    def move_to(self, target, **_kwargs):
        self.bridge.current = target
        return target


class FakeDepthBridge:
    def begin_capture(self):
        return SimpleNamespace(request_id="depth-request")

    def wait_capture(self, _ticket, output_dir, *, timeout):
        self.timeout = timeout
        output = Path(output_dir)
        depth = output / "depth.npy"
        preview = output / "depth_preview.png"
        depth.write_bytes(b"npy")
        preview.write_bytes(b"png")
        return {
            "status": "completed",
            "captured_unix_ns": time.time_ns(),
            "width": 1920,
            "height": 1080,
            "depth_path": str(depth),
            "preview_path": str(preview),
            "depth_space": "raw_device_depth",
            "metric_depth": False,
        }

    def cancel(self, _ticket):
        raise AssertionError("successful depth request must not be cancelled")


class BlackMythStaticDepthTests(unittest.TestCase):
    def test_static_capture_writes_rgb_pose_depth_and_metric_pose(self) -> None:
        bridge = FakeBridge()
        depth_bridge = FakeDepthBridge()

        def screenshotter(_pid, target):
            path = Path(target)
            path.write_bytes(b"jpg")
            return path

        runner = CaptureRunner(
            bridge=bridge,
            mover=FakeMover(bridge),
            pid=123,
            settle_seconds=0,
            image_format="jpg",
            screenshotter=screenshotter,
            depth_bridge=depth_bridge,
            depth_enabled=True,
            depth_timeout=3.5,
            screenshot_source="Program",
            screenshot_width=1920,
            screenshot_height=1080,
        )
        with tempfile.TemporaryDirectory() as temp:
            result = runner.run(
                [CapturePoint(index=7, label="sample", pose=pose())],
                temp,
                mode="scene_static22",
                run_metadata={"scene_id": "scene"},
            )
            sample = result.session_dir / "samples" / "sample_000001"
            metadata = json.loads((sample / "metadata.json").read_text(encoding="utf-8"))
            manifest = json.loads(result.manifest_json.read_text(encoding="utf-8"))
            depth_exists = (sample / "depth.npy").exists()
            preview_exists = (sample / "depth_preview.png").exists()

        self.assertEqual(depth_bridge.timeout, 3.5)
        self.assertEqual(metadata["coordinate_system"]["meters_per_unit"], 0.01)
        self.assertEqual(metadata["pose"]["before"]["position_m"]["x"], 1.0)
        self.assertEqual(metadata["depth"]["depth_space"], "raw_device_depth")
        self.assertFalse(metadata["depth"]["metric_depth"])
        self.assertEqual(metadata["synchronization"]["status"], "static_camera_best_effort")
        self.assertEqual(manifest["frames"][0]["target_x_m"], 1.0)
        self.assertTrue(depth_exists)
        self.assertTrue(preview_exists)


if __name__ == "__main__":
    unittest.main()
