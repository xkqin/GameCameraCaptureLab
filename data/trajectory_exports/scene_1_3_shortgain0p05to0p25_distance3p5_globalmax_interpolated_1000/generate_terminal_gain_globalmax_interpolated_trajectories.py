#!/usr/bin/env python3
"""Generate short terminal-gain replay curves ending at one measured global maximum.

Only the start and end anchors carry measured EZCAM scores. Materialized replay
poses between them are explicitly unscored and exist only to encode smooth,
geometrically distinct camera motion for the capture UI.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MaxNLocator

import generate_real_monotonic_trajectories as base
import generate_true_keyframe_optimal_trajectories as core


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene-id", default="scene_1.1")
    parser.add_argument("--scored-jsonl", type=Path, required=True)
    parser.add_argument("--extra-scored-jsonl", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--num-trajectories", type=int, default=1000)
    parser.add_argument("--keyframe-count", type=int, default=24)
    parser.add_argument("--time-step-sec", type=float, default=0.1)
    parser.add_argument("--maximum-lateral-offset-m", type=float, default=0.18)
    parser.add_argument("--maximum-lateral-ratio", type=float, default=0.12)
    parser.add_argument("--maximum-yaw-bulge-deg", type=float, default=3.0)
    parser.add_argument("--maximum-pitch-bulge-deg", type=float, default=1.5)
    parser.add_argument("--signature-quantization-m", type=float, default=0.01)
    parser.add_argument("--minimum-terminal-gain", type=float, default=0.08)
    parser.add_argument("--maximum-terminal-gain", type=float, default=0.45)
    parser.add_argument("--minimum-start-distance-m", type=float, default=0.4)
    parser.add_argument("--maximum-start-distance-m", type=float, default=6.0)
    parser.add_argument("--maximum-start-yaw-difference-deg", type=float, default=45.0)
    parser.add_argument("--maximum-start-pitch-difference-deg", type=float, default=45.0)
    parser.add_argument("--seed", type=int, default=20260814)
    return parser.parse_args()


def halton(index: int, base_value: int) -> float:
    result = 0.0
    factor = 1.0 / base_value
    value = index
    while value > 0:
        result += factor * (value % base_value)
        value //= base_value
        factor /= base_value
    return result


def angle_delta(start: float, end: float) -> float:
    return ((end - start + 180.0) % 360.0) - 180.0


def orthogonal_basis(direction: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    reference = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    if abs(float(np.dot(direction, reference))) > 0.9:
        reference = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    first = np.cross(direction, reference)
    first /= np.linalg.norm(first)
    second = np.cross(direction, first)
    second /= np.linalg.norm(second)
    return first, second


def curve_signature(positions: np.ndarray, quantum: float) -> str:
    quantized = np.rint(positions / quantum).astype(np.int64).tolist()
    value = json.dumps(quantized, separators=(",", ":"))
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def pose_curve_signature(frames: list[dict[str, Any]]) -> str:
    values = [
        [
            round(float(frame["x"]), 6),
            round(float(frame["y"]), 6),
            round(float(frame["z"]), 6),
            round(float(frame["yaw"]), 6),
            round(float(frame["pitch"]), 6),
        ]
        for frame in frames
    ]
    return hashlib.sha1(json.dumps(values, separators=(",", ":")).encode("utf-8")).hexdigest()


def select_start_anchors(
    nodes: list[base.Node], target_index: int, args: argparse.Namespace
) -> tuple[list[int], dict[str, Any]]:
    target = nodes[target_index]
    eligible_by_xyz: dict[tuple[float, float, float], tuple[float, float, str, int]] = {}
    rejected = Counter()
    for node_index, node in enumerate(nodes):
        if node_index == target_index:
            continue
        gain = target.raw_score - node.raw_score
        distance = math.dist((node.x, node.y, node.z), (target.x, target.y, target.z))
        yaw_difference = base.angle_diff(node.yaw, target.yaw)
        pitch_difference = abs(node.pitch - target.pitch)
        if not args.minimum_terminal_gain - 1e-12 <= gain <= args.maximum_terminal_gain + 1e-12:
            rejected["terminal_gain"] += 1
            continue
        if not args.minimum_start_distance_m - 1e-12 <= distance <= args.maximum_start_distance_m + 1e-12:
            rejected["start_distance"] += 1
            continue
        if yaw_difference > args.maximum_start_yaw_difference_deg + 1e-12:
            rejected["yaw_difference"] += 1
            continue
        if pitch_difference > args.maximum_start_pitch_difference_deg + 1e-12:
            rejected["pitch_difference"] += 1
            continue
        xyz_signature = (round(node.x, 6), round(node.y, 6), round(node.z, 6))
        candidate = (distance, -node.raw_score, node.node_id, node_index)
        previous = eligible_by_xyz.get(xyz_signature)
        if previous is not None:
            rejected["duplicate_start_xyz"] += 1
            previous_node = nodes[previous[-1]]
            previous_quality = (
                -previous_node.raw_score,
                base.angle_diff(previous_node.yaw, target.yaw),
                abs(previous_node.pitch - target.pitch),
                previous_node.node_id,
            )
            candidate_quality = (
                -node.raw_score,
                yaw_difference,
                pitch_difference,
                node.node_id,
            )
            if candidate_quality >= previous_quality:
                continue
        eligible_by_xyz[xyz_signature] = candidate
    eligible = list(eligible_by_xyz.values())
    eligible.sort()
    starts = [value[-1] for value in eligible]
    if not starts:
        raise RuntimeError("No measured start anchors satisfy the terminal-gain constraints")
    return starts, {
        "eligible_start_anchor_count": len(starts),
        "selection_policy": (
            "all unique XYZ starts inside configured terminal-gain and distance bounds; "
            "highest-scoring eligible measured pose retained at each XYZ"
        ),
        "rejected": dict(rejected),
        "eligible_start_anchors": [
            {
                "node_id": nodes[index].node_id,
                "score": round(nodes[index].raw_score, 6),
                "distance_to_global_max_m": round(
                    math.dist(
                        (nodes[index].x, nodes[index].y, nodes[index].z),
                        (target.x, target.y, target.z),
                    ),
                    6,
                ),
            }
            for index in starts
        ],
    }


def curve_parameters(
    candidate_index: int,
    family_index: int,
    maximum_offset: float,
    args: argparse.Namespace,
) -> dict[str, float]:
    if candidate_index == 0:
        return {
            "lateral_a_m": 0.0,
            "lateral_b_m": 0.0,
            "yaw_bulge_deg": 0.0,
            "pitch_bulge_deg": 0.0,
        }
    sequence_index = candidate_index + family_index * 10007 + 1
    return {
        "lateral_a_m": (2.0 * halton(sequence_index, 2) - 1.0) * maximum_offset,
        "lateral_b_m": (2.0 * halton(sequence_index, 3) - 1.0) * maximum_offset,
        "yaw_bulge_deg": (2.0 * halton(sequence_index, 5) - 1.0) * args.maximum_yaw_bulge_deg,
        "pitch_bulge_deg": (2.0 * halton(sequence_index, 7) - 1.0) * args.maximum_pitch_bulge_deg,
    }


def materialize_curve(
    start: base.Node,
    target: base.Node,
    parameters: dict[str, float],
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    start_position = np.array([start.x, start.y, start.z], dtype=np.float64)
    target_position = np.array([target.x, target.y, target.z], dtype=np.float64)
    displacement = target_position - start_position
    net = float(np.linalg.norm(displacement))
    direction = displacement / net
    normal_a, normal_b = orthogonal_basis(direction)
    offset = parameters["lateral_a_m"] * normal_a + parameters["lateral_b_m"] * normal_b

    samples = np.linspace(0.0, 1.0, args.keyframe_count)
    progress = samples**3 * (10.0 + samples * (-15.0 + 6.0 * samples))
    bump = 64.0 * samples**3 * (1.0 - samples) ** 3
    positions = start_position + progress[:, None] * displacement + bump[:, None] * offset
    yaw_values = (
        start.yaw
        + progress * angle_delta(start.yaw, target.yaw)
        + bump * parameters["yaw_bulge_deg"]
    ) % 360.0
    pitch_values = (
        start.pitch
        + progress * (target.pitch - start.pitch)
        + bump * parameters["pitch_bulge_deg"]
    )

    frames = []
    for step in range(args.keyframe_count):
        is_start = step == 0
        is_end = step == args.keyframe_count - 1
        node = start if is_start else target if is_end else None
        frames.append(
            {
                "step": step,
                "time_sec": round(step * args.time_step_sec, 6),
                "x": node.x if node is not None else round(float(positions[step, 0]), 6),
                "y": node.y if node is not None else round(float(positions[step, 1]), 6),
                "z": node.z if node is not None else round(float(positions[step, 2]), 6),
                "yaw": node.yaw if node is not None else round(float(yaw_values[step]), 6),
                "pitch": node.pitch if node is not None else round(float(pitch_values[step]), 6),
                "fov": None,
                "frame_type": "real_scored_control_point" if node is not None else "replay_interpolated_keyframe",
                "score": round(node.raw_score, 6) if node is not None else None,
                "raw_score": round(node.raw_score, 6) if node is not None else None,
                "estimated_score": None,
                "score_source": "ezcam_scored_still" if node is not None else "unscored_interpolation",
                "node_id": node.node_id if node is not None else None,
                "image_path": node.image_path if node is not None else None,
                "is_real_scored_control_point": node is not None,
            }
        )
    return frames


def motion_metrics(frames: list[dict[str, Any]], target: base.Node) -> dict[str, float | int]:
    xyz = np.array([[frame["x"], frame["y"], frame["z"]] for frame in frames], dtype=np.float64)
    steps = np.linalg.norm(np.diff(xyz, axis=0), axis=1)
    velocity = steps / max(float(frames[1]["time_sec"] - frames[0]["time_sec"]), 1e-9)
    acceleration = np.diff(velocity) / max(float(frames[1]["time_sec"] - frames[0]["time_sec"]), 1e-9)
    yaw_steps = np.array(
        [base.angle_diff(float(first["yaw"]), float(second["yaw"])) for first, second in zip(frames, frames[1:])]
    )
    pitch_steps = np.array(
        [abs(float(first["pitch"]) - float(second["pitch"])) for first, second in zip(frames, frames[1:])]
    )
    target_position = np.array([target.x, target.y, target.z], dtype=np.float64)
    target_distances = np.linalg.norm(xyz - target_position, axis=1)
    increases = np.maximum(np.diff(target_distances), 0.0)
    net = float(np.linalg.norm(xyz[-1] - xyz[0]))
    path_length = float(np.sum(steps))
    turns = []
    for first, second in zip(np.diff(xyz, axis=0), np.diff(xyz, axis=0)[1:]):
        first_norm = float(np.linalg.norm(first))
        second_norm = float(np.linalg.norm(second))
        if first_norm <= 1e-12 or second_norm <= 1e-12:
            continue
        cosine = float(np.clip(np.dot(first, second) / (first_norm * second_norm), -1.0, 1.0))
        turns.append(math.degrees(math.acos(cosine)))
    return {
        "path_xyz_length": path_length,
        "net_xyz_distance": net,
        "path_to_net_ratio": path_length / net,
        "max_xyz_step": float(np.max(steps)),
        "mean_xyz_step": float(np.mean(steps)),
        "max_speed_mps": float(np.max(velocity)),
        "max_abs_tangential_acceleration_mps2": float(np.max(np.abs(acceleration))) if len(acceleration) else 0.0,
        "max_yaw_step_deg": float(np.max(yaw_steps)),
        "max_pitch_step_deg": float(np.max(pitch_steps)),
        "max_sampled_turn_deg": max(turns, default=0.0),
        "target_distance_increase_step_count": int(np.count_nonzero(increases > 1e-8)),
        "target_distance_total_increase": float(np.sum(increases)),
    }


def rounded_metrics(metrics: dict[str, float | int]) -> dict[str, float | int]:
    return {
        key: value if isinstance(value, int) else round(float(value), 6)
        for key, value in metrics.items()
    }


def make_trajectory(
    number: int,
    family_number: int,
    variant_number: int,
    start: base.Node,
    target: base.Node,
    frames: list[dict[str, Any]],
    parameters: dict[str, float],
    args: argparse.Namespace,
) -> dict[str, Any]:
    positions = np.array([[frame["x"], frame["y"], frame["z"]] for frame in frames], dtype=np.float64)
    metrics = rounded_metrics(motion_metrics(frames, target))
    return {
        "trajectory_id": f"{args.scene_id.replace('.', '_')}_terminalgain_globalmax_interp_{number:04d}",
        "trajectory_type": "terminal_aesthetic_gain_interpolated_replay",
        "trajectory_objective": "terminal_aesthetic_gain",
        "planning_method": "measured_endpoint_single_arc_pose_interpolation",
        "direction": "high_score_start_to_global_max",
        "family_id": f"family_{family_number:02d}",
        "variant_index": variant_number,
        "keyframe_count": len(frames),
        "duration_sec": round(float(frames[-1]["time_sec"]), 6),
        "time_step_sec": args.time_step_sec,
        "real_control_point_count": 2,
        "interpolated_keyframe_count": len(frames) - 2,
        "start_score": round(start.raw_score, 6),
        "end_score": round(target.raw_score, 6),
        "terminal_gain": round(target.raw_score - start.raw_score, 6),
        "target_is_global_max": True,
        "intermediate_monotonicity_required": False,
        "intermediate_scores_measured": False,
        "all_interpolated_score_fields_null": True,
        "start_anchor_node_id": start.node_id,
        "target_anchor_node_id": target.node_id,
        "curve_parameters": {key: round(float(value), 6) for key, value in parameters.items()},
        "physical_curve_signature_1cm": curve_signature(positions, args.signature_quantization_m),
        "pose_curve_signature_exact": pose_curve_signature(frames),
        **metrics,
        "score_semantics": {
            "start_and_end": "measured EZCAM still scores",
            "interpolated_replay_keyframes": "unscored; score and raw_score are null",
            "video_frame_monotonicity": "not required for this terminal-gain dataset",
        },
        "capture_status": {
            "collision_clearance_verified": False,
            "replay_video_captured": False,
            "intermediate_video_frames_rescored": False,
        },
        "keyframes": frames,
    }


def stats(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=np.float64)
    return {
        "min": round(float(np.min(array)), 6),
        "mean": round(float(np.mean(array)), 6),
        "median": round(float(np.median(array)), 6),
        "max": round(float(np.max(array)), 6),
    }


def validate(
    trajectories: list[dict[str, Any]],
    starts: list[base.Node],
    target: base.Node,
    args: argparse.Namespace,
) -> dict[str, Any]:
    failures = Counter()
    family_counts = Counter()
    ids = set()
    exact_signatures = set()
    signatures_1cm = set()
    start_ids = {node.node_id for node in starts}
    target_pose = (target.x, target.y, target.z, target.yaw, target.pitch)

    for trajectory in trajectories:
        frames = trajectory["keyframes"]
        family_counts[trajectory["family_id"]] += 1
        ids.add(trajectory["trajectory_id"])
        exact_signatures.add(trajectory["pose_curve_signature_exact"])
        signatures_1cm.add(trajectory["physical_curve_signature_1cm"])
        if len(frames) != args.keyframe_count:
            failures["keyframe_count"] += 1
        if [frame["step"] for frame in frames] != list(range(args.keyframe_count)):
            failures["step_sequence"] += 1
        if not all(
            float(first["time_sec"]) < float(second["time_sec"])
            for first, second in zip(frames, frames[1:])
        ):
            failures["time_not_strict"] += 1
        if frames[0]["node_id"] not in start_ids or frames[0]["score_source"] != "ezcam_scored_still":
            failures["start_provenance"] += 1
        if frames[-1]["node_id"] != target.node_id or frames[-1]["score_source"] != "ezcam_scored_still":
            failures["target_provenance"] += 1
        observed_target_pose = tuple(float(frames[-1][key]) for key in ("x", "y", "z", "yaw", "pitch"))
        if any(abs(first - second) > 1e-8 for first, second in zip(observed_target_pose, target_pose)):
            failures["target_pose"] += 1
        if abs(float(frames[-1]["score"]) - target.raw_score) > 1e-6:
            failures["target_score"] += 1
        for frame in frames[1:-1]:
            if frame["score"] is not None or frame["raw_score"] is not None or frame["estimated_score"] is not None:
                failures["interpolated_score_not_null"] += 1
            if frame["score_source"] != "unscored_interpolation" or frame["node_id"] is not None:
                failures["interpolated_provenance"] += 1
        if trajectory["path_to_net_ratio"] > 1.08 + 1e-6:
            failures["path_to_net_ratio"] += 1
        if trajectory["target_distance_total_increase"] > 1e-5:
            failures["target_distance_backtrack"] += 1

    expected_minimum = len(trajectories) // len(starts)
    expected_maximum = math.ceil(len(trajectories) / len(starts))
    checks = {
        "trajectory_count_is_requested": len(trajectories) == args.num_trajectories,
        "all_eligible_start_families_used": len(family_counts) == len(starts),
        "families_balanced": all(
            expected_minimum <= value <= expected_maximum for value in family_counts.values()
        ),
        "trajectory_ids_unique": len(ids) == len(trajectories),
        "pose_curves_exactly_unique": len(exact_signatures) == len(trajectories),
        "physical_curves_unique_at_1cm_quantization": len(signatures_1cm) == len(trajectories),
        "all_end_at_single_global_max": not bool(failures.get("target_pose") or failures.get("target_score")),
        "only_real_endpoints_have_measured_scores": not bool(
            failures.get("start_provenance")
            or failures.get("target_provenance")
            or failures.get("interpolated_score_not_null")
            or failures.get("interpolated_provenance")
        ),
        "all_paths_have_no_sampled_target_distance_backtrack": not bool(failures.get("target_distance_backtrack")),
        "all_path_to_net_ratios_at_most_1p08": not bool(failures.get("path_to_net_ratio")),
        "all_structural_checks_pass": not bool(
            failures.get("keyframe_count") or failures.get("step_sequence") or failures.get("time_not_strict")
        ),
    }
    return {
        "validation_passed": all(checks.values()) and not failures,
        "checks": checks,
        "failure_counts": dict(failures),
        "trajectory_count": len(trajectories),
        "family_distribution": dict(family_counts),
        "unique_exact_pose_curve_count": len(exact_signatures),
        "unique_1cm_physical_curve_count": len(signatures_1cm),
        "global_max": {
            "node_id": target.node_id,
            "score": round(target.raw_score, 6),
            "x": target.x,
            "y": target.y,
            "z": target.z,
            "yaw": target.yaw,
            "pitch": target.pitch,
        },
        "score_semantics": {
            "measured_frames_per_trajectory": 2,
            "unscored_interpolated_frames_per_trajectory": args.keyframe_count - 2,
            "intermediate_monotonicity_required": False,
        },
        "remaining_external_validation": [
            "Current capture UI runtime was not available in this workspace for an end-to-end load test.",
            "Collision clearance is unproven without a scene collision mesh or smoke capture.",
            "Interpolated video frames are unscored until replay capture and optional rescoring.",
        ],
    }


def ui_payload(trajectories: list[dict[str, Any]], scene_id: str) -> dict[str, Any]:
    return {
        "version": 1,
        "scene_id": scene_id,
        "trajectory_type": "terminal_aesthetic_gain_interpolated_replay",
        "trajectory_count": len(trajectories),
        "coordinate_system": {
            "position_axes": "RE9 world x,y,z",
            "vertical_axis": "y",
            "yaw_unit": "degrees",
            "pitch_unit": "degrees",
        },
        "score_policy": {
            "real_start_and_end": "measured EZCAM still scores",
            "interpolated_keyframes": "null",
        },
        "trajectories": [
            {
                "trajectory_id": trajectory["trajectory_id"],
                "direction": trajectory["direction"],
                "keyframe_count": trajectory["keyframe_count"],
                "duration_sec": trajectory["duration_sec"],
                "keyframes": [
                    {
                        key: frame[key]
                        for key in ("step", "time_sec", "x", "y", "z", "yaw", "pitch", "fov", "score")
                    }
                    for frame in trajectory["keyframes"]
                ],
            }
            for trajectory in trajectories
        ],
    }


def write_summary(output_dir: Path, trajectories: list[dict[str, Any]]) -> None:
    fields = [
        "trajectory_id",
        "family_id",
        "variant_index",
        "start_score",
        "end_score",
        "terminal_gain",
        "keyframe_count",
        "real_control_point_count",
        "interpolated_keyframe_count",
        "duration_sec",
        "path_xyz_length",
        "net_xyz_distance",
        "path_to_net_ratio",
        "max_xyz_step",
        "max_speed_mps",
        "max_abs_tangential_acceleration_mps2",
        "max_yaw_step_deg",
        "max_pitch_step_deg",
        "max_sampled_turn_deg",
        "target_distance_total_increase",
        "start_anchor_node_id",
        "target_anchor_node_id",
        "physical_curve_signature_1cm",
        "pose_curve_signature_exact",
    ]
    with (output_dir / "trajectory_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for trajectory in trajectories:
            writer.writerow({field: trajectory[field] for field in fields})


def family_colors(trajectories: list[dict[str, Any]]) -> dict[str, Any]:
    family_ids = sorted({trajectory["family_id"] for trajectory in trajectories})
    palette = plt.get_cmap("tab10")(np.linspace(0.0, 0.9, len(family_ids)))
    return dict(zip(family_ids, palette))


def draw_xyz_paths(
    output_dir: Path,
    trajectories: list[dict[str, Any]],
    target: base.Node,
    scene_id: str,
) -> None:
    figure = plt.figure(figsize=(13, 9), constrained_layout=True)
    axis = figure.add_subplot(111, projection="3d")
    colors = family_colors(trajectories)
    all_xyz = []
    for trajectory in trajectories:
        xyz = np.array([[frame["x"], frame["z"], frame["y"]] for frame in trajectory["keyframes"]])
        all_xyz.append(xyz)
        axis.plot(
            xyz[:, 0],
            xyz[:, 1],
            xyz[:, 2],
            color=colors[trajectory["family_id"]],
            linewidth=0.65,
            alpha=0.12,
        )
    family_counts = Counter(trajectory["family_id"] for trajectory in trajectories)
    for family_id, color in colors.items():
        axis.plot([], [], [], color=color, linewidth=2.5, label=f"{family_id} ({family_counts[family_id]} curves)")
    axis.scatter(
        [target.x],
        [target.z],
        [target.y],
        marker="*",
        s=220,
        color="#d62728",
        label=f"global max {target.raw_score:.3f}",
    )
    axis.set_title(
        f"{scene_id} terminal-gain candidate curves (all {len(trajectories)})\n"
        "Curves are unique at 1 cm quantization; shared endpoint is intentional"
    )
    axis.set_xlabel("world x")
    axis.set_ylabel("world z")
    axis.set_zlabel("world y (height)")
    stacked = np.concatenate(all_xyz, axis=0)
    ranges = np.maximum(np.ptp(stacked, axis=0), np.array([0.25, 0.25, 0.25]))
    axis.set_box_aspect(tuple(ranges))
    axis.xaxis.set_major_locator(MaxNLocator(5))
    axis.yaxis.set_major_locator(MaxNLocator(5))
    axis.zaxis.set_major_locator(MaxNLocator(5))
    axis.tick_params(labelsize=8, pad=1)
    axis.view_init(elev=24, azim=-57)
    axis.legend(loc="upper left")
    figure.savefig(
        output_dir / f"xyz_paths_all_{len(trajectories)}.png",
        dpi=190,
        bbox_inches="tight",
    )
    plt.close(figure)


def sampled_trajectories(trajectories: list[dict[str, Any]], per_family: int = 5) -> list[dict[str, Any]]:
    selected = []
    for family_id in sorted({trajectory["family_id"] for trajectory in trajectories}):
        family = [trajectory for trajectory in trajectories if trajectory["family_id"] == family_id]
        indices = np.linspace(0, len(family) - 1, per_family).round().astype(int)
        selected.extend(family[int(index)] for index in indices)
    return selected


def smoke10_trajectories(trajectories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = []
    family_ids = sorted({trajectory["family_id"] for trajectory in trajectories})
    base_count, remainder = divmod(10, len(family_ids))
    for family_index, family_id in enumerate(family_ids):
        count = base_count + (1 if family_index < remainder else 0)
        family = [trajectory for trajectory in trajectories if trajectory["family_id"] == family_id]
        indices = np.linspace(0, len(family) - 1, count).round().astype(int)
        selected.extend(family[int(index)] for index in indices)
    return selected


def draw_sampled_xyz(output_dir: Path, trajectories: list[dict[str, Any]]) -> None:
    selected = sampled_trajectories(trajectories)
    family_count = len({trajectory["family_id"] for trajectory in selected})
    figure = plt.figure(figsize=(20, 5.5 * family_count), constrained_layout=True)
    figure.set_constrained_layout_pads(
        w_pad=0.08,
        h_pad=0.35,
        wspace=0.05,
        hspace=0.35,
    )
    for plot_index, trajectory in enumerate(selected, start=1):
        axis = figure.add_subplot(family_count, 5, plot_index, projection="3d")
        frames = trajectory["keyframes"]
        x = np.array([frame["x"] for frame in frames])
        y = np.array([frame["y"] for frame in frames])
        z = np.array([frame["z"] for frame in frames])
        sample_indices = np.array([0, len(frames) // 2, len(frames) - 1])
        forward = np.array(
            [core.camera_forward(float(frames[index]["yaw"]), float(frames[index]["pitch"])) for index in sample_indices]
        )
        axis.plot(x, z, y, color="#1769aa", linewidth=2.5)
        axis.scatter([x[0]], [z[0]], [y[0]], marker="s", s=48, color="#2ca02c")
        axis.scatter([x[-1]], [z[-1]], [y[-1]], marker="*", s=95, color="#d62728")
        net = float(trajectory["net_xyz_distance"])
        arrow_length = min(0.10, max(0.045, 0.05 * net))
        axis.quiver(
            x[sample_indices],
            z[sample_indices],
            y[sample_indices],
            forward[:, 0],
            forward[:, 2],
            forward[:, 1],
            length=arrow_length,
            normalize=True,
            color="#e67e22",
            linewidth=0.9,
        )
        axis.set_title(
            f"{trajectory['family_id']} v{trajectory['variant_index']}\n"
            f"gain {trajectory['terminal_gain']:.3f}, length {trajectory['path_xyz_length']:.2f} m",
            fontsize=9,
        )
        axis.set_xlabel("x", fontsize=8)
        axis.set_ylabel("z", fontsize=8)
        axis.set_zlabel("y", fontsize=8)
        spans = np.maximum(
            np.array([float(np.ptp(x)), float(np.ptp(z)), float(np.ptp(y))]),
            np.array([0.25, 0.25, 0.25]),
        )
        centers = np.array([float(np.mean([np.min(x), np.max(x)])), float(np.mean([np.min(z), np.max(z)])), float(np.mean([np.min(y), np.max(y)]))])
        axis.set_xlim(centers[0] - 0.6 * spans[0], centers[0] + 0.6 * spans[0])
        axis.set_ylim(centers[1] - 0.6 * spans[1], centers[1] + 0.6 * spans[1])
        axis.set_zlim(centers[2] - 0.6 * spans[2], centers[2] + 0.6 * spans[2])
        axis.set_box_aspect(tuple(spans))
        axis.xaxis.set_major_locator(MaxNLocator(3))
        axis.yaxis.set_major_locator(MaxNLocator(3))
        axis.zaxis.set_major_locator(MaxNLocator(3))
        axis.tick_params(labelsize=5, pad=0)
        axis.view_init(elev=24, azim=-57)
    figure.suptitle(
        f"{len(selected)} sampled terminal-gain curves: green=start, red=global max, orange=camera view",
        fontsize=16,
    )
    figure.savefig(
        output_dir / f"xyz_paths_sample{len(selected)}_with_camera_orientation.png",
        dpi=170,
        bbox_inches="tight",
    )
    plt.close(figure)


def draw_distance(output_dir: Path, trajectories: list[dict[str, Any]]) -> None:
    figure, axis = plt.subplots(figsize=(13, 8), constrained_layout=True)
    colors = family_colors(trajectories)
    for trajectory in trajectories:
        frames = trajectory["keyframes"]
        xyz = np.array([[frame["x"], frame["y"], frame["z"]] for frame in frames])
        cumulative = np.concatenate(([0.0], np.cumsum(np.linalg.norm(np.diff(xyz, axis=0), axis=1))))
        axis.plot(
            [frame["time_sec"] for frame in frames],
            cumulative,
            color=colors[trajectory["family_id"]],
            linewidth=0.65,
            alpha=0.12,
        )
    for family_id, color in colors.items():
        axis.plot([], [], color=color, linewidth=2.5, label=family_id)
    axis.set_title(
        f"Physical cumulative distance vs time (all {len(trajectories)} candidate curves)"
    )
    axis.set_xlabel("time_sec")
    axis.set_ylabel("physical cumulative distance (m)")
    axis.grid(True, alpha=0.35)
    axis.legend()
    figure.savefig(
        output_dir / f"physical_distance_vs_time_all_{len(trajectories)}.png",
        dpi=190,
        bbox_inches="tight",
    )
    plt.close(figure)


def draw_endpoint_scores(output_dir: Path, trajectories: list[dict[str, Any]]) -> None:
    figure, axis = plt.subplots(figsize=(11, 7), constrained_layout=True)
    family_seen = set()
    colors = family_colors(trajectories)
    for trajectory in trajectories:
        family_id = trajectory["family_id"]
        axis.plot(
            [0, 1],
            [trajectory["start_score"], trajectory["end_score"]],
            color=colors[family_id],
            linewidth=1.0,
            alpha=0.18,
            label=family_id if family_id not in family_seen else None,
        )
        family_seen.add(family_id)
    axis.set_xticks([0, 1], ["measured start", "measured global-max endpoint"])
    axis.set_ylabel("measured EZCAM score")
    axis.set_title("Measured endpoint gains\nIntermediate replay keyframes are intentionally unscored")
    axis.grid(True, alpha=0.35)
    axis.legend()
    figure.savefig(output_dir / "measured_endpoint_score_gains.png", dpi=190, bbox_inches="tight")
    plt.close(figure)


def write_design(output_dir: Path, validation: dict[str, Any], args: argparse.Namespace) -> None:
    family_count = len(validation["family_distribution"])
    text = f"""# {args.scene_id} Terminal-Gain Interpolated Preview

This is an experimental replay candidate set. It does not replace any accepted trajectory directory.

## Objective

- Generate {args.num_trajectories} short, smooth candidate motions from measured high-score starts.
- Every trajectory ends at the single measured global maximum, score {validation['global_max']['score']:.6f}.
- Intermediate aesthetic monotonicity is not required.

## Score provenance

- Exactly two frames per trajectory carry measured scores: the real start and real endpoint.
- All {args.keyframe_count - 2} materialized interpolation frames have `score=null`, `raw_score=null`, and `estimated_score=null`.
- No interpolated value is presented as measured aesthetic evidence.

## Geometry

- All {family_count} eligible measured start families inside the configured radius are used.
- Family counts differ by at most one trajectory.
- One non-oscillating smooth arc per trajectory.
- Maximum configured lateral offset: {args.maximum_lateral_offset_m:.2f} m, additionally limited to {args.maximum_lateral_ratio:.2f} of endpoint distance.
- {args.keyframe_count} replay keyframes at {args.time_step_sec:.2f} seconds per step.
- Exact pose curves and 1 cm quantized physical curves are both unique.

## Required smoke checks

- The current capture UI source is unavailable in this workspace, so runtime loading is not proven here.
- Collision clearance is not proven without a collision mesh or game smoke capture.
- Intermediate video aesthetics are unscored and intentionally not assumed monotonic.
"""
    (output_dir / "DESIGN.md").write_text(text, encoding="utf-8")


def main() -> None:
    args = parse_args()
    if args.keyframe_count < 8:
        raise ValueError("keyframe-count must be at least eight")
    args.output_dir.mkdir(parents=True, exist_ok=False)

    source_nodes = base.load_nodes(args.scene_id, args.scored_jsonl, args.extra_scored_jsonl)
    target_index = max(range(len(source_nodes)), key=lambda index: source_nodes[index].raw_score)
    target = source_nodes[target_index]
    start_indices, source_diagnostics = select_start_anchors(source_nodes, target_index, args)
    starts = [source_nodes[index] for index in start_indices]
    if args.num_trajectories < len(starts):
        raise ValueError("num-trajectories must be at least the eligible start-anchor count")

    trajectories = []
    signatures_1cm = set()
    base_family_count, family_remainder = divmod(args.num_trajectories, len(starts))
    family_targets = [
        base_family_count + (1 if family_index < family_remainder else 0)
        for family_index in range(len(starts))
    ]
    candidate_attempts = Counter()
    for family_number, (start, variants_per_family) in enumerate(
        zip(starts, family_targets), start=1
    ):
        net = math.dist((start.x, start.y, start.z), (target.x, target.y, target.z))
        maximum_offset = min(args.maximum_lateral_offset_m, args.maximum_lateral_ratio * net)
        accepted = 0
        candidate_index = 0
        while accepted < variants_per_family:
            if candidate_index > 100000:
                raise RuntimeError(f"Could not fill family {family_number}")
            candidate_attempts[f"family_{family_number:02d}"] += 1
            parameters = curve_parameters(candidate_index, family_number, maximum_offset, args)
            frames = materialize_curve(start, target, parameters, args)
            positions = np.array([[frame["x"], frame["y"], frame["z"]] for frame in frames])
            signature = curve_signature(positions, args.signature_quantization_m)
            candidate_index += 1
            if signature in signatures_1cm:
                continue
            metrics = motion_metrics(frames, target)
            if metrics["path_to_net_ratio"] > 1.08 + 1e-12:
                continue
            if metrics["target_distance_total_increase"] > 1e-5:
                continue
            signatures_1cm.add(signature)
            accepted += 1
            trajectories.append(
                make_trajectory(
                    len(trajectories) + 1,
                    family_number,
                    accepted,
                    start,
                    target,
                    frames,
                    parameters,
                    args,
                )
            )

    validation = validate(trajectories, starts, target, args)
    if not validation["validation_passed"]:
        raise RuntimeError(f"Validation failed: {validation}")

    diagnostics = {
        "scene_id": args.scene_id,
        "purpose": "high_score_start_terminal_gain_to_single_global_max",
        "generation_status": "candidate_set_requires_smoke_capture",
        "source_scored_pose_count": len(source_nodes),
        "anchor_selection_diagnostics": source_diagnostics,
        "start_anchors": [
            {
                "family_id": f"family_{number:02d}",
                "node_id": node.node_id,
                "score": round(node.raw_score, 6),
                "x": node.x,
                "y": node.y,
                "z": node.z,
                "yaw": node.yaw,
                "pitch": node.pitch,
                "terminal_gain": round(target.raw_score - node.raw_score, 6),
            }
            for number, node in enumerate(starts, start=1)
        ],
        "global_max": validation["global_max"],
        "generation_parameters": {
            key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()
        },
        "candidate_attempts": dict(candidate_attempts),
        "statistics": {
            "terminal_gain": stats([trajectory["terminal_gain"] for trajectory in trajectories]),
            "path_xyz_length": stats([trajectory["path_xyz_length"] for trajectory in trajectories]),
            "net_xyz_distance": stats([trajectory["net_xyz_distance"] for trajectory in trajectories]),
            "path_to_net_ratio": stats([trajectory["path_to_net_ratio"] for trajectory in trajectories]),
            "max_xyz_step": stats([trajectory["max_xyz_step"] for trajectory in trajectories]),
            "max_speed_mps": stats([trajectory["max_speed_mps"] for trajectory in trajectories]),
            "max_yaw_step_deg": stats([trajectory["max_yaw_step_deg"] for trajectory in trajectories]),
            "max_pitch_step_deg": stats([trajectory["max_pitch_step_deg"] for trajectory in trajectories]),
        },
        "score_semantics": validation["score_semantics"],
    }
    full_payload = {
        "version": 1,
        "scene_id": args.scene_id,
        "trajectory_type": "terminal_aesthetic_gain_interpolated_replay",
        "trajectory_count": len(trajectories),
        "dataset_status": "experimental_candidate_set_requires_smoke_capture",
        "trajectory_objective": "terminal_aesthetic_gain",
        "intermediate_monotonicity_required": False,
        "score_policy": validation["score_semantics"],
        "global_max": validation["global_max"],
        "trajectories": trajectories,
    }
    ui = ui_payload(trajectories, args.scene_id)
    smoke = smoke10_trajectories(trajectories)
    smoke_ui = ui_payload(smoke, args.scene_id)
    smoke_ui["trajectory_count"] = len(smoke)

    stem = f"{args.scene_id.replace('.', '_')}_terminalgain_globalmax_interp_{len(trajectories)}"
    (args.output_dir / f"{stem}_trajectories.json").write_text(
        json.dumps(full_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output_dir / f"{stem}_low_to_high_ui.json").write_text(
        json.dumps(ui, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output_dir / f"{args.scene_id.replace('.', '_')}_terminalgain_globalmax_interp_smoke10_low_to_high_ui.json").write_text(
        json.dumps(smoke_ui, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output_dir / "generation_diagnostics.json").write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output_dir / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_summary(args.output_dir, trajectories)
    write_design(args.output_dir, validation, args)
    draw_xyz_paths(args.output_dir, trajectories, target, args.scene_id)
    draw_sampled_xyz(args.output_dir, trajectories)
    draw_distance(args.output_dir, trajectories)
    draw_endpoint_scores(args.output_dir, trajectories)
    print(json.dumps({"output_dir": str(args.output_dir), **validation}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
