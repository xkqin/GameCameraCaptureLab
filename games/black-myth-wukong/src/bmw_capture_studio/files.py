from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

from .models import CapturePoint


POINT_FIELDS = [
    "index",
    "label",
    "time_sec",
    "x",
    "y",
    "z",
    "yaw_degrees",
    "pitch_degrees",
    "roll_degrees",
    "fov_degrees",
    "qx",
    "qy",
    "qz",
    "qw",
]


def _items_from_json(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("JSON 根节点必须是对象或数组")
    for key in ("points", "keyframes", "frames", "samples"):
        items = payload.get(key)
        if isinstance(items, list):
            return items
    trajectories = payload.get("trajectories")
    if isinstance(trajectories, list) and trajectories:
        first = trajectories[0]
        if isinstance(first, dict):
            return _items_from_json(first)
    raise ValueError("文件中没有 points、keyframes、frames 或 samples")


def load_points(path: str | Path) -> list[CapturePoint]:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".json":
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
        rows = _items_from_json(payload)
    elif suffix == ".csv":
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    else:
        raise ValueError("只支持 JSON 或 CSV 文件")
    points = [CapturePoint.from_mapping(row, index + 1) for index, row in enumerate(rows)]
    if not points:
        raise ValueError("点位文件为空")
    return points


def save_points(path: str | Path, points: Iterable[CapturePoint], *, kind: str) -> Path:
    target = Path(path)
    values = list(points)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() == ".csv":
        with target.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=POINT_FIELDS)
            writer.writeheader()
            for point in values:
                row = point.flat_dict()
                writer.writerow({field: row.get(field, "") for field in POINT_FIELDS})
    else:
        if target.suffix.lower() != ".json":
            target = target.with_suffix(".json")
        key = "keyframes" if kind == "trajectory" else "points"
        payload = {
            "format": "bmw-uuu-camera-v1",
            "kind": kind,
            "coordinate_system": {"angle_unit": "degrees"},
            "count": len(values),
            key: [point.flat_dict() for point in values],
        }
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return target
