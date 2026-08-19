"""KCD2 compatibility wrapper around the repository-wide depth bridge."""

from __future__ import annotations

from pathlib import Path
import sys


try:
    from game_camera_capture_lab.depth_bridge import (
        DepthBridge as _SharedDepthBridge,
        DepthCaptureTicket,
        SUPPORTED_FORMATS,
        decode_device_depth,
        depth_statistics,
        write_depth_preview_png,
        write_float32_npy,
    )
except ModuleNotFoundError:
    _core_src = Path(__file__).resolve().parents[4] / "core" / "src"
    if str(_core_src) not in sys.path:
        sys.path.insert(0, str(_core_src))
    from game_camera_capture_lab.depth_bridge import (
        DepthBridge as _SharedDepthBridge,
        DepthCaptureTicket,
        SUPPORTED_FORMATS,
        decode_device_depth,
        depth_statistics,
        write_depth_preview_png,
        write_float32_npy,
    )


class DepthBridge(_SharedDepthBridge):
    def __init__(self, channel_dir: str | Path | None = None) -> None:
        super().__init__(channel_dir, game_id="kcd2")


__all__ = [
    "DepthBridge",
    "DepthCaptureTicket",
    "SUPPORTED_FORMATS",
    "decode_device_depth",
    "depth_statistics",
    "write_depth_preview_png",
    "write_float32_npy",
]
