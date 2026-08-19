"""Shared native-game-unit metadata and deterministic metric conversion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class GameCoordinateScale:
    game_id: str
    position_unit: str
    meters_per_unit: float
    scale_source: str = "user_provided"

    def position_m(self, x: float, y: float, z: float) -> dict[str, float]:
        return {
            "x": float(x) * self.meters_per_unit,
            "y": float(y) * self.meters_per_unit,
            "z": float(z) * self.meters_per_unit,
        }

    def coordinate_system(
        self,
        *,
        handedness: str = "unknown",
        vertical_axis: str = "z",
        angle_unit: str = "degrees",
    ) -> dict[str, Any]:
        return {
            "handedness": handedness,
            "vertical_axis": vertical_axis,
            "angle_unit": angle_unit,
            "position_unit": self.position_unit,
            "meters_per_unit": self.meters_per_unit,
            "scale_source": self.scale_source,
            "native_position_preserved": True,
        }

    def metadata(self) -> dict[str, Any]:
        return {
            "position_unit": self.position_unit,
            "meters_per_unit": self.meters_per_unit,
            "scale_source": self.scale_source,
            "native_position_preserved": True,
        }


GAME_COORDINATE_SCALES: Mapping[str, GameCoordinateScale] = {
    "black-myth-wukong": GameCoordinateScale(
        game_id="black-myth-wukong",
        position_unit="centimeters",
        meters_per_unit=0.01,
    ),
    "kcd2": GameCoordinateScale(
        game_id="kcd2",
        position_unit="meters",
        meters_per_unit=1.0,
    ),
}


def game_coordinate_scale(game_id: str) -> GameCoordinateScale:
    normalized = game_id.strip().casefold()
    try:
        return GAME_COORDINATE_SCALES[normalized]
    except KeyError as exc:
        raise ValueError(f"No coordinate scale is configured for game: {game_id}") from exc
