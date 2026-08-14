from __future__ import annotations

import ctypes
from ctypes import wintypes
from queue import Empty, SimpleQueue
import sys
import threading


WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_NOREPEAT = 0x4000
F8_VK = 0x77
HOTKEY_ID = 0xB18


class GlobalHotkey:
    """Register one Windows hotkey on a dedicated message-loop thread."""

    def __init__(self, virtual_key: int, *, modifiers: int = MOD_NOREPEAT) -> None:
        self.virtual_key = virtual_key
        self.modifiers = modifiers
        self._events: SimpleQueue[None] = SimpleQueue()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._registration_error: RuntimeError | None = None

    @property
    def supported(self) -> bool:
        return sys.platform == "win32"

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if not self.supported:
            # RegisterHotKey is a Win32 API. Linux users can still use the
            # visible Record Point button and all file/OBS features.
            self._ready.set()
            return
        self._ready.clear()
        self._registration_error = None
        self._thread = threading.Thread(
            target=self._message_loop,
            name="bmw-record-point-hotkey",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=1.5):
            raise RuntimeError("等待全局快捷键注册超时")
        if self._registration_error is not None:
            raise self._registration_error

    def consume(self) -> bool:
        triggered = False
        while True:
            try:
                self._events.get_nowait()
            except Empty:
                return triggered
            triggered = True

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        if not self.supported:
            self._thread = None
            return
        thread_id = self._thread_id
        if thread_id:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.PostThreadMessageW.argtypes = [
                wintypes.DWORD,
                wintypes.UINT,
                wintypes.WPARAM,
                wintypes.LPARAM,
            ]
            user32.PostThreadMessageW.restype = wintypes.BOOL
            user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
        thread.join(timeout=0.5)
        self._thread = None
        self._thread_id = 0

    def _message_loop(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.PeekMessageW.restype = wintypes.BOOL
        user32.RegisterHotKey.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.RegisterHotKey.restype = wintypes.BOOL
        user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.UnregisterHotKey.restype = wintypes.BOOL
        user32.GetMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        user32.GetMessageW.restype = ctypes.c_int
        kernel32.GetCurrentThreadId.argtypes = []
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        self._thread_id = int(kernel32.GetCurrentThreadId())
        message = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        registered = bool(
            user32.RegisterHotKey(
                None,
                HOTKEY_ID,
                self.modifiers,
                self.virtual_key,
            )
        )
        if not registered:
            error_code = ctypes.get_last_error()
            self._registration_error = RuntimeError(
                f"F8 全局快捷键注册失败（Windows 错误 {error_code}），可能已被其他程序占用"
            )
            self._ready.set()
            return

        self._ready.set()
        try:
            while True:
                result = int(user32.GetMessageW(ctypes.byref(message), None, 0, 0))
                if result <= 0:
                    break
                if message.message == WM_HOTKEY and message.wParam == HOTKEY_ID:
                    self._events.put(None)
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)
