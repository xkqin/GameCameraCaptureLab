from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAN_ROOT = PROJECT_ROOT / "data" / "reconstruction_capture_plans"
OUTPUT_ROOT = PROJECT_ROOT / "data" / "reconstruction_video_trajectories"

SCENE_IDS = ("scene_3.1", "scene_3.2")
NOMINAL_SPEED = 4.0
MAX_SEGMENT_SECONDS = 180.0
SOURCE_RECORDING_FPS = 60.0
TARGET_EXTRACT_SPACING = 0.8
MAX_EXTRACT_SPACING = 1.0


def _round(value: float) -> float:
    return round(float(value), 9)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _distance(left: dict[str, Any], right: dict[str, Any]) -> float:
    return math.sqrt(
        (float(right["x"]) - float(left["x"])) ** 2
        + (float(right["y"]) - float(left["y"])) ** 2
        + (float(right["z"]) - float(left["z"])) ** 2
    )


def _dominant_axis(positions: list[dict[str, Any]]) -> str:
    x_travel = sum(abs(float(right["x"]) - float(left["x"])) for left, right in zip(positions, positions[1:]))
    z_travel = sum(abs(float(right["z"]) - float(left["z"])) for left, right in zip(positions, positions[1:]))
    return "X" if x_travel >= z_travel else "Z"


def _logical_route(
    scene_id: str,
    positions: list[dict[str, Any]],
    pitch: float,
    direct_down: bool,
) -> dict[str, Any]:
    layer_id = str(positions[0]["layer_id"])
    layer_label = layer_id.rsplit("_", 1)[-1]
    suffix = "direct_down" if direct_down else f"pitch_{str(pitch).replace('-', 'm').replace('.', 'p')}"
    route_id = f"{scene_id.replace('.', '_')}_reconstruction_{layer_label}_{suffix}"

    keyframes: list[dict[str, Any]] = []
    cumulative_distance = 0.0
    for index, position in enumerate(positions):
        distance_from_previous = 0.0 if index == 0 else _distance(positions[index - 1], position)
        cumulative_distance += distance_from_previous
        keyframes.append(
            {
                "step": index,
                "time_sec": _round(cumulative_distance / NOMINAL_SPEED),
                "x": _round(position["x"]),
                "y": _round(position["y"]),
                "z": _round(position["z"]),
                "yaw": _round(position["camera_route_yaw_deg"]),
                "pitch": _round(pitch),
                "fov": None,
                "score": None,
                "source_position_index": int(position["position_index"]),
                "source_layer_position_index": int(position["layer_position_index"]),
                "route_time_sec": _round(cumulative_distance / NOMINAL_SPEED),
                "route_cumulative_distance": _round(cumulative_distance),
                "distance_from_previous": _round(distance_from_previous),
                "legacy_anchor": bool(position["legacy_anchor"]),
                "route_bridge": bool(position["route_bridge"]),
                "synthetic_segment_boundary": False,
            }
        )

    return {
        "logical_trajectory_id": route_id,
        "scene_id": scene_id,
        "layer_id": layer_id,
        "height_index": int(positions[0]["height_index"]),
        "view_mode": "terrain_direct_down" if direct_down else "route_forward_oblique",
        "dominant_scan_axis": _dominant_axis(positions),
        "pitch_deg": _round(pitch),
        "path_length_game_units": _round(cumulative_distance),
        "duration_sec": _round(cumulative_distance / NOMINAL_SPEED),
        "source_keyframe_count": len(keyframes),
        "keyframes": keyframes,
    }


def _interpolate_angle(start: float, end: float, alpha: float) -> float:
    delta = (end - start + 180.0) % 360.0 - 180.0
    return (start + delta * alpha) % 360.0


def _frame_at_time(route: dict[str, Any], target_time: float) -> dict[str, Any]:
    frames = route["keyframes"]
    if target_time <= 0.0:
        return dict(frames[0])
    if target_time >= float(frames[-1]["time_sec"]):
        return dict(frames[-1])

    right_index = next(index for index, frame in enumerate(frames) if float(frame["time_sec"]) >= target_time)
    right = frames[right_index]
    if math.isclose(float(right["time_sec"]), target_time, abs_tol=1e-9):
        return dict(right)
    left = frames[right_index - 1]
    span = float(right["time_sec"]) - float(left["time_sec"])
    alpha = (target_time - float(left["time_sec"])) / span
    route_distance = target_time * NOMINAL_SPEED
    return {
        "step": -1,
        "time_sec": _round(target_time),
        "x": _round(float(left["x"]) + (float(right["x"]) - float(left["x"])) * alpha),
        "y": _round(float(left["y"]) + (float(right["y"]) - float(left["y"])) * alpha),
        "z": _round(float(left["z"]) + (float(right["z"]) - float(left["z"])) * alpha),
        "yaw": _round(_interpolate_angle(float(left["yaw"]), float(right["yaw"]), alpha)),
        "pitch": _round(route["pitch_deg"]),
        "fov": None,
        "score": None,
        "source_position_index": None,
        "source_layer_position_index": None,
        "route_time_sec": _round(target_time),
        "route_cumulative_distance": _round(route_distance),
        "distance_from_previous": 0.0,
        "legacy_anchor": False,
        "route_bridge": bool(left["route_bridge"] or right["route_bridge"]),
        "synthetic_segment_boundary": True,
    }


def _segment_route(route: dict[str, Any]) -> list[dict[str, Any]]:
    duration = float(route["duration_sec"])
    segment_count = max(1, math.ceil(duration / MAX_SEGMENT_SECONDS))
    boundaries = [duration * index / segment_count for index in range(segment_count + 1)]
    source_frames = route["keyframes"]
    segments: list[dict[str, Any]] = []

    for segment_index, (start_time, end_time) in enumerate(zip(boundaries, boundaries[1:]), start=1):
        route_frames = [_frame_at_time(route, start_time)]
        route_frames.extend(
            dict(frame)
            for frame in source_frames
            if start_time < float(frame["time_sec"]) < end_time
        )
        route_frames.append(_frame_at_time(route, end_time))

        segment_frames: list[dict[str, Any]] = []
        segment_distance = 0.0
        for frame_index, frame in enumerate(route_frames):
            distance_from_previous = 0.0 if frame_index == 0 else _distance(route_frames[frame_index - 1], frame)
            segment_distance += distance_from_previous
            item = dict(frame)
            item["step"] = frame_index
            item["time_sec"] = _round(float(frame["route_time_sec"]) - start_time)
            item["distance_from_previous"] = _round(distance_from_previous)
            item["segment_cumulative_distance"] = _round(segment_distance)
            segment_frames.append(item)

        segment_id = f"{route['logical_trajectory_id']}_segment_{segment_index:02d}_of_{segment_count:02d}"
        segments.append(
            {
                "trajectory_id": segment_id,
                "logical_trajectory_id": route["logical_trajectory_id"],
                "scene_id": route["scene_id"],
                "layer_id": route["layer_id"],
                "height_index": route["height_index"],
                "view_mode": route["view_mode"],
                "dominant_scan_axis": route["dominant_scan_axis"],
                "pitch_deg": route["pitch_deg"],
                "segment_index": segment_index,
                "segment_count": segment_count,
                "shared_boundary_with_previous": segment_index > 1,
                "shared_boundary_with_next": segment_index < segment_count,
                "route_start_time_sec": _round(start_time),
                "route_end_time_sec": _round(end_time),
                "route_start_distance_game_units": _round(start_time * NOMINAL_SPEED),
                "route_end_distance_game_units": _round(end_time * NOMINAL_SPEED),
                "path_length_game_units": _round(segment_distance),
                "duration_sec": _round(end_time - start_time),
                "keyframe_count": len(segment_frames),
                "keyframes": segment_frames,
            }
        )
    return segments


def _build_scene(scene_id: str) -> dict[str, Any]:
    manifest_path = PLAN_ROOT / scene_id / f"{scene_id}_reconstruction_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_layer: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for position in manifest["positions"]:
        by_layer[str(position["layer_id"])].append(position)

    logical_routes: list[dict[str, Any]] = []
    layer_order = [str(layer["layer_id"]) for layer in manifest["layers"]]
    pitch_by_layer = manifest["capture"]["pitch_by_layer_deg"]
    for layer_id in layer_order:
        logical_routes.append(
            _logical_route(
                scene_id,
                by_layer[layer_id],
                float(pitch_by_layer[layer_id]),
                direct_down=False,
            )
        )

    direct_down_heights = set(manifest["capture"]["direct_down_height_indexes"])
    direct_down_pitch = float(manifest["capture"]["direct_down_pitch_deg"])
    for layer in manifest["layers"]:
        layer_id = str(layer["layer_id"])
        if int(by_layer[layer_id][0]["height_index"]) in direct_down_heights:
            logical_routes.append(
                _logical_route(
                    scene_id,
                    by_layer[layer_id],
                    direct_down_pitch,
                    direct_down=True,
                )
            )

    segments = [segment for route in logical_routes for segment in _segment_route(route)]
    logical_index = []
    for route in logical_routes:
        route_segments = [
            segment["trajectory_id"]
            for segment in segments
            if segment["logical_trajectory_id"] == route["logical_trajectory_id"]
        ]
        logical_index.append(
            {
                key: value
                for key, value in route.items()
                if key != "keyframes"
            }
            | {
                "capture_segment_count": len(route_segments),
                "capture_segment_ids": route_segments,
            }
        )

    return {
        "version": 1,
        "scene_id": scene_id,
        "trajectory_type": "re9_3dgs_dense_oblique_video_capture_segments",
        "purpose": "Strict translated video routes for pose-distance frame extraction and 3DGS reconstruction",
        "logical_trajectory_count": len(logical_routes),
        "trajectory_count": len(segments),
        "coordinate_system": {
            "position_axes": "RE9 world x,y,z",
            "vertical_axis": "y",
            "position_unit": "RE9 game unit",
            "yaw_unit": "degrees",
            "pitch_unit": "degrees",
            "camera_yaw_convention": "yaw 0 camera-forward is world -Z",
        },
        "source": {
            "manifest": manifest_path.relative_to(PROJECT_ROOT).as_posix(),
            "manifest_sha256": _sha256(manifest_path),
            "source_position_count": len(manifest["positions"]),
            "max_source_route_step_game_units": manifest["metrics"]["max_intralayer_step"],
        },
        "playback_policy": {
            "nominal_speed_game_units_per_sec": NOMINAL_SPEED,
            "source_keyframes_define_the_route": True,
            "preserve_every_source_pose": True,
            "smooth_playback": True,
            "playback_hz": SOURCE_RECORDING_FPS,
            "segment_max_duration_sec": MAX_SEGMENT_SECONDS,
            "segment_boundaries_share_identical_pose": True,
            "fov_policy": "Keep one fixed FreeCam FOV for the complete scene; pose log is authoritative",
        },
        "frame_extraction_policy": {
            "preferred_method": "pose_distance",
            "recording_fps": SOURCE_RECORDING_FPS,
            "target_translation_spacing_game_units": TARGET_EXTRACT_SPACING,
            "maximum_translation_gap_game_units": MAX_EXTRACT_SPACING,
            "fallback_uniform_extract_fps": NOMINAL_SPEED / TARGET_EXTRACT_SPACING,
            "fallback_frame_stride_at_60_fps": int(SOURCE_RECORDING_FPS / (NOMINAL_SPEED / TARGET_EXTRACT_SPACING)),
            "algorithm": (
                "Keep the first in-trajectory frame, then keep the first frame whose pose-log XYZ is at least "
                "0.8 game unit from the last kept frame; always keep the final frame. Remove exact duplicate "
                "frames at shared segment boundaries and exclude static settle/post-roll frames."
            ),
        },
        "logical_trajectories": logical_index,
        "trajectories": segments,
    }


def _validate_scene(payload: dict[str, Any]) -> None:
    if payload["logical_trajectory_count"] != 7:
        raise ValueError(f"{payload['scene_id']}: expected seven logical routes")
    trajectories = payload["trajectories"]
    if payload["trajectory_count"] != len(trajectories):
        raise ValueError(f"{payload['scene_id']}: trajectory count mismatch")
    for trajectory in trajectories:
        frames = trajectory["keyframes"]
        if len(frames) < 2:
            raise ValueError(f"{trajectory['trajectory_id']}: fewer than two keyframes")
        if float(trajectory["duration_sec"]) > MAX_SEGMENT_SECONDS + 1e-6:
            raise ValueError(f"{trajectory['trajectory_id']}: segment exceeds maximum duration")
        if not math.isclose(float(frames[0]["time_sec"]), 0.0, abs_tol=1e-9):
            raise ValueError(f"{trajectory['trajectory_id']}: first keyframe does not start at zero")
        times = [float(frame["time_sec"]) for frame in frames]
        if any(right <= left for left, right in zip(times, times[1:])):
            raise ValueError(f"{trajectory['trajectory_id']}: keyframe times are not strictly increasing")
        if not math.isclose(
            float(trajectory["path_length_game_units"]),
            float(trajectory["duration_sec"]) * NOMINAL_SPEED,
            abs_tol=1e-5,
        ):
            raise ValueError(f"{trajectory['trajectory_id']}: route is not timed at the nominal speed")


def _write_readme(scene_payloads: list[dict[str, Any]]) -> None:
    rows = "\n".join(
        f"| {payload['scene_id']} | {payload['logical_trajectory_count']} | {payload['trajectory_count']} | "
        f"{sum(item['path_length_game_units'] for item in payload['logical_trajectories']):,.1f} | "
        f"{sum(item['duration_sec'] for item in payload['logical_trajectories']) / 60.0:.1f} min |"
        for payload in scene_payloads
    )
    readme = f"""# RE9 Reconstruction Video Trajectories

This folder contains the strict Scene 3.1 and Scene 3.2 video routes used for pose-distance frame extraction. It is separate from aesthetic trajectory exports and static reconstruction manifests.

| Scene | Logical routes | Capture segments | Total distance | Nominal time at 4 units/s |
|---|---:|---:|---:|---:|
{rows}

Each scene has five layer-wise serpentine routes at `0`, `-10`, `-25`, `-40`, and `-55` degrees, plus repeated Y04 and Y05 routes at `-82` degrees. Long routes are evenly divided into at most 180-second capture segments. Adjacent segments share the exact same boundary pose.

Use the `trajectories` array directly with the existing RE9 trajectory loader. The `logical_trajectories` array explains how capture segments belong to the seven complete routes.

For reconstruction, record at 60 FPS and extract by pose-log translation: retain a frame after approximately 0.8 game unit of movement, cap gaps at 1.0 unit, remove exact shared-boundary duplicates, and exclude settle/post-roll frames. At the nominal speed this is equivalent to about 5 FPS or every 12th frame.

Regenerate deterministically with:

```powershell
python scripts/generate_reconstruction_video_trajectories.py
```
"""
    (OUTPUT_ROOT / "README.md").write_text(readme, encoding="ascii")


def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    payloads = []
    for scene_id in SCENE_IDS:
        payload = _build_scene(scene_id)
        _validate_scene(payload)
        output_path = OUTPUT_ROOT / f"{scene_id}_reconstruction_video_trajectories.json"
        output_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        payloads.append(payload)

    summary = {
        "version": 1,
        "scene_count": len(payloads),
        "logical_trajectory_count": sum(item["logical_trajectory_count"] for item in payloads),
        "capture_segment_count": sum(item["trajectory_count"] for item in payloads),
        "nominal_speed_game_units_per_sec": NOMINAL_SPEED,
        "target_extract_spacing_game_units": TARGET_EXTRACT_SPACING,
        "scenes": [
            {
                "scene_id": item["scene_id"],
                "file": f"{item['scene_id']}_reconstruction_video_trajectories.json",
                "logical_trajectory_count": item["logical_trajectory_count"],
                "capture_segment_count": item["trajectory_count"],
                "total_path_length_game_units": _round(
                    sum(route["path_length_game_units"] for route in item["logical_trajectories"])
                ),
                "total_duration_sec": _round(
                    sum(route["duration_sec"] for route in item["logical_trajectories"])
                ),
            }
            for item in payloads
        ],
    }
    (OUTPUT_ROOT / "reconstruction_video_trajectory_summary.json").write_text(
        json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8"
    )
    _write_readme(payloads)


if __name__ == "__main__":
    main()
