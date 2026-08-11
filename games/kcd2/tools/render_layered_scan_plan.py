from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a convex-hull scene boundary with layered scan positions."
    )
    parser.add_argument("boundary_json", type=Path)
    parser.add_argument("positions_csv", type=Path)
    parser.add_argument("output_png", type=Path)
    parser.add_argument(
        "--z-display-scale",
        type=float,
        default=3.0,
        help="Visual-only vertical exaggeration used for the 3D box aspect.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    boundary = json.loads(args.boundary_json.read_text(encoding="utf-8"))
    points = np.asarray(boundary["points"], dtype=float)
    faces = np.asarray(boundary["triangles"], dtype=int)

    positions: list[tuple[int, int, float, float, float]] = []
    with args.positions_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            positions.append(
                (
                    int(row["point_index"]),
                    int(row["layer_index"]),
                    float(row["x"]),
                    float(row["y"]),
                    float(row["z"]),
                )
            )

    triangles = points[faces]
    unique_edges: set[tuple[int, int]] = set()
    edge_segments: list[np.ndarray] = []
    for face in faces:
        for start, end in ((face[0], face[1]), (face[1], face[2]), (face[2], face[0])):
            edge = tuple(sorted((int(start), int(end))))
            if edge in unique_edges:
                continue
            unique_edges.add(edge)
            edge_segments.append(points[list(edge)])

    layer_ids = sorted({row[1] for row in positions})
    colors = ["#2563eb", "#06b6d4", "#22c55e", "#f59e0b", "#ef4444"]

    fig = plt.figure(figsize=(15.5, 10.5), dpi=160, facecolor="#f7f8fa")
    ax = fig.add_subplot(111, projection="3d", facecolor="#f7f8fa")

    hull = Poly3DCollection(
        triangles,
        facecolor="#64748b",
        edgecolor="none",
        alpha=0.105,
        zorder=1,
    )
    ax.add_collection3d(hull)
    ax.add_collection3d(
        Line3DCollection(edge_segments, colors="#64748b", linewidths=0.65, alpha=0.28)
    )

    legend_handles: list[Line2D] = []
    for index, layer_id in enumerate(layer_ids):
        layer_rows = [row for row in positions if row[1] == layer_id]
        xyz = np.asarray([[row[2], row[3], row[4]] for row in layer_rows], dtype=float)
        color = colors[index % len(colors)]
        ax.scatter(
            xyz[:, 0],
            xyz[:, 1],
            xyz[:, 2],
            s=34,
            c=color,
            edgecolors="white",
            linewidths=0.6,
            depthshade=False,
            alpha=0.96,
            zorder=3,
        )
        legend_handles.append(
            Line2D(
                [0],
                [0],
                marker="o",
                linestyle="none",
                markerfacecolor=color,
                markeredgecolor="white",
                markeredgewidth=0.6,
                markersize=8,
                label=f"Layer {layer_id}  z={xyz[0, 2]:.2f}  ({len(layer_rows)} points)",
            )
        )

    mins = points.min(axis=0)
    maxs = points.max(axis=0)
    spans = maxs - mins
    padding = spans * np.asarray([0.035, 0.035, 0.06])
    ax.set_xlim(mins[0] - padding[0], maxs[0] + padding[0])
    ax.set_ylim(mins[1] - padding[1], maxs[1] + padding[1])
    ax.set_zlim(mins[2] - padding[2], maxs[2] + padding[2])
    ax.set_box_aspect((spans[0], spans[1], spans[2] * args.z_display_scale))
    ax.view_init(elev=25, azim=-58)

    ax.set_xlabel("X (world units)", labelpad=12)
    ax.set_ylabel("Y (world units)", labelpad=12)
    ax.set_zlabel("Z / vertical (world units)", labelpad=10)
    ax.set_title(
        "scene_1 | 5-layer hull-aware scan layout",
        fontsize=18,
        fontweight="semibold",
        pad=22,
    )
    ax.text2D(
        0.015,
        0.955,
        f"131 positions | 22 views/position | 2,882 images | Z display x{args.z_display_scale:g}",
        transform=ax.transAxes,
        color="#475569",
        fontsize=11,
    )
    ax.legend(
        handles=legend_handles,
        loc="upper right",
        bbox_to_anchor=(1.01, 0.93),
        frameon=False,
        labelspacing=0.85,
    )

    ax.grid(True, alpha=0.20)
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.pane.fill = False
        axis.pane.set_edgecolor((0.65, 0.68, 0.72, 0.22))
        axis._axinfo["grid"]["color"] = (0.42, 0.47, 0.53, 0.16)
        axis._axinfo["grid"]["linewidth"] = 0.6

    fig.subplots_adjust(left=0.01, right=0.985, bottom=0.03, top=0.94)
    args.output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output_png, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)


if __name__ == "__main__":
    main()
