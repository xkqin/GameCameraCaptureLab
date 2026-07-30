from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from re9_pose_recorder.trajectory_replay import (
    ReplayKeyframe,
    _confirm_lua_replay_ready,
    _prepare_lua_replay,
    _run_lua_trajectory,
    _start_lua_logging,
    _stop_active_lua_logging,
)


class FakeLuaControl:
    def __init__(
        self,
        *,
        logging_started: bool = True,
        pose_acknowledged: bool = True,
        status: dict | None = None,
        logging_stopped: bool = True,
    ) -> None:
        self.logging_started = logging_started
        self.pose_acknowledged = pose_acknowledged
        self.status = status or {}
        self.logging_stopped = logging_stopped
        self.start_sessions: list[str] = []
        self.pose_segments: list[str] = []
        self.stop_sessions: list[str] = []
        self.clear_sessions: list[str] = []
        self.play_ids: list[str] = []
        self.health_checks: list[str] = []
        self.health_acknowledged = True
        self.last_written_command_id = ""
        self.command_counter = 0

    def _next_command(self, prefix: str) -> None:
        self.command_counter += 1
        self.last_written_command_id = f"{prefix}:{self.command_counter}"

    def write_start_control(self, session_id: str, pose_log_file: Path, interval_sec: float) -> Path:
        self._next_command("start")
        self.start_sessions.append(session_id)
        return pose_log_file

    def wait_until_lua_logging_started(
        self,
        session_id: str,
        timeout_sec: float = 5,
        command_id: str = "",
    ) -> bool:
        return self.logging_started

    def write_set_pose_control(self, session_id: str, *args, segment_id: str = "", **kwargs) -> Path:
        self._next_command("set_pose")
        self.pose_segments.append(segment_id)
        return Path("control.json")

    def wait_until_scan_pose(self, segment_id: str, **kwargs) -> bool:
        return self.pose_acknowledged

    def read_status(self) -> dict:
        return dict(self.status)

    def write_stop_control(self, session_id: str) -> Path:
        self._next_command("stop")
        self.stop_sessions.append(session_id)
        return Path("control.json")

    def write_health_check_control(self, session_id: str) -> Path:
        self._next_command("health_check")
        self.health_checks.append(session_id)
        return Path("control.json")

    def wait_until_lua_control_ack(self, command_id: str, timeout_sec: float = 5) -> bool:
        return self.health_acknowledged

    def wait_until_lua_logging_stopped(
        self,
        session_id: str,
        timeout_sec: float = 5,
        command_id: str = "",
    ) -> bool:
        return self.logging_stopped

    def write_clear_pose_control(self, session_id: str) -> Path:
        self._next_command("clear_pose")
        self.clear_sessions.append(session_id)
        return Path("control.json")

    def write_play_trajectory_control(self, session_id: str, keyframes: list[dict], trajectory_id: str = "") -> Path:
        self._next_command("play")
        self.play_ids.append(trajectory_id)
        return Path("control.json")

    @staticmethod
    def _status_matches_command(status: dict, command_id: str) -> bool:
        return not command_id or status.get("last_command_id") in {None, command_id}


def first_frame() -> ReplayKeyframe:
    return ReplayKeyframe(
        step=0,
        time_sec=0.0,
        x=1.0,
        y=2.0,
        z=3.0,
        yaw=0.1,
        pitch=0.2,
        fov=50.0,
        score=None,
        image_path="",
    )


class LuaReplayPreflightTests(unittest.TestCase):
    def test_logging_must_be_acknowledged_before_pose_or_obs(self) -> None:
        control = FakeLuaControl(logging_started=False, status={"session_id": "old", "logging": True})
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(RuntimeError, "OBS was not started"):
                _prepare_lua_replay(
                    control,
                    "new",
                    "trajectory",
                    first_frame(),
                    Path(temp_dir) / "pose.csv",
                    0.033,
                    0.0,
                    True,
                )
        self.assertEqual(control.start_sessions, ["new", "new", "new"])
        self.assertEqual(control.pose_segments, [])

    def test_prepare_pose_must_be_acknowledged_before_obs(self) -> None:
        control = FakeLuaControl(
            logging_started=True,
            pose_acknowledged=False,
            status={"session_id": "new", "logging": True, "last_error": ""},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(RuntimeError, "prepare pose"):
                _prepare_lua_replay(
                    control,
                    "new",
                    "trajectory",
                    first_frame(),
                    Path(temp_dir) / "pose.csv",
                    0.033,
                    0.0,
                    True,
                )
        self.assertEqual(len(control.pose_segments), 1)
        self.assertTrue(control.pose_segments[0].startswith("trajectory_prepare_"))

    def test_cleanup_stops_the_session_lua_reports_as_active(self) -> None:
        control = FakeLuaControl(
            status={"session_id": "previous", "logging": True},
            logging_stopped=True,
        )
        self.assertTrue(_stop_active_lua_logging(control, fallback_session="attempted"))
        self.assertEqual(control.stop_sessions, ["previous"])

    def test_logging_start_stops_a_stale_active_session_first(self) -> None:
        control = FakeLuaControl(
            status={"session_id": "previous", "logging": True},
            logging_started=True,
            logging_stopped=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            _start_lua_logging(
                control,
                "current",
                Path(temp_dir) / "pose.csv",
                0.033,
                attempts=1,
                timeout_sec=0.1,
            )

        self.assertEqual(control.stop_sessions, ["previous"])
        self.assertEqual(control.start_sessions, ["current"])

    def test_unacknowledged_stop_is_left_as_final_command(self) -> None:
        control = FakeLuaControl(
            status={"session_id": "previous", "logging": True},
            logging_stopped=False,
        )
        self.assertFalse(
            _stop_active_lua_logging(
                control,
                fallback_session="attempted",
                attempts=3,
                timeout_sec=0.0,
            )
        )
        self.assertEqual(control.stop_sessions, ["previous", "previous", "previous"])
        self.assertEqual(control.clear_sessions, [])

    def test_post_prepare_health_check_must_be_acknowledged_before_obs(self) -> None:
        control = FakeLuaControl()
        control.health_acknowledged = False

        with self.assertRaisesRegex(RuntimeError, "OBS was not started"):
            _confirm_lua_replay_ready(control, "current", "trajectory", timeout_sec=0.0)

        self.assertEqual(control.health_checks, ["current"])

    def test_post_prepare_health_check_accepts_current_lua_ack(self) -> None:
        control = FakeLuaControl()

        _confirm_lua_replay_ready(control, "current", "trajectory", timeout_sec=0.1)

        self.assertEqual(control.health_checks, ["current"])

    @patch("re9_pose_recorder.trajectory_replay.time.sleep")
    @patch(
        "re9_pose_recorder.trajectory_replay._wait_for_lua_trajectory",
        side_effect=[RuntimeError("first command missed"), None],
    )
    def test_playback_retries_with_a_new_ack_id(self, wait_mock, sleep_mock) -> None:
        control = FakeLuaControl(status={"last_error": ""})
        frames = [
            first_frame(),
            ReplayKeyframe(
                step=1,
                time_sec=0.1,
                x=2.0,
                y=3.0,
                z=4.0,
                yaw=0.2,
                pitch=0.3,
                fov=50.0,
                score=None,
                image_path="",
            ),
        ]
        _run_lua_trajectory(control, "session", "trajectory", frames)
        self.assertEqual(len(control.play_ids), 2)
        self.assertNotEqual(control.play_ids[0], control.play_ids[1])
        self.assertEqual(wait_mock.call_count, 2)
        sleep_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
