from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORE_SRC = PROJECT_ROOT / "core" / "src"
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

from re9_pose_recorder.still_scan import (  # noqa: E402
    StillLayer,
    _point_in_convex_hull,
    _point_in_ellipsoid,
    linspace,
    load_still_layers,
)


DEFAULT_SPEC = PROJECT_ROOT / "data" / "reconstruction_capture_plans" / "reconstruction_capture_specs.yaml"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "reconstruction_capture_plans"


@dataclass(frozen=True)
class Position:
    position_index: int
    layer_position_index: int
    scene_id: str
    layer_id: str
    height_index: int
    grid_x_index: int
    grid_z_index: int
    x: float
    y: float
    z: float
    route_heading_deg: float
    camera_route_yaw_deg: float
    pitch_deg: float
    legacy_anchor: bool
    route_bridge: bool
    distance_from_previous: float
    distance_to_next: float


@dataclass(frozen=True)
class CaptureSample:
    sample_index: int
    position_index: int
    scene_id: str
    layer_id: str
    height_index: int
    pattern: str
    x: float
    y: float
    z: float
    yaw_deg: float
    yaw_rad: float
    pitch_deg: float
    pitch_rad: float
    yaw_offset_deg: float
    legacy_anchor: bool
    route_bridge: bool


@dataclass(frozen=True)
class GridPoint:
    layer_id: str
    height_index: int
    grid_x_index: int
    grid_z_index: int
    x: float
    y: float
    z: float
    legacy_anchor: bool
    route_bridge: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate RE9 3DGS reconstruction capture plans and maps.")
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def load_specs(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = raw.get("defaults") or {}
    scenes = raw.get("scenes") or []
    if not isinstance(scenes, list) or not scenes:
        raise ValueError(f"No scenes found in {path}")
    return defaults, scenes


def refinement_factor(base_count: int, new_count: int, axis: str, scene_id: str) -> int:
    if base_count < 2 or new_count < 2:
        if base_count == new_count == 1:
            return 1
        raise ValueError(f"{scene_id}: invalid {axis} grid counts {base_count} -> {new_count}")
    intervals = new_count - 1
    base_intervals = base_count - 1
    if intervals % base_intervals:
        raise ValueError(
            f"{scene_id}: {axis} grid {new_count} does not preserve the {base_count}-point base grid"
        )
    return intervals // base_intervals


def generate_grid_points(
    scene_id: str,
    layers: list[StillLayer],
    points_x: int,
    points_z: int,
    factor_x: int,
    factor_z: int,
) -> list[list[GridPoint]]:
    result: list[list[GridPoint]] = []
    for layer in layers:
        xs = linspace(layer.x_min, layer.x_max, points_x)
        zs = linspace(layer.z_min, layer.z_max, points_z)
        points: list[GridPoint] = []
        for z_index, z in enumerate(zs):
            for x_index, x in enumerate(xs):
                xyz = (x, layer.y, z)
                if layer.hull_points and not _point_in_convex_hull(xyz, layer.hull_points):
                    continue
                if any(_point_in_ellipsoid(xyz, ellipsoid) for ellipsoid in layer.exclude_ellipsoids):
                    continue
                points.append(
                    GridPoint(
                        layer_id=layer.layer_id,
                        height_index=layer.height_index,
                        grid_x_index=x_index,
                        grid_z_index=z_index,
                        x=x,
                        y=layer.y,
                        z=z,
                        legacy_anchor=x_index % factor_x == 0 and z_index % factor_z == 0,
                        route_bridge=False,
                    )
                )
        if not points:
            raise ValueError(f"{scene_id}: layer {layer.layer_id} contains no valid grid points")
        result.append(points)
    return result


def serpentine_candidates(points: list[GridPoint]) -> list[list[GridPoint]]:
    candidates: list[list[GridPoint]] = []
    for primary_axis in ("z", "x"):
        groups: dict[int, list[GridPoint]] = defaultdict(list)
        for point in points:
            group_index = point.grid_z_index if primary_axis == "z" else point.grid_x_index
            groups[group_index].append(point)
        indexes = sorted(groups)
        for reverse_primary in (False, True):
            group_order = list(reversed(indexes)) if reverse_primary else indexes
            for first_reverse_secondary in (False, True):
                route: list[GridPoint] = []
                for group_number, group_index in enumerate(group_order):
                    reverse_secondary = (
                        first_reverse_secondary if group_number % 2 == 0 else not first_reverse_secondary
                    )
                    secondary_key = (
                        (lambda point: point.x) if primary_axis == "z" else (lambda point: point.z)
                    )
                    route.extend(
                        sorted(groups[group_index], key=secondary_key, reverse=reverse_secondary)
                    )
                candidates.append(route)
    return candidates


def distance(left: GridPoint | Position, right: GridPoint | Position) -> float:
    return math.sqrt((right.x - left.x) ** 2 + (right.y - left.y) ** 2 + (right.z - left.z) ** 2)


def order_layers(layer_points: list[list[GridPoint]]) -> list[list[GridPoint]]:
    candidate_layers = [serpentine_candidates(points) for points in layer_points]
    intrinsic: list[list[tuple[float, float]]] = []
    for candidates in candidate_layers:
        layer_metrics = []
        for candidate in candidates:
            steps = [distance(candidate[index - 1], candidate[index]) for index in range(1, len(candidate))]
            layer_metrics.append((max(steps, default=0.0), sum(steps)))
        intrinsic.append(layer_metrics)

    scores: list[list[tuple[float, float]]] = [intrinsic[0]]
    predecessors: list[list[int | None]] = [[None] * len(candidate_layers[0])]
    for layer_index in range(1, len(candidate_layers)):
        layer_scores: list[tuple[float, float]] = []
        layer_predecessors: list[int | None] = []
        for candidate_index, candidate in enumerate(candidate_layers[layer_index]):
            intralayer_max, intralayer_total = intrinsic[layer_index][candidate_index]
            options = []
            for previous_index, previous_candidate in enumerate(candidate_layers[layer_index - 1]):
                previous_max, previous_total = scores[layer_index - 1][previous_index]
                entry_step = distance(previous_candidate[-1], candidate[0])
                score = (
                    max(previous_max, intralayer_max, entry_step),
                    previous_total + intralayer_total + entry_step,
                )
                options.append((score, previous_index))
            best_score, best_previous = min(options, key=lambda option: option[0])
            layer_scores.append(best_score)
            layer_predecessors.append(best_previous)
        scores.append(layer_scores)
        predecessors.append(layer_predecessors)

    final_index = min(range(len(scores[-1])), key=lambda index: scores[-1][index])
    chosen_indexes = [final_index]
    for layer_index in range(len(candidate_layers) - 1, 0, -1):
        previous_index = predecessors[layer_index][chosen_indexes[-1]]
        assert previous_index is not None
        chosen_indexes.append(previous_index)
    chosen_indexes.reverse()
    return [candidates[index] for candidates, index in zip(candidate_layers, chosen_indexes)]


def densify_intralayer_routes(
    ordered_layers: list[list[GridPoint]],
    layers: list[StillLayer],
    max_step: float,
) -> list[list[GridPoint]]:
    if max_step <= 0:
        raise ValueError("max_intralayer_route_step must be greater than zero")
    dense_layers: list[list[GridPoint]] = []
    for route, layer in zip(ordered_layers, layers):
        dense_route = [route[0]]
        for target in route[1:]:
            source = dense_route[-1]
            segment_distance = distance(source, target)
            segment_count = max(1, math.ceil(segment_distance / max_step))
            for segment_index in range(1, segment_count):
                alpha = segment_index / float(segment_count)
                xyz = (
                    source.x + (target.x - source.x) * alpha,
                    source.y + (target.y - source.y) * alpha,
                    source.z + (target.z - source.z) * alpha,
                )
                if layer.hull_points and not _point_in_convex_hull(xyz, layer.hull_points):
                    continue
                if any(_point_in_ellipsoid(xyz, ellipsoid) for ellipsoid in layer.exclude_ellipsoids):
                    continue
                dense_route.append(
                    GridPoint(
                        layer_id=source.layer_id,
                        height_index=source.height_index,
                        grid_x_index=-1,
                        grid_z_index=-1,
                        x=xyz[0],
                        y=xyz[1],
                        z=xyz[2],
                        legacy_anchor=False,
                        route_bridge=True,
                    )
                )
            dense_route.append(target)
        dense_layers.append(dense_route)
    return dense_layers


def heading_for(route: list[GridPoint], index: int) -> float:
    if len(route) == 1:
        return 0.0
    if index < len(route) - 1:
        dx = route[index + 1].x - route[index].x
        dz = route[index + 1].z - route[index].z
    else:
        dx = route[index].x - route[index - 1].x
        dz = route[index].z - route[index - 1].z
    return math.degrees(math.atan2(dx, dz)) % 360.0


def pitch_for_layer(layer_number: int, layer_count: int, bottom: float, top: float) -> float:
    if layer_count <= 1:
        return (bottom + top) / 2.0
    alpha = layer_number / float(layer_count - 1)
    return bottom + (top - bottom) * alpha


def make_positions(
    scene_id: str,
    ordered_layers: list[list[GridPoint]],
    pitch_bottom: float,
    pitch_top: float,
) -> list[Position]:
    positions: list[Position] = []
    global_index = 1
    layer_count = len(ordered_layers)
    previous_global: GridPoint | None = None
    for layer_number, route in enumerate(ordered_layers):
        pitch = pitch_for_layer(layer_number, layer_count, pitch_bottom, pitch_top)
        for route_index, point in enumerate(route):
            heading = heading_for(route, route_index)
            # RE9FreeCam's camera forward is -Z at yaw 0, so looking along a
            # world-space route heading requires a 180-degree yaw offset.
            camera_yaw = (heading + 180.0) % 360.0
            previous_distance = distance(previous_global, point) if previous_global is not None else 0.0
            next_distance = distance(point, route[route_index + 1]) if route_index < len(route) - 1 else 0.0
            positions.append(
                Position(
                    position_index=global_index,
                    layer_position_index=route_index + 1,
                    scene_id=scene_id,
                    layer_id=point.layer_id,
                    height_index=point.height_index,
                    grid_x_index=point.grid_x_index,
                    grid_z_index=point.grid_z_index,
                    x=point.x,
                    y=point.y,
                    z=point.z,
                    route_heading_deg=heading,
                    camera_route_yaw_deg=camera_yaw,
                    pitch_deg=pitch,
                    legacy_anchor=point.legacy_anchor,
                    route_bridge=point.route_bridge,
                    distance_from_previous=previous_distance,
                    distance_to_next=next_distance,
                )
            )
            previous_global = point
            global_index += 1
    return positions


def make_samples(scene_id: str, positions: list[Position], yaw_offsets: list[float]) -> list[CaptureSample]:
    labels = {
        min(yaw_offsets): "route_left",
        0.0: "route_forward",
        max(yaw_offsets): "route_right",
    }
    samples: list[CaptureSample] = []
    for position in positions:
        for offset in yaw_offsets:
            yaw = (position.camera_route_yaw_deg + offset) % 360.0
            samples.append(
                CaptureSample(
                    sample_index=len(samples) + 1,
                    position_index=position.position_index,
                    scene_id=scene_id,
                    layer_id=position.layer_id,
                    height_index=position.height_index,
                    pattern=labels.get(offset, f"route_offset_{offset:+g}"),
                    x=position.x,
                    y=position.y,
                    z=position.z,
                    yaw_deg=yaw,
                    yaw_rad=math.radians(yaw),
                    pitch_deg=position.pitch_deg,
                    pitch_rad=math.radians(position.pitch_deg),
                    yaw_offset_deg=offset,
                    legacy_anchor=position.legacy_anchor,
                    route_bridge=position.route_bridge,
                )
            )
    return samples


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def round_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: round(value, 9) if isinstance(value, float) else value for key, value in record.items()}


def write_csv(path: Path, records: Iterable[dict[str, Any]]) -> None:
    rows = [round_record(record) for record in records]
    if not rows:
        raise ValueError(f"Refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def convex_hull_2d(points: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    unique = sorted(set(points))
    if len(unique) <= 1:
        return unique

    def cross(origin: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def plot_3d(scene_id: str, positions: list[Position], layers: list[StillLayer], path: Path) -> None:
    figure = plt.figure(figsize=(13, 9), dpi=180)
    axis = figure.add_subplot(111, projection="3d")
    colors = plt.get_cmap("tab10")
    by_layer: dict[str, list[Position]] = defaultdict(list)
    for position in positions:
        by_layer[position.layer_id].append(position)

    for index, layer in enumerate(layers):
        route = by_layer[layer.layer_id]
        color = colors(index % 10)
        axis.plot(
            [point.x for point in route],
            [point.z for point in route],
            [point.y for point in route],
            color=color,
            linewidth=0.55,
            alpha=0.38,
        )
        axis.scatter(
            [point.x for point in route],
            [point.z for point in route],
            [point.y for point in route],
            color=color,
            s=5,
            alpha=0.82,
            label=f"{layer.layer_id} (Y={layer.y:.2f}, n={len(route)})",
        )
        anchors = [point for point in route if point.legacy_anchor]
        axis.scatter(
            [point.x for point in anchors],
            [point.z for point in anchors],
            [point.y for point in anchors],
            facecolors="none",
            edgecolors="#111827",
            linewidths=0.35,
            s=11,
            alpha=0.6,
        )

    x_values = [position.x for position in positions]
    y_values = [position.y for position in positions]
    z_values = [position.z for position in positions]
    raw_aspect = (
        max(x_values) - min(x_values),
        max(z_values) - min(z_values),
        max((max(y_values) - min(y_values)) * 2.0, 1.0),
    )
    aspect_floor = max(raw_aspect) / 8.0
    axis.set_box_aspect(
        tuple(max(value, aspect_floor) for value in raw_aspect)
    )
    axis.set_xlabel("X (game units)")
    axis.set_ylabel("Z (game units)")
    axis.set_zlabel("Y / height (game units)")
    axis.set_title(
        f"{scene_id} reconstruction capture positions\n"
        f"{len(positions):,} positions, {len(positions) * 3:,} three-view samples",
        pad=16,
    )
    axis.view_init(elev=24, azim=-58)
    axis.set_position((0.04, 0.03, 0.92, 0.86))
    axis.legend(loc="upper left", bbox_to_anchor=(0.01, 0.99), fontsize=7, framealpha=0.92)
    figure.tight_layout()
    figure.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def plot_topdown(scene_id: str, positions: list[Position], layers: list[StillLayer], path: Path) -> None:
    by_layer: dict[str, list[Position]] = defaultdict(list)
    for position in positions:
        by_layer[position.layer_id].append(position)

    column_count = 2
    row_count = math.ceil(len(layers) / column_count)
    figure, axes = plt.subplots(row_count, column_count, figsize=(14, 4.7 * row_count), dpi=180, squeeze=False)
    colors = plt.get_cmap("tab10")
    for index, layer in enumerate(layers):
        axis = axes[index // column_count][index % column_count]
        route = by_layer[layer.layer_id]
        color = colors(index % 10)
        if layer.hull_points:
            footprint = convex_hull_2d((point[0], point[2]) for point in layer.hull_points)
            if footprint:
                polygon = footprint + [footprint[0]]
                axis.fill(
                    [point[0] for point in polygon],
                    [point[1] for point in polygon],
                    color="#d1d5db",
                    alpha=0.28,
                    label="capture hull",
                )
        for ellipsoid in layer.exclude_ellipsoids:
            center_x, _, center_z, radius_x, _, radius_z = ellipsoid
            axis.add_patch(
                Ellipse(
                    (center_x, center_z),
                    width=radius_x * 2.0,
                    height=radius_z * 2.0,
                    facecolor="#ef4444",
                    edgecolor="#991b1b",
                    linewidth=1.0,
                    alpha=0.22,
                    label="excluded volume",
                )
            )
        axis.plot([point.x for point in route], [point.z for point in route], color=color, linewidth=0.7, alpha=0.65)
        axis.scatter([point.x for point in route], [point.z for point in route], color=color, s=5, alpha=0.78)
        anchors = [point for point in route if point.legacy_anchor]
        axis.scatter(
            [point.x for point in anchors],
            [point.z for point in anchors],
            facecolors="none",
            edgecolors="#111827",
            linewidths=0.4,
            s=13,
            alpha=0.68,
            label="existing 22-view anchor",
        )
        axis.scatter(route[0].x, route[0].z, marker="^", s=42, color="#16a34a", zorder=5, label="layer start")
        axis.scatter(route[-1].x, route[-1].z, marker="s", s=30, color="#dc2626", zorder=5, label="layer end")
        axis.set_title(f"{layer.layer_id} | Y={layer.y:.2f} | {len(route):,} positions")
        axis.set_xlabel("X (game units)")
        axis.set_ylabel("Z (game units)")
        axis.set_aspect("equal", adjustable="box")
        axis.grid(True, linewidth=0.4, alpha=0.35)
        handles, labels = axis.get_legend_handles_labels()
        unique = dict(zip(labels, handles))
        axis.legend(unique.values(), unique.keys(), loc="best", fontsize=6, framealpha=0.9)

    for index in range(len(layers), row_count * column_count):
        axes[index // column_count][index % column_count].axis("off")
    figure.suptitle(f"{scene_id} XZ serpentine reconstruction routes", fontsize=15, y=0.995)
    figure.tight_layout(rect=(0, 0, 1, 0.98))
    figure.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def scene_metrics(positions: list[Position], samples: list[CaptureSample], layers: list[StillLayer]) -> dict[str, Any]:
    coordinates = {(round(point.x, 9), round(point.y, 9), round(point.z, 9)) for point in positions}
    if len(coordinates) != len(positions):
        raise ValueError("Generated route contains duplicate XYZ positions")

    within_layer_steps = [point.distance_from_previous for point in positions if point.layer_position_index > 1]
    all_steps = [point.distance_from_previous for point in positions if point.position_index > 1]
    return {
        "layer_count": len(layers),
        "position_count": len(positions),
        "legacy_anchor_count": sum(point.legacy_anchor for point in positions),
        "route_bridge_count": sum(point.route_bridge for point in positions),
        "new_position_count": sum(not point.legacy_anchor for point in positions),
        "views_per_position": len(samples) // len(positions),
        "sample_count": len(samples),
        "route_distance": round(sum(all_steps), 6),
        "max_intralayer_step": round(max(within_layer_steps, default=0.0), 6),
        "max_route_step_including_layer_changes": round(max(all_steps, default=0.0), 6),
        "x_min": round(min(point.x for point in positions), 6),
        "x_max": round(max(point.x for point in positions), 6),
        "y_min": round(min(point.y for point in positions), 6),
        "y_max": round(max(point.y for point in positions), 6),
        "z_min": round(min(point.z for point in positions), 6),
        "z_max": round(max(point.z for point in positions), 6),
    }


def build_scene(
    spec: dict[str, Any],
    defaults: dict[str, Any],
    output_root: Path,
) -> dict[str, Any]:
    scene_id = str(spec["scene_id"])
    source_config = PROJECT_ROOT / str(spec["source_config"])
    source_group = str(spec.get("source_group") or scene_id)
    points_x = int(spec["grid_points_x"])
    points_z = int(spec["grid_points_z"])
    base_x = int(spec["base_grid_points_x"])
    base_z = int(spec["base_grid_points_z"])
    factor_x = refinement_factor(base_x, points_x, "X", scene_id)
    factor_z = refinement_factor(base_z, points_z, "Z", scene_id)
    yaw_offsets = [float(value) for value in spec.get("yaw_offsets_deg", defaults["yaw_offsets_deg"])]
    if len(yaw_offsets) != 3 or 0.0 not in yaw_offsets:
        raise ValueError(f"{scene_id}: exactly three yaw offsets including 0 are required")
    pitch_bottom = float(spec.get("pitch_bottom_deg", defaults["pitch_bottom_deg"]))
    pitch_top = float(spec.get("pitch_top_deg", defaults["pitch_top_deg"]))
    max_intralayer_step = float(
        spec.get("max_intralayer_route_step", defaults["max_intralayer_route_step"])
    )

    layers = [layer for layer in load_still_layers(source_config) if layer.group_id == source_group]
    if not layers:
        raise ValueError(f"{scene_id}: no layers found for group {source_group} in {source_config}")
    grid_layers = generate_grid_points(scene_id, layers, points_x, points_z, factor_x, factor_z)
    ordered_layers = order_layers(grid_layers)
    ordered_layers = densify_intralayer_routes(ordered_layers, layers, max_intralayer_step)
    positions = make_positions(scene_id, ordered_layers, pitch_bottom, pitch_top)
    samples = make_samples(scene_id, positions, yaw_offsets)
    metrics = scene_metrics(positions, samples, layers)

    expected_base = int(spec["expected_base_positions"])
    if metrics["legacy_anchor_count"] != expected_base:
        raise ValueError(
            f"{scene_id}: expected {expected_base} preserved anchors, got {metrics['legacy_anchor_count']}"
        )

    scene_dir = output_root / scene_id
    scene_dir.mkdir(parents=True, exist_ok=True)
    positions_name = f"{scene_id}_reconstruction_positions.csv"
    samples_name = f"{scene_id}_reconstruction_samples.csv"
    manifest_name = f"{scene_id}_reconstruction_manifest.json"
    map_3d_name = f"{scene_id}_reconstruction_3d.png"
    topdown_name = f"{scene_id}_reconstruction_topdown.png"

    position_records = [round_record(asdict(position)) for position in positions]
    sample_records = [round_record(asdict(sample)) for sample in samples]
    write_csv(scene_dir / positions_name, position_records)
    write_csv(scene_dir / samples_name, sample_records)
    plot_3d(scene_id, positions, layers, scene_dir / map_3d_name)
    plot_topdown(scene_id, positions, layers, scene_dir / topdown_name)

    layer_summary = []
    for layer in layers:
        layer_positions = [point for point in positions if point.layer_id == layer.layer_id]
        layer_summary.append(
            {
                "layer_id": layer.layer_id,
                "y": round(layer.y, 9),
                "position_count": len(layer_positions),
                "legacy_anchor_count": sum(point.legacy_anchor for point in layer_positions),
                "pitch_deg": round(layer_positions[0].pitch_deg, 6),
            }
        )

    manifest = {
        "schema_version": 1,
        "kind": "re9_3dgs_reconstruction_capture_plan",
        "scene_id": scene_id,
        "purpose": "Dense translated multi-view screenshots for 3D Gaussian Splatting reconstruction",
        "source": {
            "config": source_config.relative_to(PROJECT_ROOT).as_posix(),
            "sha256": sha256(source_config),
            "group_id": source_group,
        },
        "grid": {
            "points_x": points_x,
            "points_z": points_z,
            "base_points_x": base_x,
            "base_points_z": base_z,
            "refinement_factor_x": factor_x,
            "refinement_factor_z": factor_z,
            "hull_filtered": any(layer.hull_points for layer in layers),
            "excluded_ellipsoids": len(layers[0].exclude_ellipsoids),
        },
        "capture": {
            "order": "layer-major XZ serpentine route",
            "yaw_offsets_deg": yaw_offsets,
            "patterns": ["route_left", "route_forward", "route_right"],
            "pitch_bottom_deg": pitch_bottom,
            "pitch_top_deg": pitch_top,
            "max_intralayer_route_step": max_intralayer_step,
            "coordinate_units": "RE9 game units; no metric conversion is assumed",
            "camera_yaw_convention": "RE9FreeCam yaw 0 camera-forward is world -Z",
        },
        "metrics": metrics,
        "layers": layer_summary,
        "files": {
            "positions_csv": positions_name,
            "samples_csv": samples_name,
            "map_3d_png": map_3d_name,
            "topdown_png": topdown_name,
        },
        "positions": position_records,
        "samples": sample_records,
    }
    (scene_dir / manifest_name).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "scene_id": scene_id,
        **metrics,
        "source_config": source_config.relative_to(PROJECT_ROOT).as_posix(),
        "grid_points_x": points_x,
        "grid_points_z": points_z,
        "refinement_factor_x": factor_x,
        "refinement_factor_z": factor_z,
        "manifest": f"{scene_id}/{manifest_name}",
        "map_3d": f"{scene_id}/{map_3d_name}",
        "topdown_map": f"{scene_id}/{topdown_name}",
    }


def write_summary(output_root: Path, summaries: list[dict[str, Any]]) -> None:
    summary_csv = output_root / "reconstruction_capture_summary.csv"
    write_csv(summary_csv, summaries)
    summary_json = {
        "schema_version": 1,
        "scene_count": len(summaries),
        "total_positions": sum(item["position_count"] for item in summaries),
        "total_samples": sum(item["sample_count"] for item in summaries),
        "scenes": summaries,
    }
    (output_root / "reconstruction_capture_summary.json").write_text(
        json.dumps(summary_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    rows = []
    for item in summaries:
        rows.append(
            "| {scene_id} | {layer_count} | {position_count:,} | {legacy_anchor_count:,} | "
            "{new_position_count:,} | {route_bridge_count:,} | {sample_count:,} | "
            "{max_intralayer_step:.3f} |".format(**item)
        )
    readme = f"""# RE9 3DGS Reconstruction Capture Plans

This directory contains dense, translated camera routes for rebuilding all six recorded RE9 spaces with 3D Gaussian Splatting. These plans are separate from the existing 22-view aesthetic-anchor captures: no old scene plan or image is overwritten.

## Capture totals

| Scene | Layers | Positions | Preserved 22-view anchors | New positions | Route bridge points | Reconstruction screenshots | Max within-layer step |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
{chr(10).join(rows)}
| **Total** |  | **{summary_json['total_positions']:,}** |  |  |  | **{summary_json['total_samples']:,}** |  |

## Capture convention

- Capture still screenshots at every ordered position; do not record these routes as video.
- Capture three route-relative views per position: left `-35 deg`, forward `0 deg`, and right `+35 deg`.
- Pitch changes linearly from `+10 deg` on the lowest layer to `-20 deg` on the highest layer, giving both upward and downward surface coverage.
- Positions use a layer-major XZ serpentine order to keep adjacent screenshots close and preserve parallax.
- Sparse convex-hull row transitions are interpolated with reconstruction-only bridge points so within-layer steps stay at or below `2.0` game units whenever the exclusion mask permits it.
- Black outlined points in the maps are positions already present in the old 22-view grids. They remain useful as aesthetic anchors.
- Scene 2 uses `scene_2_no_lamp_scan_layers.yaml`; the chandelier ellipsoid is excluded.
- Coordinates are RE9 game units. This package does not claim a meter or centimeter conversion.

## Files

Each scene directory contains:

- `*_reconstruction_positions.csv`: one row per spatial position in capture order.
- `*_reconstruction_samples.csv`: three camera poses per position.
- `*_reconstruction_manifest.json`: metadata plus complete position and sample records.
- `*_reconstruction_3d.png`: layered 3D point and route map.
- `*_reconstruction_topdown.png`: per-layer XZ route panels.

The parent directory also contains `reconstruction_capture_summary.csv`, `reconstruction_capture_summary.json`, and the source specification used to reproduce the package.

## Regenerate

From the repository root:

```powershell
.venv\\Scripts\\python.exe scripts\\generate_reconstruction_capture_plans.py
```
"""
    (output_root / "README.md").write_text(readme, encoding="utf-8")


def main() -> None:
    args = parse_args()
    spec_path = args.spec.resolve()
    output_root = args.output.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    defaults, specs = load_specs(spec_path)
    summaries = []
    for spec in specs:
        summary = build_scene(spec, defaults, output_root)
        summaries.append(summary)
        print(
            f"{summary['scene_id']}: {summary['position_count']:,} positions, "
            f"{summary['sample_count']:,} samples, {summary['legacy_anchor_count']:,} preserved anchors"
        )
    write_summary(output_root, summaries)
    print(
        f"Total: {sum(item['position_count'] for item in summaries):,} positions, "
        f"{sum(item['sample_count'] for item in summaries):,} samples"
    )


if __name__ == "__main__":
    main()
