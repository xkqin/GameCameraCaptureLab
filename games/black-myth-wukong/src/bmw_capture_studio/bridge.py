from __future__ import annotations

import math
import mmap
import struct
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

from .models import CameraPose


MAPPING_NAME = "Local\\BmwUuuPoseBridge.v2"
MAPPING_SIZE = 64 * 1024
CAMERA_DATA = struct.Struct("<4Bf3f4f3f3f3f3f")
METADATA_OFFSET = 256
BRIDGE_METADATA = struct.Struct("<8IQ")
METADATA_MAGIC = 0x42574D42
METADATA_VERSION = 7
FLAG_BRIDGE_LOADED = 1 << 0
FLAG_CONNECT_CALLED = 1 << 1
FLAG_BUFFER_REQUESTED = 1 << 2
FLAG_NATIVE_CONTROL_READY = 1 << 3

CONTROL_OFFSET = 512
CONTROL_HEADER = struct.Struct("<8I")
CONTROL_COMMAND = struct.Struct("<7fI")
CONTROL_MAGIC = 0x43574D42
CONTROL_VERSION = 1
CONTROL_STATE_IDLE = 0
CONTROL_STATE_PENDING = 1
CONTROL_STATE_APPLIED = 2
CONTROL_STATE_ERROR = 3
CONTROL_CAP_FORWARD = 1 << 0
CONTROL_CAP_RIGHT = 1 << 1
CONTROL_CAP_UP = 1 << 2
CONTROL_CAP_YAW = 1 << 3
CONTROL_CAP_PITCH = 1 << 4
CONTROL_CAP_ROLL = 1 << 5
CONTROL_CAP_FOV = 1 << 6
CONTROL_CAP_SET_POSE = (
    CONTROL_CAP_FORWARD
    | CONTROL_CAP_RIGHT
    | CONTROL_CAP_UP
    | CONTROL_CAP_YAW
    | CONTROL_CAP_PITCH
    | CONTROL_CAP_ROLL
    | CONTROL_CAP_FOV
)
CONTROL_ERRORS = {
    0: "no error",
    1: "UUU is not loaded",
    2: "unsupported UUU version (this bridge is locked to 5.8.21)",
    3: "UUU camera feature is not available yet",
    4: "invalid native-control command",
    5: "UUU internal camera call failed",
}

TRAJECTORY_OFFSET = 1024
TRAJECTORY_HEADER = struct.Struct("<8I2fIfI3I")
TRAJECTORY_KEYFRAME = struct.Struct("<8f")
TRAJECTORY_MAGIC = 0x54574D42
TRAJECTORY_VERSION = 1
TRAJECTORY_COMMAND_START = 1
TRAJECTORY_COMMAND_STOP = 2
TRAJECTORY_STATE_IDLE = 0
TRAJECTORY_STATE_PENDING = 1
TRAJECTORY_STATE_PLAYING = 2
TRAJECTORY_STATE_COMPLETED = 3
TRAJECTORY_STATE_STOPPED = 4
TRAJECTORY_STATE_ERROR = 5
TRAJECTORY_ERRORS = {
    0: "no error",
    1: "UUU native camera control is unavailable",
    2: "invalid trajectory command",
    3: "invalid trajectory keyframes",
    4: "UUU internal camera call failed during trajectory playback",
}
MAX_TRAJECTORY_KEYFRAMES = (
    MAPPING_SIZE - TRAJECTORY_OFFSET - TRAJECTORY_HEADER.size
) // TRAJECTORY_KEYFRAME.size


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

    @property
    def native_control_ready(self) -> bool:
        return bool(self.flags & FLAG_NATIVE_CONTROL_READY)


@dataclass(frozen=True)
class NativeControlStatus:
    request_sequence: int
    acknowledge_sequence: int
    state: int
    error_code: int
    capabilities: int

    @property
    def ready(self) -> bool:
        return (self.capabilities & CONTROL_CAP_SET_POSE) == CONTROL_CAP_SET_POSE

    @property
    def error_message(self) -> str:
        if self.ready and self.state != CONTROL_STATE_ERROR:
            return CONTROL_ERRORS[0]
        return CONTROL_ERRORS.get(self.error_code, f"unknown error {self.error_code}")


@dataclass(frozen=True)
class NativeTrajectoryStatus:
    request_sequence: int
    acknowledge_sequence: int
    state: int
    error_code: int
    point_count: int
    duration_seconds: float
    playback_hz: float
    current_segment: int
    elapsed_seconds: float

    @property
    def playing(self) -> bool:
        return self.state == TRAJECTORY_STATE_PLAYING

    @property
    def completed(self) -> bool:
        return self.state == TRAJECTORY_STATE_COMPLETED

    @property
    def stopped(self) -> bool:
        return self.state == TRAJECTORY_STATE_STOPPED

    @property
    def failed(self) -> bool:
        return self.state == TRAJECTORY_STATE_ERROR

    @property
    def error_message(self) -> str:
        return TRAJECTORY_ERRORS.get(
            self.error_code, f"unknown trajectory error {self.error_code}"
        )


class UuuPoseBridge:
    def __init__(self, mapping_name: str = MAPPING_NAME) -> None:
        self.mapping_name = mapping_name
        self.mapping: mmap.mmap | None = None
        self._lock = threading.RLock()

    def connect(self) -> None:
        with self._lock:
            if self.mapping is not None:
                return
            if sys.platform != "win32":
                raise PoseUnavailableError(
                    "Linux 兼容模式不提供 UUU 共享内存；黑神话 UUU 原生位姿控制需要 Windows。"
                )
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

    def read_control_status(self) -> NativeControlStatus | None:
        with self._lock:
            self.connect()
            assert self.mapping is not None
            self.mapping.seek(CONTROL_OFFSET)
            raw = self.mapping.read(CONTROL_HEADER.size)
        (
            magic,
            version,
            size,
            request_sequence,
            acknowledge_sequence,
            state,
            error_code,
            capabilities,
        ) = CONTROL_HEADER.unpack(raw)
        if (
            magic != CONTROL_MAGIC
            or version != CONTROL_VERSION
            or size != CONTROL_HEADER.size + CONTROL_COMMAND.size
        ):
            return None
        return NativeControlStatus(
            request_sequence=request_sequence,
            acknowledge_sequence=acknowledge_sequence,
            state=state,
            error_code=error_code,
            capabilities=capabilities,
        )

    def read_trajectory_status(self) -> NativeTrajectoryStatus | None:
        with self._lock:
            self.connect()
            assert self.mapping is not None
            self.mapping.seek(TRAJECTORY_OFFSET)
            raw = self.mapping.read(TRAJECTORY_HEADER.size)
        (
            magic,
            version,
            size,
            request_sequence,
            acknowledge_sequence,
            state,
            error_code,
            point_count,
            duration_seconds,
            playback_hz,
            current_segment,
            elapsed_seconds,
            _command,
            _reserved1,
            _reserved2,
            _reserved3,
        ) = TRAJECTORY_HEADER.unpack(raw)
        if (
            magic != TRAJECTORY_MAGIC
            or version != TRAJECTORY_VERSION
            or size != TRAJECTORY_HEADER.size
        ):
            return None
        return NativeTrajectoryStatus(
            request_sequence=request_sequence,
            acknowledge_sequence=acknowledge_sequence,
            state=state,
            error_code=error_code,
            point_count=point_count,
            duration_seconds=duration_seconds,
            playback_hz=playback_hz,
            current_segment=current_segment,
            elapsed_seconds=elapsed_seconds,
        )

    def start_native_trajectory(
        self,
        points: Any,
        *,
        playback_hz: float = 60.0,
        timeout_seconds: float = 1.0,
    ) -> NativeTrajectoryStatus:
        values = list(points)
        if len(values) < 2:
            raise ValueError("平滑轨迹至少需要两个关键帧")
        if len(values) > MAX_TRAJECTORY_KEYFRAMES:
            raise ValueError(
                f"轨迹包含 {len(values)} 个关键帧，原生播放器最多支持 "
                f"{MAX_TRAJECTORY_KEYFRAMES} 个"
            )
        first_time = float(values[0].time_sec)
        rows: list[tuple[float, ...]] = []
        previous_time = -math.inf
        for point in values:
            relative_time = float(point.time_sec) - first_time
            row = (
                relative_time,
                float(point.pose.x),
                float(point.pose.y),
                float(point.pose.z),
                float(point.pose.yaw_degrees),
                float(point.pose.pitch_degrees),
                float(point.pose.roll_degrees),
                float(point.pose.fov_degrees),
            )
            if not all(math.isfinite(value) for value in row):
                raise ValueError("轨迹关键帧包含非有限数值")
            if relative_time <= previous_time:
                raise ValueError("轨迹 time_sec 必须严格递增")
            previous_time = relative_time
            rows.append(row)
        duration = rows[-1][0]
        if duration <= 0.0:
            raise ValueError("轨迹持续时间必须大于 0 秒")
        rate = min(240.0, max(30.0, float(playback_hz)))

        status = self.read_trajectory_status()
        if status is None:
            raise PoseUnavailableError(
                "Loaded pose bridge is too old for native smooth trajectory playback. "
                "Restart the game and inject the rebuilt bridge first."
            )
        if status.playing:
            raise RuntimeError("原生平滑轨迹已经在播放")
        sequence = max(status.request_sequence, status.acknowledge_sequence) + 1
        if sequence > 0x7FFFFFFF:
            sequence = 1

        with self._lock:
            self.connect()
            assert self.mapping is not None
            self.mapping.seek(TRAJECTORY_OFFSET + TRAJECTORY_HEADER.size)
            for row in rows:
                self.mapping.write(TRAJECTORY_KEYFRAME.pack(*row))
            header = TRAJECTORY_HEADER.pack(
                TRAJECTORY_MAGIC,
                TRAJECTORY_VERSION,
                TRAJECTORY_HEADER.size,
                status.request_sequence,
                status.acknowledge_sequence,
                TRAJECTORY_STATE_IDLE,
                0,
                len(rows),
                duration,
                rate,
                0,
                0.0,
                TRAJECTORY_COMMAND_START,
                0,
                0,
                0,
            )
            self.mapping.seek(TRAJECTORY_OFFSET)
            self.mapping.write(header)
            # Publish requestSequence last; the native worker treats it as the
            # commit marker after every keyframe and header field is visible.
            self.mapping.seek(TRAJECTORY_OFFSET + 12)
            self.mapping.write(struct.pack("<I", sequence))

        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            result = self.read_trajectory_status()
            if result is not None and result.acknowledge_sequence == sequence:
                if result.failed:
                    raise PoseUnavailableError(
                        f"Native trajectory start failed: {result.error_message}"
                    )
                # A very short path may already be completed before Python
                # observes the acknowledgement. The sequence acknowledgement
                # is the commit proof; any non-error state is valid here.
                return result
            time.sleep(0.001)
        raise TimeoutError("Timed out waiting for native trajectory start acknowledgement")

    def stop_native_trajectory(self, *, timeout_seconds: float = 1.0) -> NativeTrajectoryStatus:
        status = self.read_trajectory_status()
        if status is None:
            raise PoseUnavailableError("Native smooth trajectory control is unavailable")
        if status.state in (
            TRAJECTORY_STATE_IDLE,
            TRAJECTORY_STATE_COMPLETED,
            TRAJECTORY_STATE_STOPPED,
            TRAJECTORY_STATE_ERROR,
        ):
            return status
        sequence = max(status.request_sequence, status.acknowledge_sequence) + 1
        if sequence > 0x7FFFFFFF:
            sequence = 1
        with self._lock:
            self.connect()
            assert self.mapping is not None
            # NativeTrajectory.command is the uint32 immediately after
            # elapsed_seconds: offset +48.
            self.mapping.seek(TRAJECTORY_OFFSET + 48)
            self.mapping.write(struct.pack("<I", TRAJECTORY_COMMAND_STOP))
            self.mapping.seek(TRAJECTORY_OFFSET + 12)
            self.mapping.write(struct.pack("<I", sequence))
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            result = self.read_trajectory_status()
            if result is not None and result.acknowledge_sequence == sequence:
                return result
            time.sleep(0.001)
        raise TimeoutError("Timed out waiting for native trajectory stop acknowledgement")

    def apply_native_step(
        self,
        *,
        move_forward: float = 0.0,
        move_right: float = 0.0,
        move_up: float = 0.0,
        yaw_radians: float = 0.0,
        pitch_radians: float = 0.0,
        roll_radians: float = 0.0,
        fov_degrees: float = 0.0,
        set_fov: bool = False,
        timeout_seconds: float = 0.75,
    ) -> NativeControlStatus:
        values = (
            move_forward,
            move_right,
            move_up,
            yaw_radians,
            pitch_radians,
            roll_radians,
            fov_degrees,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("native UUU control values must be finite")
        status = self.read_control_status()
        if status is None:
            raise PoseUnavailableError(
                "Loaded pose bridge is too old for native UUU control. "
                "Restart the game and prepare/inject the rebuilt bridge first."
            )
        if not status.ready:
            raise PoseUnavailableError(
                "Native UUU control is not ready. UUU 5.8.21 must be injected, "
                "the free camera enabled, and Camera found must appear in its log."
            )
        sequence = max(status.request_sequence, status.acknowledge_sequence) + 1
        if sequence > 0x7FFFFFFF:
            sequence = 1
        command = CONTROL_COMMAND.pack(
            *values,
            int(set_fov),
        )
        with self._lock:
            self.connect()
            assert self.mapping is not None
            # Publish the payload first and the aligned sequence number last.
            # The in-process worker treats the sequence write as the commit.
            self.mapping.seek(CONTROL_OFFSET + CONTROL_HEADER.size)
            self.mapping.write(command)
            self.mapping.seek(CONTROL_OFFSET + 12)
            self.mapping.write(struct.pack("<I", sequence))

        deadline = time.monotonic() + max(0.05, timeout_seconds)
        while time.monotonic() < deadline:
            result = self.read_control_status()
            if result is not None and result.acknowledge_sequence == sequence:
                if result.state != CONTROL_STATE_APPLIED:
                    raise PoseUnavailableError(
                        f"Native UUU command failed: {result.error_message}"
                    )
                return result
            time.sleep(0.001)
        raise TimeoutError("Timed out waiting for native UUU camera command acknowledgement")

    def status(self) -> dict[str, Any]:
        metadata = self.read_metadata()
        control = self.read_control_status()
        trajectory = self.read_trajectory_status()
        try:
            pose = self.read_pose()
        except PoseUnavailableError as exc:
            return {
                "connected": False,
                "message": str(exc),
                "metadata": metadata,
                "control": control,
                "trajectory": trajectory,
            }
        return {
            "connected": True,
            "camera_enabled": pose.camera_enabled,
            "movement_locked": pose.movement_locked,
            "pose": pose,
            "metadata": metadata,
            "control": control,
            "trajectory": trajectory,
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
