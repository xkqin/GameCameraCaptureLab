from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from game_camera_capture_lab.registry import REPO_ROOT, load_registry
from game_camera_capture_lab.ue_runtime import (
    BytePattern,
    discover_profiles,
    load_profile,
    load_profiles,
    profile_for_process,
    scan_executable,
    validate_match_count,
)
from game_camera_capture_lab.validate import SCHEMA_NAMES, validate_repository


class GameRegistryTests(unittest.TestCase):
    def test_current_adapters_are_discovered(self) -> None:
        adapters = load_registry()
        self.assertEqual(
            {adapter.id for adapter in adapters},
            {"re9", "kcd2", "black-myth-wukong", "backrooms-lost-runners"},
        )
        kcd2 = next(adapter for adapter in adapters if adapter.id == "kcd2")
        command = kcd2.command_for("capture")
        self.assertIn("launch_unified_capture_studio.ps1", command[-3])
        self.assertEqual(command[-2:], ["-GameId", "kcd2"])
        for adapter in adapters:
            self.assertTrue(adapter.documentation.is_file())
            self.assertTrue(adapter.examples.exists())
            self.assertTrue(adapter.actions)

    def test_commands_have_no_unresolved_tokens(self) -> None:
        for adapter in load_registry():
            for action in adapter.actions:
                command = adapter.command_for(action.id, python="python-test")
                self.assertTrue(command)
                self.assertFalse(any("{" in part or "}" in part for part in command))

    def test_repository_validator_passes(self) -> None:
        self.assertEqual(validate_repository(), [])

    def test_schemas_are_valid_json_schema_documents(self) -> None:
        for name in SCHEMA_NAMES:
            payload = json.loads((REPO_ROOT / "core" / "schemas" / name).read_text(encoding="utf-8"))
            self.assertEqual(
                payload["$schema"],
                "https://json-schema.org/draft/2020-12/schema",
            )
            self.assertIn("$id", payload)

    def test_registry_is_not_limited_to_three_games(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(6):
                game = root / "games" / f"game-{index}"
                game.mkdir(parents=True)
                (game / "README.md").write_text("example", encoding="utf-8")
                (game / "examples").mkdir()
                manifest = {
                    "schema_version": 1,
                    "id": f"game-{index}",
                    "name": f"Game {index}",
                    "short_name": f"G{index}",
                    "engine": "Test Engine",
                    "maturity": "experimental",
                    "summary": "Registry scalability test.",
                    "documentation": "README.md",
                    "examples": "examples",
                    "capabilities": {"pose_read": "experimental"},
                    "actions": [
                        {
                            "id": "capture",
                            "label": "Launch",
                            "platforms": ["windows", "linux", "macos"],
                            "working_directory": ".",
                            "command": ["{python}", "-c", "pass"],
                        }
                    ],
                }
                (game / "game.json").write_text(
                    json.dumps(manifest),
                    encoding="utf-8",
                )
            self.assertEqual(len(load_registry(root)), 6)

    def test_ue_runtime_profile_is_loaded_and_process_selectable(self) -> None:
        profile_dir = REPO_ROOT / "core" / "runtime" / "ue-camera-runtime" / "profiles"
        profiles = load_profiles(profile_dir)
        self.assertEqual(len(profiles), 2)
        profile = load_profile(profile_dir / "black-myth-wukong.json")
        self.assertEqual(profile.id, "black-myth-wukong")
        self.assertEqual(profile.camera_hook.hook_offset, 9)
        self.assertEqual(profile.camera_hook.continuation_offset, 37)
        selected = profile_for_process("B1-WIN64-SHIPPING.EXE", profiles)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.id, profile.id)
        self.assertIsNone(profile_for_process("other-game.exe", profiles))
        backrooms = profile_for_process(
            "BACKROOMSLOSTRUNNERS-WIN64-SHIPPING.EXE",
            profiles,
        )
        self.assertIsNotNone(backrooms)
        assert backrooms is not None
        self.assertEqual(backrooms.id, "backrooms-lost-runners")
        self.assertEqual(backrooms.camera_hook.min_matches, 3)
        self.assertEqual(backrooms.camera_hook.max_matches, 3)

    def test_ue_runtime_pattern_scanner_handles_wildcards(self) -> None:
        pattern = BytePattern.parse("AA ?? CC")
        self.assertEqual(pattern.find_all(bytes.fromhex("00 AA 01 CC AA FF CC")), (1, 4))

    def test_ue_runtime_scanner_is_offline_and_counts_real_profile_pattern(self) -> None:
        profile = load_profile(
            REPO_ROOT / "core" / "runtime" / "ue-camera-runtime" / "profiles" / "black-myth-wukong.json"
        )
        executable = Path(r"D:\steam\steamapps\common\BlackMythWukong\b1\Binaries\Win64\b1-Win64-Shipping.exe")
        if not executable.is_file():
            self.skipTest("Black Myth executable is not installed on this machine")
        matches = scan_executable(executable, profile.camera_hook)
        self.assertTrue(validate_match_count(profile, matches), matches)

    def test_backrooms_profile_matches_installed_ue56_build(self) -> None:
        profile = load_profile(
            REPO_ROOT / "core" / "runtime" / "ue-camera-runtime" / "profiles" /
            "backrooms-lost-runners.json"
        )
        executable = Path(
            "D:/steam/steamapps/common/Backrooms Lost Runners/"
            "BackroomsLostRunners/Binaries/Win64/"
            "BackroomsLostRunners-Win64-Shipping.exe"
        )
        if not executable.is_file():
            self.skipTest("Backrooms Lost Runners is not installed on this machine")
        matches = scan_executable(executable, profile.camera_hook)
        self.assertEqual(len(matches), 3)
        self.assertTrue(validate_match_count(profile, matches), matches)


if __name__ == "__main__":
    unittest.main()
