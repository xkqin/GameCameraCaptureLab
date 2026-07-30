from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from re9_pose_recorder.platform_support import (
    command_for_popen,
    default_obs_restart_command,
    detached_process_kwargs,
    obs_process_names,
    obs_sentinel_dir,
    platform_config_names,
    platform_key,
)
from re9_pose_recorder.still_scan_gui import StillScanApp


class PlatformSupportTests(unittest.TestCase):
    def test_platform_families_and_config_precedence(self) -> None:
        self.assertEqual(platform_key("win32"), "windows")
        self.assertEqual(platform_key("linux"), "linux")
        self.assertEqual(
            platform_config_names("win32"),
            ("windows.local.yaml", "windows.yaml", "default.yaml"),
        )
        self.assertEqual(
            platform_config_names("linux"),
            ("linux.local.yaml", "linux.yaml", "default.yaml"),
        )

    def test_platform_specific_obs_processes_and_sentinel_directories(self) -> None:
        home = Path("/capture-user")
        self.assertEqual(
            obs_process_names("win32"),
            ("obs64.exe", "obs32.exe"),
        )
        self.assertEqual(obs_process_names("linux"), ("obs",))
        self.assertEqual(
            obs_sentinel_dir(
                "win32",
                environ={"APPDATA": "C:/Users/capture/AppData/Roaming"},
                home=home,
            ),
            Path("C:/Users/capture/AppData/Roaming/obs-studio/.sentinel"),
        )
        self.assertEqual(
            obs_sentinel_dir(
                "linux",
                environ={"XDG_CONFIG_HOME": "/capture-config"},
                home=home,
            ),
            Path("/capture-config/obs-studio/.sentinel"),
        )

    def test_default_obs_commands_use_native_command_line_rules(self) -> None:
        windows_executable = "C:/Program Files/obs-studio/bin/64bit/obs64.exe"
        windows_command = default_obs_restart_command(
            "win32",
            environ={},
            which=lambda name: windows_executable if name == "obs64.exe" else None,
        )
        linux_command = default_obs_restart_command(
            "linux",
            environ={},
            which=lambda name: "/usr/bin/obs" if name == "obs" else None,
        )

        self.assertTrue(windows_command.startswith('"C:/Program Files/'))
        self.assertIn("--collection RE9_Still_Scan", windows_command)
        self.assertEqual(
            command_for_popen(windows_command, "win32"),
            windows_command,
        )
        self.assertEqual(command_for_popen(linux_command, "linux")[0], "/usr/bin/obs")

    def test_detached_process_options_are_platform_specific(self) -> None:
        windows = detached_process_kwargs("win32", hide_console=True)
        linux = detached_process_kwargs("linux", hide_console=True)

        self.assertIn("creationflags", windows)
        self.assertNotIn("start_new_session", windows)
        self.assertEqual(linux, {"start_new_session": True})

    @patch.object(StillScanApp, "_windows_obs_running", return_value=False)
    @patch("re9_pose_recorder.still_scan_gui.subprocess.run")
    def test_windows_obs_shutdown_uses_taskkill(
        self,
        run_mock,
        _running_mock,
    ) -> None:
        app = object.__new__(StillScanApp)

        app._terminate_obs_processes_windows()

        commands = [call.args[0] for call in run_mock.call_args_list]
        self.assertIn(["taskkill", "/IM", "obs64.exe", "/T"], commands)
        self.assertIn(["taskkill", "/IM", "obs32.exe", "/T"], commands)


if __name__ == "__main__":
    unittest.main()
