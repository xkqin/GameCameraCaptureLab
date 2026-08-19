from __future__ import annotations

import csv
import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

from .backend import CameraBackend
from .coordinate_scale import COORDINATE_SCALE
from .depth_bridge import DepthBridge, DepthCaptureTicket
from .models import Pose
from .obs_bridge import OBSBridge
from .paths import STILLS_DIR, ensure_data_dirs
from .storage import safe_id


SAMPLE_FIELDS = [
    "sample_index",
    "scene_id",
    "label",
    "captured_at",
    "image_path",
    "depth_path",
    "depth_preview_path",
    "sample_metadata_path",
    "depth_space",
    "depth_sync_status",
    "rgb_depth_delta_ms",
    "obs_source",
    "x",
    "y",
    "z",
    "x_m",
    "y_m",
    "z_m",
    "q0",
    "q1",
    "q2",
    "q3",
    "yaw_degrees",
    "pitch_degrees",
    "roll_degrees",
    "fov_degrees",
]


def _pose_delta(before: Pose, after: Pose) -> dict[str, float]:
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


def _schema_pose(pose: Pose) -> dict[str, Any]:
    return {
        "position": {"x": pose.x, "y": pose.y, "z": pose.z},
        "position_m": COORDINATE_SCALE.position_m(pose.x, pose.y, pose.z),
        "rotation": {
            "yaw": pose.yaw_degrees,
            "pitch": pose.pitch_degrees,
            "roll": pose.roll_degrees,
        },
        "quaternion": {
            "x": pose.q0,
            "y": pose.q1,
            "z": pose.q2,
            "w": pose.q3,
        },
        "fov_degrees": pose.fov_degrees,
    }


def capture_rgb_depth_sample(
    backend: CameraBackend,
    obs: OBSBridge,
    depth_bridge: DepthBridge | None,
    *,
    sample_dir: Path,
    source_name: str,
    image_format: str,
    width: int,
    height: int,
    quality: int,
    depth_enabled: bool,
    metadata: dict[str, Any],
    depth_timeout: float = 8.0,
) -> dict[str, Any]:
    sample_dir.mkdir(parents=True, exist_ok=True)
    extension = "jpg" if image_format.lower() in {"jpg", "jpeg"} else "png"
    image_path = sample_dir / f"rgb.{extension}"
    pose_before = backend.pose()
    depth_ticket: DepthCaptureTicket | None = None
    rgb_started_at = dt.datetime.now().astimezone().isoformat()
    try:
        if depth_enabled:
            if depth_bridge is None:
                raise RuntimeError("Depth capture is enabled but no depth bridge is configured")
            depth_ticket = depth_bridge.begin_capture()
        source = obs.save_screenshot(
            image_path,
            source_name=source_name,
            image_format=extension,
            width=width,
            height=height,
            quality=quality,
        )
        rgb_completed_at = dt.datetime.now().astimezone().isoformat()
        depth = (
            depth_bridge.wait_capture(
                depth_ticket,
                sample_dir,
                timeout=depth_timeout,
            )
            if depth_enabled and depth_bridge is not None and depth_ticket is not None
            else None
        )
    except Exception:
        if depth_ticket is not None and depth_bridge is not None:
            depth_bridge.cancel(depth_ticket)
        raise

    pose_after = backend.pose()
    pose_delta = _pose_delta(pose_before, pose_after)
    camera_static = (
        pose_delta["position"] <= 0.01
        and pose_delta["yaw_degrees"] <= 0.1
        and pose_delta["pitch_degrees"] <= 0.1
        and pose_delta["roll_degrees"] <= 0.1
        and pose_delta["fov_degrees"] <= 0.1
    )
    rgb_mtime_ns = image_path.stat().st_mtime_ns
    depth_capture_ns = int(depth["captured_unix_ns"]) if depth else None
    rgb_depth_delta_ms = (
        abs(rgb_mtime_ns - depth_capture_ns) / 1_000_000.0
        if depth_capture_ns is not None
        else None
    )
    resolution_match = (
        int(depth.get("width", -1)) == width
        and int(depth.get("height", -1)) == height
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
    metadata_path = sample_dir / "metadata.json"
    sample_metadata = {
        "schema_version": "camera-static-sample/v1",
        "game_id": "kcd2",
        "coordinate_system": COORDINATE_SCALE.coordinate_system(),
        **metadata,
        "pose": {
            "before": _schema_pose(pose_before),
            "after": _schema_pose(pose_after),
            "before_captured_at": pose_before.captured_at,
            "after_captured_at": pose_after.captured_at,
            "pid": pose_before.pid,
            "delta": pose_delta,
            "camera_static": camera_static,
        },
        "rgb": {
            "path": str(image_path),
            "source": source,
            "format": extension,
            "requested_width": width,
            "requested_height": height,
            "quality": quality,
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
    metadata_path.write_text(
        json.dumps(sample_metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "pose": pose_before,
        "pose_after": pose_after,
        "image_path": image_path,
        "obs_source": source,
        "depth": depth,
        "sample_metadata_path": metadata_path,
        "depth_sync_status": sync_status,
        "rgb_depth_delta_ms": rgb_depth_delta_ms,
    }


class StillCaptureSession:
    def __init__(
        self,
        backend: CameraBackend,
        obs: OBSBridge,
        *,
        scene_id: str,
        depth_bridge: DepthBridge | None = None,
    ) -> None:
        ensure_data_dirs()
        self.backend = backend
        self.obs = obs
        self.scene_id = safe_id(scene_id)
        self.depth_bridge = depth_bridge
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = STILLS_DIR / f"{stamp}_{self.scene_id}"
        self.images_dir = self.output_dir / "images"
        self.samples_csv = self.output_dir / "samples.csv"
        self.samples_json = self.output_dir / "samples.json"
        self.rows: list[dict[str, Any]] = []

    def capture_current(
        self,
        *,
        label: str,
        source_name: str,
        image_format: str,
        width: int,
        height: int,
        quality: int,
        depth_enabled: bool = False,
        depth_timeout: float = 8.0,
    ) -> dict[str, Any]:
        sample_index = len(self.rows) + 1
        sample_dir = self.output_dir / "samples" / f"sample_{sample_index:06d}"
        captured = capture_rgb_depth_sample(
            self.backend,
            self.obs,
            self.depth_bridge,
            sample_dir=sample_dir,
            source_name=source_name,
            image_format=image_format,
            width=width,
            height=height,
            quality=quality,
            depth_enabled=depth_enabled,
            depth_timeout=depth_timeout,
            metadata={
                "scene_id": self.scene_id,
                "sample_index": sample_index,
                "label": label,
            },
        )
        pose = captured["pose"]
        depth = captured["depth"] or {}
        row = {
            "sample_index": sample_index,
            "scene_id": self.scene_id,
            "label": label,
            "captured_at": pose.captured_at,
            "image_path": str(captured["image_path"]),
            "depth_path": str(depth.get("depth_path") or ""),
            "depth_preview_path": str(depth.get("preview_path") or ""),
            "sample_metadata_path": str(captured["sample_metadata_path"]),
            "depth_space": str(depth.get("depth_space") or ""),
            "depth_sync_status": captured["depth_sync_status"],
            "rgb_depth_delta_ms": captured["rgb_depth_delta_ms"],
            "obs_source": captured["obs_source"],
            **pose.as_dict(),
            **{
                f"{axis}_m": value
                for axis, value in COORDINATE_SCALE.position_m(
                    pose.x, pose.y, pose.z
                ).items()
            },
        }
        self.rows.append(row)
        self._write_metadata()
        return row

    def _write_metadata(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.samples_csv.open(
            "w", newline="", encoding="utf-8-sig"
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=SAMPLE_FIELDS)
            writer.writeheader()
            for row in self.rows:
                writer.writerow(
                    {name: row.get(name, "") for name in SAMPLE_FIELDS}
                )
        self.samples_json.write_text(
            json.dumps(
                {
                    "scene_id": self.scene_id,
                    "count": len(self.rows),
                    "coordinate_system": COORDINATE_SCALE.coordinate_system(),
                    "samples": self.rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
