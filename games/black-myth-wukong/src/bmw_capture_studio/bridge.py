from __future__ import annotations

import math
import mmap
import os
import socket
import struct
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

from .models import CameraPose


MAPPING_NAME = "Local\\BmwCameraBridge.v1"
MAPPING_SIZE = 64 * 1024
CAMERA_DATA = struct.Struct("<4Bf3f4f3f3f3f3f")
PRECISE_POSE_OFFSET = 128
PRECISE_POSE = struct.Struct("<4I6dfI")
PRECISE_POSE_MAGIC = 0x50574D42
PRECISE_POSE_VERSION = 1
METADATA_OFFSET = 256
BRIDGE_METADATA = struct.Struct("<8IQ")
METADATA_MAGIC = 0x42574D42
METADATA_VERSION = 9
FLAG_BRIDGE_LOADED = 1 << 0
FLAG_CONNECT_CALLED = 1 << 1  # standalone camera hooks installed
FLAG_BUFFER_REQUESTED = 1 << 2  # at least one rendered pose observed
FLAG_NATIVE_CONTROL_READY = 1 << 3
FLAG_INPUT_CAPTURE_READY = 1 << 4
FLAG_HUD_CONTROL_READY = 1 << 5

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
    1: "standalone camera hooks are unavailable",
    2: "unsupported Black Myth game build",
    3: "the game camera has not rendered a pose yet",
    4: "invalid camera-control command",
    5: "standalone camera control failed internally",
}

ABSOLUTE_POSE_OFFSET = 768
ABSOLUTE_POSE = struct.Struct("<8I3d4fI3I")
ABSOLUTE_POSE_MAGIC = 0x41574D42
ABSOLUTE_POSE_VERSION = 1
ABSOLUTE_POSE_CAPABILITY = 1 << 7

HUD_CONTROL_OFFSET = 896
HUD_CONTROL = struct.Struct("<16I")
HUD_CONTROL_MAGIC = 0x48574D42
HUD_CONTROL_VERSION = 1
HUD_CONTROL_CAPABILITY = 1

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
    1: "standalone camera control is unavailable",
    2: "invalid trajectory command",
    3: "invalid trajectory keyframes",
    4: "standalone camera failed during trajectory playback",
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
    def hooks_installed(self) -> bool:
        return self.connector_called

    @property
    def buffer_requested(self) -> bool:
        return bool(self.flags & FLAG_BUFFER_REQUESTED)

    @property
    def pose_observed(self) -> bool:
        return self.buffer_requested

    @property
    def native_control_ready(self) -> bool:
        return bool(self.flags & FLAG_NATIVE_CONTROL_READY)

    @property
    def input_capture_ready(self) -> bool:
        return bool(self.flags & FLAG_INPUT_CAPTURE_READY)

    @property
    def hud_control_ready(self) -> bool:
        return bool(self.flags & FLAG_HUD_CONTROL_READY)


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
class AbsolutePoseStatus:
    request_sequence: int
    acknowledge_sequence: int
    state: int
    error_code: int
    capabilities: int

    @property
    def ready(self) -> bool:
        return bool(self.capabilities & ABSOLUTE_POSE_CAPABILITY)

    @property
    def error_message(self) -> str:
        if self.ready and self.state != CONTROL_STATE_ERROR:
            return CONTROL_ERRORS[0]
        return CONTROL_ERRORS.get(self.error_code, f"unknown error {self.error_code}")


@dataclass(frozen=True)
class HudControlStatus:
    request_sequence: int
    acknowledge_sequence: int
    state: int
    error_code: int
    capabilities: int
    hidden: bool

    @property
    def ready(self) -> bool:
        return bool(self.capabilities & HUD_CONTROL_CAPABILITY)

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


class CameraPoseBridge:
    """Windows shared-memory transport for the standalone camera bridge."""

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
                    "Linux 不能直接打开 Windows 共享内存；请配置 Camera Bridge Proton Relay。"
                )
            try:
                self.mapping = mmap.mmap(-1, MAPPING_SIZE, self.mapping_name)
            except (OSError, TypeError) as exc:
                raise PoseUnavailableError(
                    "无法打开 Camera Bridge 共享内存；请先把 BmwCameraBridge.dll 注入游戏。"
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
            precise_raw = b""
            for _ in range(3):
                self.mapping.seek(PRECISE_POSE_OFFSET)
                first_precise = self.mapping.read(PRECISE_POSE.size)
                self.mapping.seek(PRECISE_POSE_OFFSET)
                second_precise = self.mapping.read(PRECISE_POSE.size)
                precise_raw = second_precise
                if first_precise == second_precise:
                    break
        values = CAMERA_DATA.unpack(raw)
        (
            camera_enabled,
            movement_locked,
            hud_hidden,
            input_captured,
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
        if len(precise_raw) == PRECISE_POSE.size:
            (
                precise_magic,
                precise_version,
                precise_size,
                precise_sequence,
                precise_x,
                precise_y,
                precise_z,
                precise_pitch,
                precise_yaw,
                precise_roll,
                precise_fov,
                _precise_flags,
            ) = PRECISE_POSE.unpack(precise_raw)
            if (
                precise_magic == PRECISE_POSE_MAGIC
                and precise_version == PRECISE_POSE_VERSION
                and precise_size == PRECISE_POSE.size
                and precise_sequence > 0
                and precise_sequence % 2 == 0
            ):
                x, y, z = precise_x, precise_y, precise_z
                pitch = math.radians(precise_pitch)
                yaw = math.radians(precise_yaw)
                roll = math.radians(precise_roll)
                fov = precise_fov
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
            hud_hidden=bool(hud_hidden),
            input_captured=bool(input_captured),
        )
        if not self._looks_valid(pose):
            raise PoseUnavailableError(
                "共享内存已建立，但自研 Camera Bridge 尚未观察到有效游戏相机。"
                "请确认游戏画面正在渲染，且当前进程没有加载 UUU/旧 Connector。"
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

    def read_absolute_pose_status(self) -> AbsolutePoseStatus | None:
        with self._lock:
            self.connect()
            assert self.mapping is not None
            self.mapping.seek(ABSOLUTE_POSE_OFFSET)
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
            magic != ABSOLUTE_POSE_MAGIC
            or version != ABSOLUTE_POSE_VERSION
            or size != ABSOLUTE_POSE.size
        ):
            return None
        return AbsolutePoseStatus(
            request_sequence=request_sequence,
            acknowledge_sequence=acknowledge_sequence,
            state=state,
            error_code=error_code,
            capabilities=capabilities,
        )

    def read_hud_status(self) -> HudControlStatus | None:
        with self._lock:
            self.connect()
            assert self.mapping is not None
            self.mapping.seek(HUD_CONTROL_OFFSET)
            raw = self.mapping.read(HUD_CONTROL.size)
        return _decode_hud(raw)

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
            raise ValueError("native camera control values must be finite")
        status = self.read_control_status()
        if status is None:
            raise PoseUnavailableError(
                "Loaded Camera Bridge is too old for native camera control. "
                "Restart the game and inject the rebuilt standalone bridge first."
            )
        if not status.ready:
            raise PoseUnavailableError(
                "Native camera control is not ready. The standalone hooks must be installed "
                "and the game must have rendered at least one valid camera pose."
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
                        f"Native camera command failed: {result.error_message}"
                    )
                return result
            time.sleep(0.001)
        raise TimeoutError("Timed out waiting for native camera command acknowledgement")

    def set_pose(
        self,
        pose: CameraPose,
        *,
        enable_camera: bool = True,
        timeout_seconds: float = 1.0,
    ) -> CameraPose:
        values = (
            pose.x,
            pose.y,
            pose.z,
            pose.yaw_degrees,
            pose.pitch_degrees,
            pose.roll_degrees,
            pose.fov_degrees,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("absolute camera pose values must be finite")
        if not 1.0 <= pose.fov_degrees <= 179.0:
            raise ValueError("absolute camera FOV must be between 1 and 179 degrees")
        status = self.read_absolute_pose_status()
        if status is None or not status.ready:
            raise PoseUnavailableError(
                "The loaded bridge does not provide standalone absolute setPose. "
                "Restart the game without UUU and inject BmwCameraBridge.dll."
            )
        sequence = max(status.request_sequence, status.acknowledge_sequence) + 1
        if sequence > 0x7FFFFFFF:
            sequence = 1
        payload = ABSOLUTE_POSE.pack(
            ABSOLUTE_POSE_MAGIC,
            ABSOLUTE_POSE_VERSION,
            ABSOLUTE_POSE.size,
            status.request_sequence,
            status.acknowledge_sequence,
            CONTROL_STATE_IDLE,
            0,
            status.capabilities,
            float(pose.x),
            float(pose.y),
            float(pose.z),
            float(pose.yaw_degrees),
            float(pose.pitch_degrees),
            float(pose.roll_degrees),
            float(pose.fov_degrees),
            int(enable_camera),
            0,
            0,
            0,
        )
        with self._lock:
            self.connect()
            assert self.mapping is not None
            self.mapping.seek(ABSOLUTE_POSE_OFFSET)
            self.mapping.write(payload)
            self.mapping.seek(ABSOLUTE_POSE_OFFSET + 12)
            self.mapping.write(struct.pack("<I", sequence))

        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            result = self.read_absolute_pose_status()
            if result is not None and result.acknowledge_sequence == sequence:
                if result.state != CONTROL_STATE_APPLIED:
                    raise PoseUnavailableError(
                        f"Absolute setPose failed: {result.error_message}"
                    )
                break
            time.sleep(0.001)
        else:
            raise TimeoutError("Timed out waiting for absolute setPose acknowledgement")

        feedback_deadline = time.monotonic() + max(0.1, timeout_seconds)
        last_pose: CameraPose | None = None
        while time.monotonic() < feedback_deadline:
            last_pose = self.read_pose()
            position_error = math.dist(
                (last_pose.x, last_pose.y, last_pose.z),
                (pose.x, pose.y, pose.z),
            )
            angle_error = max(
                abs((last_pose.yaw_degrees - pose.yaw_degrees + 180.0) % 360.0 - 180.0),
                abs((last_pose.pitch_degrees - pose.pitch_degrees + 180.0) % 360.0 - 180.0),
                abs((last_pose.roll_degrees - pose.roll_degrees + 180.0) % 360.0 - 180.0),
            )
            if position_error <= 0.02 and angle_error <= 0.02:
                return last_pose
            time.sleep(0.002)
        if last_pose is not None:
            raise PoseUnavailableError(
                "setPose was acknowledged but precise pose feedback did not converge"
            )
        raise PoseUnavailableError("setPose was acknowledged but no pose feedback arrived")

    def set_hud_hidden(
        self,
        hidden: bool,
        *,
        timeout_seconds: float = 1.0,
    ) -> HudControlStatus:
        status = self.read_hud_status()
        if status is None or not status.ready:
            raise PoseUnavailableError(
                "当前 Camera Bridge 没有可用的 HUD 控制 Hook；请彻底重启游戏后重新注入。"
            )
        sequence = max(status.request_sequence, status.acknowledge_sequence) + 1
        if sequence > 0x7FFFFFFF:
            sequence = 1
        payload = HUD_CONTROL.pack(
            HUD_CONTROL_MAGIC,
            HUD_CONTROL_VERSION,
            HUD_CONTROL.size,
            status.request_sequence,
            status.acknowledge_sequence,
            CONTROL_STATE_IDLE,
            0,
            status.capabilities,
            int(bool(hidden)),
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        with self._lock:
            self.connect()
            assert self.mapping is not None
            self.mapping.seek(HUD_CONTROL_OFFSET)
            self.mapping.write(payload)
            self.mapping.seek(HUD_CONTROL_OFFSET + 12)
            self.mapping.write(struct.pack("<I", sequence))

        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            result = self.read_hud_status()
            if result is not None and result.acknowledge_sequence == sequence:
                if result.state != CONTROL_STATE_APPLIED:
                    raise PoseUnavailableError(
                        f"HUD visibility command failed: {result.error_message}"
                    )
                return result
            time.sleep(0.001)
        raise TimeoutError("Timed out waiting for HUD visibility acknowledgement")

    def status(self) -> dict[str, Any]:
        metadata = self.read_metadata()
        control = self.read_control_status()
        absolute_pose = self.read_absolute_pose_status()
        hud = self.read_hud_status()
        trajectory = self.read_trajectory_status()
        try:
            pose = self.read_pose()
        except PoseUnavailableError as exc:
            return {
                "connected": False,
                "message": str(exc),
                "metadata": metadata,
                "control": control,
                "absolute_pose": absolute_pose,
                "hud": hud,
                "trajectory": trajectory,
            }
        return {
            "connected": True,
            "camera_enabled": pose.camera_enabled,
            "movement_locked": pose.movement_locked,
            "pose": pose,
            "metadata": metadata,
            "control": control,
            "absolute_pose": absolute_pose,
            "hud": hud,
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


# Linux/Proton transport -------------------------------------------------
#
# The injected bridge is still a Windows PE DLL because the game runs
# inside Proton.  A Linux Python process cannot open Wine's named
# CreateFileMapping object directly, so the DLL exposes the same data/control
# ABI over a loopback-only relay when BMW_BRIDGE_PORT is set in the Proton
# environment.  Requests are short-lived and stateless: reconnecting after an
# OBS restart or a UI crash cannot leave a stale control socket in the game.
RELAY_MAGIC = b"BMWP"
RELAY_VERSION = 3
RELAY_DEFAULT_PORT = 28791
RELAY_READ_STATE = 1
RELAY_APPLY_CONTROL = 2
RELAY_START_TRAJECTORY = 3
RELAY_STOP_TRAJECTORY = 4
RELAY_SET_POSE = 5
RELAY_SET_HUD = 6
RELAY_HEADER = struct.Struct("<4sBBHI")
RELAY_STATUS_OK = 0
RELAY_STATUS_ERROR = 1
RELAY_MAX_PAYLOAD = 8 * 1024 * 1024


def parse_bridge_endpoint(value: str | None) -> tuple[str, int]:
    """Parse a loopback-only ``host:port`` for the Linux/Proton relay.

    The relay carries camera-control commands, so accepting an arbitrary DNS
    name here would accidentally turn a local capture tool into a remote
    control client.  Numeric loopback addresses and ``localhost`` are the
    only supported hosts.
    """

    raw = (value or "").strip()
    if not raw:
        raw = f"127.0.0.1:{RELAY_DEFAULT_PORT}"
    if raw.startswith("["):
        closing = raw.find("]")
        if closing <= 1 or closing + 1 >= len(raw) or raw[closing + 1] != ":":
            raise ValueError("BMW_BRIDGE_ENDPOINT must be host:port or [ipv6]:port")
        host = raw[1:closing]
        port_text = raw[closing + 2 :]
    elif ":" in raw:
        host, port_text = raw.rsplit(":", 1)
    else:
        host, port_text = raw, str(RELAY_DEFAULT_PORT)
    host = host.strip()
    normalized_host = host.rstrip(".").lower()
    is_loopback = normalized_host in {"localhost", "127.0.0.1", "::1"}
    if not is_loopback:
        raise ValueError(
            "BMW_BRIDGE_ENDPOINT must use localhost or a numeric loopback address"
        )
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError("BMW_BRIDGE_ENDPOINT port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("BMW_BRIDGE_ENDPOINT port must be between 1 and 65535")
    return host, port


def _recv_exact(connection: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = connection.recv(remaining)
        if not chunk:
            raise ConnectionError("Linux Bridge Relay closed the connection")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _decode_metadata(raw: bytes) -> BridgeMetadata | None:
    if len(raw) != BRIDGE_METADATA.size:
        raise PoseUnavailableError("Linux Bridge Relay returned an invalid metadata block")
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


def _decode_pose(raw: bytes, precise_raw: bytes | None = None) -> CameraPose:
    if len(raw) != CAMERA_DATA.size:
        raise PoseUnavailableError("Linux Bridge Relay returned an invalid pose block")
    values = CAMERA_DATA.unpack(raw)
    (
        camera_enabled,
        movement_locked,
        hud_hidden,
        input_captured,
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
    if precise_raw is not None and len(precise_raw) == PRECISE_POSE.size:
        (
            precise_magic,
            precise_version,
            precise_size,
            precise_sequence,
            precise_x,
            precise_y,
            precise_z,
            precise_pitch,
            precise_yaw,
            precise_roll,
            precise_fov,
            _precise_flags,
        ) = PRECISE_POSE.unpack(precise_raw)
        if (
            precise_magic == PRECISE_POSE_MAGIC
            and precise_version == PRECISE_POSE_VERSION
            and precise_size == PRECISE_POSE.size
            and precise_sequence > 0
            and precise_sequence % 2 == 0
        ):
            x, y, z = precise_x, precise_y, precise_z
            pitch = math.radians(precise_pitch)
            yaw = math.radians(precise_yaw)
            roll = math.radians(precise_roll)
            fov = precise_fov
    return CameraPose(
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
        hud_hidden=bool(hud_hidden),
        input_captured=bool(input_captured),
    )


def _decode_control(raw: bytes) -> NativeControlStatus | None:
    if len(raw) != CONTROL_HEADER.size:
        raise PoseUnavailableError("Linux Bridge Relay returned an invalid control block")
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


def _decode_absolute_pose(raw: bytes) -> AbsolutePoseStatus | None:
    if len(raw) != CONTROL_HEADER.size:
        raise PoseUnavailableError("Linux Bridge Relay returned an invalid setPose block")
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
        magic != ABSOLUTE_POSE_MAGIC
        or version != ABSOLUTE_POSE_VERSION
        or size != ABSOLUTE_POSE.size
    ):
        return None
    return AbsolutePoseStatus(
        request_sequence=request_sequence,
        acknowledge_sequence=acknowledge_sequence,
        state=state,
        error_code=error_code,
        capabilities=capabilities,
    )


def _decode_hud(raw: bytes) -> HudControlStatus | None:
    if len(raw) != HUD_CONTROL.size:
        raise PoseUnavailableError("Linux Bridge Relay returned an invalid HUD block")
    (
        magic,
        version,
        size,
        request_sequence,
        acknowledge_sequence,
        state,
        error_code,
        capabilities,
        hidden,
        *_reserved,
    ) = HUD_CONTROL.unpack(raw)
    if (
        magic != HUD_CONTROL_MAGIC
        or version != HUD_CONTROL_VERSION
        or size != HUD_CONTROL.size
    ):
        return None
    return HudControlStatus(
        request_sequence=request_sequence,
        acknowledge_sequence=acknowledge_sequence,
        state=state,
        error_code=error_code,
        capabilities=capabilities,
        hidden=bool(hidden),
    )


def _decode_trajectory(raw: bytes) -> NativeTrajectoryStatus | None:
    if len(raw) != TRAJECTORY_HEADER.size:
        raise PoseUnavailableError("Linux Bridge Relay returned an invalid trajectory block")
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


class LinuxRelayCameraPoseBridge:
    """Linux-side client for the standalone Windows bridge under Proton."""

    is_linux_relay = True

    def __init__(
        self,
        endpoint: str | None = None,
        *,
        timeout_seconds: float = 1.0,
    ) -> None:
        self.relay_endpoint = (
            endpoint or os.environ.get("BMW_BRIDGE_ENDPOINT") or
            f"127.0.0.1:{RELAY_DEFAULT_PORT}"
        ).strip()
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self._lock = threading.RLock()

    def _address(self) -> tuple[str, int]:
        try:
            return parse_bridge_endpoint(self.relay_endpoint)
        except ValueError as exc:
            raise PoseUnavailableError(str(exc)) from exc

    def _request(self, operation: int, payload: bytes = b"") -> bytes:
        if len(payload) > RELAY_MAX_PAYLOAD:
            raise ValueError("Linux Bridge Relay request is too large")
        host, port = self._address()
        try:
            with socket.create_connection(
                (host, port), timeout=self.timeout_seconds
            ) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.sendall(
                    RELAY_HEADER.pack(
                        RELAY_MAGIC,
                        RELAY_VERSION,
                        operation,
                        0,
                        len(payload),
                    )
                    + payload
                )
                header = _recv_exact(connection, RELAY_HEADER.size)
                magic, version, response_operation, status, size = RELAY_HEADER.unpack(header)
                if magic != RELAY_MAGIC or version != RELAY_VERSION:
                    raise ConnectionError("Linux Bridge Relay returned an invalid header")
                if response_operation != operation:
                    raise ConnectionError("Linux Bridge Relay returned an unexpected operation")
                if size > RELAY_MAX_PAYLOAD:
                    raise ConnectionError("Linux Bridge Relay response is too large")
                response = _recv_exact(connection, size) if size else b""
                if status != RELAY_STATUS_OK:
                    message = response.decode("utf-8", errors="replace") or "unknown relay error"
                    raise PoseUnavailableError(message)
                return response
        except (OSError, TimeoutError) as exc:
            raise PoseUnavailableError(
                f"Linux Bridge Relay {self.relay_endpoint} is unavailable: {exc}"
            ) from exc

    def _read_state(self) -> tuple[bytes, bytes, bytes, bytes, bytes, bytes, bytes]:
        payload = self._request(RELAY_READ_STATE)
        expected = (
            BRIDGE_METADATA.size
            + CAMERA_DATA.size
            + PRECISE_POSE.size
            + CONTROL_HEADER.size
            + CONTROL_HEADER.size
            + HUD_CONTROL.size
            + TRAJECTORY_HEADER.size
        )
        if len(payload) != expected:
            raise PoseUnavailableError("Linux Bridge Relay returned an incomplete state block")
        cursor = 0
        metadata = payload[cursor : cursor + BRIDGE_METADATA.size]
        cursor += BRIDGE_METADATA.size
        camera = payload[cursor : cursor + CAMERA_DATA.size]
        cursor += CAMERA_DATA.size
        precise = payload[cursor : cursor + PRECISE_POSE.size]
        cursor += PRECISE_POSE.size
        control = payload[cursor : cursor + CONTROL_HEADER.size]
        cursor += CONTROL_HEADER.size
        absolute_pose = payload[cursor : cursor + CONTROL_HEADER.size]
        cursor += CONTROL_HEADER.size
        hud = payload[cursor : cursor + HUD_CONTROL.size]
        cursor += HUD_CONTROL.size
        trajectory = payload[cursor : cursor + TRAJECTORY_HEADER.size]
        return metadata, camera, precise, control, absolute_pose, hud, trajectory

    def connect(self) -> None:
        self._read_state()

    def close(self) -> None:
        return None

    def read_metadata(self) -> BridgeMetadata | None:
        metadata, _camera, _precise, _control, _absolute, _hud, _trajectory = self._read_state()
        return _decode_metadata(metadata)

    def read_pose(self) -> CameraPose:
        _metadata, camera, precise, _control, _absolute, _hud, _trajectory = self._read_state()
        pose = _decode_pose(camera, precise)
        if not CameraPoseBridge._looks_valid(pose):
            raise PoseUnavailableError(
                "Linux Bridge Relay is connected, but the injected bridge has not published a valid Pose"
            )
        return pose

    def read_control_status(self) -> NativeControlStatus | None:
        _metadata, _camera, _precise, control, _absolute, _hud, _trajectory = self._read_state()
        return _decode_control(control)

    def read_absolute_pose_status(self) -> AbsolutePoseStatus | None:
        _metadata, _camera, _precise, _control, absolute, _hud, _trajectory = self._read_state()
        return _decode_absolute_pose(absolute)

    def read_hud_status(self) -> HudControlStatus | None:
        _metadata, _camera, _precise, _control, _absolute, hud, _trajectory = self._read_state()
        return _decode_hud(hud)

    def read_trajectory_status(self) -> NativeTrajectoryStatus | None:
        _metadata, _camera, _precise, _control, _absolute, _hud, trajectory = self._read_state()
        return _decode_trajectory(trajectory)

    def start_native_trajectory(
        self,
        points: Any,
        *,
        playback_hz: float = 60.0,
        timeout_seconds: float = 1.0,
    ) -> NativeTrajectoryStatus:
        values = list(points)
        if len(values) < 2:
            raise ValueError("smooth trajectory needs at least two keyframes")
        if len(values) > MAX_TRAJECTORY_KEYFRAMES:
            raise ValueError(f"trajectory has more than {MAX_TRAJECTORY_KEYFRAMES} keyframes")
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
                raise ValueError("trajectory keyframes must be finite")
            if relative_time <= previous_time:
                raise ValueError("trajectory time_sec must be strictly increasing")
            previous_time = relative_time
            rows.append(row)
        duration = rows[-1][0]
        if duration <= 0.0:
            raise ValueError("trajectory duration must be greater than zero")
        rate = min(240.0, max(30.0, float(playback_hz)))
        status = self.read_trajectory_status()
        if status is None:
            raise PoseUnavailableError("Linux Bridge Relay does not expose native trajectory state")
        if status.playing:
            raise RuntimeError("native smooth trajectory is already playing")
        sequence = max(status.request_sequence, status.acknowledge_sequence) + 1
        if sequence > 0x7FFFFFFF:
            sequence = 1
        header = TRAJECTORY_HEADER.pack(
            TRAJECTORY_MAGIC,
            TRAJECTORY_VERSION,
            TRAJECTORY_HEADER.size,
            sequence,
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
        payload = header + b"".join(TRAJECTORY_KEYFRAME.pack(*row) for row in rows)
        self._request(RELAY_START_TRAJECTORY, payload)
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            result = self.read_trajectory_status()
            if result is not None and result.acknowledge_sequence == sequence:
                if result.failed:
                    raise PoseUnavailableError(f"native trajectory start failed: {result.error_message}")
                return result
            time.sleep(0.001)
        raise TimeoutError("timed out waiting for Linux Bridge Relay trajectory acknowledgement")

    def stop_native_trajectory(
        self,
        *,
        timeout_seconds: float = 1.0,
    ) -> NativeTrajectoryStatus:
        status = self.read_trajectory_status()
        if status is None:
            raise PoseUnavailableError("native smooth trajectory control is unavailable")
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
        self._request(RELAY_STOP_TRAJECTORY, struct.pack("<I", sequence))
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            result = self.read_trajectory_status()
            if result is not None and result.acknowledge_sequence == sequence:
                return result
            time.sleep(0.001)
        raise TimeoutError("timed out waiting for Linux Bridge Relay trajectory stop acknowledgement")

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
            raise ValueError("native camera control values must be finite")
        status = self.read_control_status()
        if status is None or not status.ready:
            raise PoseUnavailableError("native camera control is not ready through Linux Bridge Relay")
        sequence = max(status.request_sequence, status.acknowledge_sequence) + 1
        if sequence > 0x7FFFFFFF:
            sequence = 1
        payload = struct.pack("<I", sequence) + CONTROL_COMMAND.pack(
            *values,
            int(set_fov),
        )
        self._request(RELAY_APPLY_CONTROL, payload)
        deadline = time.monotonic() + max(0.05, timeout_seconds)
        while time.monotonic() < deadline:
            result = self.read_control_status()
            if result is not None and result.acknowledge_sequence == sequence:
                if result.state != CONTROL_STATE_APPLIED:
                    raise PoseUnavailableError(f"native camera command failed: {result.error_message}")
                return result
            time.sleep(0.001)
        raise TimeoutError("timed out waiting for Linux Bridge Relay camera acknowledgement")

    def set_pose(
        self,
        pose: CameraPose,
        *,
        enable_camera: bool = True,
        timeout_seconds: float = 1.0,
    ) -> CameraPose:
        values = (
            pose.x,
            pose.y,
            pose.z,
            pose.yaw_degrees,
            pose.pitch_degrees,
            pose.roll_degrees,
            pose.fov_degrees,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("absolute camera pose values must be finite")
        if not 1.0 <= pose.fov_degrees <= 179.0:
            raise ValueError("absolute camera FOV must be between 1 and 179 degrees")
        status = self.read_absolute_pose_status()
        if status is None or not status.ready:
            raise PoseUnavailableError(
                "absolute setPose is not ready through Linux Bridge Relay"
            )
        sequence = max(status.request_sequence, status.acknowledge_sequence) + 1
        if sequence > 0x7FFFFFFF:
            sequence = 1
        payload = ABSOLUTE_POSE.pack(
            ABSOLUTE_POSE_MAGIC,
            ABSOLUTE_POSE_VERSION,
            ABSOLUTE_POSE.size,
            sequence,
            status.acknowledge_sequence,
            CONTROL_STATE_IDLE,
            0,
            status.capabilities,
            float(pose.x),
            float(pose.y),
            float(pose.z),
            float(pose.yaw_degrees),
            float(pose.pitch_degrees),
            float(pose.roll_degrees),
            float(pose.fov_degrees),
            int(enable_camera),
            0,
            0,
            0,
        )
        self._request(RELAY_SET_POSE, payload)
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            result = self.read_absolute_pose_status()
            if result is not None and result.acknowledge_sequence == sequence:
                if result.state != CONTROL_STATE_APPLIED:
                    raise PoseUnavailableError(
                        f"absolute setPose failed through relay: {result.error_message}"
                    )
                break
            time.sleep(0.001)
        else:
            raise TimeoutError("timed out waiting for Linux Bridge Relay setPose acknowledgement")
        feedback_deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < feedback_deadline:
            feedback = self.read_pose()
            if math.dist(
                (feedback.x, feedback.y, feedback.z),
                (pose.x, pose.y, pose.z),
            ) <= 0.02:
                return feedback
            time.sleep(0.002)
        raise PoseUnavailableError("relay setPose was acknowledged without precise feedback")

    def set_hud_hidden(
        self,
        hidden: bool,
        *,
        timeout_seconds: float = 1.0,
    ) -> HudControlStatus:
        status = self.read_hud_status()
        if status is None or not status.ready:
            raise PoseUnavailableError(
                "HUD control is not ready through Linux Bridge Relay"
            )
        sequence = max(status.request_sequence, status.acknowledge_sequence) + 1
        if sequence > 0x7FFFFFFF:
            sequence = 1
        payload = HUD_CONTROL.pack(
            HUD_CONTROL_MAGIC,
            HUD_CONTROL_VERSION,
            HUD_CONTROL.size,
            sequence,
            status.acknowledge_sequence,
            CONTROL_STATE_IDLE,
            0,
            status.capabilities,
            int(bool(hidden)),
            0,
            0,
            0,
            0,
            0,
            0,
            0,
        )
        self._request(RELAY_SET_HUD, payload)
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            result = self.read_hud_status()
            if result is not None and result.acknowledge_sequence == sequence:
                if result.state != CONTROL_STATE_APPLIED:
                    raise PoseUnavailableError(
                        f"HUD visibility command failed through relay: {result.error_message}"
                    )
                return result
            time.sleep(0.001)
        raise TimeoutError(
            "timed out waiting for Linux Bridge Relay HUD acknowledgement"
        )

    def status(self) -> dict[str, Any]:
        (
            metadata_raw,
            camera_raw,
            precise_raw,
            control_raw,
            absolute_raw,
            hud_raw,
            trajectory_raw,
        ) = self._read_state()
        metadata = _decode_metadata(metadata_raw)
        control = _decode_control(control_raw)
        absolute_pose = _decode_absolute_pose(absolute_raw)
        hud = _decode_hud(hud_raw)
        trajectory = _decode_trajectory(trajectory_raw)
        try:
            pose = _decode_pose(camera_raw, precise_raw)
            if not CameraPoseBridge._looks_valid(pose):
                raise PoseUnavailableError("relay pose is invalid")
        except PoseUnavailableError as exc:
            return {
                "connected": False,
                "message": str(exc),
                "metadata": metadata,
                "control": control,
                "absolute_pose": absolute_pose,
                "hud": hud,
                "trajectory": trajectory,
            }
        return {
            "connected": True,
            "camera_enabled": pose.camera_enabled,
            "movement_locked": pose.movement_locked,
            "pose": pose,
            "metadata": metadata,
            "control": control,
            "absolute_pose": absolute_pose,
            "hud": hud,
            "trajectory": trajectory,
        }


def create_pose_bridge(
    endpoint: str | None = None,
) -> CameraPoseBridge | LinuxRelayCameraPoseBridge:
    """Select the native Windows map or Linux/Proton relay transport."""

    selected = endpoint or os.environ.get("BMW_BRIDGE_ENDPOINT")
    if sys.platform.startswith("linux") and selected:
        return LinuxRelayCameraPoseBridge(selected)
    return CameraPoseBridge()
