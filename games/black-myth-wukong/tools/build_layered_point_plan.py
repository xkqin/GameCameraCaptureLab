from __future__ import annotations

"""Build a point plan by filling the recorded closed camera boundary.

The recorded points are treated as an ordered, closed boundary rather than as
the corners of an axis-aligned box.  PCA gives the boundary's local plane;
points are sampled inside that polygon and their third PCA coordinate is
interpolated from the recorded boundary.  This keeps the generated positions
inside the shape suggested by the recording while retaining its 3-D bend.
"""

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np


Point2 = tuple[float, float]


def cross2(a: Point2, b: Point2, c: Point2) -> float:
    """Return the signed 2-D cross product of AB and AC."""

    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def signed_area(polygon: np.ndarray) -> float:
    return float(
        0.5
        * sum(
            polygon[i, 0] * polygon[(i + 1) % len(polygon), 1]
            - polygon[(i + 1) % len(polygon), 0] * polygon[i, 1]
            for i in range(len(polygon))
        )
    )


def point_in_polygon(point: Point2, polygon: np.ndarray, *, tolerance: float) -> bool:
    """Ray-cast point-in-polygon test that includes the polygon edge."""

    x, y = point
    inside = False
    for index in range(len(polygon)):
        a = tuple(float(value) for value in polygon[index])
        b = tuple(float(value) for value in polygon[(index + 1) % len(polygon)])
        edge_cross = cross2(a, b, point)
        if (
            abs(edge_cross) <= tolerance
            and min(a[0], b[0]) - tolerance <= x <= max(a[0], b[0]) + tolerance
            and min(a[1], b[1]) - tolerance <= y <= max(a[1], b[1]) + tolerance
        ):
            return True
        if (a[1] > y) != (b[1] > y):
            intersection_x = a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x < intersection_x:
                inside = not inside
    return inside


def point_to_segment_distance(point: Point2, a: Point2, b: Point2) -> float:
    p = np.asarray(point, dtype=float)
    start = np.asarray(a, dtype=float)
    end = np.asarray(b, dtype=float)
    direction = end - start
    length_squared = float(direction @ direction)
    if length_squared <= 1e-18:
        return float(np.linalg.norm(p - start))
    factor = float(np.clip(((p - start) @ direction) / length_squared, 0.0, 1.0))
    return float(np.linalg.norm(p - (start + factor * direction)))


def boundary_distance(point: Point2, polygon: np.ndarray) -> float:
    return min(
        point_to_segment_distance(
            point,
            tuple(float(value) for value in polygon[index]),
            tuple(float(value) for value in polygon[(index + 1) % len(polygon)]),
        )
        for index in range(len(polygon))
    )


def polygon_centroid(polygon: np.ndarray) -> np.ndarray:
    area = signed_area(polygon)
    if abs(area) <= 1e-12:
        return polygon.mean(axis=0)
    numerator = np.zeros(2, dtype=float)
    for index in range(len(polygon)):
        current = polygon[index]
        following = polygon[(index + 1) % len(polygon)]
        factor = current[0] * following[1] - following[0] * current[1]
        numerator += (current + following) * factor
    result = numerator / (6.0 * area)
    return result if point_in_polygon(tuple(result), polygon, tolerance=1e-7) else polygon.mean(axis=0)


def point_in_triangle(point: Point2, triangle: np.ndarray, *, tolerance: float) -> bool:
    values = [
        cross2(
            tuple(float(value) for value in triangle[index]),
            tuple(float(value) for value in triangle[(index + 1) % 3]),
            point,
        )
        for index in range(3)
    ]
    return max(values) <= tolerance or min(values) >= -tolerance


def triangulate_polygon(polygon: np.ndarray) -> list[tuple[int, int, int]]:
    """Ear-clip a simple counter-clockwise polygon into surface triangles."""

    if len(polygon) < 3:
        raise ValueError("At least three boundary points are required")
    scale = max(float(np.ptp(polygon[:, 0])), float(np.ptp(polygon[:, 1])), 1.0)
    tolerance = scale * 1e-10
    remaining = list(range(len(polygon)))
    triangles: list[tuple[int, int, int]] = []
    guard = 0
    while len(remaining) > 3:
        guard += 1
        if guard > len(polygon) * len(polygon):
            raise ValueError("Could not triangulate the recorded boundary")
        clipped = False
        for position, current in enumerate(remaining):
            previous = remaining[position - 1]
            following = remaining[(position + 1) % len(remaining)]
            triangle = polygon[[previous, current, following]]
            if cross2(
                tuple(triangle[0]), tuple(triangle[1]), tuple(triangle[2])
            ) <= tolerance:
                continue
            contains_other_vertex = any(
                point_in_triangle(
                    tuple(polygon[candidate]),
                    triangle,
                    tolerance=tolerance,
                )
                for candidate in remaining
                if candidate not in (previous, current, following)
            )
            if contains_other_vertex:
                continue
            triangles.append((previous, current, following))
            del remaining[position]
            clipped = True
            break
        if not clipped:
            raise ValueError(
                "Recorded points do not form a simple, non-degenerate boundary"
            )
    triangles.append(tuple(remaining))
    return triangles


def mean_value_interpolate(query: Point2, polygon: np.ndarray, values: np.ndarray) -> float:
    """Interpolate the third PCA coordinate smoothly from boundary values."""

    scale = max(float(np.ptp(polygon[:, 0])), float(np.ptp(polygon[:, 1])), 1.0)
    edge_tolerance = scale * 1e-9
    for index in range(len(polygon)):
        start = polygon[index]
        end = polygon[(index + 1) % len(polygon)]
        a = tuple(float(value) for value in start)
        b = tuple(float(value) for value in end)
        segment = np.asarray(b) - np.asarray(a)
        length_squared = float(segment @ segment)
        factor = 0.0 if length_squared <= 1e-18 else float(
            np.clip((np.asarray(query) - np.asarray(a)) @ segment / length_squared, 0.0, 1.0)
        )
        closest = np.asarray(a) + factor * segment
        if float(np.linalg.norm(np.asarray(query) - closest)) <= edge_tolerance:
            return float(values[index] * (1.0 - factor) + values[(index + 1) % len(values)] * factor)

    vectors = polygon - np.asarray(query, dtype=float)
    distances = np.linalg.norm(vectors, axis=1)
    nearest = int(np.argmin(distances))
    if distances[nearest] <= edge_tolerance:
        return float(values[nearest])

    tangents: list[float] = []
    for index in range(len(polygon)):
        first = vectors[index]
        second = vectors[(index + 1) % len(polygon)]
        angle = math.atan2(
            float(first[0] * second[1] - first[1] * second[0]),
            float(first @ second),
        )
        tangents.append(math.tan(angle / 2.0))
    weights = np.asarray(
        [tangents[index - 1] + tangents[index] for index in range(len(polygon))],
        dtype=float,
    ) / distances
    denominator = float(weights.sum())
    if not np.isfinite(denominator) or abs(denominator) <= 1e-12:
        inverse = 1.0 / np.maximum(distances, edge_tolerance) ** 2
        result = float(inverse @ values / inverse.sum())
    else:
        result = float(weights @ values / denominator)

    # Mean-value coordinates should stay in the boundary-value range.  The
    # clamp is a numerical guard for an almost-collinear or nearly degenerate
    # user-drawn boundary; it is not an axis-aligned XYZ clamp.
    lower = float(values.min())
    upper = float(values.max())
    return float(np.clip(result, lower, upper))


def sample_interior_points(polygon: np.ndarray, count: int) -> np.ndarray:
    """Select a deterministic, well-spread set of points strictly inside."""

    if count <= 0:
        raise ValueError("The interior point count must be positive")
    min_xy = polygon.min(axis=0)
    max_xy = polygon.max(axis=0)
    span = max_xy - min_xy
    scale = max(float(span.max()), 1.0)
    tolerance = scale * 1e-9
    interior_margin = scale * 1e-6
    grid_size = max(24, int(math.ceil(math.sqrt(count * 8.0))))
    candidates: list[np.ndarray] = []
    for _ in range(5):
        xs = np.linspace(min_xy[0], max_xy[0], grid_size)
        ys = np.linspace(min_xy[1], max_xy[1], grid_size)
        candidates = [
            np.asarray((x, y), dtype=float)
            for x in xs
            for y in ys
            if point_in_polygon((float(x), float(y)), polygon, tolerance=tolerance)
            and boundary_distance((float(x), float(y)), polygon) > interior_margin
        ]
        if len(candidates) >= count:
            break
        grid_size *= 2
    if len(candidates) < count:
        raise ValueError(
            f"Only {len(candidates)} usable interior candidates were found for {count} points"
        )

    center = polygon_centroid(polygon)
    selected: list[np.ndarray] = [
        min(candidates, key=lambda candidate: float(np.sum((candidate - center) ** 2)))
    ]
    selected_start = next(
        index for index, candidate in enumerate(candidates) if np.array_equal(candidate, selected[0])
    )
    remaining = [candidate for index, candidate in enumerate(candidates) if index != selected_start]
    while len(selected) < count:
        next_position = max(
            range(len(remaining)),
            key=lambda position: min(
                float(np.sum((remaining[position] - chosen) ** 2)) for chosen in selected
            ),
        )
        next_candidate = remaining.pop(next_position)
        selected.append(next_candidate)
    return np.asarray(sorted(selected, key=lambda point: (float(point[1]), float(point[0]))))


def pca_frame(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    center = points.mean(axis=0)
    _, singular_values, axes = np.linalg.svd(points - center, full_matrices=False)
    axes = np.asarray(axes, dtype=float)
    if np.linalg.det(axes) < 0.0:
        axes[2] *= -1.0
    local = (points - center) @ axes.T
    return center, axes, singular_values, local


def reconstruct(local: np.ndarray, center: np.ndarray, axes: np.ndarray) -> np.ndarray:
    return np.asarray(center) + np.asarray(local) @ np.asarray(axes)


def write_png(
    raw_xyz: np.ndarray,
    surface_xyz: np.ndarray,
    triangles: list[tuple[int, int, int]],
    fill_xyz: np.ndarray,
    center: np.ndarray,
    target: Path,
) -> None:
    raw_relative = raw_xyz - center
    surface_relative = surface_xyz - center
    fill_relative = fill_xyz - center
    fig = plt.figure(figsize=(13, 9), dpi=160)
    axis = fig.add_subplot(111, projection="3d")
    surface_faces = [surface_relative[list(triangle)] for triangle in triangles]
    surface = Poly3DCollection(
        surface_faces,
        facecolor="#60a5fa",
        edgecolor="#2563eb",
        linewidth=0.45,
        alpha=0.24,
    )
    axis.add_collection3d(surface)
    axis.plot(
        np.r_[raw_relative[:, 0], raw_relative[0, 0]],
        np.r_[raw_relative[:, 1], raw_relative[0, 1]],
        np.r_[raw_relative[:, 2], raw_relative[0, 2]],
        color="#dc2626",
        linewidth=1.8,
        linestyle="--",
    )
    axis.scatter(
        raw_relative[:, 0],
        raw_relative[:, 1],
        raw_relative[:, 2],
        color="#dc2626",
        marker="^",
        s=56,
        depthshade=False,
    )
    axis.scatter(
        fill_relative[:, 0],
        fill_relative[:, 1],
        fill_relative[:, 2],
        c=np.linspace(0.08, 0.92, len(fill_relative)),
        cmap="viridis",
        s=22,
        depthshade=False,
    )
    for index, point in enumerate(raw_relative, 1):
        axis.text(point[0], point[1], point[2], f" R{index}", fontsize=7, color="#991b1b")

    all_points = np.vstack([raw_relative, fill_relative])
    min_values = all_points.min(axis=0)
    max_values = all_points.max(axis=0)
    spans = np.maximum(max_values - min_values, 1.0)
    span = float(spans.max())
    midpoint = (min_values + max_values) / 2.0
    for setter, value in (
        (axis.set_xlim, midpoint[0]),
        (axis.set_ylim, midpoint[1]),
        (axis.set_zlim, midpoint[2]),
    ):
        setter(value - span / 2.0, value + span / 2.0)
    axis.set_box_aspect((1.0, 1.0, 1.0))
    axis.set_xlabel("ΔX from recorded center")
    axis.set_ylabel("ΔY from recorded center")
    axis.set_zlabel("ΔZ from recorded center")
    axis.set_title("Black Myth: Wukong — closed boundary interior fill")
    axis.legend(
        handles=[
            Line2D([0], [0], color="#dc2626", marker="^", linestyle="--", label="Recorded closed boundary"),
            Patch(facecolor="#60a5fa", edgecolor="#2563eb", alpha=0.24, label="Inferred curved surface"),
            Line2D([0], [0], color="#22c55e", marker="o", linestyle="", label="Interior fill points"),
        ],
        loc="upper left",
        bbox_to_anchor=(0.0, 1.02),
        fontsize=8,
    )
    fig.text(
        0.01,
        0.01,
        f"10 recorded boundary points → {len(fill_xyz)} interior positions · no upper extension · PCA surface fill",
        fontsize=8,
    )
    fig.tight_layout()
    fig.savefig(target, bbox_inches="tight")
    plt.close(fig)


def write_html(
    raw_records: list[dict[str, Any]],
    raw_xyz: np.ndarray,
    surface_xyz: np.ndarray,
    triangles: list[tuple[int, int, int]],
    fill_records: list[dict[str, Any]],
    target: Path,
) -> None:
    raw_trace = {
        "type": "scatter3d",
        "mode": "markers+lines+text",
        "name": "Recorded closed boundary",
        "x": [float(value) for value in np.r_[raw_xyz[:, 0], raw_xyz[0, 0]]],
        "y": [float(value) for value in np.r_[raw_xyz[:, 1], raw_xyz[0, 1]]],
        "z": [float(value) for value in np.r_[raw_xyz[:, 2], raw_xyz[0, 2]]],
        "text": [f"R{record['index']}" for record in raw_records] + [""],
        "textposition": "top center",
        "marker": {"size": 6, "color": "#dc2626", "symbol": "diamond"},
        "line": {"color": "#dc2626", "width": 4, "dash": "dash"},
        "hovertemplate": "Recorded %{text}<br>X=%{x:.2f}<br>Y=%{y:.2f}<br>Z=%{z:.2f}<extra></extra>",
    }
    surface_trace = {
        "type": "mesh3d",
        "name": "Inferred curved surface",
        "x": [float(value) for value in surface_xyz[:, 0]],
        "y": [float(value) for value in surface_xyz[:, 1]],
        "z": [float(value) for value in surface_xyz[:, 2]],
        "i": [triangle[0] for triangle in triangles],
        "j": [triangle[1] for triangle in triangles],
        "k": [triangle[2] for triangle in triangles],
        "color": "#60a5fa",
        "opacity": 0.28,
        "flatshading": True,
        "hoverinfo": "skip",
    }
    colors = [
        matplotlib.colors.to_hex(color)
        for color in plt.cm.viridis(np.linspace(0.08, 0.92, max(len(fill_records), 1)))
    ]
    fill_trace = {
        "type": "scatter3d",
        "mode": "markers",
        "name": "Interior fill points",
        "x": [float(point["x"]) for point in fill_records],
        "y": [float(point["y"]) for point in fill_records],
        "z": [float(point["z"]) for point in fill_records],
        "text": [str(point["label"]) for point in fill_records],
        "hovertemplate": "%{text}<br>X=%{x:.2f}<br>Y=%{y:.2f}<br>Z=%{z:.2f}<extra></extra>",
        "marker": {"size": 4, "color": colors},
    }
    payload = json.dumps([raw_trace, surface_trace, fill_trace], ensure_ascii=False)
    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>Closed shape fill</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
html,body,#plot{{width:100%;height:100%;margin:0;background:#0f172a;color:#e2e8f0;font-family:Arial,sans-serif}}
#note{{position:fixed;left:16px;top:12px;z-index:2;background:#172033ee;padding:9px 12px;border:1px solid #334155;border-radius:6px;line-height:1.45}}
</style></head><body>
<div id="note">10 个记录点闭合边界 · {len(fill_records)} 个内部补点 · PCA 曲面填充 · 不含边界外扩展<br>拖动旋转，滚轮缩放</div>
<div id="plot" role="img" aria-label="Recorded closed camera boundary with interior filled positions"></div>
<script>
const traces = {payload};
Plotly.newPlot('plot', traces, {{
  paper_bgcolor:'#0f172a', plot_bgcolor:'#0f172a', font:{{color:'#e2e8f0'}},
  title:'Black Myth: Wukong — closed boundary interior fill',
  scene:{{xaxis:{{title:'X (game units)'}},yaxis:{{title:'Y (game units)'}},zaxis:{{title:'Z (game units)'}},aspectmode:'data'}},
  legend:{{orientation:'h', y:-0.02}}
}}, {{responsive:true, displaylogo:false}});
</script></body></html>"""
    target.write_text(html, encoding="utf-8")


def _raw_records(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("points"), list):
        raise ValueError("The source JSON must contain a points list")
    records = [dict(item) for item in payload["points"] if isinstance(item, dict)]
    if len(records) < 3:
        raise ValueError("At least three recorded points are required")
    records.sort(key=lambda item: int(item.get("index", 0)))
    return records


def build(source: Path, output_dir: Path, interior_count: int) -> dict[str, Any]:
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    records = _raw_records(payload)
    raw_xyz = np.asarray(
        [[float(record[axis]) for axis in ("x", "y", "z")] for record in records],
        dtype=float,
    )
    center, axes, singular_values, local = pca_frame(raw_xyz)
    order = list(range(len(records)))
    if signed_area(local[:, :2]) < 0.0:
        order.reverse()
    boundary_uv = local[order, :2]
    boundary_w = local[order, 2]
    triangles = triangulate_polygon(boundary_uv)
    fill_uv = sample_interior_points(boundary_uv, interior_count)
    fill_w = np.asarray(
        [mean_value_interpolate(tuple(point), boundary_uv, boundary_w) for point in fill_uv],
        dtype=float,
    )
    fill_local = np.column_stack([fill_uv, fill_w])
    fill_xyz = reconstruct(fill_local, center, axes)
    surface_xyz = reconstruct(
        np.column_stack([boundary_uv, boundary_w]), center, axes
    )
    source_fov = float(np.mean([float(record.get("fov_degrees", 65.0)) for record in records]))

    fill_records: list[dict[str, Any]] = []
    for index, (local_point, world_point) in enumerate(zip(fill_local, fill_xyz), 1):
        fill_records.append(
            {
                "index": index,
                "label": f"fill_{index:04d}",
                "time_sec": 0.0,
                "x": float(world_point[0]),
                "y": float(world_point[1]),
                "z": float(world_point[2]),
                "yaw_degrees": 0.0,
                "pitch_degrees": 0.0,
                "roll_degrees": 0.0,
                "fov_degrees": source_fov,
                "qx": 0.0,
                "qy": 0.0,
                "qz": 0.0,
                "qw": 1.0,
                "camera_enabled": True,
                "movement_locked": False,
                "source": source.name,
                "boundary_model": "closed_recorded_polygon_pca_surface",
                "pca_u": float(local_point[0]),
                "pca_v": float(local_point[1]),
                "pca_w": float(local_point[2]),
            }
        )

    projection_tolerance = max(float(np.ptp(boundary_uv, axis=0).max()) * 1e-9, 1e-6)
    inside_mask = [
        point_in_polygon(tuple(point), boundary_uv, tolerance=projection_tolerance)
        for point in fill_uv
    ]
    w_min = float(boundary_w.min())
    w_max = float(boundary_w.max())
    planned_local_xyz_bounds = fill_local.min(axis=0).tolist(), fill_local.max(axis=0).tolist()
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"closed_shape_fill_{interior_count}points"
    plan_path = output_dir / f"{stem}.json"
    png_path = output_dir / f"{stem}.png"
    html_path = output_dir / f"{stem}.html"
    plan = {
        "format": "bmw-closed-shape-fill-v1",
        "source_file": str(source.resolve()),
        "source_point_count": len(records),
        "source_point_order": [int(record.get("index", index + 1)) for index, record in enumerate(records)],
        "boundary_model": "recorded_sequence_closed_polygon_in_pca_plane_with_mean_value_surface_interpolation",
        "geometry_note": "This is a curved 3-D surface inferred from the recorded closed boundary, not an axis-aligned box or an upper extension.",
        "planned_point_count": len(fill_records),
        "static_image_count": len(fill_records) * 22,
        "views_per_spatial_point": 22,
        "points": fill_records,
        "boundary_points": [
            {
                "index": int(record.get("index", index + 1)),
                "label": str(record.get("label", f"point_{index + 1:04d}")),
                "x": float(record["x"]),
                "y": float(record["y"]),
                "z": float(record["z"]),
                "pca_u": float(local[index, 0]),
                "pca_v": float(local[index, 1]),
                "pca_w": float(local[index, 2]),
            }
            for index, record in enumerate(records)
        ],
        "pca_frame": {
            "center_xyz": [float(value) for value in center],
            "axes_xyz_rows": [[float(value) for value in row] for row in axes],
            "singular_values": [float(value) for value in singular_values],
            "boundary_signed_area_in_uv": float(signed_area(boundary_uv)),
            "boundary_area_in_uv": abs(float(signed_area(boundary_uv))),
            "boundary_uv_in_polygon_order": [[float(value) for value in point] for point in boundary_uv],
            "boundary_w_in_polygon_order": [float(value) for value in boundary_w],
        },
        "surface_triangles_zero_based": [list(triangle) for triangle in triangles],
        "validation": {
            "interior_points_inside_projected_boundary": bool(all(inside_mask)),
            "outside_projected_boundary_count": int(sum(not value for value in inside_mask)),
            "minimum_projected_boundary_distance": float(
                min(boundary_distance(tuple(point), boundary_uv) for point in fill_uv)
            ),
            "pca_w_range_from_recorded_boundary": [w_min, w_max],
            "planned_pca_uvw_min": [float(value) for value in planned_local_xyz_bounds[0]],
            "planned_pca_uvw_max": [float(value) for value in planned_local_xyz_bounds[1]],
            "upper_extension_used": False,
        },
    }
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    write_png(raw_xyz, surface_xyz, triangles, fill_xyz, center, png_path)
    write_html(records, raw_xyz, surface_xyz, triangles, fill_records, html_path)
    return {
        "plan": str(plan_path.resolve()),
        "png": str(png_path.resolve()),
        "html": str(html_path.resolve()),
        "source_point_count": len(records),
        "interior_point_count": len(fill_records),
        "static_image_count": len(fill_records) * 22,
        "triangles": len(triangles),
        "inside_projected_boundary": bool(all(inside_mask)),
        "outside_projected_boundary_count": int(sum(not value for value in inside_mask)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fill the closed geometry implied by the recorded camera points"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--points",
        type=int,
        default=None,
        help="Number of interior positions; default 100",
    )
    # Keep the previous command line usable while making its result a true
    # closed-shape fill. Existing old output folders are never overwritten.
    parser.add_argument("--layers", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--points-per-layer", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--upper-extension-layers", type=int, default=0, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.upper_extension_layers:
        raise SystemExit(
            "Closed-shape fill does not create upper extension layers; use the recorded boundary as-is."
        )
    interior_count = args.points
    if interior_count is None and args.layers and args.points_per_layer:
        interior_count = args.layers * args.points_per_layer
    if interior_count is None:
        interior_count = 100
    result = build(args.source, args.output_dir, interior_count)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
