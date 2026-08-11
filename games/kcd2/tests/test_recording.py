from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from kcd2_capture_studio.models import Pose
from kcd2_capture_studio.recording import VideoPoseSession
from kcd2_capture_studio import backend as backend_module
from kcd2_capture_studio import recording


class FakeBackend:
    def pose(self) -> Pose:
        return Pose(
            captured_at="2026-07-28T00:00:00+08:00",
            pid=123,
            x=1,
            y=2,
            z=3,
            q0=0,
            q1=0,
            q2=0,
            q3=1,
            pitch_degrees=0,
            yaw_degrees=0,
            roll_degrees=0,
            fov_degrees=63,
        )


class FakeOBS:
    def start_recording(self) -> None:
        pass

    def stop_recording(self) -> str:
        return "video.mkv"


class RecordingTests(unittest.TestCase):
    def test_manifest_contains_pose_to_obs_time_offset(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pose_dir = root / "pose"
            runs_dir = root / "runs"
            pose_dir.mkdir()
            runs_dir.mkdir()
            with (
                patch.object(backend_module, "POSE_LOGS_DIR", pose_dir),
                patch.object(recording, "RUNS_DIR", runs_dir),
            ):
                session = VideoPoseSession(
                    FakeBackend(),
                    FakeOBS(),
                    scene_id="scene",
                    pose_hz=20,
                )
                started = session.start()
                time.sleep(0.08)
                stopped = session.stop()
                self.assertTrue(session.manifest_path.exists())
        self.assertGreaterEqual(started["pose_time_at_obs_start_sec"], 0.0)
        self.assertGreaterEqual(stopped["pose_frames"], 1)
        self.assertEqual(stopped["video_path"], "video.mkv")


if __name__ == "__main__":
    unittest.main()
