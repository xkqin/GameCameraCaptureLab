from __future__ import annotations

import os
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from re9_pose_recorder.config import AppConfig
from re9_pose_recorder.platform_support import (
    _re9_window_id_from_xwininfo,
    _request_x11_window_activation,
    activate_re9_window,
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
    def test_re9_window_parser_ignores_project_terminals(self) -> None:
        tree = """
        0x100001 \"RE9_Still_Scan\": (\"gnome-terminal\" \"Gnome-terminal\")
        0xa200003 \"RESIDENT EVIL requiem BIOHAZARD requiem\": (\"re9.exe\" \"re9.exe\")
        """

        self.assertEqual(_re9_window_id_from_xwininfo(tree), 0xA200003)

    def test_re9_window_parser_returns_none_without_game(self) -> None:
        self.assertIsNone(
            _re9_window_id_from_xwininfo(
                '0x100001 "RE9_Still_Scan": ("gnome-terminal" "Gnome-terminal")'
            )
        )

    def test_re9_window_parser_accepts_live_steam_app_class(self) -> None:
        tree = '0x8400003 "": ("steam_app_3764200" "steam_app_3764200")'

        self.assertEqual(_re9_window_id_from_xwininfo(tree), 0x8400003)

    def test_re9_window_parser_ignores_steam_helper_windows(self) -> None:
        tree = """
        0x7a00005 (has no name): ("steam_app_3764200" "steam_app_3764200")  1x1+0+0  +0+0
        0x7a00004 "Default IME": ("steam_app_3764200" "steam_app_3764200")  1x1+0+0  +0+0
        0x7a00003 "": ("steam_app_3764200" "steam_app_3764200")  2560x1440+0+0  +0+0
        """

        self.assertEqual(_re9_window_id_from_xwininfo(tree), 0x7A00003)

    def test_x11_activation_uses_ewmh_active_window_message(self) -> None:
        x11 = Mock()
        x11.XDefaultRootWindow.return_value = 0x100
        x11.XInternAtom.return_value = 0x200
        x11.XSendEvent.return_value = 1

        self.assertTrue(_request_x11_window_activation(x11, 0x300, 0x400))

        x11.XInternAtom.assert_called_once_with(0x300, b"_NET_ACTIVE_WINDOW", 0)
        send_args = x11.XSendEvent.call_args.args
        self.assertEqual(send_args[:4], (0x300, 0x100, 0, (1 << 20) | (1 << 19)))

    @patch.dict(os.environ, {"DISPLAY": ":0"})
    @patch("re9_pose_recorder.platform_support.find_library", return_value="libX11.so")
    @patch("re9_pose_recorder.platform_support.CDLL")
    @patch("re9_pose_recorder.platform_support.subprocess.run")
    def test_activation_leaves_wm_take_focus_to_the_window_manager(
        self,
        run_mock,
        cdll_mock,
        _find_library_mock,
    ) -> None:
        run_mock.return_value = Mock(
            returncode=0,
            stdout='0xa200003 "RESIDENT EVIL requiem": ("re9.exe" "re9.exe")',
        )
        x11 = cdll_mock.return_value
        x11.XOpenDisplay.return_value = 0x300
        x11.XDefaultRootWindow.return_value = 0x100
        x11.XInternAtom.return_value = 0x200
        x11.XSendEvent.return_value = 1

        self.assertTrue(activate_re9_window("linux"))

        x11.XMapRaised.assert_called_once_with(0x300, 0xA200003)
        x11.XSetInputFocus.assert_not_called()

    @patch.dict(
        os.environ,
        {"DISPLAY": ":0", "RE9_DISABLE_WINDOW_ACTIVATION": "1"},
    )
    @patch("re9_pose_recorder.platform_support.subprocess.run")
    def test_activation_can_be_disabled_for_the_current_process(self, run_mock) -> None:
        self.assertFalse(activate_re9_window("linux"))
        run_mock.assert_not_called()

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

    @patch("re9_pose_recorder.still_scan_gui.activate_re9_window")
    @patch("re9_pose_recorder.still_scan_gui.subprocess.Popen")
    def test_obs_restart_refocuses_game_after_websocket_returns(
        self,
        _popen_mock,
        activate_mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = object.__new__(StillScanApp)
            app.root = Mock()
            app.config = AppConfig(
                raw={"report": {"output_dir": str(root / "outputs")}},
                path=root / "config.yaml",
            )
            app.obs_restart_command = "/usr/bin/obs"
            app._terminate_obs_processes = Mock()
            app._clear_obs_sentinel_files = Mock()
            app._wait_for_obs_websocket = Mock()

            app._restart_obs_between_batches(30, 100)

        app._wait_for_obs_websocket.assert_called_once_with()
        activate_mock.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
