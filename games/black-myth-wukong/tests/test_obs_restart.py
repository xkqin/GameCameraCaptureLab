from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from bmw_capture_studio.obs_restart import (
    OBSProcessRestarter,
    command_for_popen,
    default_obs_restart_command,
    obs_sentinel_dir,
    platform_key,
)


class _HealthyOBS:
    def __init__(self) -> None:
        self.tested = False

    def test(self):
        self.tested = True
        return {"obs_version": "test"}

    def close(self):
        pass


class OBSRestartTests(unittest.TestCase):
    def test_default_command_uses_obs_executable_without_forcing_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            program_files = Path(directory) / "Program Files"
            executable = program_files / "obs-studio" / "bin" / "64bit" / "obs64.exe"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"stub")
            command = default_obs_restart_command(
                "windows",
                environ={"ProgramW6432": str(program_files)},
                which=lambda _name: None,
            )

        self.assertIn("obs64.exe", command)
        self.assertNotIn("--profile", command)
        self.assertNotIn("--collection", command)

    def test_default_command_falls_back_to_local_appdata_install(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            local_appdata = Path(directory) / "AppData" / "Local"
            executable = (
                local_appdata
                / "Programs"
                / "obs-studio"
                / "bin"
                / "64bit"
                / "obs64.exe"
            )
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"stub")
            command = default_obs_restart_command(
                "windows",
                environ={"LOCALAPPDATA": str(local_appdata)},
                which=lambda _name: None,
            )

        self.assertEqual(command, str(executable))

    def test_linux_command_is_split_without_shell(self) -> None:
        self.assertEqual(platform_key("linux"), "linux")
        self.assertEqual(command_for_popen("/usr/bin/obs --verbose", "linux"), ["/usr/bin/obs", "--verbose"])

    def test_sentinel_path_is_platform_specific(self) -> None:
        windows_path = obs_sentinel_dir(
            "windows",
            environ={"APPDATA": r"C:\Users\tester\AppData\Roaming"},
        )
        linux_path = obs_sentinel_dir(
            "linux",
            environ={"XDG_CONFIG_HOME": "/tmp/test-config"},
        )
        self.assertTrue(str(windows_path).endswith("obs-studio\\.sentinel"))
        self.assertEqual(linux_path, Path("/tmp/test-config/obs-studio/.sentinel"))

    def test_wait_for_obs_returns_a_healthy_reconnected_client(self) -> None:
        values: list[_HealthyOBS] = []
        manager = OBSProcessRestarter(
            obs_factory=lambda: values.append(_HealthyOBS()) or values[-1],
            host="127.0.0.1",
            command="obs",
            wait_seconds=1.0,
        )
        candidate = manager._wait_for_obs()
        self.assertIs(candidate, values[0])
        self.assertTrue(values[0].tested)


if __name__ == "__main__":
    unittest.main()
