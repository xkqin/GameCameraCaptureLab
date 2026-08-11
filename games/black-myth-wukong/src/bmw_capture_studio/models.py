from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class CameraPose:
    x: float
    y: float
    z: float
    yaw_degrees: float
    pitch_degrees: float
    roll_degrees: float
    fov_degrees: float
    qx: float = 0.0
    qy: float = 0.0
    qz: float = 0.0
    qw: float = 1.0
    right_x: float = 1.0
    right_y: float = 0.0
    right_z: float = 0.0
    up_x: float = 0.0
    up_y: float = 0.0
    up_z: float = 1.0
    forward_x: float = 0.0
    forward_y: float = 1.0
    forward_z: float = 0.0
    camera_enabled: bool = True
    movement_locked: bool = False

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "CameraPose":
        source = dict(value)
        position = source.get("position")
        if isinstance(position, dict):
            for axis in ("x", "y", "z"):
                source.setdefault(axis, position.get(axis))
        elif isinstance(position, (list, tuple)) and len(position) >= 3:
            source.setdefault("x", position[0])
            source.setdefault("y", position[1])
            source.setdefault("z", position[2])
        rotation = source.get("rotation")
        if isinstance(rotation, dict):
            for axis in ("yaw", "pitch", "roll"):
                source.setdefault(axis, rotation.get(axis))

        def number(*names: str, default: float = 0.0) -> float:
            for name in names:
                if name in source and source[name] not in (None, ""):
                    return float(source[name])
            return default

        return cls(
            x=number("x"),
            y=number("y"),
            z=number("z"),
            yaw_degrees=number("yaw_degrees", "yaw"),
            pitch_degrees=number("pitch_degrees", "pitch"),
            roll_degrees=number("roll_degrees", "roll"),
            fov_degrees=number("fov_degrees", "fov", default=63.0),
            qx=number("qx", "q0"),
            qy=number("qy", "q1"),
            qz=number("qz", "q2"),
            qw=number("qw", "q3", default=1.0),
            right_x=number("right_x", default=1.0),
            right_y=number("right_y"),
            right_z=number("right_z"),
            up_x=number("up_x"),
            up_y=number("up_y"),
            up_z=number("up_z", default=1.0),
            forward_x=number("forward_x"),
            forward_y=number("forward_y", default=1.0),
            forward_z=number("forward_z"),
            camera_enabled=bool(source.get("camera_enabled", True)),
            movement_locked=bool(source.get("movement_locked", False)),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapturePoint:
    index: int
    label: str
    pose: CameraPose
    time_sec: float = 0.0

    @classmethod
    def from_mapping(cls, value: dict[str, Any], index: int) -> "CapturePoint":
        pose_value = value.get("pose")
        pose_source = {**value, **pose_value} if isinstance(pose_value, dict) else value
        return cls(
            index=int(value.get("index", value.get("point_index", index))),
            label=str(value.get("label", f"point_{index:04d}")),
            pose=CameraPose.from_mapping(pose_source),
            time_sec=float(value.get("time_sec", value.get("timestamp_sec", 0.0))),
        )

    def flat_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "label": self.label,
            "time_sec": self.time_sec,
            **self.pose.as_dict(),
        }
