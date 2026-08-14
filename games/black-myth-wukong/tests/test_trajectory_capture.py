from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bmw_capture_studio.files import load_trajectories
from bmw_capture_studio.models import CameraPose, CapturePoint, ImportedTrajectory
from bmw_capture_studio.trajectory_capture import (
    BatchTrajectoryRecorder,
    TrajectoryRecorder,
    find_latest_resumable_batch,
    trajectory_capture_complete,
)


def camera_pose(x: float) -> CameraPose:
    return CameraPose(
        x=x,
        y=2.0,
        z=3.0,
        yaw_degrees=4.0,
        pitch_degrees=5.0,
        roll_degrees=0.0,
        fov_degrees=63.0,
    )


def trajectory(index: int, name: str) -> ImportedTrajectory:
    return ImportedTrajectory(
        index=index,
        trajectory_id=name,
        points=(
            CapturePoint(1, "start", camera_pose(index + 1.0), 0.0),
            CapturePoint(2, "end", camera_pose(index + 2.0), 1.0),
        ),
    )


class FakeBridge:
    def __init__(self) -> None:
        self.current = camera_pose(0.0)

    def read_pose(self) -> CameraPose:
        return self.current


class FakeSmoothBridge(FakeBridge):
    def __init__(self) -> None:
        super().__init__()
        self.started_points = []
        self.started_hz = None
        self.status_reads = 0
        self.stopped = False

    def start_native_trajectory(self, points, *, playback_hz=60.0, timeout_seconds=1.0):
        self.started_points = list(points)
        self.started_hz = playback_hz
        self.status_reads = 0
        return SimpleNamespace(
            state=2,
            current_segment=0,
            completed=False,
            stopped=False,
            failed=False,
            error_message="",
        )

    def read_trajectory_status(self):
        self.status_reads += 1
        if self.status_reads == 1:
            return SimpleNamespace(
                state=2,
                current_segment=0,
                completed=False,
                stopped=False,
                failed=False,
                error_message="",
            )
        return SimpleNamespace(
            state=3,
            current_segment=len(self.started_points) - 1,
            completed=True,
            stopped=False,
            failed=False,
            error_message="",
        )

    def stop_native_trajectory(self, *, timeout_seconds=1.0):
        self.stopped = True
        return SimpleNamespace(
            state=4,
            current_segment=0,
            completed=False,
            stopped=True,
            failed=False,
            error_message="",
        )


class FailingSmoothBridge(FakeSmoothBridge):
    def read_trajectory_status(self):
        self.status_reads += 1
        return SimpleNamespace(
            state=5,
            current_segment=0,
            completed=False,
            stopped=False,
            failed=True,
            error_message="simulated trajectory failure",
        )


class SlowFirstReadSmoothBridge(FakeSmoothBridge):
    def read_trajectory_status(self):
        if self.status_reads == 0:
            # Keep the restart deadline deterministic on Windows timers; a
            # 10 ms sleep can still round to the same monotonic tick as the
            # 1 ms test interval.
            time.sleep(0.05)
        return super().read_trajectory_status()


class FakeMover:
    def __init__(self, bridge: FakeBridge) -> None:
        self.bridge = bridge
        self.targets: list[CameraPose] = []

    def move_to(self, target, *, stop_requested, on_update):
        self.targets.append(target)
        if stop_requested():
            raise InterruptedError
        self.bridge.current = target
        return target


class FailingThenRestoreMover(FakeMover):
    def __init__(self, bridge: FakeBridge) -> None:
        super().__init__(bridge)

    def move_to(self, target, *, stop_requested, on_update):
        self.targets.append(target)
        if len(self.targets) == 1:
            self.bridge.current = camera_pose(500.0)
            raise RuntimeError("simulated move failure")
        self.bridge.current = target
        return target


class FakeOBS:
    def __init__(self) -> None:
        self.directory: Path | None = None
        self.active = False
        self.restored = False
        self.closed = False

    def test(self):
        return {"obs_version": "test"}

    def set_record_directory(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        return self.directory

    def mute_all_audio_inputs(self):
        return 2

    def restore_audio_inputs(self):
        self.restored = True

    def start_recording(self):
        self.active = True

    def stop_recording(self):
        assert self.directory is not None
        video = self.directory / "video.mkv"
        video.write_bytes(b"video")
        self.active = False
        return str(video)

    def recording_status(self):
        return {"active": self.active, "output_path": None}

    def close(self):
        self.closed = True


class StopFailOBS(FakeOBS):
    def stop_recording(self):
        raise RuntimeError("stop failed")


class RecordingStateMover(FakeMover):
    def __init__(self, bridge: FakeBridge, obs: FakeOBS) -> None:
        super().__init__(bridge)
        self.obs = obs
        self.recording_states: list[bool] = []

    def move_to(self, target, *, stop_requested, on_update):
        self.recording_states.append(self.obs.active)
        return super().move_to(
            target,
            stop_requested=stop_requested,
            on_update=on_update,
        )


class StopBeforeRecordingMover(FakeMover):
    def move_to(self, target, *, stop_requested, on_update):
        raise InterruptedError


class TrajectoryCaptureTests(unittest.TestCase):
    def test_loads_every_trajectory(self) -> None:
        payload = {
            "trajectories": [
                {"trajectory_id": "a", "keyframes": [{"x": 1}, {"x": 2}]},
                {"trajectory_id": "b", "keyframes": [{"x": 3}, {"x": 4}]},
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "set.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            values = load_trajectories(path)
        self.assertEqual([value.trajectory_id for value in values], ["a", "b"])

    def test_single_capture_writes_kcd2_style_artifacts(self) -> None:
        bridge = FakeBridge()
        obs = FakeOBS()
        mover = RecordingStateMover(bridge, obs)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text("{}", encoding="utf-8")
            output = root / "traj_0001"
            recorder = TrajectoryRecorder(
                bridge=bridge,
                mover=mover,
                obs=obs,
                output_dir=output,
                pose_hz=120,
                pre_record_settle_seconds=0,
            )
            result = recorder.capture(trajectory(0, "one"), source_path=source)
            payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(trajectory_capture_complete(output))
            self.assertEqual(
                payload["control_method"],
                "uuu_native_relative_steps_pose_feedback_closed_loop",
            )
            self.assertTrue(payload["absolute_target_pose"])
            self.assertFalse(payload["atomic_absolute_set_pose"])
            self.assertFalse(payload["absolute_set_pose"])
            self.assertEqual(mover.targets, [camera_pose(1.0), camera_pose(2.0)])
            self.assertEqual(mover.recording_states, [False, True])
            self.assertFalse(payload["pre_record_positioning"]["included_in_video"])
            self.assertEqual(
                payload["pre_record_positioning"]["actual_pose"]["x"],
                1.0,
            )
            self.assertEqual(payload["audio_capture"], "disabled")
            for name in (
                "source_keyframes.csv",
                "playback_plan.csv",
                "observed_pose.csv",
                "trajectory_timing.csv",
            ):
                self.assertTrue((output / name).is_file())
            self.assertTrue(obs.restored)
            self.assertTrue(obs.closed)

    def test_smooth_bridge_receives_one_path_and_skips_per_point_moves(self) -> None:
        bridge = FakeSmoothBridge()
        obs = FakeOBS()
        mover = RecordingStateMover(bridge, obs)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text("{}", encoding="utf-8")
            output = root / "traj_0001"
            recorder = TrajectoryRecorder(
                bridge=bridge,
                mover=mover,
                obs=obs,
                output_dir=output,
                pose_hz=120,
                pre_record_settle_seconds=0,
                playback_hz=60,
            )
            result = recorder.capture(trajectory(0, "one"), source_path=source)
            payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))

            self.assertTrue(payload["smooth_playback"])
            self.assertEqual(
                payload["control_method"],
                "uuu_native_continuous_trajectory_interpolation",
            )
            self.assertEqual(
                payload["native_controller_revision"],
                "v7_clear_uuu_smoothing_before_hermite",
            )
            self.assertEqual(payload["bridge_metadata_version"], 7)
            self.assertTrue(payload["uuu_smoothing_reset_before_playback"])
            self.assertTrue(payload["pre_record_positioning"]["stable_pose_verified"])
            self.assertEqual(len(bridge.started_points), 2)
            self.assertEqual(bridge.started_hz, 60.0)
            self.assertEqual(mover.targets, [camera_pose(1.0)])
            self.assertFalse(bridge.stopped)

    def test_long_trajectory_rolls_obs_into_independent_segments(self) -> None:
        bridge = SlowFirstReadSmoothBridge()
        initial_obs = FakeOBS()
        obs_instances = [initial_obs]

        def restart_obs(_output_dir: Path):
            replacement = FakeOBS()
            obs_instances.append(replacement)
            return replacement

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text("{}", encoding="utf-8")
            output = root / "traj_0001"
            long_path = ImportedTrajectory(
                index=0,
                trajectory_id="long",
                points=(
                    CapturePoint(1, "start", camera_pose(1.0), 0.0),
                    CapturePoint(2, "end", camera_pose(2.0), 10.0),
                ),
            )
            recorder = TrajectoryRecorder(
                bridge=bridge,
                mover=FakeMover(bridge),
                obs=initial_obs,
                output_dir=output,
                obs_restart_factory=restart_obs,
                obs_restart_interval_seconds=0.001,
                pose_hz=120,
                pre_record_settle_seconds=0,
            )

            result = recorder.capture(long_path, source_path=source)
            payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))

            self.assertEqual(result.obs_restart_count, 1)
            self.assertEqual(len(payload["obs_restart_events"]), 1)
            self.assertEqual(payload["obs_restart_events"][0]["status"], "completed")
            self.assertEqual(len(payload["video_segments"]), 2)
            self.assertEqual(len(payload["video_paths"]), 2)
            self.assertNotEqual(payload["video_paths"][0], payload["video_paths"][1])
            self.assertTrue(all(Path(path).is_file() for path in payload["video_paths"]))
            self.assertEqual(len(obs_instances), 2)
            self.assertTrue(all(obs.closed for obs in obs_instances))

    def test_runtime_failure_keeps_terminal_pose_instead_of_restoring_start(self) -> None:
        bridge = FailingSmoothBridge()
        obs = FakeOBS()
        mover = RecordingStateMover(bridge, obs)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text("{}", encoding="utf-8")
            output = root / "traj_0001"
            recorder = TrajectoryRecorder(
                bridge=bridge,
                mover=mover,
                obs=obs,
                output_dir=output,
                pre_record_settle_seconds=0,
            )

            with self.assertRaisesRegex(RuntimeError, "simulated trajectory failure"):
                recorder.capture(trajectory(0, "one"), source_path=source)

            payload = json.loads(
                (output / "recording_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(mover.targets, [camera_pose(1.0)])
            self.assertFalse(payload["restore_attempted"])
            self.assertEqual(
                payload["restore_policy"],
                "pre_record_failures_only_keep_terminal_pose_after_recording",
            )

    def test_failed_move_restores_start_pose_and_records_result(self) -> None:
        bridge = FakeBridge()
        start_pose = bridge.current
        mover = FailingThenRestoreMover(bridge)
        obs = FakeOBS()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text("{}", encoding="utf-8")
            output = root / "traj_0001"
            recorder = TrajectoryRecorder(
                bridge=bridge,
                mover=mover,
                obs=obs,
                output_dir=output,
                pose_hz=120,
            )

            with self.assertRaisesRegex(RuntimeError, "simulated move failure"):
                recorder.capture(trajectory(0, "one"), source_path=source)

            payload = json.loads(
                (output / "recording_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(len(mover.targets), 2)
            self.assertEqual(mover.targets[1], start_pose)
            self.assertEqual(bridge.current, start_pose)
            self.assertTrue(payload["restore_attempted"])
            self.assertTrue(payload["restore_succeeded"])
            self.assertIsNone(payload["restore_error"])

    def test_stop_during_pre_record_positioning_never_starts_obs(self) -> None:
        bridge = FakeBridge()
        obs = FakeOBS()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text("{}", encoding="utf-8")
            output = root / "traj_0001"
            recorder = TrajectoryRecorder(
                bridge=bridge,
                mover=StopBeforeRecordingMover(bridge),
                obs=obs,
                output_dir=output,
                pre_record_settle_seconds=0,
            )

            with self.assertRaisesRegex(RuntimeError, "录像前首点定位被停止"):
                recorder.capture(trajectory(0, "one"), source_path=source)

            payload = json.loads(
                (output / "recording_manifest.json").read_text(encoding="utf-8")
            )
            self.assertFalse(obs.active)
            self.assertEqual(payload["status"], "failed")
            self.assertIsNone(payload["video_path"])

    def test_batch_resume_skips_complete_trajectory(self) -> None:
        bridge = FakeBridge()
        obs_instances: list[FakeOBS] = []

        def obs_factory():
            obs = FakeOBS()
            obs_instances.append(obs)
            return obs

        values = [trajectory(0, "one"), trajectory(1, "two")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text(json.dumps({"trajectories": []}), encoding="utf-8")
            capture_root = root / "trajectory_captures"
            with patch("bmw_capture_studio.trajectory_capture.TRAJECTORY_CAPTURES_DIR", capture_root):
                recorder = BatchTrajectoryRecorder(
                    bridge=bridge,
                    mover_factory=lambda: FakeMover(bridge),
                    obs_factory=obs_factory,
                    scene_id="scene_1",
                    pose_hz=120,
                )
                def stop_after_first(index, total, item, phase):
                    if index == 0 and phase == "completed":
                        recorder.request_stop()

                first = recorder.capture(
                    values,
                    source_path=source,
                    trajectory_callback=stop_after_first,
                )
                self.assertTrue(first["stopped"])
                resume = find_latest_resumable_batch("scene_1")
                self.assertIsNotNone(resume)
                assert resume is not None
                self.assertEqual(resume["pending_indices"], [1])
                second = recorder.capture(
                    values,
                    source_path=source,
                    batch_dir=resume["batch_dir"],
                    trajectory_indices=resume["pending_indices"],
                )
                self.assertTrue(trajectory_capture_complete(Path(second["output_dir"]) / "traj_0002"))
                self.assertIsNone(find_latest_resumable_batch("scene_1"))
                self.assertEqual(len(obs_instances), 2)

    def test_batch_restarts_obs_at_elapsed_boundary_between_short_paths(self) -> None:
        bridge = FakeBridge()
        obs_instances: list[FakeOBS] = []
        boundary_instances: list[FakeOBS] = []

        def obs_factory():
            obs = FakeOBS()
            obs_instances.append(obs)
            return obs

        def restart_obs(_output_dir: Path):
            obs = FakeOBS()
            boundary_instances.append(obs)
            return obs

        values = [trajectory(0, "one"), trajectory(1, "two")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text("{}", encoding="utf-8")
            capture_root = root / "trajectory_captures"
            with patch("bmw_capture_studio.trajectory_capture.TRAJECTORY_CAPTURES_DIR", capture_root):
                recorder = BatchTrajectoryRecorder(
                    bridge=bridge,
                    mover_factory=lambda: FakeMover(bridge),
                    obs_factory=obs_factory,
                    obs_restart_factory=restart_obs,
                    obs_restart_interval_seconds=0.0001,
                    scene_id="scene_1",
                    pose_hz=120,
                )
                result = recorder.capture(values, source_path=source)

            manifest = json.loads(Path(result["batch_manifest_path"]).read_text(encoding="utf-8"))
            self.assertEqual(len(obs_instances), 1)
            self.assertEqual(len(boundary_instances), 1)
            self.assertEqual(len(manifest["obs_restart_events"]), 1)
            self.assertEqual(manifest["obs_restart_events"][0]["status"], "completed")
            self.assertEqual(manifest["completed_trajectories"], 2)

    def test_single_planned_trajectory_does_not_resume_other_entries(self) -> None:
        bridge = FakeBridge()
        values = [trajectory(0, "one"), trajectory(1, "two")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.csv"
            source.write_text("x,y,z\n1,2,3\n", encoding="utf-8")
            capture_root = root / "trajectory_captures"
            with patch("bmw_capture_studio.trajectory_capture.TRAJECTORY_CAPTURES_DIR", capture_root):
                recorder = BatchTrajectoryRecorder(
                    bridge=bridge,
                    mover_factory=lambda: FakeMover(bridge),
                    obs_factory=FakeOBS,
                    scene_id="scene_1",
                    pose_hz=120,
                )
                result = recorder.capture(values, source_path=source, trajectory_indices=[1])
                manifest = json.loads(Path(result["batch_manifest_path"]).read_text(encoding="utf-8"))
                self.assertEqual(manifest["planned_indices"], [2])
                self.assertTrue(Path(manifest["source_copy"]).name.endswith(".csv"))
                self.assertIsNone(find_latest_resumable_batch("scene_1"))

    def test_audio_stays_muted_when_obs_is_still_recording(self) -> None:
        bridge = FakeBridge()
        obs = StopFailOBS()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text("{}", encoding="utf-8")
            recorder = TrajectoryRecorder(
                bridge=bridge,
                mover=FakeMover(bridge),
                obs=obs,
                output_dir=root / "traj_0001",
                pose_hz=120,
            )
            with self.assertRaisesRegex(RuntimeError, "OBSStopError"):
                recorder.capture(trajectory(0, "one"), source_path=source)
            self.assertFalse(obs.restored)
            payload = json.loads(
                (root / "traj_0001" / "recording_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(payload["status"], "failed")


if __name__ == "__main__":
    unittest.main()
