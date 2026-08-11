from __future__ import annotations

import csv
import datetime as dt
import json
import math
from pathlib import Path
from typing import Iterable

from .models import CapturedPoint, SpatialPoint, StillSample
from .paths import PLANS_DIR, ensure_data_dirs
from .storage import safe_id


VIEW_PATTERNS: tuple[tuple[str, float, tuple[float, ...]], ...] = (
    ("middle", 0.0, tuple(index * 45.0 for index in range(8))),
    ("upper", 45.0, tuple(index * 60.0 for index in range(6))),
    ("lower", -45.0, tuple(index * 60.0 for index in range(6))),
    ("ceiling", 90.0, (0.0,)),
    ("floor", -90.0, (0.0,)),
)


def linspace(start: float, stop: float, count: int) -> list[float]:
    if count <= 0:
        raise ValueError("Grid counts must be positive")
    if count == 1:
        return [(start + stop) * 0.5]
    step = (stop - start) / (count - 1)
    return [start + index * step for index in range(count)]


def captured_bounds(points: Iterable[CapturedPoint]) -> dict[str, float]:
    values = list(points)
    if len(values) < 2:
        raise ValueError("Capture at least two scene points before planning")
    return {
        "x_min": min(point.pose.x for point in values),
        "x_max": max(point.pose.x for point in values),
        "y_min": min(point.pose.y for point in values),
        "y_max": max(point.pose.y for point in values),
        "z_min": min(point.pose.z for point in values),
        "z_max": max(point.pose.z for point in values),
    }


def build_spatial_grid(
    points: Iterable[CapturedPoint],
    *,
    count_x: int,
    count_y: int,
    count_z: int,
) -> tuple[dict[str, float], list[SpatialPoint]]:
    bounds = captured_bounds(points)
    xs = linspace(bounds["x_min"], bounds["x_max"], count_x)
    ys = linspace(bounds["y_min"], bounds["y_max"], count_y)
    zs = linspace(bounds["z_min"], bounds["z_max"], count_z)
    positions: list[SpatialPoint] = []
    index = 1
    for z in zs:
        for y in ys:
            for x in xs:
                positions.append(SpatialPoint(index, x, y, z))
                index += 1
    return bounds, positions


def build_22_view_plan(
    positions: Iterable[SpatialPoint],
    *,
    fov_degrees: float,
) -> list[StillSample]:
    samples: list[StillSample] = []
    sample_index = 1
    for point in positions:
        for pattern, pitch_degrees, yaw_values in VIEW_PATTERNS:
            for yaw_degrees in yaw_values:
                samples.append(
                    StillSample(
                        sample_index=sample_index,
                        point_index=point.point_index,
                        pattern=pattern,
                        x=point.x,
                        y=point.y,
                        z=point.z,
                        yaw_degrees=yaw_degrees,
                        pitch_degrees=pitch_degrees,
                        fov_degrees=fov_degrees,
                    )
                )
                sample_index += 1
    return samples


def save_scan_plan(
    scene_id: str,
    bounds: dict[str, float],
    positions: list[SpatialPoint],
    samples: list[StillSample],
    *,
    count_x: int,
    count_y: int,
    count_z: int,
) -> dict[str, Path]:
    ensure_data_dirs()
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = f"{stamp}_{safe_id(scene_id)}"
    positions_csv = PLANS_DIR / f"{prefix}_positions.csv"
    samples_csv = PLANS_DIR / f"{prefix}_22view_samples.csv"
    manifest = PLANS_DIR / f"{prefix}_plan.json"
    _write_csv(positions_csv, [item.as_dict() for item in positions])
    _write_csv(samples_csv, [item.as_dict() for item in samples])
    manifest.write_text(
        json.dumps(
            {
                "scene_id": safe_id(scene_id),
                "created_at": dt.datetime.now().astimezone().isoformat(),
                "bounds": bounds,
                "grid_counts": {
                    "x": count_x,
                    "y": count_y,
                    "z": count_z,
                },
                "spatial_point_count": len(positions),
                "views_per_point": 22,
                "image_count": len(samples),
                "vertical_axis": "z",
                "positions_csv": str(positions_csv),
                "samples_csv": str(samples_csv),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "manifest": manifest,
        "positions_csv": positions_csv,
        "samples_csv": samples_csv,
    }


def estimate_path_length(positions: Iterable[SpatialPoint]) -> float:
    ordered = list(positions)
    return sum(
        math.dist(
            (left.x, left.y, left.z),
            (right.x, right.y, right.z),
        )
        for left, right in zip(ordered, ordered[1:])
    )


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
