from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bmw_capture_studio import integration_repair
from bmw_capture_studio.integration_repair import (
    CameraPreflightReport,
    PreflightIssue,
)


class IntegrationRepairTests(unittest.TestCase):
    def test_linux_keeps_the_existing_proton_injection_helper(self) -> None:
        with patch.object(
            integration_repair.sys, "platform", "linux"
        ), patch.object(
            integration_repair,
            "inject_bridge",
            return_value={"pid": 77, "already_loaded": False},
        ) as inject:
            result = integration_repair.repair_and_inject(auto_repair=True)

        inject.assert_called_once_with()
        self.assertEqual(result["pid"], 77)
        self.assertTrue(result["linux_helper"])

    def test_preflight_accepts_a_validated_profile_before_injection(self) -> None:
        hook = SimpleNamespace(min_matches=1, max_matches=2)
        hud = SimpleNamespace(min_matches=1, max_matches=1)
        profile = SimpleNamespace(
            id="black-myth-wukong",
            camera_hook=hook,
            hud_hook=hud,
        )
        with patch.object(
            integration_repair,
            "integration_status",
            return_value={
                "game_running": True,
                "module_scan_ok": True,
                "pid": 42,
                "bridge_loaded": False,
                "conflicting_camera_tool": False,
            },
        ), patch.object(
            integration_repair,
            "process_executable_path",
            return_value=Path("C:/Games/b1-Win64-Shipping.exe"),
        ), patch.object(
            integration_repair, "load_profiles", return_value=(profile,)
        ), patch.object(
            integration_repair, "profile_for_process", return_value=profile
        ), patch.object(
            integration_repair,
            "scan_executable_hooks",
            return_value={"camera": ((".text", 10, 19),), "hud": ((".text", 20, 20),)},
        ), patch.object(
            integration_repair,
            "_native_build_needed",
            return_value=(False, "ready"),
        ):
            report = integration_repair.preflight_camera_integration()

        self.assertTrue(report.ready)
        self.assertEqual(report.camera_match_count, 1)
        self.assertEqual(report.hud_match_count, 1)

    def test_signature_mismatch_is_not_blindly_auto_repaired(self) -> None:
        report = CameraPreflightReport(
            pid=42,
            executable="C:/Games/game.exe",
            profile_id="black-myth-wukong",
            profile_path="profile.json",
            camera_match_count=0,
            hud_match_count=1,
            bridge_loaded=False,
            runtime_path="runtime.dll",
            injector_path="injector.exe",
            issues=(
                PreflightIssue(
                    "camera_signature_mismatch",
                    "camera signature mismatch",
                    repairable=False,
                ),
            ),
        )
        self.assertFalse(report.ready)
        self.assertFalse(report.can_auto_repair)

    def test_auto_repair_builds_then_writes_config_and_injects(self) -> None:
        needs_build = CameraPreflightReport(
            pid=42,
            executable="C:/Games/game.exe",
            profile_id="black-myth-wukong",
            profile_path="profile.json",
            camera_match_count=1,
            hud_match_count=1,
            bridge_loaded=False,
            runtime_path="runtime.dll",
            injector_path="injector.exe",
            issues=(PreflightIssue("native_build_required", "missing", True),),
        )
        ready = CameraPreflightReport(
            pid=42,
            executable="C:/Games/game.exe",
            profile_id="black-myth-wukong",
            profile_path="profile.json",
            camera_match_count=1,
            hud_match_count=1,
            bridge_loaded=False,
            runtime_path="runtime.dll",
            injector_path="injector.exe",
            issues=(),
        )
        with patch.object(
            integration_repair,
            "preflight_camera_integration",
            side_effect=(needs_build, ready),
        ), patch.object(
            integration_repair, "_build_native_runtime", return_value="built"
        ) as build, patch.object(
            integration_repair,
            "_write_active_config",
            return_value=Path("active.json"),
        ) as write_config, patch.object(
            integration_repair,
            "_write_preflight_diagnostic",
            return_value=Path("diagnostic.json"),
        ), patch.object(
            integration_repair,
            "inject_bridge",
            return_value={"pid": 42, "already_loaded": False},
        ) as inject:
            result = integration_repair.repair_and_inject(auto_repair=True)

        build.assert_called_once_with()
        write_config.assert_called_once_with(ready)
        inject.assert_called_once()
        self.assertTrue(result["native_rebuilt"])
        self.assertEqual(result["profile"], "black-myth-wukong")


if __name__ == "__main__":
    unittest.main()
