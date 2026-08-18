from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter, deque
from itertools import permutations
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORE_SRC = PROJECT_ROOT / "core" / "src"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

from re9_pose_recorder.still_scan import load_still_layers  # noqa: E402
from scripts.generate_reconstruction_capture_plans import (  # noqa: E402
    GridPoint,
    distance,
    generate_grid_points,
    heading_for,
)


SOURCE_CONFIG = PROJECT_ROOT / "core" / "configs" / "scene_2_no_lamp_scan_layers.yaml"
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "data"
    / "reconstruction_capture_plans"
    / "scene_2_indoor_oblique"
    / "scene_2_indoor_oblique_pose_plan.json"
)
GRID_DIRECTIONS = ((1, 0), (-1, 0), (0, 1), (0, -1))
HORIZONTAL_VIEWS = (
    ("horizontal_left", -35.0),
    ("horizontal_forward", 0.0),
    ("horizontal_right", 35.0),
)
VERTICAL_VIEWS = {
    "scene_2_y01": ("ceiling_oblique", 60.0),
    "scene_2_y02": ("ceiling_oblique", 45.0),
    "scene_2_y03": ("upper_wall_oblique", 25.0),
    "scene_2_y04": ("lower_wall_oblique", -25.0),
    "scene_2_y05": ("floor_oblique", -45.0),
    "scene_2_y06": ("floor_oblique", -60.0),
}
DIRECT_VERTICAL_VIEWS = {
    "scene_2_y01": ("ceiling_direct", 82.0),
    "scene_2_y02": ("ceiling_direct", 82.0),
    "scene_2_y03": ("ceiling_direct", 82.0),
    "scene_2_y04": ("floor_direct", -82.0),
    "scene_2_y05": ("floor_direct", -82.0),
    "scene_2_y06": ("floor_direct", -82.0),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the UI-loadable scene 2 indoor oblique reconstruction plan."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def coordinate(point: GridPoint) -> tuple[int, int]:
    return point.grid_x_index, point.grid_z_index


def coverage_route(
    points: list[GridPoint],
    start: tuple[int, int],
    direction_order: tuple[tuple[int, int], ...],
) -> list[GridPoint]:
    """Cover a connected grid while revisiting old cells only to route around holes."""
    nodes = {coordinate(point): point for point in points}

    def neighbors(cell: tuple[int, int]) -> list[tuple[int, int]]:
        x_index, z_index = cell
        return [
            (x_index + dx, z_index + dz)
            for dx, dz in direction_order
            if (x_index + dx, z_index + dz) in nodes
        ]

    visited = {start}
    route = [start]
    current = start
    while len(visited) < len(nodes):
        fresh = [neighbor for neighbor in neighbors(current) if neighbor not in visited]
        if fresh:
            current = fresh[0]
            visited.add(current)
            route.append(current)
            continue

        queue = deque([current])
        parent: dict[tuple[int, int], tuple[int, int] | None] = {current: None}
        frontier: tuple[int, int] | None = None
        while queue and frontier is None:
            candidate = queue.popleft()
            if any(neighbor not in visited for neighbor in neighbors(candidate)):
                frontier = candidate
                break
            for neighbor in neighbors(candidate):
                if neighbor in visited and neighbor not in parent:
                    parent[neighbor] = candidate
                    queue.append(neighbor)
        if frontier is None:
            raise ValueError("Scene 2 capture grid is not connected.")

        bridge: list[tuple[int, int]] = []
        cursor = frontier
        while cursor != current:
            bridge.append(cursor)
            previous = parent[cursor]
            if previous is None:
                raise RuntimeError("Coverage route bridge reconstruction failed.")
            cursor = previous
        route.extend(reversed(bridge))
        current = frontier

    return [nodes[cell] for cell in route]


def layer_route_candidates(points: list[GridPoint]) -> list[list[GridPoint]]:
    best_by_endpoints: dict[
        tuple[tuple[int, int], tuple[int, int], int],
        tuple[float, list[GridPoint]],
    ] = {}
    minimum_revisits: int | None = None
    for point in points:
        start = coordinate(point)
        for direction_order in permutations(GRID_DIRECTIONS):
            route = coverage_route(points, start, direction_order)
            revisits = len(route) - len(points)
            minimum_revisits = revisits if minimum_revisits is None else min(minimum_revisits, revisits)
            for candidate in (route, list(reversed(route))):
                total = sum(distance(left, right) for left, right in zip(candidate, candidate[1:]))
                key = (coordinate(candidate[0]), coordinate(candidate[-1]), revisits)
                current = best_by_endpoints.get(key)
                if current is None or total < current[0]:
                    best_by_endpoints[key] = (total, candidate)
    if minimum_revisits is None:
        raise ValueError("Cannot build an indoor route for an empty layer.")
    return [
        route
        for (_, _, revisits), (_, route) in best_by_endpoints.items()
        if revisits == minimum_revisits
    ]


def select_layer_routes(grid_layers: list[list[GridPoint]]) -> list[list[GridPoint]]:
    """Select connected routes that minimize the worst transition across all layers."""
    candidates = [layer_route_candidates(points) for points in grid_layers]
    scores: list[list[tuple[float, float]]] = []
    predecessors: list[list[int | None]] = []
    first_scores = []
    for route in candidates[0]:
        steps = [distance(left, right) for left, right in zip(route, route[1:])]
        first_scores.append((max(steps, default=0.0), sum(steps)))
    scores.append(first_scores)
    predecessors.append([None] * len(candidates[0]))

    for layer_index in range(1, len(candidates)):
        layer_scores: list[tuple[float, float]] = []
        layer_predecessors: list[int | None] = []
        for route in candidates[layer_index]:
            intrinsic_steps = [distance(left, right) for left, right in zip(route, route[1:])]
            intrinsic_max = max(intrinsic_steps, default=0.0)
            intrinsic_total = sum(intrinsic_steps)
            options = []
            for previous_index, previous_route in enumerate(candidates[layer_index - 1]):
                previous_max, previous_total = scores[layer_index - 1][previous_index]
                entry = distance(previous_route[-1], route[0])
                options.append(
                    (
                        (max(previous_max, intrinsic_max, entry), previous_total + intrinsic_total + entry),
                        previous_index,
                    )
                )
            best_score, best_previous = min(options, key=lambda item: item[0])
            layer_scores.append(best_score)
            layer_predecessors.append(best_previous)
        scores.append(layer_scores)
        predecessors.append(layer_predecessors)

    selected = [min(range(len(candidates[-1])), key=lambda index: scores[-1][index])]
    for layer_index in range(len(candidates) - 1, 0, -1):
        previous = predecessors[layer_index][selected[-1]]
        if previous is None:
            raise RuntimeError("Indoor layer route orientation reconstruction failed.")
        selected.append(previous)
    selected.reverse()
    return [candidates[index][candidate_index] for index, candidate_index in enumerate(selected)]


def rounded(value: float) -> float:
    return round(float(value), 9)


def source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def build_plan() -> dict[str, object]:
    layers = [
        layer
        for layer in load_still_layers(SOURCE_CONFIG)
        if layer.group_id == "scene_2"
    ]
    grid_layers = generate_grid_points("scene_2", layers, 15, 15, 1, 1)
    ordered_layers = select_layer_routes(grid_layers)

    subscenes = []
    global_position_index = 1
    global_sample_index = 1
    previous_global: GridPoint | None = None
    view_counts: Counter[str] = Counter()
    route_revisit_count = 0
    within_layer_steps: list[float] = []
    all_steps: list[float] = []

    for layer, route in zip(layers, ordered_layers):
        seen_cells: set[tuple[int, int]] = set()
        positions = []
        samples = []
        vertical_kind, vertical_pitch = VERTICAL_VIEWS[layer.layer_id]
        direct_vertical_kind, direct_vertical_pitch = DIRECT_VERTICAL_VIEWS[layer.layer_id]
        for route_index, point in enumerate(route, start=1):
            cell = coordinate(point)
            route_revisit = cell in seen_cells
            seen_cells.add(cell)
            if route_revisit:
                route_revisit_count += 1

            heading = heading_for(route, route_index - 1)
            camera_yaw = (heading + 180.0) % 360.0
            previous_distance = distance(previous_global, point) if previous_global else 0.0
            if previous_global is not None:
                all_steps.append(previous_distance)
            if route_index > 1:
                within_layer_steps.append(previous_distance)
            position_id = f"{layer.layer_id}_r{route_index:04d}"
            positions.append(
                {
                    "position_index": global_position_index,
                    "layer_position_index": route_index,
                    "position_id": position_id,
                    "grid_x_index": point.grid_x_index,
                    "grid_z_index": point.grid_z_index,
                    "x": rounded(point.x),
                    "y": rounded(point.y),
                    "z": rounded(point.z),
                    "route_heading_deg": rounded(heading),
                    "camera_route_yaw_deg": rounded(camera_yaw),
                    "route_revisit": route_revisit,
                    "distance_from_previous": rounded(previous_distance),
                }
            )

            views = [
                (kind, offset, 0.0)
                for kind, offset in HORIZONTAL_VIEWS
            ]
            views.append((vertical_kind, 0.0, vertical_pitch))
            views.append((direct_vertical_kind, 0.0, direct_vertical_pitch))
            for kind, yaw_offset, pitch in views:
                yaw = (camera_yaw + yaw_offset) % 360.0
                sample_id = f"scene_2_indoor_s{global_sample_index:05d}_{kind}"
                samples.append(
                    {
                        "sample_id": sample_id,
                        "position_id": position_id,
                        "position_index": global_position_index,
                        "layer_position_index": route_index,
                        "x": rounded(point.x),
                        "y": rounded(point.y),
                        "z": rounded(point.z),
                        "yaw": rounded(yaw),
                        "pitch": rounded(pitch),
                        "yaw_offset_deg": yaw_offset,
                        "kind": kind,
                        "priority": "reconstruction",
                        "route_revisit": route_revisit,
                    }
                )
                view_counts[kind] += 1
                global_sample_index += 1

            global_position_index += 1
            previous_global = point

        subscenes.append(
            {
                "subscene_id": f"{layer.layer_id}_indoor_oblique",
                "source_layer_id": layer.layer_id,
                "height_index": layer.height_index,
                "y": rounded(layer.y),
                "vertical_view_kind": vertical_kind,
                "vertical_pitch_deg": vertical_pitch,
                "direct_vertical_view_kind": direct_vertical_kind,
                "direct_vertical_pitch_deg": direct_vertical_pitch,
                "unique_position_count": len(grid_layers[layer.height_index - 1]),
                "route_position_count": len(route),
                "positions": positions,
                "samples": samples,
            }
        )

    unique_positions = {
        (rounded(point.x), rounded(point.y), rounded(point.z))
        for points in grid_layers
        for point in points
    }
    route_position_count = global_position_index - 1
    sample_count = global_sample_index - 1
    return {
        "schema_version": 1,
        "kind": "re9_indoor_oblique_reconstruction_pose_plan",
        "plan_id": "scene_2_indoor_oblique_reconstruction_v2",
        "scene_id": "scene_2",
        "purpose": "Indoor oblique still capture with wall, ceiling, floor, and obstacle-aware coverage",
        "ui_compatible": True,
        "preserves_existing_capture_files": True,
        "source": {
            "config": str(SOURCE_CONFIG.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "sha256": source_sha256(SOURCE_CONFIG),
            "group_id": "scene_2",
            "chandelier_exclusion_enabled": True,
        },
        "capture": {
            "mode": "still_images",
            "horizontal_yaw_offsets_deg": [-35.0, 0.0, 35.0],
            "horizontal_pitch_deg": 0.0,
            "vertical_pitch_by_layer_deg": {
                layer_id: pitch for layer_id, (_, pitch) in VERTICAL_VIEWS.items()
            },
            "direct_vertical_pitch_by_layer_deg": {
                layer_id: pitch for layer_id, (_, pitch) in DIRECT_VERTICAL_VIEWS.items()
            },
            "views_per_route_position": 5,
            "depth_capture_recommended": True,
            "camera_yaw_convention": "RE9FreeCam yaw 0 camera-forward is world -Z",
            "coordinate_units": "RE9 game units; depth export uses the configured 1 unit = 1 m scale",
        },
        "metrics": {
            "layer_count": len(layers),
            "unique_position_count": len(unique_positions),
            "route_position_count": route_position_count,
            "route_revisit_count": route_revisit_count,
            "views_per_route_position": 5,
            "sample_count": sample_count,
            "max_intralayer_step": rounded(max(within_layer_steps, default=0.0)),
            "max_route_step_including_layer_changes": rounded(max(all_steps, default=0.0)),
            "view_counts": dict(sorted(view_counts.items())),
        },
        "subscenes": subscenes,
    }


def write_plan(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    args = parse_args()
    output = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
    payload = build_plan()
    write_plan(output, payload)
    metrics = payload["metrics"]
    print(f"Wrote: {output}")
    print(
        "Positions: {route_position_count} ({unique_position_count} unique, "
        "{route_revisit_count} route revisits); samples: {sample_count}; "
        "max within-layer step: {max_intralayer_step}".format(**metrics)
    )


if __name__ == "__main__":
    main()
