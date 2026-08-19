from __future__ import annotations

from pathlib import Path
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from bmw_capture_studio import integration_repair
from bmw_capture_studio.integration_repair import (
    CameraPreflightReport,
    PreflightIssue,
)


class IntegrationRepairTests(unittest.TestCase):
    def test_native_source_scan_ignores_all_build_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "standalone" / "BmwNativeDepth.cpp"
            build_script = root / "build_standalone.ps1"
            generated = root / "build_native_depth_check" / "CMakeCache.txt"
            source.parent.mkdir(parents=True)
            generated.parent.mkdir(parents=True)
            source.write_text("source", encoding="utf-8")
            build_script.write_text("build", encoding="utf-8")
            generated.write_text("generated", encoding="utf-8")

            with (
                patch.object(integration_repair, "NATIVE_DIR", root),
                patch.object(integration_repair, "REPOSITORY_ROOT", root.parent),
            ):
                discovered = integration_repair._native_sources()

        self.assertIn(source, discovered)
        self.assertIn(build_script, discovered)
        self.assertNotIn(generated, discovered)

    def test_runtime_input_source_does_not_make_unchanged_injector_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "UeCameraRuntime.dll"
            injector = root / "UeCameraInjector.exe"
            input_source = root / "BmwCameraInput.cpp"
            injector_source = root / "UeCameraInjector.cpp"
            for path in (runtime, injector, input_source, injector_source):
                path.write_bytes(b"x")
            os.utime(injector_source, (10, 10))
            os.utime(injector, (20, 20))
            os.utime(input_source, (30, 30))
            os.utime(runtime, (40, 40))
            with patch.object(integration_repair, "UE_RUNTIME_PATH", runtime), patch.object(
                integration_repair, "UE_INJECTOR_PATH", injector
            ), patch.object(
                integration_repair,
                "_native_sources",
                return_value=(input_source, injector_source),
            ):
                needed, _reason = integration_repair._native_build_needed()

        self.assertFalse(needed)

    def test_verified_build_stamp_prevents_permanent_cmake_timestamp_false_positive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "UeCameraRuntime.dll"
            injector = root / "UeCameraInjector.exe"
            cmake = root / "CMakeLists.txt"
            runtime_source = root / "BmwCameraBridge.cpp"
            injector_source = root / "UeCameraInjector.cpp"
            stamp = root / "native_build_verified.json"
            for path, content in (
                (runtime, b"runtime"),
                (injector, b"injector"),
                (cmake, b"cmake"),
                (runtime_source, b"camera"),
                (injector_source, b"loader"),
            ):
                path.write_bytes(content)
            os.utime(injector, (20, 20))
            os.utime(cmake, (30, 30))
            os.utime(runtime_source, (10, 10))
            os.utime(injector_source, (10, 10))
            os.utime(runtime, (40, 40))
            sources = (cmake, runtime_source, injector_source)
            with (
                patch.object(integration_repair, "REPOSITORY_ROOT", root),
                patch.object(integration_repair, "UE_RUNTIME_PATH", runtime),
                patch.object(integration_repair, "UE_INJECTOR_PATH", injector),
                patch.object(integration_repair, "NATIVE_BUILD_STAMP_PATH", stamp),
                patch.object(
                    integration_repair, "_native_sources", return_value=sources
                ),
            ):
                needed_before, _reason = integration_repair._native_build_needed()
                self.assertTrue(needed_before)
                integration_repair._write_native_build_stamp()
                needed_after, _reason = integration_repair._native_build_needed()
                self.assertFalse(needed_after)
                cmake.write_bytes(b"cmake changed")
                needed_changed, _reason = integration_repair._native_build_needed()
                self.assertTrue(needed_changed)

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
