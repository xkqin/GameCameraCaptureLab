from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


POSE_COLUMNS = ["x", "y", "z", "yaw", "pitch", "fov"]


def build_score_ascent_trajectory(
    scores_with_pose_csv: str | Path,
    output_csv: str | Path,
    output_plot: str | Path | None = None,
    start_mode: str = "first",
    max_step_distance: float | None = None,
    neighbor_count: int = 50,
) -> pd.DataFrame:
    """Build a monotonic score-ascent path through sampled poses.

    The reversed output order is a high-to-low score path from the best sampled
    pose back down the constructed approach path.
    """
    data = pd.read_csv(scores_with_pose_csv)
    required = {"score", "x", "y", "z"}
    missing = required - set(data.columns)
    if missing:
        raise ValueError(f"scores_with_pose.csv is missing columns: {sorted(missing)}")

    valid = data.copy()
    if "alignment_valid" in valid.columns:
        valid = valid[valid["alignment_valid"].fillna(False).astype(bool)]
    valid = valid.dropna(subset=["score", "x", "y", "z"]).reset_index(drop=True)
    if valid.empty:
        raise ValueError("No valid scored pose rows were found.")

    best_i = int(valid["score"].astype(float).idxmax())
    start_i = _choose_start(valid, start_mode)
    path_indices, forced_edges = _greedy_ascent_indices(
        valid,
        start_i=start_i,
        best_i=best_i,
        max_step_distance=max_step_distance,
        neighbor_count=neighbor_count,
    )

    rows = valid.iloc[path_indices].copy().reset_index(drop=True)
    rows.insert(0, "trajectory_order", np.arange(len(rows), dtype=int))
    rows.insert(1, "reverse_order", np.arange(len(rows) - 1, -1, -1, dtype=int))
    rows["is_best_sample"] = False
    rows.loc[rows.index[-1], "is_best_sample"] = True
    rows["step_distance"] = _step_distances(rows)
    rows["score_delta_from_previous"] = rows["score"].astype(float).diff().fillna(0.0)
    rows["forced_bridge"] = False
    for edge_to_index in forced_edges:
        if 0 <= edge_to_index < len(rows):
            rows.loc[edge_to_index, "forced_bridge"] = True

    preferred = [
        "trajectory_order",
        "reverse_order",
        "timestamp_sec",
        "score",
        "x",
        "y",
        "z",
        "yaw",
        "pitch",
        "fov",
        "step_distance",
        "score_delta_from_previous",
        "forced_bridge",
        "is_best_sample",
        "frame_path",
        "file_name",
    ]
    ordered = [column for column in preferred if column in rows.columns]
    rows = rows[ordered + [column for column in rows.columns if column not in ordered]]

    out_csv = Path(output_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    rows.to_csv(out_csv, index=False)
    if output_plot:
        _plot_trajectory(rows, Path(output_plot))
    return rows


def _choose_start(data: pd.DataFrame, start_mode: str) -> int:
    if start_mode == "first":
        if "timestamp_sec" in data.columns:
            return int(data["timestamp_sec"].astype(float).idxmin())
        return 0
    if start_mode == "lowest":
        return int(data["score"].astype(float).idxmin())
    if start_mode == "nearest-origin":
        distances = np.sqrt(data["x"].astype(float) ** 2 + data["y"].astype(float) ** 2 + data["z"].astype(float) ** 2)
        return int(distances.idxmin())
    raise ValueError("start_mode must be one of: first, lowest, nearest-origin")


def _greedy_ascent_indices(
    data: pd.DataFrame,
    start_i: int,
    best_i: int,
    max_step_distance: float | None,
    neighbor_count: int,
) -> tuple[list[int], set[int]]:
    xyz = data[["x", "y", "z"]].astype(float).to_numpy()
    scores = data["score"].astype(float).to_numpy()
    best_xyz = xyz[best_i]
    current = start_i
    path = [current]
    visited = {current}
    forced_edges: set[int] = set()

    while current != best_i:
        current_xyz = xyz[current]
        distances = np.linalg.norm(xyz - current_xyz, axis=1)
        best_distances = np.linalg.norm(xyz - best_xyz, axis=1)
        current_best_distance = best_distances[current]

        candidates = np.where(scores > scores[current])[0]
        candidates = np.array([idx for idx in candidates if idx not in visited], dtype=int)
        if len(candidates) == 0:
            if best_i not in visited:
                path.append(best_i)
                forced_edges.add(len(path) - 1)
            break

        if max_step_distance is not None:
            local = candidates[distances[candidates] <= max_step_distance]
        else:
            local = candidates
        if len(local) == 0:
            local = candidates
            force_next = True
        else:
            force_next = False

        nearest = local[np.argsort(distances[local])[: max(1, neighbor_count)]]
        score_gain = scores[nearest] - scores[current]
        distance_penalty = distances[nearest] + 1e-6
        progress = np.maximum(0.0, current_best_distance - best_distances[nearest])
        utility = score_gain / distance_penalty + 0.05 * progress

        if best_i in nearest and (max_step_distance is None or distances[best_i] <= max_step_distance):
            next_i = best_i
        else:
            next_i = int(nearest[int(np.argmax(utility))])

        path.append(next_i)
        if force_next:
            forced_edges.add(len(path) - 1)
        visited.add(next_i)
        current = next_i

        if len(path) > len(data):
            break

    return path, forced_edges


def _step_distances(rows: pd.DataFrame) -> list[float]:
    xyz = rows[["x", "y", "z"]].astype(float).to_numpy()
    distances = [0.0]
    for index in range(1, len(xyz)):
        distances.append(float(np.linalg.norm(xyz[index] - xyz[index - 1])))
    return distances


def _plot_trajectory(rows: pd.DataFrame, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(7, 7))
    plt.plot(rows["x"], rows["z"], color="black", alpha=0.35, linewidth=1.0)
    points = plt.scatter(rows["x"], rows["z"], c=rows["score"], cmap="viridis", s=42)
    plt.scatter(rows["x"].iloc[-1], rows["z"].iloc[-1], marker="*", s=220, color="red", label="best sampled pose")
    plt.xlabel("x")
    plt.ylabel("z")
    plt.title("Score-Ascent Trajectory To Best Sampled Pose")
    plt.colorbar(points, label="aesthetic score")
    plt.legend()
    plt.axis("equal")
    plt.tight_layout()
    plt.savefig(output_path, dpi=160)
    plt.close()
