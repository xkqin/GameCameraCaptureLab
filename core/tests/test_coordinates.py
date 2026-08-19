from __future__ import annotations

import unittest

from game_camera_capture_lab.coordinates import game_coordinate_scale


class CoordinateScaleTests(unittest.TestCase):
    def test_black_myth_game_units_are_centimeters(self) -> None:
        scale = game_coordinate_scale("black-myth-wukong")
        self.assertEqual(scale.meters_per_unit, 0.01)
        self.assertEqual(scale.position_m(100.0, -250.0, 0.5), {
            "x": 1.0,
            "y": -2.5,
            "z": 0.005,
        })
        self.assertEqual(scale.coordinate_system()["scale_source"], "user_provided")

    def test_kcd2_game_units_are_meters(self) -> None:
        scale = game_coordinate_scale("kcd2")
        self.assertEqual(scale.meters_per_unit, 1.0)
        self.assertEqual(scale.position_m(1.0, 2.0, 3.0), {
            "x": 1.0,
            "y": 2.0,
            "z": 3.0,
        })


if __name__ == "__main__":
    unittest.main()
