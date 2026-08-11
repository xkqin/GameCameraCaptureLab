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
    """Advance between keyframes with UUU hotkeys and live pose feedback.

    This is intentionally low-rate. Smooth per-frame interpolation requires a
    future native write bridge; Python only closes the loop between samples.
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
    ) -> None:
        self.bridge = bridge
        self.position_tolerance = position_tolerance
        self.angle_tolerance = angle_tolerance
        self.fov_tolerance = fov_tolerance
        self.move_pulse_sec = move_pulse_sec
        self.rotate_pulse_sec = rotate_pulse_sec
        self.max_seconds = max_seconds
        self.focus_game = focus_game

    def move_to(
        self,
        target: CameraPose,
        *,
        stop_requested: Callable[[], bool] = lambda: False,
        on_update: Callable[[str], None] | None = None,
    ) -> CameraPose:
        started = time.monotonic()
        last_distance = math.inf
        stagnant_since = started
        last_update_at = 0.0
        self.focus_game()

        while True:
            if stop_requested():
                raise InterruptedError("采集已停止")
            current = self.bridge.read_pose()
            if not current.camera_enabled:
                raise RuntimeError("UUU 相机未启用，请回到游戏按 Insert")
            if current.movement_locked:
                raise RuntimeError("UUU 相机移动被锁定，请解除 Camera Lock")

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

            if (
                distance <= self.position_tolerance
                and abs(yaw_error) <= self.angle_tolerance
                and abs(pitch_error) <= self.angle_tolerance
                and abs(roll_error) <= self.angle_tolerance
                and abs(fov_error) <= self.fov_tolerance
            ):
                return current

            if time.monotonic() - started > self.max_seconds:
                raise TimeoutError(
                    f"相机在 {self.max_seconds:.0f} 秒内未到达点位；"
                    f"位置误差 {distance:.2f}，yaw 误差 {yaw_error:.2f}°"
                )

            if distance < last_distance - max(0.05, self.position_tolerance * 0.05):
                stagnant_since = time.monotonic()
                last_distance = distance
            elif (
                time.monotonic() - stagnant_since > 4.0
                and distance > self.position_tolerance
            ):
                raise RuntimeError(
                    "相机位置没有继续收敛。请确认游戏窗口获得焦点，"
                    "并在 UUU 中使用默认数字键盘移动绑定。"
                )

            right = (current.right_x, current.right_y, current.right_z)
            up = (current.up_x, current.up_y, current.up_z)
            forward = (
                current.forward_x,
                current.forward_y,
                current.forward_z,
            )
            right_error = _dot(error, right)
            up_error = _dot(error, up)
            forward_error = _dot(error, forward)
            threshold = self.position_tolerance * 0.35
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

            if abs(yaw_error) > self.angle_tolerance:
                keys.append(VK_RIGHT if yaw_error > 0 else VK_LEFT)
            if abs(pitch_error) > self.angle_tolerance:
                keys.append(VK_UP if pitch_error > 0 else VK_DOWN)
            if abs(roll_error) > self.angle_tolerance:
                keys.append(VK_NUMPAD3 if roll_error > 0 else VK_NUMPAD1)
            if abs(fov_error) > self.fov_tolerance:
                keys.append(VK_ADD if fov_error > 0 else VK_SUBTRACT)

            now = time.monotonic()
            if on_update is not None and now - last_update_at >= 0.25:
                on_update(
                    f"位置误差 {distance:.2f} | yaw {yaw_error:.2f}° | "
                    f"pitch {pitch_error:.2f}° | FOV {fov_error:.2f}°"
                )
                last_update_at = now
            pulse = (
                min(self.move_pulse_sec, 0.08)
                if distance > self.position_tolerance
                else min(self.rotate_pulse_sec, 0.05)
            )
            hold_keys(keys, pulse)
            time.sleep(0.012)
