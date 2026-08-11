from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path
import shutil
import time
from typing import Any

from .models import CapturedPoint, Pose, TrajectoryKeyframe
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
    "q0",
    "q1",
    "q2",
    "q3",
    "yaw_degrees",
    "pitch_degrees",
    "roll_degrees",
    "fov_degrees",
]


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
        points: list[CapturedPoint] = []
        for item in entries:
            pose = Pose.from_mapping(item)
            points.append(
                CapturedPoint(
                    index=int(item["index"]),
                    scene_id=str(item.get("scene_id") or self.scene_id),
                    label=str(item.get("label") or ""),
                    timestamp_sec=float(item.get("timestamp_sec") or 0.0),
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
                    "scene_id": self.scene_id,
                    "count": len(rows),
                    "points": rows,
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
        return [TrajectoryKeyframe(**item) for item in frames]

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
        self._write([])
        self.started = time.perf_counter()

    def _write(self, frames: list[TrajectoryKeyframe]) -> None:
        rows = [frame.as_dict() for frame in frames]
        self.json_path.write_text(
            json.dumps(
                {
                    "trajectory_id": self.trajectory_id,
                    "coordinate_system": {
                        "angle_unit": "degrees",
                        "vertical_axis": "z",
                    },
                    "keyframes": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        fieldnames = list(TrajectoryKeyframe.__dataclass_fields__)
        with self.csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
