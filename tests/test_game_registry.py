from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from game_camera_capture_lab.registry import REPO_ROOT, load_registry
from game_camera_capture_lab.validate import SCHEMA_NAMES, validate_repository


class GameRegistryTests(unittest.TestCase):
    def test_current_adapters_are_discovered(self) -> None:
        adapters = load_registry()
        self.assertEqual(
            {adapter.id for adapter in adapters},
            {"re9", "kcd2", "black-myth-wukong"},
        )
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
            payload = json.loads((REPO_ROOT / "schemas" / name).read_text(encoding="utf-8"))
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


if __name__ == "__main__":
    unittest.main()
