from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Pose:
    captured_at: str
    pid: int
    x: float
    y: float
    z: float
    q0: float
    q1: float
    q2: float
    q3: float
    pitch_degrees: float
    yaw_degrees: float
    roll_degrees: float
    fov_degrees: float

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> "Pose":
        return cls(
            captured_at=str(raw.get("captured_at") or ""),
            pid=int(raw.get("pid") or 0),
            x=float(raw["x"]),
            y=float(raw["y"]),
            z=float(raw["z"]),
            q0=float(raw["q0"]),
            q1=float(raw["q1"]),
            q2=float(raw["q2"]),
            q3=float(raw["q3"]),
            pitch_degrees=float(raw["pitch_degrees"]),
            yaw_degrees=float(raw["yaw_degrees"]),
            roll_degrees=float(raw["roll_degrees"]),
            fov_degrees=float(raw["fov_degrees"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapturedPoint:
    index: int
    scene_id: str
    label: str
    timestamp_sec: float
    pose: Pose

    def flat_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "scene_id": self.scene_id,
            "label": self.label,
            "timestamp_sec": self.timestamp_sec,
            **self.pose.as_dict(),
        }


@dataclass(frozen=True)
class SpatialPoint:
    point_index: int
    x: float
    y: float
    z: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StillSample:
    sample_index: int
    point_index: int
    pattern: str
    x: float
    y: float
    z: float
    yaw_degrees: float
    pitch_degrees: float
    fov_degrees: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TrajectoryKeyframe:
    step: int
    time_sec: float
    x: float
    y: float
    z: float
    yaw_degrees: float
    pitch_degrees: float
    roll_degrees: float
    fov_degrees: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
