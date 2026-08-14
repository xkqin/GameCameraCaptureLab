from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from bmw_capture_studio import bridge, global_hotkey, platform_support, screen_capture, uuu
from bmw_capture_studio.connection import classify_connection
from bmw_capture_studio.global_hotkey import F8_VK, GlobalHotkey


class LinuxCompatibilityTests(unittest.TestCase):
    def test_linux_integration_status_is_explicitly_unsupported_for_uuu(self) -> None:
        with patch.object(sys, "platform", "linux"):
            status = uuu.integration_status()

        self.assertTrue(status["platform_unsupported"])
        self.assertEqual(status["platform"], "linux")
        self.assertIn("Windows", str(status["message"]))

        report = classify_connection(status, None, {"connected": False})
        self.assertEqual(report.code, "platform_unsupported")
        self.assertIsNone(report.pose)

    def test_linux_pose_bridge_fails_before_opening_windows_shared_memory(self) -> None:
        bridge_instance = bridge.UuuPoseBridge()
        with patch.object(sys, "platform", "linux"), self.assertRaisesRegex(
            bridge.PoseUnavailableError, "Linux"
        ):
            bridge_instance.connect()

    def test_linux_dpi_awareness_does_not_call_win32(self) -> None:
        with patch.object(sys, "platform", "linux"), patch.object(
            screen_capture, "_user32"
        ) as user32:
            screen_capture.enable_dpi_awareness()
        user32.assert_not_called()

    def test_linux_global_hotkey_is_a_noop_and_does_not_start_a_thread(self) -> None:
        hotkey = GlobalHotkey(F8_VK)
        with patch.object(sys, "platform", "linux"), patch.object(
            global_hotkey.ctypes, "WinDLL", side_effect=AssertionError("Win32 called")
        ):
            self.assertFalse(hotkey.supported)
            hotkey.start()
            hotkey.stop()
        self.assertIsNone(hotkey._thread)

    def test_linux_open_path_uses_xdg_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "outputs"
            with patch.object(sys, "platform", "linux"), patch.object(
                platform_support.shutil, "which", return_value="/usr/bin/xdg-open"
            ), patch.object(platform_support.subprocess, "Popen") as popen:
                platform_support.open_path(target)
        popen.assert_called_once_with(["/usr/bin/xdg-open", str(target.resolve())])

    def test_linux_open_path_reports_missing_desktop_opener(self) -> None:
        with patch.object(sys, "platform", "linux"), patch.object(
            platform_support.shutil, "which", return_value=None
        ), self.assertRaisesRegex(RuntimeError, "xdg-open"):
            platform_support.open_path(Path("/tmp/outputs"))


if __name__ == "__main__":
    unittest.main()
