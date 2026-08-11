from __future__ import annotations

import math
import mmap
import struct
import threading
from dataclasses import dataclass
from typing import Any

from .models import CameraPose


MAPPING_NAME = "Local\\BmwUuuPoseBridge.v1"
MAPPING_SIZE = 8 * 1024
CAMERA_DATA = struct.Struct("<4Bf3f4f3f3f3f3f")
METADATA_OFFSET = 256
BRIDGE_METADATA = struct.Struct("<8IQ")
METADATA_MAGIC = 0x42574D42
METADATA_VERSION = 2
FLAG_BRIDGE_LOADED = 1 << 0
FLAG_CONNECT_CALLED = 1 << 1
FLAG_BUFFER_REQUESTED = 1 << 2


class PoseUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class BridgeMetadata:
    version: int
    size: int
    process_id: int
    connect_call_count: int
    buffer_request_count: int
    flags: int
    load_tick_milliseconds: int

    @property
    def bridge_loaded(self) -> bool:
        return bool(self.flags & FLAG_BRIDGE_LOADED)

    @property
    def connector_called(self) -> bool:
        return bool(self.flags & FLAG_CONNECT_CALLED)

    @property
    def buffer_requested(self) -> bool:
        return bool(self.flags & FLAG_BUFFER_REQUESTED)


class UuuPoseBridge:
    def __init__(self, mapping_name: str = MAPPING_NAME) -> None:
        self.mapping_name = mapping_name
        self.mapping: mmap.mmap | None = None
        self._lock = threading.RLock()

    def connect(self) -> None:
        with self._lock:
            if self.mapping is not None:
                return
            try:
                self.mapping = mmap.mmap(-1, MAPPING_SIZE, self.mapping_name)
            except (OSError, TypeError) as exc:
                raise PoseUnavailableError(
                    "无法打开 UUU 位姿共享内存；请先运行 Windows 版程序"
                ) from exc

    def close(self) -> None:
        with self._lock:
            if self.mapping is not None:
                self.mapping.close()
                self.mapping = None

    def read_metadata(self) -> BridgeMetadata | None:
        with self._lock:
            self.connect()
            assert self.mapping is not None
            self.mapping.seek(METADATA_OFFSET)
            raw = self.mapping.read(BRIDGE_METADATA.size)
        (
            magic,
            version,
            size,
            process_id,
            connect_call_count,
            buffer_request_count,
            flags,
            _reserved,
            load_tick_milliseconds,
        ) = BRIDGE_METADATA.unpack(raw)
        if magic != METADATA_MAGIC or version < METADATA_VERSION:
            return None
        return BridgeMetadata(
            version=version,
            size=size,
            process_id=process_id,
            connect_call_count=connect_call_count,
            buffer_request_count=buffer_request_count,
            flags=flags,
            load_tick_milliseconds=load_tick_milliseconds,
        )

    def read_pose(self) -> CameraPose:
        with self._lock:
            self.connect()
            assert self.mapping is not None

            # Two identical reads reduce the chance of observing a half-written pose.
            raw = b""
            for _ in range(3):
                self.mapping.seek(0)
                first = self.mapping.read(CAMERA_DATA.size)
                self.mapping.seek(0)
                second = self.mapping.read(CAMERA_DATA.size)
                raw = second
                if first == second:
                    break
        values = CAMERA_DATA.unpack(raw)
        (
            camera_enabled,
            movement_locked,
            _reserved1,
            _reserved2,
            fov,
            x,
            y,
            z,
            qx,
            qy,
            qz,
            qw,
            up_x,
            up_y,
            up_z,
            right_x,
            right_y,
            right_z,
            forward_x,
            forward_y,
            forward_z,
            pitch,
            yaw,
            roll,
        ) = values
        pose = CameraPose(
            x=x,
            y=y,
            z=z,
            yaw_degrees=math.degrees(yaw),
            pitch_degrees=math.degrees(pitch),
            roll_degrees=math.degrees(roll),
            fov_degrees=fov,
            qx=qx,
            qy=qy,
            qz=qz,
            qw=qw,
            right_x=right_x,
            right_y=right_y,
            right_z=right_z,
            up_x=up_x,
            up_y=up_y,
            up_z=up_z,
            forward_x=forward_x,
            forward_y=forward_y,
            forward_z=forward_z,
            camera_enabled=bool(camera_enabled),
            movement_locked=bool(movement_locked),
        )
        if not self._looks_valid(pose):
            raise PoseUnavailableError(
                "共享内存已建立，但 UUU 尚未写入有效位姿。"
                "请确认先加载位姿桥、再由 UUU 注入，并按 Insert 启用相机。"
            )
        return pose

    def status(self) -> dict[str, Any]:
        metadata = self.read_metadata()
        try:
            pose = self.read_pose()
        except PoseUnavailableError as exc:
            return {
                "connected": False,
                "message": str(exc),
                "metadata": metadata,
            }
        return {
            "connected": True,
            "camera_enabled": pose.camera_enabled,
            "movement_locked": pose.movement_locked,
            "pose": pose,
            "metadata": metadata,
        }

    @staticmethod
    def _looks_valid(pose: CameraPose) -> bool:
        values = (
            pose.x,
            pose.y,
            pose.z,
            pose.qx,
            pose.qy,
            pose.qz,
            pose.qw,
            pose.fov_degrees,
        )
        quaternion_norm = math.sqrt(
            pose.qx * pose.qx
            + pose.qy * pose.qy
            + pose.qz * pose.qz
            + pose.qw * pose.qw
        )
        return (
            all(math.isfinite(value) for value in values)
            and 1.0 <= pose.fov_degrees <= 179.0
            and 0.5 <= quaternion_norm <= 1.5
        )
