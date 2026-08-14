from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import time


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def _user32() -> ctypes.WinDLL:
    if sys.platform != "win32":
        raise RuntimeError("游戏窗口控制目前仅支持 Windows；Linux 请使用 OBS/Proton 适配器")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    return user32


def enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    user32 = _user32()
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def find_main_window(pid: int) -> int:
    user32 = _user32()
    result: list[tuple[int, int]] = []
    callback_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL

    @callback_type
    def callback(hwnd: int, _: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            rect = RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                if rect.right - rect.left > 320 and rect.bottom - rect.top > 240:
                    area = (rect.right - rect.left) * (rect.bottom - rect.top)
                    result.append((area, hwnd))
        return True

    user32.EnumWindows(callback, 0)
    if not result:
        raise RuntimeError("没有找到《黑神话：悟空》的可见游戏窗口")
    return max(result, key=lambda item: item[0])[1]


def focus_game_window(pid: int) -> None:
    hwnd = find_main_window(pid)
    user32 = _user32()
    for _ in range(3):
        user32.ShowWindow(hwnd, 9)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        if user32.GetForegroundWindow() == hwnd:
            return
        time.sleep(0.06)


def foreground_process_id() -> int | None:
    user32 = _user32()
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    return int(owner.value) if owner.value else None


def client_bbox(pid: int) -> tuple[int, int, int, int]:
    hwnd = find_main_window(pid)
    user32 = _user32()
    rect = RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("无法读取游戏画面尺寸")
    top_left = POINT(rect.left, rect.top)
    bottom_right = POINT(rect.right, rect.bottom)
    if not user32.ClientToScreen(hwnd, ctypes.byref(top_left)):
        raise RuntimeError("无法换算游戏客户区左上角坐标")
    if not user32.ClientToScreen(hwnd, ctypes.byref(bottom_right)):
        raise RuntimeError("无法换算游戏客户区右下角坐标")
    if bottom_right.x <= top_left.x or bottom_right.y <= top_left.y:
        raise RuntimeError("游戏客户区尺寸无效")
    return top_left.x, top_left.y, bottom_right.x, bottom_right.y


def save_game_screenshot(pid: int, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    raise RuntimeError("窗口截屏已禁用，请通过 OBS WebSocket 保存静态采集图像")
    if image.width < 320 or image.height < 240:
        raise RuntimeError("截取到的游戏画面尺寸异常")
    image.save(target)
    return target
