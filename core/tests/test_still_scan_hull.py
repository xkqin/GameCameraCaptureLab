from __future__ import annotations

import unittest
from collections import Counter
from pathlib import Path

from re9_pose_recorder.still_scan import (
    _point_in_convex_hull,
    build_layered_still_scan_plan,
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


if __name__ == "__main__":
    unittest.main()
