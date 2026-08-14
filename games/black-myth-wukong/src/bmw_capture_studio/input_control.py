from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import time
from typing import Callable

from .bridge import UuuPoseBridge
from .models import CameraPose


VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_NUMPAD1 = 0x61
VK_NUMPAD3 = 0x63
VK_NUMPAD4 = 0x64
VK_NUMPAD5 = 0x65
VK_NUMPAD6 = 0x66
VK_NUMPAD7 = 0x67
VK_NUMPAD8 = 0x68
VK_NUMPAD9 = 0x69
VK_ADD = 0x6B
VK_SUBTRACT = 0x6D


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


def _send_key(vk: int, *, up: bool) -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = [
        wintypes.UINT,
        ctypes.POINTER(INPUT),
        ctypes.c_int,
    ]
    user32.SendInput.restype = wintypes.UINT
    event = INPUT(
        type=1,
        ki=KEYBDINPUT(
            wVk=vk,
            wScan=0,
            dwFlags=0x0002 if up else 0,
            time=0,
            dwExtraInfo=0,
        ),
    )
    sent = user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))
    if sent != 1:
        raise RuntimeError(f"发送 UUU 按键失败：VK 0x{vk:02X}")


def hold_keys(keys: list[int], duration: float) -> None:
    if not keys:
        time.sleep(max(0.005, duration))
        return
    pressed: list[int] = []
    try:
        for key in keys:
            _send_key(key, up=False)
            pressed.append(key)
        time.sleep(max(0.005, duration))
    finally:
        for key in reversed(pressed):
            _send_key(key, up=True)


def wrap_degrees(value: float) -> float:
    return (value + 180.0) % 360.0 - 180.0


def _dot(error: tuple[float, float, float], axis: tuple[float, float, float]) -> float:
    return error[0] * axis[0] + error[1] * axis[1] + error[2] * axis[2]


class ClosedLoopMover:
    """Reach an absolute pose through UUU native camera calls and pose feedback.

    UUU 5.8.21 does not export one monolithic setPose function. The in-process
    bridge calls its movement/rotation/FOV camera methods directly; this class
    closes the loop in world coordinates until the requested absolute pose is
    reached. Keyboard input is retained only as an explicit compatibility
    fallback for older bridges.
    """

    def __init__(
        self,
        bridge: UuuPoseBridge,
        *,
        position_tolerance: float,
        angle_tolerance: float,
        fov_tolerance: float,
        move_pulse_sec: float,
        rotate_pulse_sec: float,
        max_seconds: float,
        focus_game: Callable[[], None],
        prefer_native: bool = True,
        allow_hotkey_fallback: bool = False,
        feedback_timeout_sec: float = 0.5,
    ) -> None:
        self.bridge = bridge
        self.position_tolerance = position_tolerance
        self.angle_tolerance = angle_tolerance
        self.fov_tolerance = fov_tolerance
        self.move_pulse_sec = move_pulse_sec
        self.rotate_pulse_sec = rotate_pulse_sec
        self.max_seconds = max_seconds
        self.focus_game = focus_game
        self.prefer_native = prefer_native
        self.allow_hotkey_fallback = allow_hotkey_fallback
        self.feedback_timeout_sec = max(0.2, float(feedback_timeout_sec))

    def _native_ready(self) -> bool:
        status = self.bridge.read_control_status()
        return bool(status is not None and status.ready)

    def move_to(
        self,
        target: CameraPose,
        *,
        stop_requested: Callable[[], bool] = lambda: False,
        on_update: Callable[[str], None] | None = None,
    ) -> CameraPose:
        last_update_at = 0.0
        native = self.prefer_native and self._native_ready()
        if not native and not self.allow_hotkey_fallback:
            raise RuntimeError(
                "UUU native camera control is unavailable. Restart the game, "
                "load the rebuilt bridge before injecting UUU 5.8.21, then enable Camera."
            )
        if not native:
            self.focus_game()

        def read_checked_pose() -> CameraPose:
            if stop_requested():
                raise InterruptedError("采集已停止")
            current = self.bridge.read_pose()
            if not current.camera_enabled:
                raise RuntimeError("UUU 相机未启用，请回到游戏按 Insert")
            if current.movement_locked:
                raise RuntimeError("UUU 相机移动被锁定，请解除 Camera Lock")
            return current

        def errors(current: CameraPose) -> tuple[float, float, float, float, float]:
            error = (
                target.x - current.x,
                target.y - current.y,
                target.z - current.z,
            )
            distance = math.sqrt(sum(value * value for value in error))
            yaw_error = wrap_degrees(target.yaw_degrees - current.yaw_degrees)
            pitch_error = wrap_degrees(target.pitch_degrees - current.pitch_degrees)
            roll_error = wrap_degrees(target.roll_degrees - current.roll_degrees)
            fov_error = target.fov_degrees - current.fov_degrees
            return distance, yaw_error, pitch_error, roll_error, fov_error

        def report(phase: str, current: CameraPose) -> None:
            nonlocal last_update_at
            now = time.monotonic()
            if on_update is None or now - last_update_at < 0.25:
                return
            distance, yaw_error, pitch_error, _roll_error, fov_error = errors(current)
            on_update(
                f"{phase} | 位置误差 {distance:.2f} | yaw {yaw_error:.2f}° | "
                f"pitch {pitch_error:.2f}° | FOV {fov_error:.2f}°"
            )
            last_update_at = now

        def limited(value: float, limit: float) -> float:
            return max(-limit, min(limit, value))

        def wait_for_native_feedback(previous: CameraPose, *, position: bool) -> None:
            """Do not issue a second correction against the same camera frame."""
            deadline = time.monotonic() + self.feedback_timeout_sec
            while time.monotonic() < deadline:
                if stop_requested():
                    raise InterruptedError("采集已停止")
                updated = self.bridge.read_pose()
                if position:
                    changed = any(
                        abs(after - before) > 1e-4
                        for before, after in (
                            (previous.x, updated.x),
                            (previous.y, updated.y),
                            (previous.z, updated.z),
                        )
                    )
                else:
                    changed = any(
                        abs(wrap_degrees(after - before)) > 1e-4
                        for before, after in (
                            (previous.yaw_degrees, updated.yaw_degrees),
                            (previous.pitch_degrees, updated.pitch_degrees),
                            (previous.roll_degrees, updated.roll_degrees),
                        )
                    ) or abs(updated.fov_degrees - previous.fov_degrees) > 1e-4
                if changed:
                    return
                time.sleep(0.002)
            raise TimeoutError(
                "UUU accepted the native camera command, but no new Pose feedback "
                f"arrived within {self.feedback_timeout_sec:.2f} seconds"
            )

        current = read_checked_pose()
        initial_distance = errors(current)[0]

        # Phase 1: hold orientation fixed and converge world XYZ. Rotating while
        # moving changes the local basis used to project the world-space error
        # and was the main cause of long moves stalling or curving away.
        position_started = time.monotonic()
        # Large scene-to-scene jumps need more wall-clock time than a nearby
        # point. Stagnation detection still fails fast when feedback is not
        # moving, so scaling this deadline does not hide a dead control path.
        position_timeout = max(
            self.max_seconds,
            min(300.0, initial_distance * 0.1),
        )
        best_distance = initial_distance
        stagnant_since = position_started
        progress_epsilon = max(0.02, self.position_tolerance * 0.01)
        while True:
            current = read_checked_pose()
            distance, _yaw_error, _pitch_error, _roll_error, _fov_error = errors(current)
            if distance <= self.position_tolerance:
                break

            now = time.monotonic()
            if now - position_started > position_timeout:
                raise TimeoutError(
                    f"相机 XYZ 在 {position_timeout:.0f} 秒内未收敛；"
                    f"位置误差 {distance:.2f}"
                )
            if distance < best_distance - progress_epsilon:
                best_distance = distance
                stagnant_since = now
            elif now - stagnant_since > 4.0:
                method = "UUU 原生控制桥" if native else "UUU 热键后备控制"
                raise RuntimeError(
                    f"相机 XYZ 通过{method}没有继续收敛；"
                    f"当前误差 {distance:.2f}，历史最佳 {best_distance:.2f}。"
                )

            error = (
                target.x - current.x,
                target.y - current.y,
                target.z - current.z,
            )
            right = (current.right_x, current.right_y, current.right_z)
            up = (current.up_x, current.up_y, current.up_z)
            forward = (current.forward_x, current.forward_y, current.forward_z)
            right_error = _dot(error, right)
            up_error = _dot(error, up)
            forward_error = _dot(error, forward)
            threshold = self.position_tolerance * 0.35

            if native:
                # UUU's move methods are frame-scaled on some game builds. A
                # fixed 8-unit command made an 800-unit target require thousands
                # of frames. Grow the cap with remaining distance; feedback and
                # the 0.72 gain keep the final approach conservative.
                max_position_step = max(
                    1.0,
                    self.position_tolerance * 2.0,
                    min(256.0, distance * 0.72),
                )
                self.bridge.apply_native_step(
                    move_forward=limited(forward_error * 0.72, max_position_step)
                    if abs(forward_error) > threshold
                    else 0.0,
                    move_right=limited(right_error * 0.72, max_position_step)
                    if abs(right_error) > threshold
                    else 0.0,
                    move_up=limited(up_error * 0.72, max_position_step)
                    if abs(up_error) > threshold
                    else 0.0,
                    yaw_radians=0.0,
                    pitch_radians=0.0,
                    roll_radians=0.0,
                    fov_degrees=target.fov_degrees,
                    set_fov=False,
                )
                report("XYZ", current)
                # Command acknowledgement happens before UUU publishes the next
                # pose. Wait for that feedback instead of correcting the same
                # stale frame repeatedly and overshooting.
                wait_for_native_feedback(current, position=True)
                continue

            keys: list[int] = []
            if right_error > threshold:
                keys.append(VK_NUMPAD6)
            elif right_error < -threshold:
                keys.append(VK_NUMPAD4)
            if forward_error > threshold:
                keys.append(VK_NUMPAD8)
            elif forward_error < -threshold:
                keys.append(VK_NUMPAD5)
            if up_error > threshold:
                keys.append(VK_NUMPAD7)
            elif up_error < -threshold:
                keys.append(VK_NUMPAD9)
            report("XYZ", current)
            hold_keys(keys, min(self.move_pulse_sec, 0.08))
            time.sleep(0.012)

        # Phase 2: position is now fixed; converge angles and FOV without
        # sending any translation command.
        orientation_started = time.monotonic()
        stagnant_since = orientation_started
        best_orientation_error = math.inf
        angle_scale = max(self.angle_tolerance, 1e-6)
        fov_scale = max(self.fov_tolerance, 1e-6)
        while True:
            current = read_checked_pose()
            distance, yaw_error, pitch_error, roll_error, fov_error = errors(current)
            orientation_error = max(
                abs(yaw_error) / angle_scale,
                abs(pitch_error) / angle_scale,
                abs(roll_error) / angle_scale,
                abs(fov_error) / fov_scale,
            )
            if orientation_error <= 1.0:
                return current

            now = time.monotonic()
            if now - orientation_started > self.max_seconds:
                raise TimeoutError(
                    f"相机朝向/FOV 在 {self.max_seconds:.0f} 秒内未收敛；"
                    f"yaw {yaw_error:.2f}°，pitch {pitch_error:.2f}°，"
                    f"roll {roll_error:.2f}°，FOV {fov_error:.2f}°"
                )
            if orientation_error < best_orientation_error - 0.02:
                best_orientation_error = orientation_error
                stagnant_since = now
            elif now - stagnant_since > 4.0:
                method = "UUU 原生控制桥" if native else "UUU 热键后备控制"
                raise RuntimeError(
                    f"相机朝向/FOV 通过{method}没有继续收敛；"
                    f"yaw {yaw_error:.2f}°，pitch {pitch_error:.2f}°，"
                    f"roll {roll_error:.2f}°，FOV {fov_error:.2f}°。"
                )

            if native:
                max_angle_step = math.radians(max(0.25, self.angle_tolerance * 2.0))
                self.bridge.apply_native_step(
                    move_forward=0.0,
                    move_right=0.0,
                    move_up=0.0,
                    yaw_radians=limited(math.radians(yaw_error) * 0.72, max_angle_step)
                    if abs(yaw_error) > self.angle_tolerance
                    else 0.0,
                    pitch_radians=limited(math.radians(pitch_error) * 0.72, max_angle_step)
                    if abs(pitch_error) > self.angle_tolerance
                    else 0.0,
                    roll_radians=limited(math.radians(roll_error) * 0.72, max_angle_step)
                    if abs(roll_error) > self.angle_tolerance
                    else 0.0,
                    fov_degrees=target.fov_degrees,
                    set_fov=abs(fov_error) > self.fov_tolerance,
                )
                report("朝向/FOV", current)
                wait_for_native_feedback(current, position=False)
                continue

            keys = []
            if abs(yaw_error) > self.angle_tolerance:
                keys.append(VK_RIGHT if yaw_error > 0 else VK_LEFT)
            if abs(pitch_error) > self.angle_tolerance:
                keys.append(VK_UP if pitch_error > 0 else VK_DOWN)
            if abs(roll_error) > self.angle_tolerance:
                keys.append(VK_NUMPAD3 if roll_error > 0 else VK_NUMPAD1)
            if abs(fov_error) > self.fov_tolerance:
                keys.append(VK_ADD if fov_error > 0 else VK_SUBTRACT)
            report("朝向/FOV", current)
            hold_keys(keys, min(self.rotate_pulse_sec, 0.05))
            time.sleep(0.012)
