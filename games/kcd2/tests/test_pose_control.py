from __future__ import annotations

import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import kcd2_pose_control as low_level

from kcd2_capture_studio.models import Pose
from kcd2_capture_studio.pose_control import (
    ClosedLoopPoseController,
    PoseConvergenceError,
    PoseTarget,
    PoseTolerance,
    pose_error,
    within_tolerance,
    wrapped_degrees,
)


def make_pose(
    *,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
    yaw: float = 0.0,
    pitch: float = 0.0,
    roll: float = 0.0,
    fov: float = 63.0,
) -> Pose:
    return Pose(
        captured_at="2026-07-28T00:00:00+08:00",
        pid=123,
        x=x,
        y=y,
        z=z,
        q0=0.0,
        q1=0.0,
        q2=0.0,
        q3=1.0,
        pitch_degrees=pitch,
        yaw_degrees=yaw,
        roll_degrees=roll,
        fov_degrees=fov,
    )


class FakeBackend:
    def __init__(self, controls_move: bool = True) -> None:
        self.current = make_pose()
        self.origin = self.current
        self.controls_move = controls_move
        self.restored = False

    def pose(self) -> Pose:
        return self.current

    def start_export_pose(self, **target):
        self._set_export(target)
        return {"session_left_active": True}

    def adjust_active_export_pose(self, **target):
        self._set_export(target)
        return {"session_left_active": True}

    def send_action(self, action: str, duration_ms: int) -> None:
        if not self.controls_move:
            return
        pitch = self.current.pitch_degrees
        roll = self.current.roll_degrees
        delta = duration_ms * 0.1
        if action == "rotate_up":
            pitch += delta
        elif action == "rotate_down":
            pitch -= delta
        elif action == "roll_right":
            roll += delta
        elif action == "roll_left":
            roll -= delta
        self.current = make_pose(
            x=self.current.x,
            y=self.current.y,
            z=self.current.z,
            yaw=self.current.yaw_degrees,
            pitch=pitch,
            roll=roll,
            fov=self.current.fov_degrees,
        )

    def restore_export_session(self):
        before = self.current
        self.current = self.origin
        self.restored = True
        return {"before": before.as_dict(), "after": self.current.as_dict()}

    def _set_export(self, target) -> None:
        self.current = make_pose(
            x=target["x"],
            y=target["y"],
            z=target["z"],
            yaw=target["yaw_degrees"],
            pitch=self.current.pitch_degrees,
            roll=self.current.roll_degrees,
            fov=target["fov_degrees"],
        )


class PoseControlTests(unittest.TestCase):
    def test_dense_playback_restores_raw_pose_without_screenshot_session(self) -> None:
        original = bytes(range(56))

        class FakeProcess:
            def __init__(self, *args, **kwargs):
                self.writes = []

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, address, size):
                self.assertions = (address, size)
                return original

            def write(self, address, data):
                self.writes.append((address, data))

        fake_process = FakeProcess()
        frame = {
            "step": 0,
            "time_sec": 0.0,
            "x": 1,
            "y": 2,
            "z": 3,
            "yaw_degrees": 0,
            "pitch_degrees": 0,
            "roll_degrees": 0,
            "fov_degrees": 63,
        }
        pose = {
            "x": 9,
            "y": 8,
            "z": 7,
            "yaw_degrees": 0,
            "pitch_degrees": 0,
            "roll_degrees": 0,
            "fov_degrees": 63,
        }
        with tempfile.TemporaryDirectory() as temp:
            with (
                patch.object(
                    low_level,
                    "_sha256",
                    return_value=low_level.EXPECTED_DLL_SHA256,
                ),
                patch.object(
                    low_level, "latest_camera_enabled_state", return_value=True
                ),
                patch.object(low_level, "find_process_id", return_value=123),
                patch.object(
                    low_level, "find_module", return_value={"base": 0x1000}
                ),
                patch.object(
                    low_level,
                    "_validated_trajectory_block_offset",
                    return_value=0x18,
                ),
                patch.object(
                    low_level,
                    "resolve_core",
                    return_value={"camera": 0x2000},
                ),
                patch.object(
                    low_level, "ProcessReader", return_value=fake_process
                ),
                patch.object(low_level, "read_pose", return_value=pose),
                patch.object(low_level.time, "sleep", return_value=None),
            ):
                result = low_level.play_absolute_trajectory(
                    low_level.DEFAULT_POSE_CONFIG,
                    [frame, dict(frame, step=1)],
                    timing_csv_path=Path(temp) / "timing.csv",
                )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["restore_mode"], "direct_original_pose_block")
        self.assertEqual(fake_process.writes[-1], (0x2018, original))

    def test_dense_trajectory_pose_is_one_contiguous_56_byte_write(self) -> None:
        block = low_level._pack_trajectory_pose(
            {
                "x": 10,
                "y": 20,
                "z": 30,
                "yaw_degrees": 90,
                "pitch_degrees": 0,
                "roll_degrees": 0,
                "fov_degrees": 63,
            }
        )
        values = struct.unpack("<ddd8f", block)
        self.assertEqual(len(block), 56)
        self.assertEqual(values[:3], (10.0, 20.0, 30.0))
        self.assertAlmostEqual(values[7], math.radians(63), places=6)
        self.assertAlmostEqual(values[9], math.radians(90), places=6)
        self.assertEqual(
            low_level._validated_trajectory_block_offset(
                low_level.DEFAULT_POSE_CONFIG
            ),
            0x18,
        )

    def test_dense_trajectory_rejects_unverified_roll(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "roll=0"):
            low_level._pack_trajectory_pose(
                {
                    "x": 0,
                    "y": 0,
                    "z": 0,
                    "yaw_degrees": 0,
                    "pitch_degrees": 0,
                    "roll_degrees": 1,
                    "fov_degrees": 63,
                }
            )

    def test_multishot_plan_aligns_lateral_axis_to_world_xy(self) -> None:
        before = {
            "x": 498.9398645199738,
            "y": 269.91037984657953,
            "z": 134.41771213147857,
            "yaw_degrees": 202.76879577703068,
        }
        target_x = 506.1275058298
        target_y = 198.7763985571
        plan = low_level._build_export_goto_plan(
            before,
            target_x=target_x,
            target_y=target_y,
            target_z=111.126591503,
            target_yaw_degrees=0.0,
        )

        displacement_angle = math.radians(
            270.0 - plan["movement_yaw_degrees"]
        )
        self.assertAlmostEqual(
            math.cos(displacement_angle) * plan["horizontal_distance"],
            target_x - before["x"],
            places=6,
        )
        self.assertAlmostEqual(
            math.sin(displacement_angle) * plan["horizontal_distance"],
            target_y - before["y"],
            places=6,
        )

    def test_wrapped_angle(self) -> None:
        self.assertEqual(wrapped_degrees(359.0), -1.0)
        self.assertEqual(wrapped_degrees(-359.0), 1.0)

    def test_closed_loop_reaches_pose_and_restores(self) -> None:
        backend = FakeBackend()
        controller = ClosedLoopPoseController(
            backend,
            tolerance=PoseTolerance(
                position=0.01,
                yaw_degrees=0.01,
                pitch_degrees=0.2,
                roll_degrees=0.2,
                fov_degrees=0.01,
            ),
        )
        target = PoseTarget(10, 20, 30, 170, 45, -12, 70)
        report = controller.move_to(target)
        self.assertTrue(report["reached"])
        self.assertTrue(controller.session_active)
        self.assertLess(abs(report["error"]["pitch_degrees"]), 0.2)
        self.assertLess(abs(report["error"]["roll_degrees"]), 0.2)
        controller.restore_start()
        self.assertFalse(controller.session_active)
        self.assertTrue(backend.restored)
        self.assertEqual(backend.pose().x, 0.0)

    def test_strict_mode_rejects_non_moving_rotation(self) -> None:
        backend = FakeBackend(controls_move=False)
        controller = ClosedLoopPoseController(backend)
        with self.assertRaises(PoseConvergenceError):
            controller.move_to(PoseTarget(1, 2, 3, 0, 30, 0, 63))
        controller.restore_start()

    def test_pose_error_and_tolerance(self) -> None:
        target = PoseTarget(0, 0, 0, -179, 10, 0, 63)
        error = pose_error(make_pose(yaw=179, pitch=10), target)
        self.assertEqual(error["yaw_degrees"], -2.0)
        self.assertTrue(
            within_tolerance(
                error,
                PoseTolerance(
                    position=0.1,
                    yaw_degrees=2.1,
                    pitch_degrees=0.1,
                    roll_degrees=0.1,
                    fov_degrees=0.1,
                ),
            )
        )


if __name__ == "__main__":
    unittest.main()
