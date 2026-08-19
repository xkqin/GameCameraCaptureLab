from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path
import shutil
import time
from typing import Any

from .models import CapturedPoint, Pose, TrajectoryKeyframe
from .coordinate_scale import COORDINATE_SCALE
from .paths import BACKUPS_DIR, POINTS_DIR, TRAJECTORIES_DIR, ensure_data_dirs


POINT_FIELDS = [
    "index",
    "scene_id",
    "label",
    "timestamp_sec",
    "captured_at",
    "pid",
    "x",
    "y",
    "z",
    "x_m",
    "y_m",
    "z_m",
    "q0",
    "q1",
    "q2",
    "q3",
    "yaw_degrees",
    "pitch_degrees",
    "roll_degrees",
    "fov_degrees",
]

COORDINATE_SYSTEM = COORDINATE_SCALE.coordinate_system()


def _pose_payload(pose: Pose) -> dict[str, Any]:
    return {
        "position": {"x": pose.x, "y": pose.y, "z": pose.z},
        "position_m": COORDINATE_SCALE.position_m(pose.x, pose.y, pose.z),
        "rotation": {
            "yaw": pose.yaw_degrees,
            "pitch": pose.pitch_degrees,
            "roll": pose.roll_degrees,
        },
        "quaternion": {
            "x": pose.q0,
            "y": pose.q1,
            "z": pose.q2,
            "w": pose.q3,
        },
        "fov_degrees": pose.fov_degrees,
    }


def _shared_pose_to_legacy(item: dict[str, Any]) -> dict[str, Any]:
    """Flatten a shared-schema pose for the existing KCD2 dataclasses."""
    pose = item["pose"]
    position = pose["position"]
    rotation = pose["rotation"]
    quaternion = pose.get("quaternion") or {}
    metadata = item.get("metadata") or {}
    return {
        "captured_at": metadata.get("captured_at", ""),
        "pid": metadata.get("pid", 0),
        "x": position["x"],
        "y": position["y"],
        "z": position["z"],
        "q0": quaternion.get("x", 0.0),
        "q1": quaternion.get("y", 0.0),
        "q2": quaternion.get("z", 0.0),
        "q3": quaternion.get("w", 1.0),
        "yaw_degrees": rotation["yaw"],
        "pitch_degrees": rotation["pitch"],
        "roll_degrees": rotation["roll"],
        "fov_degrees": pose["fov_degrees"],
    }


def safe_id(value: str, fallback: str = "new_scene") -> str:
    cleaned = "".join(
        char if char.isalnum() or char in "._-" else "_"
        for char in value.strip()
    ).strip("._")
    return cleaned or fallback


class PointStore:
    def __init__(self, scene_id: str) -> None:
        ensure_data_dirs()
        self.scene_id = safe_id(scene_id)
        self.csv_path = POINTS_DIR / f"{self.scene_id}_points.csv"
        self.json_path = POINTS_DIR / f"{self.scene_id}_points.json"
        self.started = time.perf_counter()

    def load(self) -> list[CapturedPoint]:
        if not self.json_path.exists():
            return []
        raw = json.loads(self.json_path.read_text(encoding="utf-8-sig"))
        entries = raw.get("points", raw if isinstance(raw, list) else [])
        shared_schema = raw.get("schema_version") == "camera-point-set/v1"
        points: list[CapturedPoint] = []
        for ordinal, item in enumerate(entries, start=1):
            pose = Pose.from_mapping(
                _shared_pose_to_legacy(item) if shared_schema else item
            )
            metadata = item.get("metadata") or {}
            point_index = (
                int(metadata.get("index", ordinal))
                if shared_schema
                else int(item["index"])
            )
            points.append(
                CapturedPoint(
                    index=point_index,
                    scene_id=str(item.get("scene_id") or self.scene_id),
                    label=str(item.get("label") or ""),
                    timestamp_sec=float(
                        item.get("time_sec", 0.0)
                        if shared_schema
                        else item.get("timestamp_sec", 0.0)
                    ),
                    pose=pose,
                )
            )
        return points

    def append(self, pose: Pose, label: str = "") -> CapturedPoint:
        points = self.load()
        point = CapturedPoint(
            index=len(points) + 1,
            scene_id=self.scene_id,
            label=label.strip(),
            timestamp_sec=time.perf_counter() - self.started,
            pose=pose,
        )
        points.append(point)
        self._write(points)
        return point

    def reset(self) -> tuple[Path | None, Path | None]:
        ensure_data_dirs()
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_backup: Path | None = None
        json_backup: Path | None = None
        if self.csv_path.exists():
            csv_backup = BACKUPS_DIR / f"{stamp}_{self.csv_path.name}"
            shutil.copy2(self.csv_path, csv_backup)
        if self.json_path.exists():
            json_backup = BACKUPS_DIR / f"{stamp}_{self.json_path.name}"
            shutil.copy2(self.json_path, json_backup)
        self._write([])
        self.started = time.perf_counter()
        return csv_backup, json_backup

    def _write(self, points: list[CapturedPoint]) -> None:
        rows = [point.flat_dict() for point in points]
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        with self.csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=POINT_FIELDS)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in POINT_FIELDS})
        self.json_path.write_text(
            json.dumps(
                {
                    "schema_version": "camera-point-set/v1",
                    "game_id": "kcd2",
                    "scene_id": self.scene_id,
                    "coordinate_system": COORDINATE_SYSTEM,
                    "points": [
                        {
                            "id": f"point_{point.index:04d}",
                            "label": point.label,
                            "time_sec": point.timestamp_sec,
                            "pose": _pose_payload(point.pose),
                            "metadata": {
                                "index": point.index,
                                "captured_at": point.pose.captured_at,
                                "pid": point.pose.pid,
                                "source": "kcd2-camera-tools",
                            },
                        }
                        for point in points
                    ],
                    "metadata": {"count": len(points)},
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


class TrajectoryStore:
    def __init__(self, trajectory_id: str) -> None:
        ensure_data_dirs()
        self.trajectory_id = safe_id(trajectory_id, "trajectory")
        self.json_path = TRAJECTORIES_DIR / f"{self.trajectory_id}.json"
        self.csv_path = TRAJECTORIES_DIR / f"{self.trajectory_id}.csv"
        self.started = time.perf_counter()

    def load(self) -> list[TrajectoryKeyframe]:
        if not self.json_path.exists():
            return []
        payload = json.loads(self.json_path.read_text(encoding="utf-8-sig"))
        frames = payload.get("keyframes", [])
        if payload.get("schema_version") != "camera-trajectory/v1":
            return [TrajectoryKeyframe(**item) for item in frames]
        converted: list[TrajectoryKeyframe] = []
        for item in frames:
            pose = item["pose"]
            position = pose["position"]
            rotation = pose["rotation"]
            converted.append(
                TrajectoryKeyframe(
                    step=int(item["index"]),
                    time_sec=float(item["time_sec"]),
                    x=float(position["x"]),
                    y=float(position["y"]),
                    z=float(position["z"]),
                    yaw_degrees=float(rotation["yaw"]),
                    pitch_degrees=float(rotation["pitch"]),
                    roll_degrees=float(rotation["roll"]),
                    fov_degrees=float(pose["fov_degrees"]),
                )
            )
        return converted

    def append_pose(self, pose: Pose) -> TrajectoryKeyframe:
        frames = self.load()
        frame = TrajectoryKeyframe(
            step=len(frames),
            time_sec=time.perf_counter() - self.started,
            x=pose.x,
            y=pose.y,
            z=pose.z,
            yaw_degrees=pose.yaw_degrees,
            pitch_degrees=pose.pitch_degrees,
            roll_degrees=pose.roll_degrees,
            fov_degrees=pose.fov_degrees,
        )
        frames.append(frame)
        self._write(frames)
        return frame

    def reset(self) -> None:
        # The shared trajectory schema requires at least one keyframe. An empty
        # trajectory is therefore represented by the absence of its files.
        self.json_path.unlink(missing_ok=True)
        self.csv_path.unlink(missing_ok=True)
        self.started = time.perf_counter()

    def _write(self, frames: list[TrajectoryKeyframe]) -> None:
        rows = [frame.as_dict() for frame in frames]
        self.json_path.write_text(
            json.dumps(
                {
                    "schema_version": "camera-trajectory/v1",
                    "game_id": "kcd2",
                    "trajectory_id": self.trajectory_id,
                    "coordinate_system": COORDINATE_SYSTEM,
                    "keyframes": [
                        {
                            "index": frame.step,
                            "time_sec": frame.time_sec,
                            "pose": {
                                "position": {
                                    "x": frame.x,
                                    "y": frame.y,
                                    "z": frame.z,
                                },
                                "position_m": COORDINATE_SCALE.position_m(
                                    frame.x, frame.y, frame.z
                                ),
                                "rotation": {
                                    "yaw": frame.yaw_degrees,
                                    "pitch": frame.pitch_degrees,
                                    "roll": frame.roll_degrees,
                                },
                                "fov_degrees": frame.fov_degrees,
                            },
                        }
                        for frame in frames
                    ],
                    "metadata": {
                        "source": "kcd2-camera-tools",
                        **COORDINATE_SCALE.metadata(),
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        fieldnames = list(TrajectoryKeyframe.__dataclass_fields__) + [
            "x_m",
            "y_m",
            "z_m",
        ]
        with self.csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
