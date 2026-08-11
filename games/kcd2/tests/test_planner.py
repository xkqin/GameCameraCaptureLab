from __future__ import annotations

import unittest

from kcd2_capture_studio.models import CapturedPoint, Pose
from kcd2_capture_studio.planner import (
    build_22_view_plan,
    build_spatial_grid,
    captured_bounds,
    linspace,
)


def pose(x: float, y: float, z: float) -> Pose:
    return Pose(
        captured_at="2026-07-28T00:00:00+08:00",
        pid=123,
        x=x,
        y=y,
        z=z,
        q0=0.0,
        q1=0.0,
        q2=0.0,
        q3=1.0,
        pitch_degrees=0.0,
        yaw_degrees=0.0,
        roll_degrees=0.0,
        fov_degrees=63.0,
    )


def captured(index: int, x: float, y: float, z: float) -> CapturedPoint:
    return CapturedPoint(index, "scene", f"p{index}", 0.0, pose(x, y, z))


class PlannerTests(unittest.TestCase):
    def test_linspace_includes_bounds(self) -> None:
        self.assertEqual(linspace(-2.0, 2.0, 3), [-2.0, 0.0, 2.0])
        self.assertEqual(linspace(-2.0, 2.0, 1), [0.0])

    def test_eight_points_produce_expected_bounds(self) -> None:
        points = [
            captured(1, -2, -4, 1),
            captured(2, 8, -4, 1),
            captured(3, -2, 6, 1),
            captured(4, 8, 6, 1),
            captured(5, -2, -4, 7),
            captured(6, 8, -4, 7),
            captured(7, -2, 6, 7),
            captured(8, 8, 6, 7),
        ]
        self.assertEqual(
            captured_bounds(points),
            {
                "x_min": -2,
                "x_max": 8,
                "y_min": -4,
                "y_max": 6,
                "z_min": 1,
                "z_max": 7,
            },
        )

    def test_grid_count_and_exactly_22_views_per_point(self) -> None:
        points = [captured(1, 0, 0, 0), captured(2, 10, 20, 30)]
        _, positions = build_spatial_grid(
            points, count_x=4, count_y=3, count_z=2
        )
        samples = build_22_view_plan(positions, fov_degrees=63.0)
        self.assertEqual(len(positions), 24)
        self.assertEqual(len(samples), 24 * 22)
        for point in positions:
            self.assertEqual(
                sum(sample.point_index == point.point_index for sample in samples),
                22,
            )
        first = [sample for sample in samples if sample.point_index == 1]
        self.assertEqual(sum(sample.pitch_degrees == 0 for sample in first), 8)
        self.assertEqual(sum(sample.pitch_degrees == 45 for sample in first), 6)
        self.assertEqual(sum(sample.pitch_degrees == -45 for sample in first), 6)
        self.assertEqual(sum(sample.pitch_degrees == 90 for sample in first), 1)
        self.assertEqual(sum(sample.pitch_degrees == -90 for sample in first), 1)


if __name__ == "__main__":
    unittest.main()
