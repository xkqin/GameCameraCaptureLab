from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .backend import CameraBackend
from .models import TrajectoryKeyframe
from .storage import TrajectoryStore


class TrajectoryService:
    def __init__(self, backend: CameraBackend, trajectory_id: str) -> None:
        self.backend = backend
        self.store = TrajectoryStore(trajectory_id)
        self.last_import_path: Path | None = None
        self.last_import_trajectory_id: str | None = None

    def set_trajectory_id(self, trajectory_id: str) -> None:
        self.store = TrajectoryStore(trajectory_id)

    def capture_keyframe(self) -> TrajectoryKeyframe:
        return self.store.append_pose(self.backend.pose())

    def clear_keyframes(self) -> None:
        self.store.reset()

    def load_keyframes(self) -> list[TrajectoryKeyframe]:
        return self.store.load()

    def load_external_json(self, path: str | Path) -> list[TrajectoryKeyframe]:
        source = Path(path)
        payload = json.loads(source.read_text(encoding="utf-8"))
        coordinate_system = (
            payload.get("coordinate_system", {})
            if isinstance(payload, dict)
            else {}
        )
        if isinstance(payload, list):
            raw_frames = payload
            imported_trajectory_id = source.stem
        else:
            selected = payload
            if "trajectories" in payload:
                trajectories = payload.get("trajectories") or []
                if not trajectories:
                    raise ValueError("Trajectory JSON contains no trajectories")
                selected = trajectories[0]
            raw_frames = selected.get("keyframes") or selected.get("frames") or []
            imported_trajectory_id = str(
                selected.get("trajectory_id") or source.stem
            )
        if not raw_frames:
            raise ValueError("Trajectory JSON contains no keyframes")
        generic_angles: list[float] = []
        for item in raw_frames:
            for name in ("yaw", "pitch", "roll"):
                if name in item and item[name] not in (None, ""):
                    generic_angles.append(abs(float(item[name])))
        unit_text = str(
            coordinate_system.get("angle_unit")
            or coordinate_system.get("yaw_unit")
            or ""
        ).lower()
        if "radian" in unit_text:
            generic_unit = "radians"
        elif "degree" in unit_text:
            generic_unit = "degrees"
        else:
            generic_unit = (
                "degrees"
                if generic_angles and max(generic_angles) > math.tau + 0.5
                else "radians"
            )

        def angle(item: dict[str, Any], name: str) -> float:
            explicit = f"{name}_degrees"
            if explicit in item:
                return float(item[explicit])
            value = float(item.get(name, 0.0))
            return math.degrees(value) if generic_unit == "radians" else value

        frames: list[TrajectoryKeyframe] = []
        for index, item in enumerate(raw_frames):
            frames.append(
                TrajectoryKeyframe(
                    step=int(item.get("step", index)),
                    time_sec=float(item.get("time_sec", index * 0.2)),
                    x=float(item["x"]),
                    y=float(item["y"]),
                    z=float(item["z"]),
                    yaw_degrees=angle(item, "yaw"),
                    pitch_degrees=angle(item, "pitch"),
                    roll_degrees=angle(item, "roll"),
                    fov_degrees=float(
                        item.get("fov_degrees", item.get("fov", 63.0))
                    ),
                )
            )
        self.last_import_path = source.resolve()
        self.last_import_trajectory_id = imported_trajectory_id
        return frames

    def run_random(
        self,
        *,
        duration: float,
        hz: float,
        seed: int | None,
        xy_scale: float,
    ) -> dict[str, Any]:
        return self.backend.run_random_trajectory(
            duration=duration,
            hz=hz,
            seed=seed,
            xy_scale=xy_scale,
        )

    def restore_start(self) -> dict[str, Any]:
        return self.backend.restore_export_session()
