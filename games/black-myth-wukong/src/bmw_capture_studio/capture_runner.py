from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import re
import time
from typing import Callable, Iterable, Protocol

from .coordinate_scale import COORDINATE_SCALE
from .depth_bridge import DepthBridge, DepthCaptureTicket
from .models import CameraPose, CapturePoint


class PoseReader(Protocol):
    def read_pose(self) -> CameraPose: ...


class PoseMover(Protocol):
    def move_to(
        self,
        target: CameraPose,
        *,
        stop_requested: Callable[[], bool],
        on_update: Callable[[str], None] | None,
    ) -> CameraPose: ...


class CaptureTarget(Protocol):
    index: int
    label: str
    pose: CameraPose
    time_sec: float


@dataclass(frozen=True)
class CaptureRunResult:
    session_dir: Path
    manifest_json: Path
    manifest_csv: Path
    captured_count: int
    requested_count: int
    stopped: bool


def _safe_label(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip())
    return cleaned.strip("._")[:48] or "point"


def _sleep_interruptible(seconds: float, stop_requested: Callable[[], bool]) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        if stop_requested():
            raise InterruptedError("采集已停止 / Capture stopped")
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


def _pose_columns(prefix: str, pose: CameraPose) -> dict[str, object]:
    values = {f"{prefix}_{key}": value for key, value in pose.as_dict().items()}
    values.update(
        {
            f"{prefix}_{axis}_m": value
            for axis, value in COORDINATE_SCALE.position_m(
                pose.x, pose.y, pose.z
            ).items()
        }
    )
    return values


def _schema_pose(pose: CameraPose) -> dict[str, object]:
    return {
        "position": {"x": pose.x, "y": pose.y, "z": pose.z},
        "position_m": COORDINATE_SCALE.position_m(pose.x, pose.y, pose.z),
        "rotation": {
            "yaw": pose.yaw_degrees,
            "pitch": pose.pitch_degrees,
            "roll": pose.roll_degrees,
        },
        "quaternion": {
            "x": pose.qx,
            "y": pose.qy,
            "z": pose.qz,
            "w": pose.qw,
        },
        "fov_degrees": pose.fov_degrees,
    }


def _pose_delta(before: CameraPose, after: CameraPose) -> dict[str, float]:
    position = math.sqrt(
            (after.x - before.x) ** 2
            + (after.y - before.y) ** 2
            + (after.z - before.z) ** 2
        )
    return {
        "position": position,
        "position_m": position * COORDINATE_SCALE.meters_per_unit,
        "yaw_degrees": abs(after.yaw_degrees - before.yaw_degrees),
        "pitch_degrees": abs(after.pitch_degrees - before.pitch_degrees),
        "roll_degrees": abs(after.roll_degrees - before.roll_degrees),
        "fov_degrees": abs(after.fov_degrees - before.fov_degrees),
    }


def _capture_metadata(point: CaptureTarget) -> dict[str, object]:
    getter = getattr(point, "capture_metadata", None)
    if not callable(getter):
        return {}
    value = getter()
    return dict(value) if isinstance(value, dict) else {}


class CaptureRunner:
    def __init__(
        self,
        *,
        bridge: PoseReader,
        mover: PoseMover,
        pid: int,
        settle_seconds: float = 0.12,
        image_format: str = "png",
        screenshotter: Callable[[int, str | Path], Path] | None = None,
        depth_bridge: DepthBridge | None = None,
        depth_enabled: bool = False,
        depth_timeout: float = 8.0,
        screenshot_source: str = "obs_websocket_source",
        screenshot_width: int = 1920,
        screenshot_height: int = 1080,
        screenshot_quality: int = 100,
    ) -> None:
        self.bridge = bridge
        self.mover = mover
        self.pid = pid
        self.settle_seconds = max(0.0, settle_seconds)
        self.image_format = image_format.lower().lstrip(".") or "png"
        self.screenshotter = screenshotter
        self.depth_bridge = depth_bridge
        self.depth_enabled = bool(depth_enabled)
        self.depth_timeout = max(0.1, float(depth_timeout))
        self.screenshot_source = screenshot_source
        self.screenshot_width = max(1, int(screenshot_width))
        self.screenshot_height = max(1, int(screenshot_height))
        self.screenshot_quality = min(100, max(0, int(screenshot_quality)))
        self.last_session_dir: Path | None = None

    def run(
        self,
        points: Iterable[CaptureTarget],
        output_root: str | Path,
        *,
        mode: str,
        stop_requested: Callable[[], bool] = lambda: False,
        on_progress: Callable[[int, int, str], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        respect_timestamps: bool = False,
        run_metadata: dict[str, object] | None = None,
    ) -> CaptureRunResult:
        values = list(points)
        if self.screenshotter is None:
            raise RuntimeError(
                "统一静态采集必须连接 OBS WebSocket；窗口截屏回退已禁用 / "
                "Unified still capture requires OBS WebSocket; window-capture fallback is disabled"
            )
        if not values:
            raise ValueError("没有可采集的点位 / No capture points")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        session_dir = Path(output_root) / f"{mode}_{stamp}"
        self.last_session_dir = session_dir.resolve()
        images_dir = session_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=False)
        samples_dir = session_dir / "samples"
        samples_dir.mkdir(parents=True, exist_ok=False)
        manifest_json = session_dir / "manifest.json"
        manifest_csv = session_dir / "manifest.csv"
        rows: list[dict[str, object]] = []
        stopped = False
        run_error: str | None = None
        run_started = time.monotonic()
        start_pose: CameraPose | None = None
        restore_attempted = False
        restore_succeeded: bool | None = None
        restored_pose: CameraPose | None = None
        restore_error: str | None = None

        try:
            start_pose = self.bridge.read_pose()
            for ordinal, point in enumerate(values, start=1):
                if stop_requested():
                    raise InterruptedError("采集已停止 / Capture stopped")
                if respect_timestamps and point.time_sec > 0:
                    scheduled = run_started + point.time_sec
                    _sleep_interruptible(scheduled - time.monotonic(), stop_requested)

                if on_progress is not None:
                    on_progress(ordinal - 1, len(values), f"前往 / Moving to {point.label}")
                self.mover.move_to(
                    point.pose,
                    stop_requested=stop_requested,
                    on_update=on_log,
                )
                _sleep_interruptible(self.settle_seconds, stop_requested)
                pose_before = self.bridge.read_pose()
                filename = (
                    f"{ordinal:05d}_{_safe_label(point.label)}.{self.image_format}"
                )
                sample_dir = samples_dir / f"sample_{ordinal:06d}"
                sample_dir.mkdir(parents=True, exist_ok=False)
                depth_ticket: DepthCaptureTicket | None = None
                rgb_started_at = datetime.now().astimezone().isoformat(
                    timespec="milliseconds"
                )
                try:
                    if self.depth_enabled:
                        if self.depth_bridge is None:
                            raise RuntimeError(
                                "Depth capture is enabled but no depth bridge is configured"
                            )
                        depth_ticket = self.depth_bridge.begin_capture()
                    image_path = Path(
                        self.screenshotter(self.pid, images_dir / filename)
                    )
                    rgb_completed_at = datetime.now().astimezone().isoformat(
                        timespec="milliseconds"
                    )
                    depth = (
                        self.depth_bridge.wait_capture(
                            depth_ticket,
                            sample_dir,
                            timeout=self.depth_timeout,
                        )
                        if self.depth_enabled
                        and self.depth_bridge is not None
                        and depth_ticket is not None
                        else None
                    )
                except Exception:
                    if depth_ticket is not None and self.depth_bridge is not None:
                        self.depth_bridge.cancel(depth_ticket)
                    raise
                pose_after = self.bridge.read_pose()
                captured_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
                delta = _pose_delta(pose_before, pose_after)
                camera_static = (
                    delta["position"] <= 1.0
                    and delta["yaw_degrees"] <= 0.1
                    and delta["pitch_degrees"] <= 0.1
                    and delta["roll_degrees"] <= 0.1
                    and delta["fov_degrees"] <= 0.1
                )
                rgb_mtime_ns = image_path.stat().st_mtime_ns
                depth_capture_ns = int(depth["captured_unix_ns"]) if depth else None
                rgb_depth_delta_ms = (
                    abs(rgb_mtime_ns - depth_capture_ns) / 1_000_000.0
                    if depth_capture_ns is not None
                    else None
                )
                resolution_match = (
                    int(depth.get("width", -1)) == self.screenshot_width
                    and int(depth.get("height", -1)) == self.screenshot_height
                    if depth is not None
                    else None
                )
                sync_status = (
                    "static_camera_best_effort"
                    if depth is not None and camera_static
                    else "camera_moved_during_pair"
                    if depth is not None
                    else "rgb_only"
                )
                point_metadata = _capture_metadata(point)
                sample_metadata_path = sample_dir / "metadata.json"
                sample_metadata = {
                    "schema_version": "camera-static-sample/v1",
                    "game_id": "black-myth-wukong",
                    "coordinate_system": COORDINATE_SCALE.coordinate_system(),
                    "scene_id": str((run_metadata or {}).get("scene_id") or mode),
                    "sample_index": ordinal,
                    "label": point.label,
                    "point_index": int(point_metadata.get("point_index") or point.index),
                    **point_metadata,
                    "target_pose": _schema_pose(point.pose),
                    "pose": {
                        "before": _schema_pose(pose_before),
                        "after": _schema_pose(pose_after),
                        "before_captured_at": rgb_started_at,
                        "after_captured_at": captured_at,
                        "pid": self.pid,
                        "delta": delta,
                        "camera_static": camera_static,
                    },
                    "rgb": {
                        "path": str(Path("..") / ".." / "images" / image_path.name),
                        "source": self.screenshot_source,
                        "format": self.image_format,
                        "requested_width": self.screenshot_width,
                        "requested_height": self.screenshot_height,
                        "quality": self.screenshot_quality,
                        "started_at": rgb_started_at,
                        "completed_at": rgb_completed_at,
                        "file_mtime_unix_ns": rgb_mtime_ns,
                    },
                    "depth": depth
                    or {
                        "status": "disabled",
                        "metric_depth": False,
                        "depth_space": None,
                    },
                    "synchronization": {
                        "status": sync_status,
                        "rgb_depth_delta_ms": rgb_depth_delta_ms,
                        "resolution_match": resolution_match,
                        "pixel_alignment": "unverified_obs_scene_transform",
                        "guarantee": "static-camera timestamp alignment; not same GPU frame",
                    },
                }
                sample_metadata_path.write_text(
                    json.dumps(sample_metadata, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                row: dict[str, object] = {
                    "sequence": ordinal,
                    "point_index": point.index,
                    "label": point.label,
                    "time_sec": point.time_sec,
                    **point_metadata,
                    "image": str(Path("images") / image_path.name),
                    "depth": str(Path("samples") / sample_dir.name / "depth.npy")
                    if depth is not None
                    else "",
                    "depth_preview": str(
                        Path("samples") / sample_dir.name / "depth_preview.png"
                    )
                    if depth is not None
                    else "",
                    "sample_metadata": str(
                        Path("samples") / sample_dir.name / "metadata.json"
                    ),
                    "depth_space": str(depth.get("depth_space") or "") if depth else "",
                    "depth_sync_status": sync_status,
                    "rgb_depth_delta_ms": rgb_depth_delta_ms,
                    "captured_at": captured_at,
                    **_pose_columns("target", point.pose),
                    **_pose_columns("actual", pose_before),
                    **_pose_columns("actual_after", pose_after),
                }
                rows.append(row)
                if on_progress is not None:
                    on_progress(ordinal, len(values), f"已保存 / Saved {filename}")
        except InterruptedError:
            stopped = True
        except Exception as exc:
            run_error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if (run_error is not None or stopped) and start_pose is not None:
                restore_attempted = True
                try:
                    restored_pose = self.mover.move_to(
                        start_pose,
                        stop_requested=lambda: False,
                        on_update=(
                            (lambda message: on_log(f"回位 / Restore：{message}"))
                            if on_log is not None
                            else None
                        ),
                    )
                    restore_succeeded = True
                except Exception as exc:
                    restore_succeeded = False
                    restore_error = f"{type(exc).__name__}: {exc}"
                    if on_log is not None:
                        on_log(f"相机自动回位失败 / Camera restore failed: {restore_error}")
            payload = {
                "format": "bmw-standalone-capture-manifest-v2",
                "mode": mode,
                "status": "failed" if run_error else ("stopped" if stopped else "complete"),
                "error": run_error,
                "control_method": "standalone_absolute_set_pose",
                "absolute_target_pose": True,
                "atomic_absolute_set_pose": True,
                "requested_count": len(values),
                "captured_count": len(rows),
                "capture_plan": dict(run_metadata or {}),
                "coordinate_system": COORDINATE_SCALE.coordinate_system(),
                "depth_enabled": self.depth_enabled,
                "depth_timeout_seconds": self.depth_timeout,
                "depth_space": "raw_device_depth" if self.depth_enabled else None,
                "metric_depth": False,
                "start_pose": start_pose.as_dict() if start_pose is not None else None,
                "restore_attempted": restore_attempted,
                "restore_succeeded": restore_succeeded,
                "restored_pose": (
                    restored_pose.as_dict() if restored_pose is not None else None
                ),
                "restore_error": restore_error,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "frames": rows,
            }
            manifest_json.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if rows:
                with manifest_csv.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(rows)
            else:
                manifest_csv.write_text("sequence,label,image\n", encoding="utf-8-sig")

        return CaptureRunResult(
            session_dir=session_dir,
            manifest_json=manifest_json,
            manifest_csv=manifest_csv,
            captured_count=len(rows),
            requested_count=len(values),
            stopped=stopped,
        )
