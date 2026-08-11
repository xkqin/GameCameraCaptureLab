from __future__ import annotations

import csv
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable

from .paths import CAMERA_TOOLS_DIR


IGCS_CLIENT_PATH = CAMERA_TOOLS_DIR / "IGCSClient.exe"
DLL_TO_CLIENT_PIPE = "IgcsDllToClient"
CLIENT_TO_DLL_PIPE = "IgcsClientToDll"
PIPE_ROOT = r"\\.\pipe"


class IGCSClientError(RuntimeError):
    pass


def _find_client_pids() -> list[int]:
    completed = subprocess.run(
        [
            "tasklist.exe",
            "/FI",
            "IMAGENAME eq IGCSClient.exe",
            "/FO",
            "CSV",
            "/NH",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        return []

    pids: list[int] = []
    for row in csv.reader(completed.stdout.splitlines()):
        if len(row) < 2 or row[0].lower() != "igcsclient.exe":
            continue
        try:
            pids.append(int(row[1]))
        except ValueError:
            continue
    return pids


def _list_named_pipes() -> set[str]:
    try:
        return {name.lower() for name in os.listdir(PIPE_ROOT)}
    except OSError:
        return set()


def _launch_client(path: Path) -> subprocess.Popen[bytes]:
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 6  # SW_SHOWMINIMIZED
    return subprocess.Popen(
        [str(path)],
        cwd=str(path.parent),
        startupinfo=startupinfo,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def _hide_windows_for_pid(pid: int) -> None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_proc_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )

    user32.EnumWindows.argtypes = [enum_proc_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL

    @enum_proc_type
    def callback(hwnd: int, _: int) -> bool:
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value == pid and user32.IsWindowVisible(hwnd):
            user32.ShowWindow(hwnd, 0)  # SW_HIDE
        return True

    user32.EnumWindows(callback, 0)


class IGCSClientManager:
    """Owns the original IGCS Client process and its two named pipes."""

    def __init__(
        self,
        *,
        client_path: Path = IGCS_CLIENT_PATH,
        process_finder: Callable[[], list[int]] = _find_client_pids,
        pipe_lister: Callable[[], set[str]] = _list_named_pipes,
        launcher: Callable[[Path], Any] = _launch_client,
        window_hider: Callable[[int], None] = _hide_windows_for_pid,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client_path = client_path
        self.process_finder = process_finder
        self.pipe_lister = pipe_lister
        self.launcher = launcher
        self.window_hider = window_hider
        self.monotonic = monotonic
        self.sleeper = sleeper

    def status(self) -> dict[str, Any]:
        pids = self.process_finder()
        pipes = self.pipe_lister()
        return {
            "exe": str(self.client_path),
            "exe_exists": self.client_path.exists(),
            "pids": pids,
            "running": bool(pids),
            "dll_to_client_pipe": DLL_TO_CLIENT_PIPE.lower() in pipes,
            "client_to_dll_pipe": CLIENT_TO_DLL_PIPE.lower() in pipes,
        }

    def ensure_server_ready(
        self,
        *,
        timeout: float = 12.0,
        hide_started_window: bool = True,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        if not self.client_path.exists():
            raise IGCSClientError(
                f"IGCS Client 不存在：{self.client_path}"
            )

        existing = self.process_finder()
        process = None
        started = not existing
        if started:
            if progress is not None:
                progress("正在后台启动 IGCS Client…")
            try:
                process = self.launcher(self.client_path)
            except OSError as exc:
                raise IGCSClientError(
                    f"无法启动 IGCS Client：{exc}"
                ) from exc
        elif progress is not None:
            progress(f"IGCS Client 已运行（PID {existing[0]}）")

        deadline = self.monotonic() + timeout
        selected_pid = existing[0] if existing else None
        while self.monotonic() < deadline:
            pids = self.process_finder()
            if pids:
                selected_pid = pids[0]
            if (
                DLL_TO_CLIENT_PIPE.lower() in self.pipe_lister()
                and selected_pid is not None
            ):
                if started and hide_started_window:
                    try:
                        self.window_hider(selected_pid)
                    except OSError:
                        pass
                result = self.status()
                result["started"] = started
                result["pid"] = selected_pid
                if progress is not None:
                    progress("IGCS Client 管道已就绪")
                return result
            if process is not None and process.poll() is not None:
                raise IGCSClientError(
                    "IGCS Client 启动后立即退出；请检查 camera_tools 文件是否完整"
                )
            self.sleeper(0.1)

        raise IGCSClientError(
            "IGCS Client 已启动，但 DLL → Client 管道在 "
            f"{timeout:.0f} 秒内没有就绪"
        )

    def wait_for_bidirectional_pipes(
        self,
        *,
        timeout: float = 12.0,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        deadline = self.monotonic() + timeout
        while self.monotonic() < deadline:
            state = self.status()
            if (
                state["dll_to_client_pipe"]
                and state["client_to_dll_pipe"]
            ):
                if progress is not None:
                    progress("IGCS 双向管道已连接")
                return state
            self.sleeper(0.1)
        state = self.status()
        raise IGCSClientError(
            "相机 DLL 已加载，但 IGCS 双向管道没有在 "
            f"{timeout:.0f} 秒内全部出现：{state}"
        )
