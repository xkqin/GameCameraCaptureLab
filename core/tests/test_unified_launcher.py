from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(
    os.name == "nt" and shutil.which("powershell.exe"),
    "The unified Windows launcher is PowerShell-only.",
)
class UnifiedLauncherTests(unittest.TestCase):
    def test_kcd2_dry_run_selects_igcs_backend_without_launching_game(self) -> None:
        script = REPO_ROOT / "launchers" / "launch_unified_capture_studio.ps1"
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-GameId",
                "kcd2",
                "-DryRun",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["game_id"], "kcd2")
        self.assertEqual(payload["backend"], "kcd2_igcs_camera_tools")
        self.assertTrue(payload["data_root"].lower().endswith("capture_data\\kcd2"))
        self.assertEqual(payload["absolute_pose_status"], "unverified_visual_result")
        self.assertIn("camera_tools_source", payload)
        self.assertIn("camera_tools_hash_matches", payload)

    def test_kcd2_explicit_camera_tools_path_is_reported_and_validated(self) -> None:
        script = REPO_ROOT / "launchers" / "launch_unified_capture_studio.ps1"
        with tempfile.TemporaryDirectory() as temp:
            tools_dir = Path(temp)
            (tools_dir / "KCD2CameraTools.dll").write_bytes(b"not-the-real-dll")
            (tools_dir / "IGCSClient.exe").write_bytes(b"placeholder")
            completed = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    "-GameId",
                    "kcd2",
                    "-CameraToolsDir",
                    str(tools_dir),
                    "-DryRun",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["camera_tools_source"], "parameter")
            self.assertTrue(payload["camera_tools_dll_exists"])
            self.assertTrue(payload["camera_tools_client_exists"])
            self.assertFalse(payload["camera_tools_hash_matches"])


if __name__ == "__main__":
    unittest.main()
