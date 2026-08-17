from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .config import AppConfig


DEPTH_REQUEST_FILENAME = "re9_depth_request.json"
DEPTH_STATUS_FILENAME = "re9_depth_status.json"
DEPTH_HEARTBEAT_FILENAME = "re9_depth_heartbeat.json"
DEPTH_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DepthCaptureResult:
    capture_id: str
    depth_path: Path
    raw_path: Path
    preview_path: Path
    valid_mask_path: Path
    camera_metadata_path: Path
    width: int
    height: int
    near_clip: float
    far_clip: float
    projection_matrix: tuple[float, ...]
    render_frame_id: int
    reversed_z: bool
    valid_pixel_count: int


class DepthBridge:
    """File protocol for the optional REFramework D3D12 depth plugin."""

    def __init__(self, config: AppConfig, meters_per_game_unit: float = 1.0) -> None:
        game_config = config.raw.get("game") or {}
        configured_data_dir = game_config.get("reframework_data_dir")
        self.data_dir = (
            Path(os.path.expandvars(str(configured_data_dir))).expanduser()
            if configured_data_dir
            else config.control_file.parent
        )
        self.request_file = self.data_dir / DEPTH_REQUEST_FILENAME
        self.status_file = self.data_dir / DEPTH_STATUS_FILENAME
        self.heartbeat_file = self.data_dir / DEPTH_HEARTBEAT_FILENAME
        self.meters_per_game_unit = float(meters_per_game_unit)
        if not math.isfinite(self.meters_per_game_unit) or self.meters_per_game_unit <= 0:
            raise ValueError("meters_per_game_unit must be a positive finite number.")

    def read_heartbeat(self) -> dict[str, Any] | None:
        return _read_json(self.heartbeat_file)

    def is_ready(self, max_age_sec: float = 5.0) -> bool:
        heartbeat = self.read_heartbeat()
        if not heartbeat or heartbeat.get("status") != "ready":
            return False
        if int(heartbeat.get("schema_version") or 0) != DEPTH_SCHEMA_VERSION:
            return False
        updated_at = float(heartbeat.get("updated_at_unix") or 0.0)
        return updated_at > 0 and time.time() - updated_at <= max(0.1, float(max_age_sec))

    def wait_until_ready(self, timeout_sec: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout_sec))
        while True:
            if self.is_ready():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.1, remaining))

    def status_text(self) -> str:
        heartbeat = self.read_heartbeat()
        if not heartbeat:
            return "Depth plugin: not detected"
        version = str(heartbeat.get("plugin_version") or "unknown")
        renderer = str(heartbeat.get("renderer") or "unknown")
        if self.is_ready():
            return f"Depth plugin: ready ({version}, {renderer})"
        return f"Depth plugin: offline or stale ({version}, {renderer})"

    def capture(
        self,
        capture_id: str,
        dataset_dir: str | Path,
        sample_id: str,
        timeout_sec: float = 10.0,
        expected_width: int | None = None,
        expected_height: int | None = None,
    ) -> DepthCaptureResult:
        if not capture_id.strip():
            raise ValueError("capture_id must not be empty.")
        if not sample_id.strip():
            raise ValueError("sample_id must not be empty.")
        if not self.wait_until_ready(timeout_sec=min(5.0, max(0.1, timeout_sec))):
            raise RuntimeError(
                "RE9 depth plugin is not ready. Install re9_depth_bridge.dll under "
                "reframework/plugins and keep the game running before enabling depth capture."
            )

        root = Path(dataset_dir)
        raw_path = root / "depth_raw" / f"{sample_id}.raw"
        depth_path = root / "depth" / f"{sample_id}.npy"
        preview_path = root / "depth_preview" / f"{sample_id}.png"
        valid_mask_path = root / "valid_masks" / f"{sample_id}.png"
        camera_metadata_path = root / "cameras" / f"{sample_id}.json"
        for path in (raw_path, depth_path, preview_path, valid_mask_path, camera_metadata_path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.unlink(missing_ok=True)

        request = {
            "schema_version": DEPTH_SCHEMA_VERSION,
            "capture_id": capture_id,
            "raw_output_path": str(raw_path.resolve()),
            "requested_at_unix": time.time(),
            "expected_width": int(expected_width or 0),
            "expected_height": int(expected_height or 0),
        }
        _write_json_atomic(self.request_file, request)
        status = self._wait_for_capture_status(capture_id, timeout_sec)
        if status.get("status") != "ok":
            detail = str(status.get("error") or status.get("status") or "unknown plugin error")
            raise RuntimeError(f"Depth capture {capture_id} failed: {detail}")

        width = int(status.get("width") or 0)
        height = int(status.get("height") or 0)
        row_pitch = int(status.get("row_pitch") or 0)
        if width <= 0 or height <= 0 or row_pitch <= 0:
            raise RuntimeError(f"Depth capture {capture_id} returned invalid dimensions or row pitch.")
        if expected_width and width != int(expected_width):
            raise RuntimeError(
                f"Depth width {width} does not match RGB width {expected_width}; "
                "set the game render resolution to the OBS still resolution."
            )
        if expected_height and height != int(expected_height):
            raise RuntimeError(
                f"Depth height {height} does not match RGB height {expected_height}; "
                "set the game render resolution to the OBS still resolution."
            )

        status_raw_path = Path(str(status.get("raw_path") or raw_path))
        if status_raw_path.resolve() != raw_path.resolve():
            raise RuntimeError(f"Depth plugin returned an unexpected raw path for {capture_id}.")
        required_size = row_pitch * height
        if not raw_path.exists() or raw_path.stat().st_size < required_size:
            raise RuntimeError(
                f"Depth raw file is incomplete for {capture_id}: expected at least "
                f"{required_size} bytes at {raw_path}."
            )

        raw_depth = decode_raw_depth(raw_path, status)
        projection = tuple(float(value) for value in status.get("projection_matrix") or ())
        if len(projection) != 16:
            raise RuntimeError(f"Depth capture {capture_id} did not return a 4x4 projection matrix.")
        near_clip = float(status.get("near_clip") or 0.0)
        far_clip = float(status.get("far_clip") or 0.0)
        if not (math.isfinite(near_clip) and math.isfinite(far_clip) and 0 < near_clip < far_clip):
            raise RuntimeError(f"Depth capture {capture_id} returned invalid near/far clip planes.")

        linear_game_units, reversed_z = linearize_depth(
            raw_depth,
            projection,
            near_clip=near_clip,
            far_clip=far_clip,
        )
        linear_meters = linear_game_units.astype(np.float32, copy=False) * self.meters_per_game_unit
        far_endpoint = raw_depth <= 1e-7 if reversed_z else raw_depth >= 1.0 - 1e-7
        valid = (
            np.isfinite(linear_meters)
            & (linear_meters > near_clip * self.meters_per_game_unit * 0.5)
            & (linear_meters < far_clip * self.meters_per_game_unit * 1.001)
            & ~far_endpoint
        )
        linear_meters = np.where(valid, linear_meters, np.nan).astype(np.float32, copy=False)

        _save_npy_atomic(depth_path, linear_meters)
        _save_mask_atomic(valid_mask_path, valid)
        _save_preview_atomic(preview_path, linear_meters, valid)

        metadata = {
            **status,
            "schema_version": DEPTH_SCHEMA_VERSION,
            "capture_id": capture_id,
            "depth_path": str(depth_path),
            "depth_raw_path": str(raw_path),
            "depth_preview_path": str(preview_path),
            "valid_mask_path": str(valid_mask_path),
            "depth_unit": "m",
            "depth_representation": "linear_view_z",
            "meters_per_game_unit": self.meters_per_game_unit,
            "reversed_z": reversed_z,
            "valid_pixel_count": int(valid.sum()),
            "total_pixel_count": int(width * height),
            "processed_at_unix": time.time(),
        }
        _write_json_atomic(camera_metadata_path, metadata)

        return DepthCaptureResult(
            capture_id=capture_id,
            depth_path=depth_path,
            raw_path=raw_path,
            preview_path=preview_path,
            valid_mask_path=valid_mask_path,
            camera_metadata_path=camera_metadata_path,
            width=width,
            height=height,
            near_clip=near_clip,
            far_clip=far_clip,
            projection_matrix=projection,
            render_frame_id=int(status.get("render_frame_id") or 0),
            reversed_z=reversed_z,
            valid_pixel_count=int(valid.sum()),
        )

    def _wait_for_capture_status(self, capture_id: str, timeout_sec: float) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.1, float(timeout_sec))
        last_status: dict[str, Any] | None = None
        while True:
            status = _read_json(self.status_file)
            if status:
                last_status = status
                if str(status.get("capture_id") or "") == capture_id:
                    state = str(status.get("status") or "")
                    if state in {"ok", "error"}:
                        return status
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stale = str((last_status or {}).get("capture_id") or "none")
                raise TimeoutError(
                    f"Timed out waiting {timeout_sec:.1f}s for depth capture {capture_id}; "
                    f"last plugin capture id was {stale}."
                )
            time.sleep(min(0.05, remaining))


def decode_raw_depth(path: str | Path, status: dict[str, Any]) -> np.ndarray:
    width = int(status.get("width") or 0)
    height = int(status.get("height") or 0)
    row_pitch = int(status.get("row_pitch") or 0)
    stride = int(status.get("pixel_stride_bytes") or 0)
    encoding = str(status.get("depth_encoding") or "").lower()
    if width <= 0 or height <= 0 or row_pitch <= 0 or stride <= 0:
        raise ValueError("Depth status has invalid raw layout metadata.")
    if row_pitch < width * stride:
        raise ValueError("Depth row pitch is smaller than one decoded row.")

    raw = np.fromfile(Path(path), dtype=np.uint8, count=row_pitch * height)
    if raw.size != row_pitch * height:
        raise ValueError("Depth raw file ended before the declared row layout.")
    rows = raw.reshape(height, row_pitch)[:, : width * stride]

    if encoding == "float32":
        if stride < 4:
            raise ValueError("float32 depth requires at least four bytes per pixel.")
        pixels = rows.reshape(height, width, stride)[:, :, :4].copy()
        return pixels.view("<f4").reshape(height, width).copy()
    if encoding == "d24_unorm":
        if stride < 4:
            raise ValueError("d24_unorm depth requires at least four bytes per pixel.")
        pixels = rows.reshape(height, width, stride)[:, :, :4].copy()
        packed = pixels.view("<u4").reshape(height, width)
        return ((packed & 0x00FF_FFFF).astype(np.float32) / np.float32(0x00FF_FFFF)).copy()
    if encoding == "d16_unorm":
        if stride < 2:
            raise ValueError("d16_unorm depth requires at least two bytes per pixel.")
        pixels = rows.reshape(height, width, stride)[:, :, :2].copy()
        packed = pixels.view("<u2").reshape(height, width)
        return (packed.astype(np.float32) / np.float32(0xFFFF)).copy()
    raise ValueError(f"Unsupported depth encoding from plugin: {encoding or 'missing'}")


def linearize_depth(
    raw_depth: np.ndarray,
    projection_matrix: tuple[float, ...] | list[float],
    near_clip: float,
    far_clip: float,
) -> tuple[np.ndarray, bool]:
    """Convert D3D depth to absolute view-space Z and detect reverse-Z."""
    matrix = np.asarray(projection_matrix, dtype=np.float64)
    if matrix.size != 16:
        raise ValueError("projection_matrix must contain 16 values.")
    matrix = matrix.reshape(4, 4)
    near = float(near_clip)
    far = float(far_clip)
    if not (math.isfinite(near) and math.isfinite(far) and 0 < near < far):
        raise ValueError("near_clip and far_clip must satisfy 0 < near < far.")

    best: tuple[float, np.ndarray, bool] | None = None
    for candidate in (matrix, matrix.T):
        a = candidate[2, 2]
        b = candidate[2, 3]
        c = candidate[3, 2]
        d = candidate[3, 3]
        for reversed_z in (False, True):
            near_raw, far_raw = (1.0, 0.0) if reversed_z else (0.0, 1.0)
            near_value = _projected_view_z(near_raw, a, b, c, d)
            far_value = _projected_view_z(far_raw, a, b, c, d)
            if not (math.isfinite(near_value) and math.isfinite(far_value)):
                continue
            near_value = abs(near_value)
            far_value = abs(far_value)
            if near_value <= 0 or far_value <= 0:
                continue
            cost = abs(math.log(near_value / near)) + abs(math.log(far_value / far))
            if best is None or cost < best[0]:
                best = (cost, candidate, reversed_z)

    if best is None or best[0] > 1.0:
        raise ValueError("Projection matrix does not agree with the reported near/far clip planes.")
    candidate = best[1]
    reversed_z = best[2]
    a = candidate[2, 2]
    b = candidate[2, 3]
    c = candidate[3, 2]
    d = candidate[3, 3]
    raw = np.asarray(raw_depth, dtype=np.float64)
    denominator = raw * c - a
    with np.errstate(divide="ignore", invalid="ignore"):
        linear = np.abs((b - raw * d) / denominator)
    return linear.astype(np.float32), reversed_z


def _projected_view_z(raw: float, a: float, b: float, c: float, d: float) -> float:
    denominator = raw * c - a
    if abs(denominator) < 1e-12:
        return math.inf
    return float((b - raw * d) / denominator)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _save_npy_atomic(path: Path, values: np.ndarray) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temporary.open("wb") as handle:
            np.save(handle, values, allow_pickle=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _save_mask_atomic(path: Path, valid: np.ndarray) -> None:
    _save_png_atomic(path, np.where(valid, 255, 0).astype(np.uint8))


def _save_preview_atomic(path: Path, depth: np.ndarray, valid: np.ndarray) -> None:
    preview = np.zeros(depth.shape, dtype=np.uint16)
    values = depth[valid]
    if values.size:
        low, high = np.percentile(values, [1.0, 99.0])
        if not math.isfinite(float(low)) or not math.isfinite(float(high)) or high <= low:
            low = float(np.nanmin(values))
            high = float(np.nanmax(values))
        if high > low:
            normalized = (np.log(np.clip(depth, low, high)) - math.log(low)) / (math.log(high) - math.log(low))
            preview[valid] = np.round((1.0 - normalized[valid]) * 65535.0).astype(np.uint16)
        else:
            preview[valid] = np.uint16(65535)
    _save_png_atomic(path, preview)


def _save_png_atomic(path: Path, values: np.ndarray) -> None:
    temporary = path.with_name(f".{path.stem}.{os.getpid()}.{time.time_ns()}.tmp.png")
    try:
        Image.fromarray(values).save(temporary, format="PNG")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
