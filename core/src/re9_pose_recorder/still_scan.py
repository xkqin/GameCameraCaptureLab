from __future__ import annotations

import csv
import json
import math
import shutil
import time
from dataclasses import asdict, dataclass
from functools import lru_cache
from itertools import combinations
from pathlib import Path
from threading import Event
from typing import Any, Callable, Iterable

import yaml

from .config import AppConfig
from .depth_bridge import DepthBridge, DepthCaptureResult
from .lua_control import LuaControl, make_session_id
from .obs_control import OBSController
from .paths import ensure_dir
from .utils import console


@dataclass(frozen=True)
class StillSample:
    sample_index: int
    point_index: int
    group_id: str
    layer_id: str
    zone_id: str
    height_index: int
    pattern: str
    x: float
    y: float
    z: float
    yaw_deg: float
    yaw_rad: float
    pitch_deg: float
    pitch_rad: float


@dataclass(frozen=True)
class StillLayer:
    group_id: str
    layer_id: str
    zone_id: str
    height_index: int
    x_min: float
    x_max: float
    y: float
    z_min: float
    z_max: float
    points_x: int | None = None
    points_z: int | None = None
    hull_points: tuple[tuple[float, float, float], ...] | None = None
    exclude_ellipsoids: tuple[tuple[float, float, float, float, float, float], ...] = ()


BASE_SAMPLE_FIELDS = [
    "session_id",
    "dataset_id",
    "sample_index",
    "point_index",
    "group_id",
    "layer_id",
    "zone_id",
    "height_index",
    "pattern",
    "x",
    "y",
    "z",
    "yaw_deg",
    "yaw_rad",
    "pitch_deg",
    "pitch_rad",
    "source_name",
    "image_path",
    "captured_at_unix",
]

DEPTH_SAMPLE_FIELDS = [
    "capture_id",
    "depth_path",
    "depth_raw_path",
    "depth_preview_path",
    "valid_mask_path",
    "camera_metadata_path",
    "depth_status",
    "depth_width",
    "depth_height",
    "depth_unit",
    "meters_per_game_unit",
    "near_clip",
    "far_clip",
    "projection_matrix",
    "render_frame_id",
    "reversed_z",
    "valid_depth_pixels",
]


def linspace(start: float, stop: float, count: int) -> list[float]:
    if count <= 0:
        raise ValueError("count must be greater than zero.")
    if count == 1:
        return [float(start)]
    step = (stop - start) / float(count - 1)
    return [float(start + step * index) for index in range(count)]


def parse_float_list(value: str | Iterable[float]) -> list[float]:
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
        if not items:
            raise ValueError("Expected at least one numeric value.")
        return [float(item) for item in items]
    return [float(item) for item in value]


def build_still_scan_plan(
    x_min: float,
    x_max: float,
    z_min: float,
    z_max: float,
    y_values: Iterable[float],
    points_x: int = 5,
    points_z: int = 3,
) -> list[StillSample]:
    """Build the 22-shot view set for each sampled x/z/y point."""
    xs = linspace(x_min, x_max, points_x)
    zs = linspace(z_min, z_max, points_z)
    heights = list(y_values)
    if not heights:
        raise ValueError("At least one y value is required.")

    patterns: list[tuple[str, float, list[float]]] = [
        ("middle", 0.0, [index * 45.0 for index in range(8)]),
        ("upper", 45.0, [index * 60.0 for index in range(6)]),
        ("lower", -45.0, [index * 60.0 for index in range(6)]),
        ("ceiling", 90.0, [0.0]),
        ("floor", -90.0, [0.0]),
    ]

    samples: list[StillSample] = []
    sample_index = 1
    for height_index, y in enumerate(heights, start=1):
        point_index = 1
        for z in zs:
            for x in xs:
                for pattern, pitch_deg, yaw_degrees in patterns:
                    for yaw_deg in yaw_degrees:
                        samples.append(
                            StillSample(
                                sample_index=sample_index,
                                point_index=point_index,
                                group_id="full",
                                layer_id=f"y{height_index:02d}",
                                zone_id="full",
                                height_index=height_index,
                                pattern=pattern,
                                x=x,
                                y=y,
                                z=z,
                                yaw_deg=yaw_deg,
                                yaw_rad=math.radians(yaw_deg),
                                pitch_deg=pitch_deg,
                                pitch_rad=math.radians(pitch_deg),
                            )
                        )
                        sample_index += 1
                point_index += 1
    return samples


def build_layered_still_scan_plan(layers: Iterable[StillLayer], points_x: int = 5, points_z: int = 3) -> list[StillSample]:
    """Build the 22-shot view set for layers that each have their own x/z bounds."""
    patterns: list[tuple[str, float, list[float]]] = [
        ("middle", 0.0, [index * 45.0 for index in range(8)]),
        ("upper", 45.0, [index * 60.0 for index in range(6)]),
        ("lower", -45.0, [index * 60.0 for index in range(6)]),
        ("ceiling", 90.0, [0.0]),
        ("floor", -90.0, [0.0]),
    ]

    samples: list[StillSample] = []
    sample_index = 1
    for layer in layers:
        zone_points_x = layer.points_x or points_x
        zone_points_z = layer.points_z or points_z
        xs = linspace(layer.x_min, layer.x_max, zone_points_x)
        zs = linspace(layer.z_min, layer.z_max, zone_points_z)
        point_index = 1
        for z in zs:
            for x in xs:
                point = (x, layer.y, z)
                if layer.hull_points and not _point_in_convex_hull(point, layer.hull_points):
                    continue
                if any(_point_in_ellipsoid(point, ellipsoid) for ellipsoid in layer.exclude_ellipsoids):
                    continue
                for pattern, pitch_deg, yaw_degrees in patterns:
                    for yaw_deg in yaw_degrees:
                        samples.append(
                            StillSample(
                                sample_index=sample_index,
                                point_index=point_index,
                                group_id=layer.group_id,
                                layer_id=layer.layer_id,
                                zone_id=layer.zone_id,
                                height_index=layer.height_index,
                                pattern=pattern,
                                x=x,
                                y=layer.y,
                                z=z,
                                yaw_deg=yaw_deg,
                                yaw_rad=math.radians(yaw_deg),
                                pitch_deg=pitch_deg,
                                pitch_rad=math.radians(pitch_deg),
                            )
                        )
                        sample_index += 1
                point_index += 1
    return samples


def load_still_layers(path: str | Path) -> list[StillLayer]:
    config_path = Path(path)
    if not config_path.is_absolute():
        from .paths import resolve_project_path

        config_path = resolve_project_path(config_path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    entries = raw.get("layers") or []
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"No layers found in {config_path}")

    hull_points = _hull_points(raw.get("space_hull"))
    exclude_ellipsoids = _exclude_ellipsoids(raw.get("exclude_ellipsoids"))
    layers: list[StillLayer] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Layer {index} must be a mapping.")
        layer_id = str(entry.get("id") or f"dataset_y{index:02d}")
        group_id = str(entry.get("group_id") or entry.get("dataset_id") or layer_id)
        layer_point_a = _point(entry.get("point_a"), f"layer {index} point_a")
        layer_point_b = _point(entry.get("point_b"), f"layer {index} point_b")
        layer_y = float(entry.get("y", max(float(layer_point_a["y"]), float(layer_point_b["y"]))))
        zones = entry.get("zones") or [
            {
                "id": "full",
                "point_a": layer_point_a,
                "point_b": layer_point_b,
                "y": layer_y,
                "points_x": entry.get("points_x"),
                "points_z": entry.get("points_z"),
            }
        ]
        if not isinstance(zones, list) or not zones:
            raise ValueError(f"Layer {index} zones must be a non-empty list.")

        for zone_index, zone in enumerate(zones, start=1):
            if not isinstance(zone, dict):
                raise ValueError(f"Layer {index} zone {zone_index} must be a mapping.")
            point_a = _point(zone.get("point_a", layer_point_a), f"layer {index} zone {zone_index} point_a")
            point_b = _point(zone.get("point_b", layer_point_b), f"layer {index} zone {zone_index} point_b")
            zone_y = float(zone.get("y", layer_y))
            layers.append(
                StillLayer(
                    group_id=group_id,
                    layer_id=layer_id,
                    zone_id=str(zone.get("id") or f"zone{zone_index:02d}"),
                    height_index=index,
                    x_min=min(float(point_a["x"]), float(point_b["x"])),
                    x_max=max(float(point_a["x"]), float(point_b["x"])),
                    y=zone_y,
                    z_min=min(float(point_a["z"]), float(point_b["z"])),
                    z_max=max(float(point_a["z"]), float(point_b["z"])),
                    points_x=_optional_int(zone.get("points_x")),
                    points_z=_optional_int(zone.get("points_z")),
                    hull_points=hull_points,
                    exclude_ellipsoids=exclude_ellipsoids,
                )
            )
    return layers


def run_layered_still_scan(
    config: AppConfig,
    obs_password: str,
    layers: Iterable[StillLayer],
    points_x: int = 5,
    points_z: int = 3,
    settle_seconds: float = 0.6,
    source_name: str | None = None,
    image_format: str = "jpg",
    image_width: int = 1920,
    image_height: int = 1080,
    image_quality: int = 100,
    session_id: str | None = None,
    max_samples: int | None = None,
    progress_callback: Callable[[StillSample, int, Path], None] | None = None,
    stop_event: Event | None = None,
    resume_from_layer: str | None = None,
    capture_depth: bool = False,
    depth_timeout: float = 10.0,
    depth_bridge: DepthBridge | None = None,
) -> dict[str, Path]:
    plan = build_layered_still_scan_plan(layers, points_x=points_x, points_z=points_z)
    replace_layer_ids: set[str] | None = None
    if resume_from_layer:
        plan = slice_still_scan_plan_from_layer(plan, resume_from_layer)
        replace_layer_ids = {sample.layer_id for sample in plan}
    return _run_plan(
        config=config,
        obs_password=obs_password,
        plan=plan,
        settle_seconds=settle_seconds,
        source_name=source_name,
        image_format=image_format,
        image_width=image_width,
        image_height=image_height,
        image_quality=image_quality,
        session_id=session_id,
        max_samples=max_samples,
        progress_callback=progress_callback,
        stop_event=stop_event,
        append_existing=bool(resume_from_layer),
        replace_layer_ids=replace_layer_ids,
        capture_depth=capture_depth,
        depth_timeout=depth_timeout,
        depth_bridge=depth_bridge,
    )


def slice_still_scan_plan_from_layer(plan: list[StillSample], layer_id: str) -> list[StillSample]:
    for index, sample in enumerate(plan):
        if sample.layer_id == layer_id:
            return plan[index:]
    raise ValueError(f"Resume layer was not found in the scan plan: {layer_id}")


def load_still_pose_plan(path: str | Path, group_id: str = "scene_1_extra") -> list[StillSample]:
    """Load explicit still poses from a high-score resampling JSON plan."""
    plan_path = Path(path)
    with plan_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    samples: list[StillSample] = []
    sample_index = 1
    point_index_by_layer: dict[str, int] = {}
    height_index_by_layer: dict[str, int] = {}
    subscenes = raw.get("subscenes") or []
    if not isinstance(subscenes, list):
        raise ValueError(f"Expected subscenes list in {plan_path}")

    for subscene in subscenes:
        if not isinstance(subscene, dict):
            continue
        layer_id = str(subscene.get("subscene_id") or f"subscene_{len(height_index_by_layer) + 1:02d}")
        if layer_id not in height_index_by_layer:
            height_index_by_layer[layer_id] = len(height_index_by_layer) + 1
            point_index_by_layer[layer_id] = 0
        for item in subscene.get("samples") or []:
            if not isinstance(item, dict):
                continue
            point_index_by_layer[layer_id] += 1
            yaw_deg = float(item.get("yaw") or 0.0)
            pitch_deg = float(item.get("pitch") or 0.0)
            priority = str(item.get("priority") or "normal")
            kind = str(item.get("kind") or "explicit_pose")
            kind_short = {
                "local_high_anchor_refinement": "refine",
                "bridge_5x_to_high_anchor": "bridge",
                "bridge_side_probe": "probe",
            }.get(kind, _safe_name(kind)[:12])
            samples.append(
                StillSample(
                    sample_index=sample_index,
                    point_index=point_index_by_layer[layer_id],
                    group_id=group_id,
                    layer_id=layer_id,
                    zone_id=f"{_safe_name(priority)[:3]}_{kind_short}",
                    height_index=height_index_by_layer[layer_id],
                    pattern=str(item.get("sample_id") or "explicit_pose"),
                    x=float(item["x"]),
                    y=float(item["y"]),
                    z=float(item["z"]),
                    yaw_deg=yaw_deg,
                    yaw_rad=math.radians(yaw_deg),
                    pitch_deg=pitch_deg,
                    pitch_rad=math.radians(pitch_deg),
                )
            )
            sample_index += 1

    if not samples:
        raise ValueError(f"No explicit samples found in {plan_path}")
    return samples


def run_still_pose_plan(
    config: AppConfig,
    obs_password: str,
    pose_plan: str | Path,
    settle_seconds: float = 0.6,
    source_name: str | None = None,
    image_format: str = "jpg",
    image_width: int = 1920,
    image_height: int = 1080,
    image_quality: int = 100,
    session_id: str | None = None,
    max_samples: int | None = None,
    progress_callback: Callable[[StillSample, int, Path], None] | None = None,
    stop_event: Event | None = None,
    capture_depth: bool = False,
    depth_timeout: float = 10.0,
    depth_bridge: DepthBridge | None = None,
) -> dict[str, Path]:
    """Move FreeCam through explicit poses and save one still per pose."""
    plan = load_still_pose_plan(pose_plan, group_id=session_id or "scene_1_extra")
    return _run_plan(
        config=config,
        obs_password=obs_password,
        plan=plan,
        settle_seconds=settle_seconds,
        source_name=source_name,
        image_format=image_format,
        image_width=image_width,
        image_height=image_height,
        image_quality=image_quality,
        session_id=session_id,
        max_samples=max_samples,
        progress_callback=progress_callback,
        stop_event=stop_event,
        capture_depth=capture_depth,
        depth_timeout=depth_timeout,
        depth_bridge=depth_bridge,
    )


def run_still_scan(
    config: AppConfig,
    obs_password: str,
    x_min: float,
    x_max: float,
    z_min: float,
    z_max: float,
    y_values: Iterable[float],
    points_x: int = 5,
    points_z: int = 3,
    settle_seconds: float = 0.6,
    source_name: str | None = None,
    image_format: str = "jpg",
    image_width: int = 1920,
    image_height: int = 1080,
    image_quality: int = 100,
    session_id: str | None = None,
    max_samples: int | None = None,
    progress_callback: Callable[[StillSample, int, Path], None] | None = None,
    stop_event: Event | None = None,
    capture_depth: bool = False,
    depth_timeout: float = 10.0,
    depth_bridge: DepthBridge | None = None,
) -> dict[str, Path]:
    """Move FreeCam through a grid and save OBS still screenshots plus metadata."""
    plan = build_still_scan_plan(
        x_min=x_min,
        x_max=x_max,
        z_min=z_min,
        z_max=z_max,
        y_values=y_values,
        points_x=points_x,
        points_z=points_z,
    )
    return _run_plan(
        config=config,
        obs_password=obs_password,
        plan=plan,
        settle_seconds=settle_seconds,
        source_name=source_name,
        image_format=image_format,
        image_width=image_width,
        image_height=image_height,
        image_quality=image_quality,
        session_id=session_id,
        max_samples=max_samples,
        progress_callback=progress_callback,
        stop_event=stop_event,
        capture_depth=capture_depth,
        depth_timeout=depth_timeout,
        depth_bridge=depth_bridge,
    )


def _run_plan(
    config: AppConfig,
    obs_password: str,
    plan: list[StillSample],
    settle_seconds: float,
    source_name: str | None,
    image_format: str,
    image_width: int,
    image_height: int,
    image_quality: int,
    session_id: str | None,
    max_samples: int | None,
    progress_callback: Callable[[StillSample, int, Path], None] | None,
    stop_event: Event | None,
    append_existing: bool = False,
    replace_layer_ids: set[str] | None = None,
    capture_depth: bool = False,
    depth_timeout: float = 10.0,
    depth_bridge: DepthBridge | None = None,
) -> dict[str, Path]:
    obs_cfg = config.raw["obs"]
    session = session_id or f"stills_{make_session_id()}"
    output_dir = ensure_dir(Path("data/stills/scans") / session)
    datasets_dir = ensure_dir(output_dir / "datasets")
    samples_csv = output_dir / "samples.csv"
    plan_csv = output_dir / "scan_plan.csv"

    if max_samples is not None:
        plan = plan[: max(0, int(max_samples))]

    controller = OBSController(obs_cfg["host"], int(obs_cfg["port"]), obs_password or obs_cfg.get("password", ""))
    control = LuaControl(config)
    depth_controller: DepthBridge | None = None
    if capture_depth:
        depth_controller = depth_bridge or DepthBridge(config)
        if not depth_controller.wait_until_ready(timeout_sec=min(5.0, max(0.1, depth_timeout))):
            raise RuntimeError(
                "Depth capture is enabled, but re9_depth_bridge.dll is not reporting ready. "
                "Install the plugin under reframework/plugins and keep RE9 running."
            )
    samples_backup: Path | None = None
    if append_existing and replace_layer_ids:
        samples_backup = _remove_sample_rows_for_layers(samples_csv, replace_layer_ids)
    if not append_existing or not plan_csv.exists():
        _write_plan(plan_csv, plan)

    source_used = ""
    dataset_files: dict[str, tuple[object, csv.DictWriter]] = {}
    fieldnames = _sample_fieldnames(capture_depth)
    schema_backup: Path | None = None
    if append_existing:
        fieldnames, schema_backup = _prepare_csv_for_append(
            samples_csv,
            fieldnames,
            create_backup=samples_backup is None,
        )
    try:
        write_header = not append_existing or not samples_csv.exists() or samples_csv.stat().st_size == 0
        mode = "a" if append_existing else "w"
        with samples_csv.open(mode, encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            total = plan[-1].sample_index if append_existing and plan else len(plan)
            for sample in plan:
                if stop_event is not None and stop_event.is_set():
                    break
                sample_id = _sample_id(sample)
                # The Lua control file is polled by REFramework from the game
                # thread.  Include a per-shot nonce so an old status file can
                # never be mistaken for an acknowledgement of this shot.
                command_segment_id = f"{sample_id}:{time.monotonic_ns()}"
                pose_ack_timeout = max(5.0, float(settle_seconds) + 1.0)
                pose_acknowledged = False
                for attempt in range(3):
                    # Rewriting the same pose is safe: Lua assigns a fresh
                    # command id on every write, while the unique segment id
                    # still prevents an old status file from being accepted.
                    control.write_set_pose_control(
                        session,
                        x=sample.x,
                        y=sample.y,
                        z=sample.z,
                        yaw=sample.yaw_rad,
                        pitch=sample.pitch_rad,
                        segment_id=command_segment_id,
                        yaw_end=sample.yaw_rad,
                        duration_sec=0.0,
                    )
                    if control.wait_until_scan_pose(
                        command_segment_id,
                        timeout_sec=pose_ack_timeout,
                    ):
                        pose_acknowledged = True
                        break
                    if attempt < 2:
                        time.sleep(0.25)
                if not pose_acknowledged:
                    raise RuntimeError(
                        f"REFramework did not acknowledge pose {sample_id} "
                        f"after 3 attempts ({pose_ack_timeout:.1f}s each); "
                        "check that FreeCam remains enabled and REFramework is responsive."
                    )
                time.sleep(max(0.0, settle_seconds))
                dataset_id = _dataset_id(sample)
                dataset_dir = ensure_dir(datasets_dir / _safe_name(sample.group_id) / dataset_id)
                image_dir = ensure_dir(dataset_dir / "images")
                extension = "jpg" if image_format.lower().lstrip(".") in {"jpg", "jpeg"} else "png"
                image_path = image_dir / f"{sample_id}.{extension}"
                source_used = controller.save_source_screenshot(
                    image_path,
                    source_name=source_name,
                    image_format=image_format,
                    width=image_width,
                    height=image_height,
                    quality=image_quality,
                )
                depth_result: DepthCaptureResult | None = None
                capture_id = ""
                if capture_depth:
                    assert depth_controller is not None
                    capture_id = f"{session}:{sample_id}:{time.time_ns()}"
                    try:
                        depth_result = depth_controller.capture(
                            capture_id=capture_id,
                            dataset_dir=dataset_dir,
                            sample_id=sample_id,
                            timeout_sec=depth_timeout,
                            expected_width=image_width,
                            expected_height=image_height,
                        )
                    except Exception:
                        # A depth-enabled sample is atomic: an unpaired RGB must
                        # not look like a completed reconstruction sample.
                        image_path.unlink(missing_ok=True)
                        raise
                row = {
                    **asdict(sample),
                    "session_id": session,
                    "dataset_id": dataset_id,
                    "source_name": source_used,
                    "image_path": str(image_path),
                    "captured_at_unix": f"{time.time():.6f}",
                }
                if depth_result is not None:
                    row.update(_depth_row(depth_result))
                writer.writerow(row)
                dataset_writer = _dataset_writer(dataset_files, dataset_dir, fieldnames)
                dataset_writer.writerow(row)
                handle.flush()
                _flush_dataset(dataset_files, str(dataset_dir))
                if progress_callback is not None:
                    progress_callback(sample, total, image_path)
                console.print(
                    f"[cyan]{sample.sample_index:04d}/{total:04d}[/cyan] "
                    f"x={sample.x:.2f} y={sample.y:.2f} z={sample.z:.2f} "
                    f"yaw={sample.yaw_deg:.1f} pitch={sample.pitch_deg:.1f} -> {image_path.name}"
                )
    finally:
        for dataset_handle, _ in dataset_files.values():
            dataset_handle.close()
        control.write_clear_pose_control(session)

    outputs = {
        "output_dir": output_dir,
        "datasets": datasets_dir,
        "samples_csv": samples_csv,
        "scan_plan_csv": plan_csv,
    }
    if samples_backup is not None:
        outputs["samples_backup"] = samples_backup
    if schema_backup is not None:
        outputs["samples_schema_backup"] = schema_backup
    return outputs


def _depth_row(result: DepthCaptureResult) -> dict[str, object]:
    return {
        "capture_id": result.capture_id,
        "depth_path": str(result.depth_path),
        "depth_raw_path": str(result.raw_path),
        "depth_preview_path": str(result.preview_path),
        "valid_mask_path": str(result.valid_mask_path),
        "camera_metadata_path": str(result.camera_metadata_path),
        "depth_status": "ok",
        "depth_width": result.width,
        "depth_height": result.height,
        "depth_unit": "m",
        "meters_per_game_unit": "1.0",
        "near_clip": f"{result.near_clip:.9g}",
        "far_clip": f"{result.far_clip:.9g}",
        "projection_matrix": json.dumps(result.projection_matrix, separators=(",", ":")),
        "render_frame_id": result.render_frame_id,
        "reversed_z": str(result.reversed_z).lower(),
        "valid_depth_pixels": result.valid_pixel_count,
    }


def _sample_fieldnames(capture_depth: bool) -> list[str]:
    fieldnames = list(BASE_SAMPLE_FIELDS)
    if capture_depth:
        fieldnames.extend(DEPTH_SAMPLE_FIELDS)
    return fieldnames


def _prepare_csv_for_append(
    path: Path,
    required_fieldnames: list[str],
    *,
    create_backup: bool = True,
) -> tuple[list[str], Path | None]:
    """Preserve old rows while extending a resumed CSV to the current schema."""
    if not path.exists() or path.stat().st_size == 0:
        return list(required_fieldnames), None

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        existing_fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if not existing_fieldnames:
        return list(required_fieldnames), None

    merged_fieldnames = existing_fieldnames + [
        field for field in required_fieldnames if field not in existing_fieldnames
    ]
    if merged_fieldnames == existing_fieldnames:
        return existing_fieldnames, None

    backup: Path | None = None
    if create_backup:
        backup = path.with_name(f"samples_before_schema_upgrade_{time.time_ns()}.csv")
        shutil.copy2(path, backup)

    temporary = path.with_name(f".{path.name}.schema-{time.time_ns()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=merged_fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return merged_fieldnames, backup


def _remove_sample_rows_for_layers(path: Path, layer_ids: set[str]) -> Path | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        rows = list(reader)
    if not fieldnames:
        return None
    kept = [row for row in rows if row.get("layer_id") not in layer_ids]
    if len(kept) == len(rows):
        return None
    backup = path.with_name(f"samples_before_resume_{int(time.time())}.csv")
    shutil.copy2(path, backup)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept)
    return backup


def _write_plan(path: Path, plan: list[StillSample]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(plan[0]).keys()) if plan else ["sample_index"])
        writer.writeheader()
        for sample in plan:
            writer.writerow(asdict(sample))


def _sample_id(sample: StillSample) -> str:
    if sample.pattern.startswith("scene_") and "_extra_" in sample.pattern:
        return f"s{sample.sample_index:04d}_{_safe_name(sample.pattern)}"
    return (
        f"s{sample.sample_index:04d}_p{sample.point_index:02d}_h{sample.height_index:02d}_"
        f"{_safe_name(sample.zone_id)}_"
        f"{sample.pattern}_x{_num(sample.x)}_y{_num(sample.y)}_z{_num(sample.z)}_"
        f"yaw{_num(sample.yaw_deg)}_pitch{_num(sample.pitch_deg)}"
    )


def _dataset_id(sample: StillSample) -> str:
    return f"{_safe_name(sample.layer_id)}_{_safe_name(sample.zone_id)}_y{sample.height_index:02d}_{_num(sample.y)}"


def _dataset_writer(
    dataset_files: dict[str, tuple[object, csv.DictWriter]],
    dataset_dir: Path,
    fieldnames: list[str],
) -> csv.DictWriter:
    dataset_id = str(dataset_dir)
    if dataset_id in dataset_files:
        return dataset_files[dataset_id][1]
    path = dataset_dir / "samples.csv"
    handle = path.open("w", encoding="utf-8", newline="")
    writer = csv.DictWriter(handle, fieldnames=fieldnames)
    writer.writeheader()
    dataset_files[dataset_id] = (handle, writer)
    return writer


def _flush_dataset(dataset_files: dict[str, tuple[object, csv.DictWriter]], dataset_id: str) -> None:
    handle = dataset_files.get(dataset_id, (None, None))[0]
    if handle is not None:
        handle.flush()


def _num(value: float) -> str:
    text = f"{value:.2f}".replace("-", "m").replace(".", "p")
    return text


def _safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"_", "-", "."} else "_" for char in value.strip())
    return cleaned or "dataset"


def _point(value: Any, label: str) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain x, y, and z.")
    return {axis: float(value[axis]) for axis in ("x", "y", "z")}


def _hull_points(value: Any) -> tuple[tuple[float, float, float], ...] | None:
    if value is None:
        return None
    entries = value.get("points") if isinstance(value, dict) else value
    if not isinstance(entries, list) or len(entries) < 4:
        raise ValueError("space_hull must contain at least four 3D points.")
    parsed = [_point(entry, f"space_hull point {index}") for index, entry in enumerate(entries, start=1)]
    points = tuple((point["x"], point["y"], point["z"]) for point in parsed)
    _convex_hull_planes(points)
    return points


def _exclude_ellipsoids(value: Any) -> tuple[tuple[float, float, float, float, float, float], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("exclude_ellipsoids must be a list.")
    ellipsoids = []
    for index, entry in enumerate(value, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"exclude_ellipsoids entry {index} must be a mapping.")
        center = _point(entry.get("center"), f"exclude_ellipsoids entry {index} center")
        radius = entry.get("radius")
        if not isinstance(radius, dict):
            raise ValueError(f"exclude_ellipsoids entry {index} radius must contain x, y, and z.")
        radii = tuple(float(radius[axis]) for axis in ("x", "y", "z"))
        if any(component <= 0 for component in radii):
            raise ValueError(f"exclude_ellipsoids entry {index} radii must be greater than zero.")
        ellipsoids.append((center["x"], center["y"], center["z"], *radii))
    return tuple(ellipsoids)


def _point_in_ellipsoid(
    point: tuple[float, float, float],
    ellipsoid: tuple[float, float, float, float, float, float],
) -> bool:
    center_x, center_y, center_z, radius_x, radius_y, radius_z = ellipsoid
    return (
        ((point[0] - center_x) / radius_x) ** 2
        + ((point[1] - center_y) / radius_y) ** 2
        + ((point[2] - center_z) / radius_z) ** 2
        <= 1.0
    )


def _point_in_convex_hull(
    point: tuple[float, float, float],
    hull_points: tuple[tuple[float, float, float], ...],
    tolerance: float = 1e-6,
) -> bool:
    return all(_dot3(normal, point) + offset <= tolerance for normal, offset in _convex_hull_planes(hull_points))


@lru_cache(maxsize=32)
def _convex_hull_planes(
    points: tuple[tuple[float, float, float], ...],
) -> tuple[tuple[tuple[float, float, float], float], ...]:
    epsilon = 1e-8
    planes: dict[tuple[float, float, float, float], tuple[tuple[float, float, float], float]] = {}
    for first, second, third in combinations(points, 3):
        normal = _cross3(_subtract3(second, first), _subtract3(third, first))
        length = math.sqrt(_dot3(normal, normal))
        if length <= epsilon:
            continue
        normal = tuple(component / length for component in normal)
        distances = [_dot3(normal, _subtract3(candidate, first)) for candidate in points]
        if max(distances) <= epsilon:
            pass
        elif min(distances) >= -epsilon:
            normal = tuple(-component for component in normal)
        else:
            continue
        offset = -_dot3(normal, first)
        key = tuple(round(component, 6) for component in (*normal, offset))
        planes[key] = (normal, offset)
    if len(planes) < 4:
        raise ValueError("space_hull points do not form a valid 3D convex volume.")
    return tuple(planes.values())


def _subtract3(
    left: tuple[float, float, float], right: tuple[float, float, float]
) -> tuple[float, float, float]:
    return left[0] - right[0], left[1] - right[1], left[2] - right[2]


def _dot3(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return left[0] * right[0] + left[1] * right[1] + left[2] * right[2]


def _cross3(
    left: tuple[float, float, float], right: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    number = int(value)
    if number <= 0:
        raise ValueError("points_x and points_z must be greater than zero.")
    return number
