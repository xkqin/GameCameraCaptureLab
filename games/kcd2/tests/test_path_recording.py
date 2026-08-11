from __future__ import annotations

import unittest
from unittest.mock import patch

from kcd2_capture_studio.path_recording import PathPlaybackRecorder
from kcd2_capture_studio import path_recording


class FakeBackend:
    def __init__(self) -> None:
        self.actions: list[str] = []

    def send_action(self, action: str, duration_ms: int) -> None:
        self.actions.append(action)


class FakeVideoPoseSession:
    def __init__(self, *args, **kwargs) -> None:
        self.manifest = {"session_id": "session", "pose_csv": "pose.csv"}
        self.manifest_path = "manifest.json"

    def start(self):
        return dict(self.manifest)

    def stop(self):
        self.manifest.update({"pose_frames": 10, "video_path": "video.mkv"})
        return dict(self.manifest)

    def update_metadata(self, values):
        self.manifest.update(values)


class PathRecordingTests(unittest.TestCase):
    def test_start_and_stop_wrap_path_with_recording(self) -> None:
        backend = FakeBackend()
        with patch.object(
            path_recording, "VideoPoseSession", FakeVideoPoseSession
        ):
            recorder = PathPlaybackRecorder(
                backend,
                object(),
                scene_id="scene",
                trajectory_id="walk",
                pose_hz=30,
            )
            started = recorder.start()
            stopped = recorder.stop()
        self.assertEqual(
            backend.actions,
            ["path_play_pause", "path_stop"],
        )
        self.assertEqual(started["session_id"], "session")
        self.assertEqual(stopped["pose_frames"], 10)
        self.assertFalse(recorder.active)


if __name__ == "__main__":
    unittest.main()
