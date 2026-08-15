from __future__ import annotations

from pathlib import Path
import unittest

from game_camera_capture_lab.registry import REPO_ROOT
from game_camera_capture_lab.support_catalog import load_support_catalog


class SupportCatalogTests(unittest.TestCase):
    def test_catalog_keeps_public_camera_and_native_runtime_claims_separate(self) -> None:
        catalog = load_support_catalog()
        runtime_verified = catalog.select(["project_runtime_verified"])
        public_only = catalog.select(["public_free_camera_verified"])

        self.assertEqual(
            {game.id for game in runtime_verified},
            {"black-myth-wukong", "backrooms-lost-runners"},
        )
        self.assertGreaterEqual(len(public_only), 13)
        self.assertTrue(all(game.native_profile_status == "profile_required" for game in public_only))
        self.assertTrue(all(game.can_use_public_free_camera for game in public_only))

    def test_every_online_candidate_has_camera_path_pose_field_evidence(self) -> None:
        catalog = load_support_catalog()
        for game in catalog.select(["public_free_camera_verified"]):
            self.assertIn("camera_paths", game.public_camera_features, game.id)
            self.assertIn(
                "camera_path_location_orientation_fov",
                game.public_camera_features,
                game.id,
            )

    def test_only_runtime_verified_games_have_native_profiles(self) -> None:
        catalog = load_support_catalog()
        profile_dir = REPO_ROOT / "runtime" / "ue-camera-runtime" / "profiles"
        profile_ids = {path.stem for path in profile_dir.glob("*.json")}
        runtime_ids = {
            game.id
            for game in catalog.select(["project_runtime_verified"])
            if game.native_profile_status == "runtime_verified"
        }
        self.assertEqual(profile_ids, runtime_ids)
        self.assertTrue(
            all(
                game.id not in profile_ids
                for game in catalog.select(["public_free_camera_verified"])
            )
        )

    def test_project_sources_resolve_inside_repository(self) -> None:
        catalog = load_support_catalog()
        for source in catalog.sources:
            if source.kind == "project":
                self.assertTrue((REPO_ROOT / Path(source.uri)).is_file(), source.uri)


if __name__ == "__main__":
    unittest.main()
