from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kcd2_capture_studio import backend as backend_module
from kcd2_capture_studio import recording
from kcd2_capture_studio.models import Pose, TrajectoryKeyframe
from kcd2_capture_studio.trajectory_recording import ImportedTrajectoryRecorder


class FakeBackend:
    def pose(self) -> Pose:
        return Pose(
            captured_at="2026-08-10T00:00:00+08:00",
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

    def run_imported_trajectory(
        self,
        frames,
        *,
        timing_csv_path,
        stop_requested,
        progress_callback,
    ):
        timing_csv_path.write_text("frame_id,schedule_error_ms\n0,0\n", encoding="utf-8")
        if progress_callback:
            progress_callback(len(frames), len(frames))
        return {
            "status": "completed",
            "requested_frames": len(frames),
            "completed_frames": len(frames),
            "timing_csv": str(timing_csv_path),
        }


class FakeOBS:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start_recording(self) -> None:
        self.started = True

    def stop_recording(self) -> str:
        self.stopped = True
        return "trajectory.mkv"


class ImportedTrajectoryRecordingTests(unittest.TestCase):
    def test_capture_keeps_source_timing_pose_and_manifest(self) -> None:
        frames = [
            TrajectoryKeyframe(0, 0.0, 1, 2, 3, 0, -20, 0, 63),
            TrajectoryKeyframe(1, 1 / 60, 2, 2, 3, 1, -19, 0, 63),
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.json"
            source.write_text(json.dumps({"frames": []}), encoding="utf-8")
            pose_dir = root / "pose"
            runs_dir = root / "runs"
            pose_dir.mkdir()
            runs_dir.mkdir()
            obs = FakeOBS()
            with (
                patch.object(backend_module, "POSE_LOGS_DIR", pose_dir),
                patch.object(recording, "RUNS_DIR", runs_dir),
            ):
                recorder = ImportedTrajectoryRecorder(
                    FakeBackend(),
                    obs,
                    scene_id="scene_1",
                    trajectory_id="one_path",
                )
                result = recorder.capture(frames, source_path=source)
                manifest = json.loads(
                    recorder.session.manifest_path.read_text(encoding="utf-8")
                )
                self.assertTrue(
                    Path(manifest["trajectory_source_copy"]).is_file()
                )
                self.assertTrue(
                    Path(manifest["trajectory_timing_csv"]).is_file()
                )
        self.assertTrue(obs.started)
        self.assertTrue(obs.stopped)
        self.assertEqual(result["capture_mode"], "direct_absolute_trajectory_60fps")
        self.assertEqual(result["trajectory_playback"]["completed_frames"], 2)
        self.assertEqual(result["video_path"], "trajectory.mkv")


if __name__ == "__main__":
    unittest.main()
