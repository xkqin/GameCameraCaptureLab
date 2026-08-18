from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path

from re9_pose_recorder.still_scan import (
    _point_in_convex_hull,
    _point_in_ellipsoid,
    build_layered_still_scan_plan,
    load_still_pose_plan,
    load_still_layers,
    slice_still_scan_plan_from_layer,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StillScanHullTests(unittest.TestCase):
    def test_scene_2_dense_plan_keeps_only_hull_points(self) -> None:
        layers = load_still_layers(PROJECT_ROOT / "configs" / "scene_2_scan_layers.yaml")
        plan = build_layered_still_scan_plan(layers)

        self.assertEqual(len(plan), 13_530)
        position_counts = Counter(sample.layer_id for sample in plan if sample.pattern == "middle" and sample.yaw_deg == 0.0)
        self.assertEqual(list(position_counts.values()), [19, 133, 158, 155, 140, 10])

        hull_points = layers[0].hull_points
        self.assertIsNotNone(hull_points)
        assert hull_points is not None
        unique_positions = {(sample.x, sample.y, sample.z) for sample in plan}
        self.assertEqual(len(unique_positions), 615)
        self.assertTrue(all(_point_in_convex_hull(point, hull_points) for point in unique_positions))

    def test_existing_scene_1_plan_is_unchanged_without_hull(self) -> None:
        layers = load_still_layers(PROJECT_ROOT / "configs" / "scene01_scan_layers.yaml")
        plan = build_layered_still_scan_plan(layers)
        self.assertEqual(len(plan), 24_508)

    def test_scene_2_resume_starts_at_y03_without_rebuilding_earlier_layers(self) -> None:
        layers = load_still_layers(PROJECT_ROOT / "configs" / "scene_2_scan_layers.yaml")
        plan = build_layered_still_scan_plan(layers)
        resumed = slice_still_scan_plan_from_layer(plan, "scene_2_y03")

        self.assertEqual(len(resumed), 10_186)
        self.assertEqual(resumed[0].layer_id, "scene_2_y03")
        self.assertEqual(resumed[0].sample_index, 3_345)
        self.assertEqual(resumed[-1].layer_id, "scene_2_y06")

    def test_scene_2_indoor_oblique_plan_is_dense_ui_loadable_and_avoids_chandelier(self) -> None:
        repository_root = PROJECT_ROOT.parent
        plan_path = (
            repository_root
            / "data"
            / "reconstruction_capture_plans"
            / "scene_2_indoor_oblique"
            / "scene_2_indoor_oblique_pose_plan.json"
        )
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
        metrics = payload["metrics"]
        self.assertEqual(metrics["layer_count"], 6)
        self.assertEqual(metrics["unique_position_count"], 567)
        self.assertEqual(metrics["route_position_count"], 570)
        self.assertEqual(metrics["route_revisit_count"], 3)
        self.assertEqual(metrics["sample_count"], 2_850)
        self.assertEqual(metrics["views_per_route_position"], 5)
        self.assertLessEqual(metrics["max_intralayer_step"], 0.631)
        self.assertLessEqual(metrics["max_route_step_including_layer_changes"], 2.848)

        loaded = load_still_pose_plan(plan_path, group_id="scene_2_indoor_oblique")
        self.assertEqual(len(loaded), 2_850)
        self.assertEqual(len({sample.layer_id for sample in loaded}), 6)

        layers = load_still_layers(PROJECT_ROOT / "configs" / "scene_2_no_lamp_scan_layers.yaml")
        hull_points = layers[0].hull_points
        self.assertIsNotNone(hull_points)
        assert hull_points is not None
        ellipsoids = layers[0].exclude_ellipsoids
        unique_positions = {(sample.x, sample.y, sample.z) for sample in loaded}
        self.assertEqual(len(unique_positions), 567)
        self.assertTrue(all(_point_in_convex_hull(point, hull_points) for point in unique_positions))
        self.assertTrue(
            all(not any(_point_in_ellipsoid(point, item) for item in ellipsoids) for point in unique_positions)
        )

        pitches = Counter(sample.pitch_deg for sample in loaded)
        self.assertIn(60.0, pitches)
        self.assertIn(-60.0, pitches)
        self.assertEqual(pitches[82.0], 299)
        self.assertEqual(pitches[-82.0], 271)
        self.assertEqual(pitches[0.0], 1_710)

    def test_scene_3_outdoor_plans_cover_ground_without_upward_sky_views(self) -> None:
        repository_root = PROJECT_ROOT.parent
        expectations = {
            "scene_3.1": {
                "positions": 4_856,
                "samples": 16_405,
                "direct_down": 1_837,
                "max_step": 1.869,
            },
            "scene_3.2": {
                "positions": 7_527,
                "samples": 24_957,
                "direct_down": 2_376,
                "max_step": 1.956,
            },
        }
        for scene_id, expected in expectations.items():
            with self.subTest(scene_id=scene_id):
                plan_path = (
                    repository_root
                    / "data"
                    / "reconstruction_capture_plans"
                    / scene_id
                    / f"{scene_id}_reconstruction_manifest.json"
                )
                payload = json.loads(plan_path.read_text(encoding="utf-8"))
                metrics = payload["metrics"]
                self.assertTrue(payload["ui_compatible"])
                self.assertEqual(payload["capture"]["profile"], "outdoor_ground_coverage")
                self.assertEqual(metrics["position_count"], expected["positions"])
                self.assertEqual(metrics["sample_count"], expected["samples"])
                self.assertEqual(metrics["views_per_position_min"], 3)
                self.assertEqual(metrics["views_per_position_max"], 4)
                self.assertEqual(
                    metrics["view_counts"]["terrain_direct_down"],
                    expected["direct_down"],
                )
                self.assertLessEqual(metrics["max_intralayer_step"], expected["max_step"])

                loaded = load_still_pose_plan(plan_path, group_id=f"{scene_id}_reconstruction")
                self.assertEqual(len(loaded), expected["samples"])
                self.assertEqual(len({sample.layer_id for sample in loaded}), 5)
                pitches = Counter(sample.pitch_deg for sample in loaded)
                self.assertEqual(pitches[-82.0], expected["direct_down"])
                self.assertTrue(all(pitch <= 0.0 for pitch in pitches))
                self.assertEqual(set(pitches), {0.0, -10.0, -25.0, -40.0, -55.0, -82.0})


if __name__ == "__main__":
    unittest.main()
