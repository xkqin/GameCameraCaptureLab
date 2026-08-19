from __future__ import annotations

"""Build a dense five-layer point map inside a recorded 3-D camera envelope.

The spatial layout is designed for reconstruction rather than simple coverage:
horizontal samples are maximin-spread, adjacent height layers use different
sampling phases, and the final ordering follows serpentine rows to avoid large
unnecessary set-pose jumps.  The Black Myth UI expands every spatial position
into its existing 22-view still pattern.
"""

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from scipy.spatial import ConvexHull, cKDTree

LAYER_FRACTIONS = (0.12, 0.30, 0.48, 0.66, 0.84)
LAYER_PITCHES = (-10.0, -20.0, -30.0, -40.0, -55.0)
VIEW_PATTERN = (
    {"pattern": "middle", "pitch_degrees": 0.0, "yaw_degrees": list(range(0, 360, 45))},
    {"pattern": "upper", "pitch_degrees": 45.0, "yaw_degrees": list(range(0, 360, 60))},
    {"pattern": "lower", "pitch_degrees": -45.0, "yaw_degrees": list(range(0, 360, 60))},
    {"pattern": "ceiling", "pitch_degrees": 90.0, "yaw_degrees": [0.0]},
    {"pattern": "floor", "pitch_degrees": -90.0, "yaw_degrees": [0.0]},
)


def load_records(source: Path, source_count: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[int]]:
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict) or not isinstance(payload.get("points"), list):
        raise TypeError("The source JSON must contain a points list")
    records = [dict(item) for item in payload["points"] if isinstance(item, dict)]
    records.sort(key=lambda item: int(item.get("index", 0)))
    if len(records) < source_count:
        raise ValueError(f"Requested {source_count} source points, but only {len(records)} are available")
    ignored = [int(item.get("index", index + 1)) for index, item in enumerate(records[source_count:])]
    return payload, records[:source_count], ignored


def hex_candidates(
    bounds_xy: tuple[np.ndarray, np.ndarray],
    z_value: float,
    hull_equations: np.ndarray,
    margin_m: float,
    spacing_m: float,
    phase: int,
) -> np.ndarray:
    minimum, maximum = bounds_xy
    row_step = spacing_m * math.sqrt(3.0) / 2.0
    y_start = math.floor(minimum[1] / row_step) * row_step
    rows: list[np.ndarray] = []
    row_index = 0
    y = y_start
    while y <= maximum[1] + row_step:
        x_shift = ((row_index + phase) % 2) * spacing_m / 2.0
        x_shift += (phase % 3) * spacing_m / 6.0
        x_start = math.floor((minimum[0] - x_shift) / spacing_m) * spacing_m + x_shift
        xs = np.arange(x_start, maximum[0] + spacing_m, spacing_m)
        if xs.size:
            rows.append(np.column_stack([xs, np.full_like(xs, y), np.full_like(xs, z_value)]))
        y += row_step
        row_index += 1
    candidates = np.vstack(rows)
    signed = candidates @ hull_equations[:, :3].T + hull_equations[:, 3]
    inside = np.max(signed, axis=1) <= -margin_m
    return candidates[inside]


def allocate_layer_counts(weights: np.ndarray, total: int, minimum_per_layer: int) -> np.ndarray:
    layer_count = len(weights)
    if total < minimum_per_layer * layer_count:
        raise ValueError("Target count is smaller than the requested per-layer minimum")
    base = np.full(layer_count, minimum_per_layer, dtype=int)
    remaining = total - int(base.sum())
    normalized = weights / float(weights.sum())
    exact = normalized * remaining
    extra = np.floor(exact).astype(int)
    result = base + extra
    for index in np.argsort(-(exact - extra))[: total - int(result.sum())]:
        result[index] += 1
    return result


def farthest_sample(candidates: np.ndarray, count: int, phase: int) -> np.ndarray:
    if len(candidates) < count:
        raise ValueError(f"Only {len(candidates)} candidates are available for {count} samples")
    xy = candidates[:, :2]
    center = xy.mean(axis=0)
    span = np.maximum(np.ptp(xy, axis=0), 1.0)
    phase_vectors = (
        np.asarray((0.00, 0.00)),
        np.asarray((0.13, -0.09)),
        np.asarray((-0.11, 0.14)),
        np.asarray((0.16, 0.12)),
        np.asarray((-0.15, -0.10)),
    )
    target = center + phase_vectors[phase % len(phase_vectors)] * span
    first = int(np.argmin(np.sum((xy - target) ** 2, axis=1)))
    selected = [first]
    available = np.ones(len(candidates), dtype=bool)
    available[first] = False
    minimum_distance = np.sum((xy - xy[first]) ** 2, axis=1)
    for _ in range(1, count):
        choice = int(np.argmax(np.where(available, minimum_distance, -1.0)))
        selected.append(choice)
        available[choice] = False
        distance = np.sum((xy - xy[choice]) ** 2, axis=1)
        minimum_distance = np.minimum(minimum_distance, distance)
    return candidates[np.asarray(selected, dtype=int)]


def serpentine_order(points: np.ndarray, horizontal_center: np.ndarray, horizontal_axes: np.ndarray) -> np.ndarray:
    local = (points[:, :2] - horizontal_center) @ horizontal_axes.T
    tree = cKDTree(local)
    distances, _ = tree.query(local, k=2)
    typical_spacing = max(float(np.median(distances[:, 1])), 1.0)
    row_step = typical_spacing * 0.9
    row_ids = np.floor((local[:, 1] - local[:, 1].min()) / row_step + 0.5).astype(int)
    ordered: list[int] = []
    for row_position, row_id in enumerate(sorted({int(value) for value in row_ids})):
        indices = np.where(row_ids == row_id)[0]
        direction = 1.0 if row_position % 2 == 0 else -1.0
        row_order = indices[np.argsort(direction * local[indices, 0])]
        ordered.extend(int(value) for value in row_order)
    return points[np.asarray(ordered, dtype=int)]


def nearest_neighbor_stats(points: np.ndarray) -> dict[str, float]:
    tree = cKDTree(points)
    distances, _ = tree.query(points, k=2)
    nearest = distances[:, 1]
    return {
        "minimum_m": float(nearest.min()),
        "median_m": float(np.median(nearest)),
        "p90_m": float(np.percentile(nearest, 90.0)),
        "maximum_m": float(nearest.max()),
    }


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = (
        "index",
        "label",
        "layer_id",
        "layer_height_m",
        "x",
        "y",
        "z",
        "x_m",
        "y_m",
        "z_m",
        "yaw_degrees",
        "pitch_degrees",
        "roll_degrees",
        "fov_degrees",
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in fields})


def write_png(
    path: Path,
    source_xyz_m: np.ndarray,
    hull: ConvexHull,
    layer_points: list[np.ndarray],
    split: int,
) -> None:
    figure = plt.figure(figsize=(14, 10), dpi=180, facecolor="#0f172a")
    axis = figure.add_subplot(111, projection="3d", facecolor="#0f172a")
    faces = [source_xyz_m[simplex] for simplex in hull.simplices]
    mesh = Poly3DCollection(
        faces,
        facecolor="#38bdf8",
        edgecolor="#7dd3fc",
        linewidth=0.35,
        alpha=0.11,
    )
    axis.add_collection3d(mesh)
    axis.plot(
        source_xyz_m[:split, 0],
        source_xyz_m[:split, 1],
        source_xyz_m[:split, 2],
        color="#94a3b8",
        linewidth=1.1,
        alpha=0.72,
    )
    axis.plot(
        source_xyz_m[split:, 0],
        source_xyz_m[split:, 1],
        source_xyz_m[split:, 2],
        color="#94a3b8",
        linewidth=1.1,
        alpha=0.72,
    )
    colors = plt.cm.viridis(np.linspace(0.08, 0.92, len(layer_points)))
    for layer_index, (points, color) in enumerate(zip(layer_points, colors), 1):
        axis.scatter(
            points[:, 0],
            points[:, 1],
            points[:, 2],
            color=color,
            s=8,
            depthshade=False,
            label=f"Layer {layer_index} · {len(points)} points · Z={points[0, 2]:.1f} m",
        )
    all_points = np.vstack([source_xyz_m, *layer_points])
    minimum = all_points.min(axis=0)
    maximum = all_points.max(axis=0)
    spans = np.maximum(maximum - minimum, 1.0)
    midpoint = (minimum + maximum) / 2.0
    axis.set_xlim(midpoint[0] - spans[0] * 0.54, midpoint[0] + spans[0] * 0.54)
    axis.set_ylim(midpoint[1] - spans[1] * 0.54, midpoint[1] + spans[1] * 0.54)
    axis.set_zlim(midpoint[2] - spans[2] * 0.56, midpoint[2] + spans[2] * 0.56)
    axis.set_box_aspect(spans)
    axis.set_xlabel("X (m)", color="#e2e8f0")
    axis.set_ylabel("Y (m)", color="#e2e8f0")
    axis.set_zlabel("Z / height (m)", color="#e2e8f0")
    axis.set_title("Black Myth: five-layer dense oblique capture positions", color="#f8fafc", pad=18)
    axis.tick_params(colors="#cbd5e1")
    for pane_axis in (axis.xaxis, axis.yaxis, axis.zaxis):
        pane_axis.pane.set_facecolor((0.08, 0.11, 0.17, 0.9))
        pane_axis.pane.set_edgecolor((0.45, 0.52, 0.62, 0.35))
    axis.view_init(elev=24, azim=-61)
    legend = axis.legend(loc="upper left", fontsize=8, framealpha=0.18)
    for text in legend.get_texts():
        text.set_color("#e2e8f0")
    figure.text(
        0.01,
        0.01,
        f"{sum(len(points) for points in layer_points)} spatial positions × 22 views = {sum(len(points) for points in layer_points) * 22:,} images · first 193 valid records · 1.5 m hull clearance",
        color="#cbd5e1",
        fontsize=8,
    )
    figure.tight_layout()
    figure.savefig(path, bbox_inches="tight", facecolor=figure.get_facecolor())
    plt.close(figure)


def build(
    source: Path,
    output_dir: Path,
    template: Path,
    visualization: Path,
    scene_id: str,
    source_count: int,
    target_count: int,
    margin_m: float,
    candidate_spacing_m: float,
    write_csv_output: bool,
) -> dict[str, Any]:
    source_payload, source_records, ignored_indices = load_records(source, source_count)
    source_xyz_native = np.asarray(
        [[float(record[axis]) for axis in ("x", "y", "z")] for record in source_records],
        dtype=float,
    )
    source_xyz_m = source_xyz_native * 0.01
    hull = ConvexHull(source_xyz_m)
    z_min = float(source_xyz_m[:, 2].min())
    z_max = float(source_xyz_m[:, 2].max())
    z_values = np.asarray([z_min + fraction * (z_max - z_min) for fraction in LAYER_FRACTIONS])
    bounds_xy = (source_xyz_m[:, :2].min(axis=0), source_xyz_m[:, :2].max(axis=0))
    candidates = [
        hex_candidates(
            bounds_xy,
            float(z_value),
            hull.equations,
            margin_m,
            candidate_spacing_m,
            layer_index,
        )
        for layer_index, z_value in enumerate(z_values)
    ]
    if any(len(values) == 0 for values in candidates):
        raise ValueError("At least one height layer has no candidates after applying the hull margin")
    counts = allocate_layer_counts(
        np.asarray([len(values) for values in candidates], dtype=float),
        target_count,
        minimum_per_layer=30,
    )
    horizontal_center = source_xyz_m[:, :2].mean(axis=0)
    _, _, horizontal_axes = np.linalg.svd(source_xyz_m[:, :2] - horizontal_center, full_matrices=False)
    layer_points: list[np.ndarray] = []
    for layer_index, (candidate_set, count) in enumerate(zip(candidates, counts)):
        selected = farthest_sample(candidate_set, int(count), layer_index)
        layer_points.append(serpentine_order(selected, horizontal_center, horizontal_axes))

    center_xy = source_xyz_m[:, :2].mean(axis=0)
    average_fov = float(np.mean([float(record.get("fov_degrees", 65.0)) for record in source_records]))
    records: list[dict[str, Any]] = []
    layers: list[dict[str, Any]] = []
    next_index = 1
    for layer_index, (points_m, pitch) in enumerate(zip(layer_points, LAYER_PITCHES), 1):
        layer_id = f"layer_{layer_index:02d}"
        layers.append(
            {
                "id": layer_id,
                "ordinal": layer_index,
                "height_m": float(points_m[0, 2]),
                "point_count": len(points_m),
                "seed_pitch_degrees": float(pitch),
            }
        )
        for layer_point_index, point_m in enumerate(points_m, 1):
            dx = float(center_xy[0] - point_m[0])
            dy = float(center_xy[1] - point_m[1])
            yaw = math.degrees(math.atan2(dx, dy)) % 360.0
            point_native = point_m / 0.01
            records.append(
                {
                    "index": next_index,
                    "label": f"oblique_l{layer_index:02d}_p{layer_point_index:04d}",
                    "time_sec": 0.0,
                    "x": float(point_native[0]),
                    "y": float(point_native[1]),
                    "z": float(point_native[2]),
                    "x_m": float(point_m[0]),
                    "y_m": float(point_m[1]),
                    "z_m": float(point_m[2]),
                    "yaw_degrees": float(yaw),
                    "pitch_degrees": float(pitch),
                    "roll_degrees": 0.0,
                    "fov_degrees": average_fov,
                    "camera_enabled": True,
                    "movement_locked": False,
                    "layer_id": layer_id,
                    "layer_index": layer_index,
                    "layer_point_index": layer_point_index,
                    "capture_design": "five_layer_staggered_oblique_photogrammetry",
                }
            )
            next_index += 1

    planned_xyz_m = np.asarray([[record[axis] for axis in ("x_m", "y_m", "z_m")] for record in records])
    signed = planned_xyz_m @ hull.equations[:, :3].T + hull.equations[:, 3]
    clearances = -np.max(signed, axis=1)
    layer_stats = {
        layer["id"]: nearest_neighbor_stats(points)
        for layer, points in zip(layers, layer_points)
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = scene_id
    json_path = output_dir / f"{stem}.json"
    csv_path = output_dir / f"{stem}.csv"
    png_path = output_dir / f"{stem}.png"
    summary_path = output_dir / "plan_summary.json"
    coordinate_system = dict(source_payload.get("coordinate_system") or {})
    coordinate_system.update(
        position_unit="centimeters",
        meters_per_unit=0.01,
        native_position_preserved=True,
    )
    plan = {
        "format": "bmw-uuu-camera-v1",
        "kind": "points",
        "game_id": "black-myth-wukong",
        "scene_id": scene_id,
        "coordinate_system": coordinate_system,
        "count": len(records),
        "views_per_spatial_point": 22,
        "static_image_count": len(records) * 22,
        "source_file": source.name,
        "source_provenance": "local captured point map; only the first validated records are used",
        "source_point_count": source_count,
        "ignored_source_indices": ignored_indices,
        "capture_design": {
            "layout": "five_horizontal_maximin_layers_inside_3d_convex_hull",
            "purpose": "dense multi-layer static reconstruction with translational parallax and oblique coverage",
            "layer_fractions_of_recorded_z_range": list(LAYER_FRACTIONS),
            "layer_seed_pitch_degrees": list(LAYER_PITCHES),
            "hull_clearance_m": margin_m,
            "candidate_spacing_m": candidate_spacing_m,
            "ordering": "serpentine_in_horizontal_pca_frame",
            "point_pose_note": "Seed yaw/pitch faces inward and downward; the current static-capture UI expands every spatial point into the shared 22-view pattern below.",
            "shared_22_view_pattern": list(VIEW_PATTERN),
        },
        "layers": layers,
        "validation": {
            "all_points_inside_convex_hull": bool(np.all(np.max(signed, axis=1) <= 1e-7)),
            "all_points_meet_requested_clearance": bool(np.all(clearances >= margin_m - 1e-6)),
            "minimum_hull_clearance_m": float(clearances.min()),
            "source_bounds_m": {
                "minimum": [float(value) for value in source_xyz_m.min(axis=0)],
                "maximum": [float(value) for value in source_xyz_m.max(axis=0)],
            },
            "planned_bounds_m": {
                "minimum": [float(value) for value in planned_xyz_m.min(axis=0)],
                "maximum": [float(value) for value in planned_xyz_m.max(axis=0)],
            },
            "nearest_neighbor_by_layer": layer_stats,
        },
        "points": records,
    }
    json_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    if write_csv_output:
        write_csv(csv_path, records)
    write_png(png_path, source_xyz_m, hull, layer_points, split=125)

    ray_anchors: list[list[float]] = []
    for layer_index, points in enumerate(layer_points):
        for point in points[np.linspace(0, len(points) - 1, 3, dtype=int)]:
            dx = float(center_xy[0] - point[0])
            dy = float(center_xy[1] - point[1])
            yaw = math.degrees(math.atan2(dx, dy)) % 360.0
            ray_anchors.append(
                [float(point[0]), float(point[1]), float(point[2]), layer_index + 1, 0, yaw, LAYER_PITCHES[layer_index]]
            )
    visualization_payload = {
        "recorded": [
            [float(point[0]), float(point[1]), float(point[2]), index + 1]
            for index, point in enumerate(source_xyz_m)
        ],
        "split": 125,
        "hullFaces": [[int(value) for value in simplex] for simplex in hull.simplices],
        "planned": [
            [
                float(record["x_m"]),
                float(record["y_m"]),
                float(record["z_m"]),
                int(record["layer_index"]),
                int(record["index"]),
                float(record["yaw_degrees"]),
                float(record["pitch_degrees"]),
            ]
            for record in records
        ],
        "layers": [
            {
                "id": int(layer["ordinal"]),
                "label": f"L{int(layer['ordinal'])} · Z {float(layer['height_m']):.1f} m",
            }
            for layer in layers
        ],
        "rayAnchors": ray_anchors,
        "rayLengthM": 12.0,
    }
    template_text = template.read_text(encoding="utf-8")
    if template_text.count("__PAYLOAD__") != 1:
        raise ValueError("Visualization template must contain exactly one __PAYLOAD__ token")
    visualization.parent.mkdir(parents=True, exist_ok=True)
    visualization.write_text(
        template_text.replace("__PAYLOAD__", json.dumps(visualization_payload, ensure_ascii=False, separators=(",", ":"))),
        encoding="utf-8",
    )

    summary = {
        "scene_id": scene_id,
        "source_file": source.name,
        "source_point_count_used": source_count,
        "ignored_source_indices": ignored_indices,
        "spatial_point_count": len(records),
        "views_per_spatial_point": 22,
        "planned_image_count": len(records) * 22,
        "layer_point_counts": [int(value) for value in counts],
        "layer_heights_m": [float(value) for value in z_values],
        "hull_vertices": len(hull.vertices),
        "hull_triangles": len(hull.simplices),
        "hull_volume_m3": float(hull.volume),
        "minimum_hull_clearance_m": float(clearances.min()),
        "all_points_inside": bool(np.all(np.max(signed, axis=1) <= 1e-7)),
        "outputs": {
            "point_map_json": json_path.name,
            "preview_png": png_path.name,
            "visualization_html": visualization.name,
        },
    }
    if write_csv_output:
        summary["outputs"]["point_map_csv"] = csv_path.name
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a dense oblique five-layer Black Myth point map")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--visualization", type=Path, required=True)
    parser.add_argument("--scene-id", default="scen_1_heifengdong_dongwai")
    parser.add_argument("--source-count", type=int, default=193)
    parser.add_argument("--target-count", type=int, default=980)
    parser.add_argument("--margin-m", type=float, default=1.5)
    parser.add_argument("--candidate-spacing-m", type=float, default=1.5)
    parser.add_argument("--write-csv", action="store_true")
    args = parser.parse_args()
    result = build(
        args.source,
        args.output_dir,
        args.template,
        args.visualization,
        args.scene_id,
        args.source_count,
        args.target_count,
        args.margin_m,
        args.candidate_spacing_m,
        args.write_csv,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
