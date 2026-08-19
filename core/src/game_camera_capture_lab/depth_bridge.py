from __future__ import annotations

from array import array
from dataclasses import dataclass
import datetime as dt
import json
import math
import os
from pathlib import Path
import struct
import sys
import time
from typing import Any
import uuid
import zlib


SUPPORTED_FORMATS = {
    "r32_float": 4,
    "r32_typeless": 4,
    "d32_float": 4,
    "r24_g8_typeless": 4,
    "d24_unorm_s8_uint": 4,
    "d24_unorm_x8_uint": 4,
    "r24_unorm_x8_uint": 4,
    "r16_typeless": 2,
    "d16_unorm": 2,
    "r16_unorm": 2,
    "r16_float": 2,
    "r32_g8_typeless": 8,
    "d32_float_s8_uint": 8,
    "r32_float_x8_uint": 8,
}


def _default_channel_dir(game_id: str) -> Path:
    configured = os.environ.get("GAME_CAMERA_DEPTH_BRIDGE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData/Local"
    return base / "GameCameraCaptureLab" / "depth_bridge" / game_id


@dataclass(frozen=True)
class DepthCaptureTicket:
    request_id: str
    request_path: Path
    response_path: Path
    raw_path: Path
    requested_at: str


class DepthBridge:
    """File-IPC client for the repository-owned native D3D12 depth runtime.

    The injected camera runtime lazily installs its D3D12 hooks only after a
    ``*.request`` marker appears. It copies the selected scene-depth resource
    to a CPU readback buffer and writes a raw payload plus JSON response. This
    Python side converts that payload to portable float32 NPY and a 16-bit
    preview PNG. No ReShade installation is required.
    """

    def __init__(
        self,
        channel_dir: str | Path | None = None,
        *,
        game_id: str = "unified",
    ) -> None:
        normalized_game_id = game_id.strip().casefold()
        if not normalized_game_id or any(
            char not in "abcdefghijklmnopqrstuvwxyz0123456789-_"
            for char in normalized_game_id
        ):
            raise ValueError("Depth bridge game_id must be a safe file-name component")
        self.game_id = normalized_game_id
        self.channel_dir = (
            Path(channel_dir).expanduser().resolve()
            if channel_dir is not None
            else _default_channel_dir(self.game_id)
        )
        self.requests_dir = self.channel_dir / "requests"
        self.responses_dir = self.channel_dir / "responses"

    def status(self) -> dict[str, Any]:
        latest = None
        last_capture = None
        runtime = None
        if self.responses_dir.exists():
            responses = sorted(
                self.responses_dir.glob("*.json"),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
            if responses:
                try:
                    latest = json.loads(responses[0].read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    latest = {"status": "invalid_response", "path": str(responses[0])}
        last_capture_path = self.channel_dir / "last_capture.json"
        if last_capture_path.exists():
            try:
                last_capture = json.loads(
                    last_capture_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError):
                last_capture = {
                    "status": "invalid_last_capture",
                    "path": str(last_capture_path),
                }
        runtime_status_path = self.channel_dir / "runtime_status.json"
        if runtime_status_path.exists():
            try:
                runtime = json.loads(runtime_status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                runtime = {
                    "state": "invalid_runtime_status",
                    "path": str(runtime_status_path),
                }
        return {
            "game_id": self.game_id,
            "channel_dir": str(self.channel_dir),
            "requests_dir": str(self.requests_dir),
            "responses_dir": str(self.responses_dir),
            "latest_response": latest,
            "last_capture": last_capture,
            "runtime": runtime,
            "backend": "native_d3d12_runtime",
            "protocol": "game-camera-depth-bridge/v2",
        }

    def begin_capture(self) -> DepthCaptureTicket:
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        self.responses_dir.mkdir(parents=True, exist_ok=True)
        request_id = uuid.uuid4().hex
        request_path = self.requests_dir / f"{request_id}.request"
        response_path = self.responses_dir / f"{request_id}.json"
        raw_path = self.responses_dir / f"{request_id}.raw"
        requested_at = dt.datetime.now().astimezone().isoformat()
        temporary = self.requests_dir / f"{request_id}.tmp"
        temporary.write_text(
            json.dumps(
                {
                    "protocol": "game-camera-depth-bridge/v2",
                    "request_id": request_id,
                    "requested_at": requested_at,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(request_path)
        return DepthCaptureTicket(
            request_id=request_id,
            request_path=request_path,
            response_path=response_path,
            raw_path=raw_path,
            requested_at=requested_at,
        )

    def wait_capture(
        self,
        ticket: DepthCaptureTicket,
        output_dir: str | Path,
        *,
        timeout: float = 8.0,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if ticket.response_path.exists():
                try:
                    response = json.loads(
                        ticket.response_path.read_text(encoding="utf-8")
                    )
                except (OSError, json.JSONDecodeError):
                    time.sleep(0.02)
                    continue
                if response.get("request_id") != ticket.request_id:
                    raise RuntimeError("Depth bridge response ID does not match request")
                if response.get("status") != "completed":
                    raise RuntimeError(
                        f"Depth bridge failed: {response.get('error') or response}"
                    )
                return self._convert_response(ticket, response, Path(output_dir))
            time.sleep(0.01)
        ticket.request_path.unlink(missing_ok=True)
        raise TimeoutError(
            "Native D3D12 depth capture timed out. Restart the game and inject "
            "the current repository-owned UeCameraRuntime.dll, then verify "
            f"the channel directory: {self.channel_dir}"
        )

    def capture(
        self,
        output_dir: str | Path,
        *,
        timeout: float = 8.0,
    ) -> dict[str, Any]:
        ticket = self.begin_capture()
        return self.wait_capture(ticket, output_dir, timeout=timeout)

    def cancel(self, ticket: DepthCaptureTicket) -> None:
        ticket.request_path.unlink(missing_ok=True)
        ticket.response_path.unlink(missing_ok=True)
        ticket.raw_path.unlink(missing_ok=True)

    def _convert_response(
        self,
        ticket: DepthCaptureTicket,
        response: dict[str, Any],
        output_dir: Path,
    ) -> dict[str, Any]:
        output_dir.mkdir(parents=True, exist_ok=True)
        raw_path = Path(response.get("raw_path") or ticket.raw_path)
        if not raw_path.is_absolute():
            raw_path = ticket.response_path.parent / raw_path
        raw_path = raw_path.resolve()
        response_dir = ticket.response_path.parent.resolve()
        if (
            raw_path.parent != response_dir
            or raw_path.name != f"{ticket.request_id}.raw"
        ):
            raise RuntimeError("Depth bridge response points outside its IPC response file")
        width = int(response["width"])
        height = int(response["height"])
        row_pitch = int(response["row_pitch"])
        if width <= 0 or height <= 0 or width > 16384 or height > 16384:
            raise ValueError("Depth bridge returned invalid dimensions")
        if row_pitch <= 0 or row_pitch > 1024 * 1024:
            raise ValueError("Depth bridge returned an invalid row pitch")
        expected_size = row_pitch * height
        if raw_path.stat().st_size != expected_size:
            raise ValueError("Depth bridge payload size does not match its metadata")
        raw = raw_path.read_bytes()
        format_name = str(response["format"]).lower()
        values = decode_device_depth(raw, width, height, row_pitch, format_name)
        stats = depth_statistics(values)
        configured_reversed_z = response.get("reversed_z")
        configured_reversed_z_source = str(
            response.get("reversed_z_source") or "unknown"
        )
        verified_sources = {
            "game_projection_calibration",
            "verified_game_profile",
        }
        reversed_z_is_verified = (
            isinstance(configured_reversed_z, bool)
            and configured_reversed_z_source in verified_sources
        )
        reversed_z = (
            configured_reversed_z
            if reversed_z_is_verified
            else stats["inferred_reversed_z"]
        )
        reversed_z_source = (
            configured_reversed_z_source
            if reversed_z_is_verified
            else "clear_value_distribution_heuristic"
        )

        depth_path = output_dir / "depth.npy"
        preview_path = output_dir / "depth_preview.png"
        write_float32_npy(depth_path, values, (height, width))
        preview_low, preview_high = depth_preview_range(values)
        preview_width, preview_height = write_depth_preview_png(
            preview_path,
            values,
            width,
            height,
            reversed_z=reversed_z,
            normalization_range=(preview_low, preview_high),
        )
        converted_at = dt.datetime.now().astimezone().isoformat()
        result = {
            **response,
            "protocol": str(
                response.get("protocol") or "game-camera-depth-bridge/v2"
            ),
            "request_id": ticket.request_id,
            "requested_at": ticket.requested_at,
            "converted_at": converted_at,
            "depth_path": str(depth_path),
            "preview_path": str(preview_path),
            "preview_width": preview_width,
            "preview_height": preview_height,
            "depth_encoding": "float32_numpy",
            "depth_space": "raw_device_depth",
            "metric_depth": False,
            "near_plane": None,
            "far_plane": None,
            "reversed_z": reversed_z,
            "reversed_z_source": reversed_z_source,
            "configured_reversed_z": configured_reversed_z,
            "configured_reversed_z_source": configured_reversed_z_source,
            "preview_transform": "near_is_white",
            "preview_normalization": "sampled_p01_p99_gamma",
            "preview_encoding": "uint8_grayscale_png",
            "preview_gamma": 0.5,
            "preview_depth_low": preview_low,
            "preview_depth_high": preview_high,
            "statistics": stats,
        }
        last_capture_path = self.channel_dir / "last_capture.json"
        last_capture_temporary = self.channel_dir / "last_capture.tmp"
        last_capture_temporary.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        last_capture_temporary.replace(last_capture_path)
        raw_path.unlink(missing_ok=True)
        ticket.response_path.unlink(missing_ok=True)
        ticket.request_path.unlink(missing_ok=True)
        return result


def decode_device_depth(
    raw: bytes,
    width: int,
    height: int,
    row_pitch: int,
    format_name: str,
) -> array:
    if width <= 0 or height <= 0:
        raise ValueError("Depth dimensions must be positive")
    if format_name not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported depth format: {format_name}")
    bytes_per_pixel = SUPPORTED_FORMATS[format_name]
    tight_pitch = width * bytes_per_pixel
    if row_pitch < tight_pitch or len(raw) < row_pitch * height:
        raise ValueError("Depth readback payload is shorter than its metadata")

    values = array("f")
    is_little_endian = sys.byteorder == "little"
    for y in range(height):
        row = raw[y * row_pitch : y * row_pitch + tight_pitch]
        if format_name in {"r32_float", "r32_typeless", "d32_float"}:
            decoded = array("f")
            decoded.frombytes(row)
            if not is_little_endian:
                decoded.byteswap()
            values.extend(decoded)
        elif format_name in {
            "r24_g8_typeless",
            "d24_unorm_s8_uint",
            "d24_unorm_x8_uint",
            "r24_unorm_x8_uint",
        }:
            decoded_u32 = array("I")
            decoded_u32.frombytes(row)
            if not is_little_endian:
                decoded_u32.byteswap()
            values.extend((value & 0xFFFFFF) / 16777215.0 for value in decoded_u32)
        elif format_name in {"r16_typeless", "d16_unorm", "r16_unorm"}:
            decoded_u16 = array("H")
            decoded_u16.frombytes(row)
            if not is_little_endian:
                decoded_u16.byteswap()
            values.extend(value / 65535.0 for value in decoded_u16)
        elif format_name == "r16_float":
            values.extend(
                struct.unpack_from("<e", row, x * bytes_per_pixel)[0]
                for x in range(width)
            )
        elif format_name in {
            "r32_g8_typeless",
            "d32_float_s8_uint",
            "r32_float_x8_uint",
        }:
            values.extend(
                struct.unpack_from("<f", row, x * bytes_per_pixel)[0]
                for x in range(width)
            )
    if len(values) != width * height:
        raise RuntimeError("Decoded depth sample count is inconsistent")
    return values


def depth_statistics(values: array) -> dict[str, Any]:
    finite_count = 0
    minimum = math.inf
    maximum = -math.inf
    total = 0.0
    stride = max(1, len(values) // 65536)
    sample: list[float] = []
    for index, raw_value in enumerate(values):
        value = float(raw_value)
        if not math.isfinite(value):
            continue
        finite_count += 1
        minimum = min(minimum, value)
        maximum = max(maximum, value)
        total += value
        if index % stride == 0:
            sample.append(value)
    if finite_count == 0:
        raise ValueError("Depth payload contains no finite samples")
    sample.sort()
    median = sample[len(sample) // 2]
    inferred_reversed = median < 0.5
    return {
        "count": len(values),
        "finite_count": finite_count,
        "min": minimum,
        "max": maximum,
        "mean": total / finite_count,
        "sampled_median": median,
        "inferred_reversed_z": inferred_reversed,
    }


def write_float32_npy(path: Path, values: array, shape: tuple[int, int]) -> None:
    height, width = shape
    if len(values) != height * width:
        raise ValueError("NPY shape does not match depth sample count")
    header = (
        "{'descr': '<f4', 'fortran_order': False, "
        f"'shape': ({height}, {width}), }}"
    )
    preamble_size = 10
    padding = (16 - ((preamble_size + len(header) + 1) % 16)) % 16
    encoded_header = (header + (" " * padding) + "\n").encode("latin1")
    payload = array("f", values)
    if sys.byteorder != "little":
        payload.byteswap()
    with path.open("wb") as handle:
        handle.write(b"\x93NUMPY")
        handle.write(bytes((1, 0)))
        handle.write(struct.pack("<H", len(encoded_header)))
        handle.write(encoded_header)
        payload.tofile(handle)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def depth_preview_range(values: array) -> tuple[float, float]:
    """Choose a robust display range without changing stored device depth.

    Reversed-Z games often place almost the entire useful scene in a tiny raw
    interval near zero. Mapping that interval directly to 0..1 produces an
    apparently black PNG even though the depth buffer is valid. Sampled 1st
    and 99th percentiles preserve scene contrast while leaving ``depth.npy``
    untouched and auditable.
    """

    if not values:
        return 0.0, 1.0
    stride = max(1, len(values) // 65536)
    sample = sorted(
        min(1.0, max(0.0, float(values[index])))
        for index in range(0, len(values), stride)
        if math.isfinite(float(values[index]))
    )
    if not sample:
        return 0.0, 1.0
    last = len(sample) - 1
    low = sample[int(last * 0.01)]
    high = sample[math.ceil(last * 0.99)]
    if high - low <= 1.0e-8:
        low = sample[0]
        high = sample[-1]
    if high - low <= 1.0e-8:
        return 0.0, 1.0
    return low, high


def write_depth_preview_png(
    path: Path,
    values: array,
    width: int,
    height: int,
    *,
    reversed_z: bool,
    normalization_range: tuple[float, float] | None = None,
) -> tuple[int, int]:
    low, high = normalization_range or depth_preview_range(values)
    scale_denominator = max(1.0e-8, high - low)
    scale = min(1.0, 960.0 / width, 540.0 / height)
    preview_width = max(1, round(width * scale))
    preview_height = max(1, round(height * scale))
    preview = bytearray()
    for y in range(preview_height):
        source_y = min(height - 1, int(y * height / preview_height))
        offset = source_y * width
        for x in range(preview_width):
            source_x = min(width - 1, int(x * width / preview_width))
            value = float(values[offset + source_x])
            if not math.isfinite(value):
                normalized = 0.0
            else:
                stretched = min(
                    1.0,
                    max(0.0, (value - low) / scale_denominator),
                )
                closeness = stretched if reversed_z else 1.0 - stretched
                normalized = math.sqrt(closeness)
            preview.append(round(normalized * 255.0))
    preview_bytes = bytes(preview)
    row_bytes = preview_width
    scanlines = bytearray()
    for y in range(preview_height):
        scanlines.append(0)
        start = y * row_bytes
        scanlines.extend(preview_bytes[start : start + row_bytes])
    ihdr = struct.pack(
        ">IIBBBBB", preview_width, preview_height, 8, 0, 0, 0, 0
    )
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(scanlines), level=6))
        + _png_chunk(b"IEND", b"")
    )
    return preview_width, preview_height
