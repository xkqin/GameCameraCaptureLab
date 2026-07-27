from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from re9_pose_recorder.config import AppConfig
from re9_pose_recorder.lua_control import (
    LuaControl,
    _write_control_in_bounded_helper,
)
from re9_pose_recorder.lua_patcher import build_lua_block


class LuaControlPoseAckTests(unittest.TestCase):
    def _control(self, status_file: Path) -> LuaControl:
        config = AppConfig(
            raw={
                "lua_logger": {
                    "control_file": str(status_file.with_name("control.json")),
                    "status_file": str(status_file),
                }
            },
            path=status_file,
        )
        return LuaControl(config)

    def test_wait_until_scan_pose_requires_current_unique_segment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status_file = Path(temp_dir) / "status.json"
            status_file.write_text(
                json.dumps({"scan_pose_enabled": True, "scan_segment_id": "old"}),
                encoding="utf-8",
            )
            control = self._control(status_file)

            def publish_ack() -> None:
                time.sleep(0.06)
                status_file.write_text(
                    json.dumps({"scan_pose_enabled": True, "scan_segment_id": "new"}),
                    encoding="utf-8",
                )

            thread = threading.Thread(target=publish_ack)
            thread.start()
            try:
                self.assertTrue(
                    control.wait_until_scan_pose(
                        "new",
                        timeout_sec=1.0,
                        poll_interval_sec=0.01,
                        stable_polls=2,
                    )
                )
            finally:
                thread.join()

    def test_wait_until_scan_pose_times_out_on_stale_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status_file = Path(temp_dir) / "status.json"
            status_file.write_text(
                json.dumps({"scan_pose_enabled": True, "scan_segment_id": "old"}),
                encoding="utf-8",
            )
            control = self._control(status_file)
            self.assertFalse(
                control.wait_until_scan_pose(
                    "new",
                    timeout_sec=0.05,
                    poll_interval_sec=0.01,
                )
            )

    def test_logging_ack_rejects_a_stale_command_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status_file = Path(temp_dir) / "status.json"
            status_file.write_text(
                json.dumps(
                    {
                        "session_id": "current",
                        "logging": True,
                        "last_command_id": "start:stale",
                    }
                ),
                encoding="utf-8",
            )
            control = self._control(status_file)
            self.assertFalse(
                control.wait_until_lua_logging_started(
                    "current",
                    timeout_sec=0.05,
                    command_id="start:expected",
                )
            )

    def test_logging_ack_accepts_the_exact_command_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status_file = Path(temp_dir) / "status.json"
            status_file.write_text(
                json.dumps(
                    {
                        "session_id": "current",
                        "logging": True,
                        "last_command_id": "start:expected",
                    }
                ),
                encoding="utf-8",
            )
            control = self._control(status_file)
            self.assertTrue(
                control.wait_until_lua_logging_started(
                    "current",
                    timeout_sec=0.05,
                    command_id="start:expected",
                )
            )

    def test_bounded_helper_overwrites_control_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control_file = Path(temp_dir) / "control.json"
            control_file.write_text('{"command":"old","padding":"xxxxxxxx"}', encoding="utf-8")
            content = b'{"command":"stop"}'
            _write_control_in_bounded_helper(control_file, content, timeout_sec=1.0)
            self.assertEqual(control_file.read_bytes(), content)

    @patch("re9_pose_recorder.lua_control._filesystem_type_for_path", return_value="ntfs3")
    @patch("re9_pose_recorder.lua_control._write_control_in_bounded_helper")
    def test_ntfs_write_uses_bounded_helper_and_timestamp(
        self,
        writer_mock: Mock,
        filesystem_mock: Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            control = self._control(Path(temp_dir) / "status.json")
            control.write_stop_control("current")

        payload = json.loads(writer_mock.call_args.args[1])
        self.assertEqual(payload["command"], "stop")
        self.assertEqual(payload["session_id"], "current")
        self.assertGreater(payload["issued_at"], 0)
        self.assertEqual(control.last_written_command_id, payload["command_id"])
        filesystem_mock.assert_called_once()

    @patch("re9_pose_recorder.lua_control._reap_process_later")
    @patch("re9_pose_recorder.lua_control.subprocess.Popen")
    def test_bounded_helper_times_out_without_waiting_forever(
        self,
        popen_mock: Mock,
        reaper_mock: Mock,
    ) -> None:
        process = Mock()
        process.communicate.side_effect = [
            subprocess.TimeoutExpired("writer", 0.1),
            subprocess.TimeoutExpired("writer", 0.2),
        ]
        popen_mock.return_value = process

        with self.assertRaisesRegex(TimeoutError, "Timed out writing"):
            _write_control_in_bounded_helper(Path("/tmp/control.json"), b"{}", timeout_sec=0.1)

        process.kill.assert_called_once()
        reaper_mock.assert_called_once_with(process)

    def test_lua_patch_rejects_delayed_control_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = AppConfig(
                raw={
                    "lua_logger": {
                        "control_file": str(root / "control.json"),
                        "status_file": str(root / "status.json"),
                        "pose_log_file": str(root / "pose.csv"),
                        "default_interval_sec": 0.033333,
                    }
                },
                path=root / "config.yaml",
            )
            block = build_lua_block(config)

        self.assertIn("last_command_issued_at", block)
        self.assertIn("issued_at = tonumber(text:match", block)
        self.assertIn("issued_at < (tonumber(re9_pose_logger.last_command_issued_at)", block)


if __name__ == "__main__":
    unittest.main()
