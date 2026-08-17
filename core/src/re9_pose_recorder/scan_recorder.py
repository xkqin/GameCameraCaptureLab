from __future__ import annotations

import csv
import math
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .config import AppConfig
from .lua_control import LuaControl, make_session_id
from .obs_control import OBSController
from .paths import ensure_dir


@dataclass(frozen=True)
class ScanSegment:
    segment_index: int
    point_index: int
    yaw_index: int
    x: float
    y: float
    z: float
    yaw_deg: float
    yaw_rad: float
    yaw_end_deg: float
    yaw_end_rad: float
    pitch_rad: float
    fov: float | None
    segment_id: str


def build_scan_plan(
    x_min: float,
    x_max: float,
    z_min: float,
    z_max: float,
    y: float,
    points_x: int = 5,
    points_z: int = 3,
    yaw_step_deg: float = 30.0,
    pitch_deg: float = 0.0,
    fov: float | None = None,
) -> list[ScanSegment]:
    """Build a rectangular X/Z grid and 360-degree yaw sweep per point."""
    if points_x <= 0 or points_z <= 0:
        raise ValueError("points_x and points_z must be positive.")
    if yaw_step_deg <= 0 or yaw_step_deg > 360:
        raise ValueError("yaw_step_deg must be in (0, 360].")

    xs = _linspace(x_min, x_max, points_x)
    zs = _linspace(z_min, z_max, points_z)
    yaw_values = []
    yaw = 0.0
    while yaw < 360.0 - 1e-9:
        yaw_values.append(yaw)
        yaw += yaw_step_deg

    segments: list[ScanSegment] = []
    segment_index = 1
    point_index = 1
    for z in zs:
        for x in xs:
            yaw_index = 1
            for yaw_deg in yaw_values:
                yaw_end_deg = yaw_deg + yaw_step_deg
                segment_id = f"seg_{segment_index:03d}_p{point_index:02d}_yaw{int(round(yaw_deg)):03d}"
                segments.append(
                    ScanSegment(
                        segment_index=segment_index,
                        point_index=point_index,
                        yaw_index=yaw_index,
                        x=x,
                        y=y,
                        z=z,
                        yaw_deg=yaw_deg,
                        yaw_rad=math.radians(yaw_deg),
                        yaw_end_deg=yaw_end_deg,
                        yaw_end_rad=math.radians(yaw_end_deg),
                        pitch_rad=math.radians(pitch_deg),
                        fov=fov,
                        segment_id=segment_id,
                    )
                )
                segment_index += 1
                yaw_index += 1
            point_index += 1
    return segments


def run_region_scan(
    config: AppConfig,
    obs_password: str,
    x_min: float,
    x_max: float,
    z_min: float,
    z_max: float,
    y: float,
    points_x: int = 5,
    points_z: int = 3,
    yaw_step_deg: float = 30.0,
    segment_seconds: float = 7.0,
    settle_seconds: float = 0.5,
    post_stop_seconds: float = 1.5,
    pitch_deg: float = 0.0,
    fov: float | None = None,
    max_segments: int | None = None,
    start_segment: int = 1,
    session_id: str | None = None,
) -> dict[str, Path]:
    """Run an OBS-backed scan: set FreeCam poses, record 7s clips, and write local metadata."""
    if segment_seconds <= 0:
        raise ValueError("segment_seconds must be positive.")
    if settle_seconds < 0:
        raise ValueError("settle_seconds must be non-negative.")
    if post_stop_seconds < 0:
        raise ValueError("post_stop_seconds must be non-negative.")
    if start_segment <= 0:
        raise ValueError("start_segment must be positive.")

    session = session_id or make_session_id()
    output_root = ensure_dir(Path("data/videos/scans") / session)
    raw_dir = ensure_dir(output_root / "raw")
    clips_dir = ensure_dir(output_root / "clips")
    pose_log = config.pose_log_file.with_name(f"{config.pose_log_file.stem}_{session}.csv")

    plan = build_scan_plan(
        x_min=x_min,
        x_max=x_max,
        z_min=z_min,
        z_max=z_max,
        y=y,
        points_x=points_x,
        points_z=points_z,
        yaw_step_deg=yaw_step_deg,
        pitch_deg=pitch_deg,
        fov=fov,
    )
    if max_segments is not None:
        if max_segments <= 0:
            raise ValueError("max_segments must be positive when provided.")
        plan = plan[:max_segments]
    if start_segment > 1:
        plan = [item for item in plan if item.segment_index >= start_segment]
    if not plan:
        raise ValueError("No scan segments remain after applying start_segment/max_segments.")
    plan_csv = output_root / "scan_plan.csv"
    segments_csv = output_root / "segments.csv"
    _write_plan(plan_csv, plan, session, segment_seconds)

    obs_cfg = config.raw["obs"]
    controller = OBSController(obs_cfg["host"], int(obs_cfg["port"]), obs_password or obs_cfg.get("password", ""))
    try:
        controller.set_record_directory(raw_dir)
    except Exception:
        # OBS versions before SetRecordDirectory support can still be used if the profile already points to raw_dir.
        pass

    control = LuaControl(config)
    control.write_start_control(session, pose_log, float(config.raw["lua_logger"]["default_interval_sec"]))
    control.wait_until_lua_logging_started(session, timeout_sec=3)

    rows: list[dict[str, object]] = _read_existing_rows(segments_csv) if start_segment > 1 else []
    started_at = 0.0
    recording_started = False

    try:
        first = plan[0]
        control.write_set_pose_control(
            session,
            first.x,
            first.y,
            first.z,
            first.yaw_rad,
            first.pitch_rad,
            fov=first.fov,
            segment_id=first.segment_id,
            yaw_end=first.yaw_rad,
            duration_sec=0.0,
        )
        time.sleep(settle_seconds)

        started_at = time.time()

        for segment in plan:
            control.write_set_pose_control(
                session,
                segment.x,
                segment.y,
                segment.z,
                segment.yaw_rad,
                segment.pitch_rad,
                fov=segment.fov,
                segment_id=segment.segment_id,
                yaw_end=segment.yaw_rad,
                duration_sec=0.0,
            )
            time.sleep(settle_seconds)
            controller.start_recording()
            recording_started = True
            _wait_for_obs_record_active(controller, timeout_sec=5.0)
            segment_start = time.time()
            control.write_set_pose_control(
                session,
                segment.x,
                segment.y,
                segment.z,
                segment.yaw_rad,
                segment.pitch_rad,
                fov=segment.fov,
                segment_id=segment.segment_id,
                yaw_end=segment.yaw_end_rad,
                duration_sec=segment_seconds,
            )
            time.sleep(segment_seconds)
            raw_output = _stop_recording_best_effort(controller)
            recording_started = False
            segment_end = time.time()
            _wait_for_obs_record_idle(controller, timeout_sec=10.0)
            raw_path = Path(raw_output) if raw_output else None
            if raw_path is None or not raw_path.exists():
                raw_path = _wait_for_segment_file(raw_dir, segment_start, config.supported_video_extensions)
            elif not _wait_for_existing_file_stable(raw_path, timeout_sec=10.0):
                raw_path = _wait_for_segment_file(raw_dir, segment_start, config.supported_video_extensions) or raw_path
            clip_path = clips_dir / f"{segment.segment_id}_x{segment.x:.3f}_y{segment.y:.3f}_z{segment.z:.3f}_yaw{int(round(segment.yaw_deg)):03d}.mp4"
            if raw_path is not None:
                _move_or_copy(raw_path, clip_path)
            rows.append(
                _segment_row(
                    session=session,
                    segment=segment,
                    video_path=clip_path if clip_path.exists() else raw_path,
                    segment_start_abs=segment_start,
                    segment_end_abs=segment_end,
                    recording_started_abs=started_at,
                    requested_seconds=segment_seconds,
                    settle_seconds=settle_seconds,
                )
            )
            _write_rows(segments_csv, rows)
            time.sleep(post_stop_seconds)
    finally:
        if recording_started:
            try:
                controller.stop_recording()
            except Exception:
                pass
        control.write_clear_pose_control(session)
        time.sleep(0.5)
        control.write_stop_control(session)
        time.sleep(0.5)
        control.write_clear_pose_control(session)

    pose_copy = output_root / "pose_log.csv"
    if pose_log.exists():
        shutil.copy2(pose_log, pose_copy)

    return {
        "output_dir": output_root,
        "clips_dir": clips_dir,
        "raw_dir": raw_dir,
        "plan_csv": plan_csv,
        "segments_csv": segments_csv,
        "pose_log": pose_copy if pose_copy.exists() else pose_log,
    }


def _linspace(start: float, end: float, count: int) -> list[float]:
    if count == 1:
        return [(start + end) / 2.0]
    step = (end - start) / float(count - 1)
    return [start + step * index for index in range(count)]


def _write_plan(path: Path, plan: list[ScanSegment], session: str, segment_seconds: float) -> None:
    rows = [
        {
            "session_id": session,
            "segment_index": item.segment_index,
            "point_index": item.point_index,
            "yaw_index": item.yaw_index,
            "segment_id": item.segment_id,
            "x": item.x,
            "y": item.y,
            "z": item.z,
            "yaw_deg": item.yaw_deg,
            "yaw_rad": item.yaw_rad,
            "yaw_end_deg": item.yaw_end_deg,
            "yaw_end_rad": item.yaw_end_rad,
            "pitch_rad": item.pitch_rad,
            "fov": "" if item.fov is None else item.fov,
            "duration_sec": segment_seconds,
        }
        for item in plan
    ]
    _write_dicts(path, rows)


def _segment_row(
    session: str,
    segment: ScanSegment,
    video_path: Path | None,
    segment_start_abs: float,
    segment_end_abs: float,
    recording_started_abs: float,
    requested_seconds: float,
    settle_seconds: float,
) -> dict[str, object]:
    return {
        "session_id": session,
        "segment_index": segment.segment_index,
        "point_index": segment.point_index,
        "yaw_index": segment.yaw_index,
        "segment_id": segment.segment_id,
        "video_path": str(video_path) if video_path else "",
        "x": segment.x,
        "y": segment.y,
        "z": segment.z,
        "yaw_deg": segment.yaw_deg,
        "yaw_rad": segment.yaw_rad,
        "yaw_end_deg": segment.yaw_end_deg,
        "yaw_end_rad": segment.yaw_end_rad,
        "pitch_rad": segment.pitch_rad,
        "fov": "" if segment.fov is None else segment.fov,
        "requested_duration_sec": requested_seconds,
        "settle_seconds": settle_seconds,
        "recording_start_sec": round(segment_start_abs - recording_started_abs, 3),
        "recording_end_sec": round(segment_end_abs - recording_started_abs, 3),
        "actual_duration_sec": round(segment_end_abs - segment_start_abs, 3),
    }


def _write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    _write_dicts(path, rows)


def _read_existing_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _write_dicts(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _wait_for_segment_file(directory: Path, since_time: float, suffixes: set[str], timeout_sec: float = 15.0) -> Path | None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        candidates = [
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.suffix.lower() in suffixes
            and path.stat().st_size > 4096
            and path.stat().st_mtime >= since_time - 1.0
        ]
        candidates.sort(key=lambda item: item.stat().st_mtime)
        for candidate in candidates:
            if _is_stable_file(candidate):
                return candidate
        time.sleep(0.25)
    return None


def _wait_for_existing_file_stable(path: Path, timeout_sec: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if path.exists() and _is_stable_file(path):
            return True
        time.sleep(0.25)
    return False


def _wait_for_obs_record_idle(controller: OBSController, timeout_sec: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            status = controller.get_record_status()
            active = bool(getattr(status, "output_active", False))
            if not active:
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def _wait_for_obs_record_active(controller: OBSController, timeout_sec: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            status = controller.get_record_status()
            active = bool(getattr(status, "output_active", False))
            if active:
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def _stop_recording_best_effort(controller: OBSController) -> str | None:
    try:
        status = controller.get_record_status()
        if not bool(getattr(status, "output_active", False)):
            return None
    except Exception:
        pass
    try:
        return controller.stop_recording()
    except Exception:
        return None


def _is_stable_file(path: Path) -> bool:
    try:
        first = path.stat().st_size
        time.sleep(0.35)
        second = path.stat().st_size
        return first > 4096 and first == second
    except OSError:
        return False


def _move_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        source.replace(destination)
    except OSError:
        shutil.copy2(source, destination)
