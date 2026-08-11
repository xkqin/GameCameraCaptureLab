#!/usr/bin/env python3
"""KCD2 OPM camera pose probe, recorder, and relative controller.

This tool intentionally uses only the Python standard library and files beside
the original KCD2 Camera Tools package. It does not modify the closed-source
camera DLL.

The offsets below are specific to KCD2CameraTools.dll v1.0.5 whose SHA-256 is:
9600C8CE3B32AE78177603695287126B05B3B165AD8820283544E8AD420B5D96
"""

from __future__ import annotations

import argparse
import base64
import csv
import ctypes
from ctypes import wintypes
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import random
import struct
import sys
import time
from typing import Any, Callable, Iterable


PROJECT_ROOT = Path(__file__).resolve().parent
ROOT_DIR = PROJECT_ROOT / "camera_tools"
DLL_PATH = ROOT_DIR / "KCD2CameraTools.dll"
LOG_PATH = ROOT_DIR / "KCD2CameraTools.dll.log"
DATA_DIR = PROJECT_ROOT / "capture_studio_data" / "low_level"
DEFAULT_POSE_CONFIG = PROJECT_ROOT / "pose_offsets.json"

EXPECTED_DLL_SHA256 = (
    "9600C8CE3B32AE78177603695287126B05B3B165AD8820283544E8AD420B5D96"
)

# Absolute CameraTools fields remain stable after one write.  Keep only short
# readback delays so strict pose validation still observes a rendered frame.
ABSOLUTE_POSE_HOLD_MS = 0
POSE_WRITE_READBACK_DELAY_SECONDS = 0.03

PROCESS_NAME = "KingdomCome.exe"
MODULE_NAME = "KCD2CameraTools.dll"
ERROR_BAD_LENGTH = 24
MODULE_SNAPSHOT_MAX_ATTEMPTS = 20
_MODULE_CACHE: dict[tuple[int, str], dict[str, Any]] = {}

# Recovered from the four public exports in v1.0.5. Each export reads the same
# CameraFeature pointer from [module base + 0x15EF40]. The CameraBase pointer
# used by those exports is stored at CameraFeature + 0x1C48.
CAMERA_FEATURE_PTR_RVA = 0x15EF40
CAMERA_OBJECT_PTR_OFFSET = 0x1C48

ROOT_CAPTURE_SIZE = 0x1D00
CAMERA_CAPTURE_SIZE = 0x500
POINTER_CAPTURE_SIZE = 0x300

TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
PROCESS_CREATE_THREAD = 0x0002
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_EXECUTE_READWRITE = 0x40
PAGE_READWRITE = 0x04
WAIT_OBJECT_0 = 0x00000000
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_ubyte)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * 260),
    ]


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


class _INPUTUNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTUNION)]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HWND,
    wintypes.LPARAM,
)

kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(PROCESSENTRY32W),
]
kernel32.Process32FirstW.restype = wintypes.BOOL
kernel32.Process32NextW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(PROCESSENTRY32W),
]
kernel32.Process32NextW.restype = wintypes.BOOL
kernel32.Module32FirstW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(MODULEENTRY32W),
]
kernel32.Module32FirstW.restype = wintypes.BOOL
kernel32.Module32NextW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(MODULEENTRY32W),
]
kernel32.Module32NextW.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCVOID,
    wintypes.LPVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.WriteProcessMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.LPCVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.WriteProcessMemory.restype = wintypes.BOOL
kernel32.VirtualAllocEx.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    ctypes.c_size_t,
    wintypes.DWORD,
    wintypes.DWORD,
]
kernel32.VirtualAllocEx.restype = wintypes.LPVOID
kernel32.VirtualFreeEx.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    ctypes.c_size_t,
    wintypes.DWORD,
]
kernel32.VirtualFreeEx.restype = wintypes.BOOL
kernel32.CreateRemoteThread.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    ctypes.c_size_t,
    wintypes.LPVOID,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.CreateRemoteThread.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.GetExitCodeThread.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.GetExitCodeThread.restype = wintypes.BOOL
kernel32.FlushInstructionCache.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCVOID,
    ctypes.c_size_t,
]
kernel32.FlushInstructionCache.restype = wintypes.BOOL
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetProcAddress.argtypes = [wintypes.HMODULE, ctypes.c_char_p]
kernel32.GetProcAddress.restype = wintypes.LPVOID
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindowAsync.restype = wintypes.BOOL
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [
    wintypes.HWND,
    wintypes.LPWSTR,
    ctypes.c_int,
]
user32.GetWindowTextW.restype = ctypes.c_int
user32.AttachThreadInput.argtypes = [
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.BOOL,
]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND
user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
user32.VkKeyScanW.restype = ctypes.c_short


VK: dict[str, int] = {
    "toggle_camera": 0x2D,  # Insert
    "lock_camera": 0x24,  # Home
    "forward": 0x57,  # W
    "backward": 0x53,  # S
    "left": 0x41,  # A
    "right": 0x44,  # D
    "up": 0x67,  # Numpad 7
    "down": 0x69,  # Numpad 9
    "rotate_up": 0x26,
    "rotate_down": 0x28,
    "rotate_left": 0x25,
    "rotate_right": 0x27,
    "roll_left": 0x61,  # Numpad 1
    "roll_right": 0x63,  # Numpad 3
    "reset_roll": 0x62,  # Numpad 2
    "fov_in": 0x6D,  # Numpad -
    "fov_out": 0x6B,  # Numpad +
    "fov_reset": 0x6A,  # Numpad *
    "hud": 0x2E,  # Delete
    "pause": 0x60,  # Numpad 0
    "time_earlier": 0xBC,  # Comma
    "time_later": 0xBE,  # Period
    "path_add": 0x73,  # F4
    "path_play_pause": 0x76,  # F7
    "path_stop": 0x77,  # F8
    "path_add_node": 0x79,  # F10
}


def _last_error(label: str) -> OSError:
    return ctypes.WinError(ctypes.get_last_error(), label)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def find_process_id(exe_name: str = PROCESS_NAME) -> int:
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        raise _last_error("CreateToolhelp32Snapshot(process)")
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == exe_name.lower():
                return int(entry.th32ProcessID)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    raise RuntimeError(f"{exe_name} is not running")


def _create_module_snapshot(pid: int) -> int:
    flags = TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32
    for attempt in range(MODULE_SNAPSHOT_MAX_ATTEMPTS):
        snapshot = kernel32.CreateToolhelp32Snapshot(flags, pid)
        if snapshot != INVALID_HANDLE_VALUE:
            return int(snapshot)
        error_code = ctypes.get_last_error()
        if error_code != ERROR_BAD_LENGTH:
            raise ctypes.WinError(error_code, "CreateToolhelp32Snapshot(module)")
        if attempt + 1 < MODULE_SNAPSHOT_MAX_ATTEMPTS:
            time.sleep(min(0.002 * (attempt + 1), 0.02))
    raise ctypes.WinError(
        ERROR_BAD_LENGTH,
        "CreateToolhelp32Snapshot(module) failed after retries",
    )


def find_module(
    pid: int,
    module_name: str = MODULE_NAME,
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    cache_key = (pid, module_name.casefold())
    if not refresh and cache_key in _MODULE_CACHE:
        return dict(_MODULE_CACHE[cache_key])

    snapshot = _create_module_snapshot(pid)
    try:
        entry = MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Module32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szModule.lower() == module_name.lower():
                result = {
                    "base": int(ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value),
                    "size": int(entry.modBaseSize),
                    "path": entry.szExePath,
                }
                _MODULE_CACHE[cache_key] = result
                return dict(result)
            ok = kernel32.Module32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    raise RuntimeError(
        f"{module_name} is not loaded. Start IGCSClient and click Inject DLL first."
    )


class ProcessReader:
    def __init__(
        self,
        pid: int,
        *,
        writable: bool = False,
        remote_execute: bool = False,
    ):
        self.pid = pid
        access = PROCESS_VM_READ | PROCESS_QUERY_INFORMATION
        if writable:
            access |= PROCESS_VM_WRITE | PROCESS_VM_OPERATION
        if remote_execute:
            access |= PROCESS_CREATE_THREAD | PROCESS_VM_WRITE | PROCESS_VM_OPERATION
        self.handle = kernel32.OpenProcess(
            access, False, pid
        )
        if not self.handle:
            raise _last_error("OpenProcess")

    def close(self) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self) -> "ProcessReader":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def read(self, address: int, size: int) -> bytes:
        if not address or size <= 0:
            raise ValueError("invalid address/size")
        buffer = (ctypes.c_ubyte * size)()
        read_count = ctypes.c_size_t()
        ok = kernel32.ReadProcessMemory(
            self.handle,
            ctypes.c_void_p(address),
            buffer,
            size,
            ctypes.byref(read_count),
        )
        if not ok or read_count.value != size:
            raise _last_error(f"ReadProcessMemory(0x{address:X}, {size})")
        return bytes(buffer)

    def try_read(self, address: int, size: int) -> bytes | None:
        try:
            return self.read(address, size)
        except (OSError, ValueError):
            return None

    def u64(self, address: int) -> int:
        return struct.unpack("<Q", self.read(address, 8))[0]

    def f32(self, address: int) -> float:
        return struct.unpack("<f", self.read(address, 4))[0]

    def f64(self, address: int) -> float:
        return struct.unpack("<d", self.read(address, 8))[0]

    def write(self, address: int, data: bytes) -> None:
        if not address or not data:
            raise ValueError("invalid address/data")
        buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        write_count = ctypes.c_size_t()
        ok = kernel32.WriteProcessMemory(
            self.handle,
            ctypes.c_void_p(address),
            buffer,
            len(data),
            ctypes.byref(write_count),
        )
        if not ok or write_count.value != len(data):
            raise _last_error(f"WriteProcessMemory(0x{address:X}, {len(data)})")

    def write_scalar(self, address: int, data_type: str, value: float) -> None:
        if data_type == "f32":
            self.write(address, struct.pack("<f", value))
            return
        if data_type == "f64":
            self.write(address, struct.pack("<d", value))
            return
        raise ValueError(f"Unsupported field type: {data_type}")

    def call_remote(self, address: int, parameter: int = 0) -> int:
        thread_id = wintypes.DWORD()
        thread = kernel32.CreateRemoteThread(
            self.handle,
            None,
            0,
            ctypes.c_void_p(address),
            ctypes.c_void_p(parameter),
            0,
            ctypes.byref(thread_id),
        )
        if not thread:
            raise _last_error("CreateRemoteThread")
        try:
            wait_result = kernel32.WaitForSingleObject(thread, 10_000)
            if wait_result != WAIT_OBJECT_0:
                raise RuntimeError(f"Remote call wait failed: 0x{wait_result:X}")
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeThread(thread, ctypes.byref(exit_code)):
                raise _last_error("GetExitCodeThread")
            return int(exit_code.value)
        finally:
            kernel32.CloseHandle(thread)

    def call_stub(self, code: bytes) -> int:
        remote_code = kernel32.VirtualAllocEx(
            self.handle,
            None,
            len(code),
            MEM_COMMIT | MEM_RESERVE,
            PAGE_EXECUTE_READWRITE,
        )
        if not remote_code:
            raise _last_error("VirtualAllocEx")
        remote_address = int(ctypes.cast(remote_code, ctypes.c_void_p).value)
        try:
            self.write(remote_address, code)
            if not kernel32.FlushInstructionCache(
                self.handle, ctypes.c_void_p(remote_address), len(code)
            ):
                raise _last_error("FlushInstructionCache")
            return self.call_remote(remote_address)
        finally:
            kernel32.VirtualFreeEx(
                self.handle,
                ctypes.c_void_p(remote_address),
                0,
                MEM_RELEASE,
            )


def resolve_core(reader: ProcessReader, module_base: int) -> dict[str, int]:
    feature = reader.u64(module_base + CAMERA_FEATURE_PTR_RVA)
    if not feature:
        raise RuntimeError(
            "CameraFeature pointer is null. Wait until the IGCS log says 'Camera found.'"
        )
    camera = reader.u64(feature + CAMERA_OBJECT_PTR_OFFSET)
    if not camera:
        raise RuntimeError("Camera object pointer is null")
    return {"feature": feature, "camera": camera}


def _plausible_pointer(value: int) -> bool:
    return 0x10000 <= value < 0x0000800000000000 and value % 2 == 0


def _add_pointer_regions(
    reader: ProcessReader,
    regions: dict[str, dict[str, Any]],
    source_name: str,
    source_base: int,
    source_data: bytes,
    seen_targets: set[int],
    *,
    limit: int = 128,
) -> None:
    added = 0
    for offset in range(0, len(source_data) - 7, 8):
        target = struct.unpack_from("<Q", source_data, offset)[0]
        if not _plausible_pointer(target) or target in seen_targets:
            continue
        data = reader.try_read(target, POINTER_CAPTURE_SIZE)
        if data is None:
            continue
        seen_targets.add(target)
        regions[f"{source_name}_ptr_{offset:04X}"] = {
            "base": target,
            "source_base": source_base,
            "source_offset": offset,
            "size": len(data),
            "data_b64": base64.b64encode(data).decode("ascii"),
        }
        added += 1
        if added >= limit:
            break


def capture_snapshot() -> dict[str, Any]:
    pid = find_process_id()
    module = find_module(pid)
    with ProcessReader(pid) as reader:
        core = resolve_core(reader, module["base"])
        feature_data = reader.read(core["feature"], ROOT_CAPTURE_SIZE)
        camera_data = reader.read(core["camera"], CAMERA_CAPTURE_SIZE)
        regions: dict[str, dict[str, Any]] = {
            "feature": {
                "base": core["feature"],
                "size": len(feature_data),
                "data_b64": base64.b64encode(feature_data).decode("ascii"),
            },
            "camera": {
                "base": core["camera"],
                "size": len(camera_data),
                "data_b64": base64.b64encode(camera_data).decode("ascii"),
            },
        }
        seen_targets = {core["feature"], core["camera"]}
        _add_pointer_regions(
            reader,
            regions,
            "feature",
            core["feature"],
            feature_data,
            seen_targets,
        )
        _add_pointer_regions(
            reader,
            regions,
            "camera",
            core["camera"],
            camera_data,
            seen_targets,
        )
    return {
        "captured_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(),
        "pid": pid,
        "module": module,
        "camera_feature_ptr_rva": CAMERA_FEATURE_PTR_RVA,
        "camera_object_ptr_offset": CAMERA_OBJECT_PTR_OFFSET,
        "regions": regions,
    }


def save_snapshot(label: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = DATA_DIR / f"{stamp}_{label}.json"
    data = capture_snapshot()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _load_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _region_float_map(snapshot: dict[str, Any]) -> dict[tuple[str, int], float]:
    result: dict[tuple[str, int], float] = {}
    for region_name, region in snapshot["regions"].items():
        raw = base64.b64decode(region["data_b64"])
        for offset in range(0, len(raw) - 3, 4):
            value = struct.unpack_from("<f", raw, offset)[0]
            if math.isfinite(value) and abs(value) < 1.0e10:
                result[(region_name, offset)] = value
    return result


def float_differences(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    epsilon: float = 1.0e-6,
) -> list[dict[str, Any]]:
    before_map = _region_float_map(before)
    after_map = _region_float_map(after)
    differences: list[dict[str, Any]] = []
    for key in before_map.keys() & after_map.keys():
        left = before_map[key]
        right = after_map[key]
        delta = right - left
        if abs(delta) <= epsilon:
            continue
        region, offset = key
        differences.append(
            {
                "region": region,
                "offset": offset,
                "offset_hex": f"0x{offset:X}",
                "before": left,
                "after": right,
                "delta": delta,
            }
        )
    differences.sort(key=lambda item: abs(item["delta"]), reverse=True)
    return differences


def save_diff(before_path: Path, after_path: Path, output: Path | None) -> Path:
    before = _load_snapshot(before_path)
    after = _load_snapshot(after_path)
    rows = float_differences(before, after)
    if output is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        output = DATA_DIR / (
            f"diff_{before_path.stem}_TO_{after_path.stem}.csv"
        )
    with output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "region",
                "offset",
                "offset_hex",
                "before",
                "after",
                "delta",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    return output


def find_game_window(pid: int) -> int:
    windows: list[tuple[int, str]] = []

    @WNDENUMPROC
    def callback(hwnd: int, _: int) -> bool:
        window_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value == pid and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            buffer = ctypes.create_unicode_buffer(max(1, length + 1))
            user32.GetWindowTextW(hwnd, buffer, len(buffer))
            windows.append((int(hwnd), buffer.value))
        return True

    user32.EnumWindows(callback, 0)
    if not windows:
        raise RuntimeError("No visible KingdomCome.exe window found")
    for hwnd, title in windows:
        if title.strip().casefold() == "kingdom come: deliverance ii":
            return hwnd
    candidates = [
        (hwnd, title)
        for hwnd, title in windows
        if not title.lower().endswith("kingdomcome.exe")
    ]
    if len(candidates) == 1:
        return candidates[0][0]
    raise RuntimeError(
        "Could not uniquely select the main game window. Visible titles: "
        + ", ".join(repr(title) for _, title in windows)
    )


def _send_key(vk: int, key_up: bool) -> None:
    flags = KEYEVENTF_KEYUP if key_up else 0
    event = INPUT(
        type=INPUT_KEYBOARD,
        union=_INPUTUNION(ki=KEYBDINPUT(vk, 0, flags, 0, 0)),
    )
    sent = user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))
    if sent != 1:
        raise _last_error("SendInput")


def _send_unicode_character(character: str, key_up: bool) -> None:
    if len(character) != 1:
        raise ValueError("Expected exactly one Unicode character")
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if key_up else 0)
    event = INPUT(
        type=INPUT_KEYBOARD,
        union=_INPUTUNION(
            ki=KEYBDINPUT(0, ord(character), flags, 0, 0)
        ),
    )
    sent = user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT))
    if sent != 1:
        raise _last_error("SendInput Unicode")


def _send_unicode_text(text: str) -> None:
    for character in text:
        _send_unicode_character(character, False)
        _send_unicode_character(character, True)
        time.sleep(0.004)


def _send_virtual_text(text: str) -> None:
    modifier_keys = ((1, 0x10), (2, 0x11), (4, 0x12))
    for character in text:
        key_state = int(user32.VkKeyScanW(character))
        if key_state == -1:
            raise ValueError(
                f"Character cannot be typed with the active keyboard layout: {character!r}"
            )
        vk = key_state & 0xFF
        modifiers = (key_state >> 8) & 0xFF
        active_modifiers = [
            vk_modifier
            for flag, vk_modifier in modifier_keys
            if modifiers & flag
        ]
        for vk_modifier in active_modifiers:
            _send_key(vk_modifier, False)
        _send_key(vk, False)
        _send_key(vk, True)
        for vk_modifier in reversed(active_modifiers):
            _send_key(vk_modifier, True)
        time.sleep(0.012)


def _focus_game_window(hwnd: int) -> None:
    if user32.IsIconic(hwnd):
        user32.ShowWindowAsync(hwnd, 9)  # SW_RESTORE
        time.sleep(0.15)
    foreground = user32.GetForegroundWindow()
    current_thread = kernel32.GetCurrentThreadId()
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    foreground_thread = (
        user32.GetWindowThreadProcessId(foreground, None)
        if foreground
        else 0
    )
    attached: list[int] = []
    try:
        for thread_id in (foreground_thread, target_thread):
            if thread_id and thread_id != current_thread and thread_id not in attached:
                if user32.AttachThreadInput(current_thread, thread_id, True):
                    attached.append(thread_id)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        for thread_id in reversed(attached):
            user32.AttachThreadInput(current_thread, thread_id, False)
    time.sleep(0.12)


def _require_focused_game_window() -> int:
    pid = find_process_id()
    hwnd = find_game_window(pid)
    if user32.GetForegroundWindow() != hwnd:
        _focus_game_window(hwnd)
    if user32.GetForegroundWindow() != hwnd:
        raise RuntimeError(
            "Could not focus the game window. Bring Kingdom Come: Deliverance II "
            "to the foreground and retry the control command."
        )
    time.sleep(0.12)
    return hwnd


def perform_action(action: str, duration_ms: int = 120) -> None:
    if action not in VK:
        raise ValueError(f"Unknown action: {action}")
    _require_focused_game_window()
    vk = VK[action]
    _send_key(vk, False)
    time.sleep(max(0.02, duration_ms / 1000.0))
    _send_key(vk, True)
    time.sleep(0.15)


def send_console_command(command: str) -> None:
    normalized = command.strip()
    if not normalized:
        raise ValueError("Console command cannot be empty")
    if "\n" in normalized or "\r" in normalized:
        raise ValueError("Console command must be a single line")

    _require_focused_game_window()
    _send_key(0xC0, False)  # Grave accent / tilde opens the dev console.
    _send_key(0xC0, True)
    time.sleep(0.3)
    _send_virtual_text(normalized)
    _send_key(0x0D, False)  # Enter
    _send_key(0x0D, True)
    time.sleep(0.4)
    _send_key(0xC0, False)
    _send_key(0xC0, True)
    time.sleep(0.15)


def latest_camera_enabled_state() -> bool | None:
    if not LOG_PATH.exists():
        return None
    try:
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if "[USERMSG]" not in line:
            continue
        if "Camera enabled" in line:
            return True
        if "Camera disabled" in line:
            return False
    return None


def calibrate(duration_ms: int, force: bool) -> Path:
    state = latest_camera_enabled_state()
    if state is not True and not force:
        raise RuntimeError(
            "The latest IGCS log does not say 'Camera enabled'. Enable the free "
            "camera first, then rerun calibrate. Use --force only if the log is stale."
        )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    baseline_path = save_snapshot("cal_baseline")
    baseline = _load_snapshot(baseline_path)

    perform_action("forward", duration_ms)
    time.sleep(0.35)
    forward_path = save_snapshot("cal_forward")
    forward = _load_snapshot(forward_path)

    perform_action("backward", duration_ms)
    time.sleep(0.45)
    returned_path = save_snapshot("cal_returned")
    returned = _load_snapshot(returned_path)

    perform_action("rotate_right", max(100, duration_ms // 2))
    time.sleep(0.35)
    rotated_path = save_snapshot("cal_rotated")
    rotated = _load_snapshot(rotated_path)

    maps = [_region_float_map(item) for item in (baseline, forward, returned, rotated)]
    common = set(maps[0])
    for item in maps[1:]:
        common &= set(item)

    rows: list[dict[str, Any]] = []
    for key in common:
        b, f, ret, rot = (item[key] for item in maps)
        move_delta = f - b
        return_error = ret - b
        rotate_delta = rot - ret
        if max(abs(move_delta), abs(rotate_delta)) < 1.0e-6:
            continue
        if not all(math.isfinite(v) and abs(v) < 1.0e10 for v in (b, f, ret, rot)):
            continue

        kind = "changed"
        if (
            abs(move_delta) > 1.0e-5
            and abs(return_error) <= max(1.0e-4, abs(move_delta) * 0.35)
            and abs(rotate_delta) <= max(1.0e-4, abs(move_delta) * 0.20)
        ):
            kind = "position_candidate"
        elif (
            abs(rotate_delta) > 1.0e-5
            and abs(move_delta) <= max(1.0e-4, abs(rotate_delta) * 0.20)
        ):
            kind = "orientation_candidate"

        rows.append(
            {
                "kind": kind,
                "region": key[0],
                "offset": key[1],
                "offset_hex": f"0x{key[1]:X}",
                "baseline": b,
                "forward": f,
                "returned": ret,
                "rotated": rot,
                "move_delta": move_delta,
                "return_error": return_error,
                "rotate_delta": rotate_delta,
            }
        )

    priority = {
        "position_candidate": 0,
        "orientation_candidate": 1,
        "changed": 2,
    }
    rows.sort(
        key=lambda row: (
            priority[row["kind"]],
            row["region"],
            row["offset"],
        )
    )
    report_path = DATA_DIR / (
        f"{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}_calibration.csv"
    )
    with report_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)

    manifest_path = report_path.with_suffix(".json")
    manifest_path.write_text(
        json.dumps(
            {
                "baseline": str(baseline_path),
                "forward": str(forward_path),
                "returned": str(returned_path),
                "rotated": str(rotated_path),
                "report": str(report_path),
                "duration_ms": duration_ms,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return report_path


def _resolve_named_base(
    reader: ProcessReader,
    module_base: int,
    base_name: str,
) -> int:
    core = resolve_core(reader, module_base)
    if base_name == "feature":
        return core["feature"]
    if base_name == "camera":
        return core["camera"]
    for prefix, root_name in (("feature_ptr_", "feature"), ("camera_ptr_", "camera")):
        if base_name.startswith(prefix):
            offset = int(base_name[len(prefix) :], 16)
            return reader.u64(core[root_name] + offset)
    raise ValueError(f"Unsupported base name in pose config: {base_name}")


def _field_address(base: int, spec: dict[str, Any]) -> int:
    offset = spec["offset"]
    return base + (int(offset, 0) if isinstance(offset, str) else int(offset))


def _read_field(
    reader: ProcessReader,
    address: int,
    data_type: str,
) -> float:
    if data_type == "f32":
        return reader.f32(address)
    if data_type == "f64":
        return reader.f64(address)
    raise ValueError(f"Unsupported field type: {data_type}")


def _rva_to_file_offset(blob: bytes, rva: int) -> int:
    pe_offset = struct.unpack_from("<I", blob, 0x3C)[0]
    section_count = struct.unpack_from("<H", blob, pe_offset + 6)[0]
    optional_size = struct.unpack_from("<H", blob, pe_offset + 20)[0]
    section_table = pe_offset + 24 + optional_size
    for index in range(section_count):
        section = section_table + index * 40
        virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from(
            "<IIII", blob, section + 8
        )
        if virtual_address <= rva < virtual_address + max(virtual_size, raw_size):
            return raw_offset + (rva - virtual_address)
    raise RuntimeError(f"RVA 0x{rva:X} is not mapped by a PE section")


def dll_export_rvas() -> dict[str, int]:
    blob = DLL_PATH.read_bytes()
    pe_offset = struct.unpack_from("<I", blob, 0x3C)[0]
    optional_header = pe_offset + 24
    if struct.unpack_from("<H", blob, optional_header)[0] != 0x20B:
        raise RuntimeError("Expected a 64-bit PE DLL")
    export_rva = struct.unpack_from("<I", blob, optional_header + 112)[0]
    export_offset = _rva_to_file_offset(blob, export_rva)
    (
        function_count,
        name_count,
        functions_rva,
        names_rva,
        ordinals_rva,
    ) = struct.unpack_from("<IIIII", blob, export_offset + 20)
    functions_offset = _rva_to_file_offset(blob, functions_rva)
    names_offset = _rva_to_file_offset(blob, names_rva)
    ordinals_offset = _rva_to_file_offset(blob, ordinals_rva)
    exports: dict[str, int] = {}
    for index in range(name_count):
        name_rva = struct.unpack_from("<I", blob, names_offset + index * 4)[0]
        name_offset = _rva_to_file_offset(blob, name_rva)
        name_end = blob.index(b"\0", name_offset)
        name = blob[name_offset:name_end].decode("ascii")
        ordinal = struct.unpack_from("<H", blob, ordinals_offset + index * 2)[0]
        if ordinal >= function_count:
            continue
        exports[name] = struct.unpack_from(
            "<I", blob, functions_offset + ordinal * 4
        )[0]
    return exports


def _remote_export_addresses(module_base: int) -> dict[str, int]:
    required = {
        "IGCS_StartScreenshotSession",
        "IGCS_MoveCameraPanorama",
        "IGCS_MoveCameraMultishot",
        "IGCS_EndScreenshotSession",
    }
    rvas = dll_export_rvas()
    missing = required - rvas.keys()
    if missing:
        raise RuntimeError(f"Missing expected DLL exports: {sorted(missing)}")
    return {name: module_base + rvas[name] for name in required}


def _move_multishot_stub(
    function_address: int,
    step_left_right: float,
    step_up_down: float,
    fov_degrees: float,
    from_start_position: bool,
) -> bytes:
    code = bytearray(b"\x48\x83\xEC\x28")
    for bits, movd in (
        (struct.pack("<f", step_left_right), b"\x66\x0F\x6E\xC0"),
        (struct.pack("<f", step_up_down), b"\x66\x0F\x6E\xC8"),
        (struct.pack("<f", fov_degrees), b"\x66\x0F\x6E\xD0"),
    ):
        code += b"\xB8" + bits + movd
    code += b"\x41\xB9" + struct.pack("<I", int(from_start_position))
    code += b"\x48\xB8" + struct.pack("<Q", function_address)
    code += b"\xFF\xD0\x48\x83\xC4\x28\x31\xC0\xC3"
    return bytes(code)


def _move_panorama_stub(function_address: int, step_angle: float) -> bytes:
    return (
        b"\x48\x83\xEC\x28"
        + b"\xB8"
        + struct.pack("<f", step_angle)
        + b"\x66\x0F\x6E\xC0"
        + b"\x48\xB8"
        + struct.pack("<Q", function_address)
        + b"\xFF\xD0\x48\x83\xC4\x28\x31\xC0\xC3"
    )


def _trajectory_step_stub(
    panorama_address: int,
    multishot_address: int,
    panorama_step: float,
    step_left_right: float,
    step_up_down: float,
    fov_degrees: float,
) -> bytes:
    code = bytearray(b"\x48\x83\xEC\x28")
    code += b"\xB8" + struct.pack("<f", panorama_step) + b"\x66\x0F\x6E\xC0"
    code += b"\x48\xB8" + struct.pack("<Q", panorama_address) + b"\xFF\xD0"
    for bits, movd in (
        (struct.pack("<f", step_left_right), b"\x66\x0F\x6E\xC0"),
        (struct.pack("<f", step_up_down), b"\x66\x0F\x6E\xC8"),
        (struct.pack("<f", fov_degrees), b"\x66\x0F\x6E\xD0"),
    ):
        code += b"\xB8" + bits + movd
    code += b"\x45\x31\xC9"
    code += b"\x48\xB8" + struct.pack("<Q", multishot_address) + b"\xFF\xD0"
    code += b"\x48\x83\xC4\x28\x31\xC0\xC3"
    return bytes(code)


def exported_camera_test(
    config_path: Path,
    *,
    step_left_right: float,
    step_up_down: float,
    fov_degrees: float,
    panorama_degrees: float,
    hold_seconds: float,
) -> dict[str, Any]:
    if _sha256(DLL_PATH) != EXPECTED_DLL_SHA256:
        raise RuntimeError("DLL hash mismatch; refusing remote export calls")
    pid = find_process_id()
    module = find_module(pid)
    exports = _remote_export_addresses(module["base"])
    report: dict[str, Any] = {"before": read_pose(config_path)}
    with ProcessReader(pid, writable=True, remote_execute=True) as process:
        session_type = 0 if panorama_degrees and not (
            step_left_right or step_up_down or fov_degrees
        ) else 1
        start_result = process.call_remote(
            exports["IGCS_StartScreenshotSession"], session_type
        )
        report["start_result"] = start_result
        if start_result != 0:
            raise RuntimeError(f"IGCS_StartScreenshotSession returned {start_result}")
        try:
            if panorama_degrees:
                process.call_stub(
                    _move_panorama_stub(
                        exports["IGCS_MoveCameraPanorama"],
                        math.radians(panorama_degrees),
                    )
                )
            if step_left_right or step_up_down or fov_degrees:
                process.call_stub(
                    _move_multishot_stub(
                        exports["IGCS_MoveCameraMultishot"],
                        step_left_right,
                        step_up_down,
                        fov_degrees,
                        False,
                    )
                )
            time.sleep(0.15)
            report["moved"] = read_pose(config_path)
            if hold_seconds > 0:
                time.sleep(hold_seconds)
        finally:
            process.call_remote(exports["IGCS_EndScreenshotSession"])
    time.sleep(0.15)
    report["restored"] = read_pose(config_path)
    return report


def _wrap_degrees(value: float) -> float:
    return math.remainder(value, 360.0)


def _build_export_goto_plan(
    before: dict[str, Any],
    *,
    target_x: float,
    target_y: float,
    target_z: float,
    target_yaw_degrees: float,
) -> dict[str, float]:
    dx = target_x - float(before["x"])
    dy = target_y - float(before["y"])
    dz = target_z - float(before["z"])
    horizontal_distance = math.hypot(dx, dy)
    world_direction_degrees = (
        math.degrees(math.atan2(dy, dx))
        if horizontal_distance > 1.0e-9
        else float(before["yaw_degrees"])
    ) % 360.0
    # IGCS_MoveCameraMultishot moves along the camera's left/right axis, not
    # along its forward axis. KCD2's reported world yaw also runs in the
    # opposite sense to atan2(y, x), so align the lateral axis with the
    # requested world-space displacement before applying the scalar step.
    lateral_camera_yaw = (270.0 - world_direction_degrees) % 360.0
    return {
        "dx": dx,
        "dy": dy,
        "dz": dz,
        "horizontal_distance": horizontal_distance,
        "world_direction_degrees": world_direction_degrees,
        "movement_yaw_degrees": lateral_camera_yaw,
        "first_panorama_step_degrees": _wrap_degrees(
            float(before["yaw_degrees"]) - lateral_camera_yaw
        ),
        "final_panorama_step_degrees": _wrap_degrees(
            lateral_camera_yaw - target_yaw_degrees
        ),
    }


def _apply_export_goto_plan(
    process: ProcessReader,
    exports: dict[str, int],
    plan: dict[str, float],
    *,
    target_fov_degrees: float,
) -> None:
    first_step = plan["first_panorama_step_degrees"]
    if abs(first_step) > 1.0e-7:
        process.call_stub(
            _move_panorama_stub(
                exports["IGCS_MoveCameraPanorama"],
                math.radians(first_step),
            )
        )
    process.call_stub(
        _move_multishot_stub(
            exports["IGCS_MoveCameraMultishot"],
            plan["horizontal_distance"],
            plan["dz"],
            target_fov_degrees,
            False,
        )
    )
    final_step = plan["final_panorama_step_degrees"]
    if abs(final_step) > 1.0e-7:
        process.call_stub(
            _move_panorama_stub(
                exports["IGCS_MoveCameraPanorama"],
                math.radians(final_step),
            )
        )


def _pose_target_error(
    observed: dict[str, Any],
    *,
    target_x: float,
    target_y: float,
    target_z: float,
    target_yaw_degrees: float,
    target_fov_degrees: float,
) -> dict[str, float]:
    return {
        "x": float(observed["x"]) - target_x,
        "y": float(observed["y"]) - target_y,
        "z": float(observed["z"]) - target_z,
        "yaw_degrees": _wrap_degrees(
            float(observed["yaw_degrees"]) - target_yaw_degrees
        ),
        "fov_degrees": float(observed["fov_degrees"]) - target_fov_degrees,
    }


def _write_absolute_export_pose(
    config_path: Path,
    *,
    target_x: float,
    target_y: float,
    target_z: float,
    target_yaw_degrees: float,
    target_pitch_degrees: float,
    target_roll_degrees: float,
    target_fov_degrees: float,
) -> dict[str, Any]:
    """Write a complete export pose through the verified CameraTools fields.

    The IGCS Multishot export accepts scalar relative movement values.  Its
    lateral axis is not documented in world coordinates and empirical tests
    showed that converting a requested XYZ displacement into that axis can
    miss the target by tens of metres.  The CameraTools camera object itself
    exposes stable writable XYZ/Euler/FOV fields, so export capture uses those
    fields for absolute positioning and keeps the IGCS screenshot session only
    for its lifecycle/restore behavior.
    """
    return set_pose(
        config_path,
        {
            "x": target_x,
            "y": target_y,
            "z": target_z,
            "pitch_radians": math.radians(target_pitch_degrees),
            "yaw_radians": math.radians(target_yaw_degrees),
            "roll_radians": math.radians(target_roll_degrees),
            "fov_radians": math.radians(target_fov_degrees),
        },
        {},
        dry_run=False,
        hold_ms=ABSOLUTE_POSE_HOLD_MS,
        write_report=False,
    )


def exported_camera_goto(
    config_path: Path,
    *,
    target_x: float,
    target_y: float,
    target_z: float,
    target_yaw_degrees: float,
    target_pitch_degrees: float = 0.0,
    target_roll_degrees: float = 0.0,
    target_fov_degrees: float,
    write_report: bool = True,
) -> dict[str, Any]:
    if _sha256(DLL_PATH) != EXPECTED_DLL_SHA256:
        raise RuntimeError("DLL hash mismatch; refusing remote export calls")
    pid = find_process_id()
    module = find_module(pid)
    exports = _remote_export_addresses(module["base"])
    before = read_pose(config_path)
    report: dict[str, Any] = {
        "before": before,
        "target": {
            "x": target_x,
            "y": target_y,
            "z": target_z,
            "yaw_degrees": target_yaw_degrees,
            "pitch_degrees": target_pitch_degrees,
            "roll_degrees": target_roll_degrees,
            "fov_degrees": target_fov_degrees,
        },
        "control_mode": "absolute_pose_write",
    }
    with ProcessReader(pid, writable=True, remote_execute=True) as process:
        start_result = process.call_remote(
            exports["IGCS_StartScreenshotSession"], 1
        )
        report["start_result"] = start_result
        if start_result != 0:
            raise RuntimeError(f"IGCS_StartScreenshotSession returned {start_result}")
        try:
            report["pose_write"] = _write_absolute_export_pose(
                config_path,
                target_x=target_x,
                target_y=target_y,
                target_z=target_z,
                target_yaw_degrees=target_yaw_degrees,
                target_pitch_degrees=target_pitch_degrees,
                target_roll_degrees=target_roll_degrees,
                target_fov_degrees=target_fov_degrees,
            )
            moved = report["pose_write"]["observed_after"]
            report["observed"] = moved
            report["error"] = _pose_target_error(
                moved,
                target_x=target_x,
                target_y=target_y,
                target_z=target_z,
                target_yaw_degrees=target_yaw_degrees,
                target_fov_degrees=target_fov_degrees,
            )
            report["error"].update(
                {
                    "pitch_degrees": moved["pitch_degrees"] - target_pitch_degrees,
                    "roll_degrees": moved["roll_degrees"] - target_roll_degrees,
                }
            )
            report["session_left_active"] = True
        except Exception:
            process.call_remote(exports["IGCS_EndScreenshotSession"])
            raise
    if write_report:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        report_path = DATA_DIR / (
            f"{dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_export_goto.json"
        )
        report["report_path"] = str(report_path)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return report


def adjust_active_export_to(
    config_path: Path,
    *,
    target_x: float,
    target_y: float,
    target_z: float,
    target_yaw_degrees: float,
    target_pitch_degrees: float = 0.0,
    target_roll_degrees: float = 0.0,
    target_fov_degrees: float,
) -> dict[str, Any]:
    """Correct an active screenshot session with an absolute pose write."""
    if _sha256(DLL_PATH) != EXPECTED_DLL_SHA256:
        raise RuntimeError("DLL hash mismatch; refusing remote export calls")
    pid = find_process_id()
    module = find_module(pid)
    pose_write = _write_absolute_export_pose(
        config_path,
        target_x=target_x,
        target_y=target_y,
        target_z=target_z,
        target_yaw_degrees=target_yaw_degrees,
        target_pitch_degrees=target_pitch_degrees,
        target_roll_degrees=target_roll_degrees,
        target_fov_degrees=target_fov_degrees,
    )
    before = pose_write["before"]
    observed = pose_write["observed_after"]
    error = _pose_target_error(
        observed,
        target_x=target_x,
        target_y=target_y,
        target_z=target_z,
        target_yaw_degrees=target_yaw_degrees,
        target_fov_degrees=target_fov_degrees,
    )
    error.update(
        {
            "pitch_degrees": observed["pitch_degrees"] - target_pitch_degrees,
            "roll_degrees": observed["roll_degrees"] - target_roll_degrees,
        }
    )
    return {
        "before": before,
        "target": {
            "x": target_x,
            "y": target_y,
            "z": target_z,
            "yaw_degrees": target_yaw_degrees,
            "pitch_degrees": target_pitch_degrees,
            "roll_degrees": target_roll_degrees,
            "fov_degrees": target_fov_degrees,
        },
        "control_mode": "absolute_pose_write",
        "pose_write": pose_write,
        "observed": observed,
        "error": error,
        "session_left_active": True,
    }


def end_export_session(config_path: Path) -> dict[str, Any]:
    pid = find_process_id()
    module = find_module(pid)
    exports = _remote_export_addresses(module["base"])
    before = read_pose(config_path)
    with ProcessReader(pid, writable=True, remote_execute=True) as process:
        process.call_remote(exports["IGCS_EndScreenshotSession"])
    time.sleep(0.15)
    return {"before": before, "after": read_pose(config_path)}


def random_trajectory(
    config_path: Path,
    *,
    duration: float,
    hz: float,
    seed: int | None,
    xy_scale: float,
) -> dict[str, Any]:
    if not 2.0 <= duration <= 60.0:
        raise RuntimeError("duration must be between 2 and 60 seconds")
    if not 5.0 <= hz <= 30.0:
        raise RuntimeError("hz must be between 5 and 30")
    if not 0.1 <= xy_scale <= 100.0:
        raise RuntimeError("xy-scale must be between 0.1 and 100")
    if _sha256(DLL_PATH) != EXPECTED_DLL_SHA256:
        raise RuntimeError("DLL hash mismatch; refusing remote export calls")

    actual_seed = (
        seed if seed is not None else random.SystemRandom().randrange(1, 2**31)
    )
    rng = random.Random(actual_seed)
    start_pose = read_pose(config_path)
    base_fov = float(start_pose["fov_degrees"])
    parameters = {
        "right_bias": rng.uniform(-1.2, 1.2),
        "right_amp": rng.uniform(1.5, 3.2),
        "right_phase": rng.uniform(0.0, math.tau),
        "up_bias": rng.uniform(-0.25, 0.25),
        "up_amp": rng.uniform(0.35, 1.0),
        "up_phase": rng.uniform(0.0, math.tau),
        "yaw_bias_deg_s": rng.uniform(-1.8, 1.8),
        "yaw_amp_deg_s": rng.uniform(3.0, 7.0),
        "yaw_phase": rng.uniform(0.0, math.tau),
        "fov_amp_deg": rng.uniform(1.2, 3.0),
        "fov_phase": rng.uniform(0.0, math.tau),
    }

    pid = find_process_id()
    module = find_module(pid)
    exports = _remote_export_addresses(module["base"])
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = DATA_DIR / f"{stamp}_random_trajectory.csv"
    manifest_path = DATA_DIR / f"{stamp}_random_trajectory.json"
    fieldnames = [
        "frame_id",
        "timestamp_sec",
        "planned_right_step",
        "planned_up_step",
        "planned_panorama_step_deg",
        "planned_fov_deg",
        "x",
        "y",
        "z",
        "q0",
        "q1",
        "q2",
        "q3",
        "pitch_degrees",
        "yaw_degrees",
        "roll_degrees",
        "fov_degrees",
    ]
    frame_count = max(2, round(duration * hz))
    interval = 1.0 / hz
    started = time.perf_counter()
    completed_frames = 0

    with ProcessReader(pid, writable=True, remote_execute=True) as process:
        start_result = process.call_remote(
            exports["IGCS_StartScreenshotSession"], 1
        )
        if start_result != 0:
            raise RuntimeError(f"IGCS_StartScreenshotSession returned {start_result}")
        try:
            with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                for frame_id in range(frame_count):
                    deadline = started + frame_id * interval
                    now = time.perf_counter()
                    if now < deadline:
                        time.sleep(deadline - now)
                    u = frame_id / (frame_count - 1)
                    envelope = math.sin(math.pi * u) ** 2
                    right_velocity = (
                        parameters["right_bias"]
                        + parameters["right_amp"]
                        * math.sin(math.tau * 0.85 * u + parameters["right_phase"])
                        + 0.35
                        * parameters["right_amp"]
                        * math.sin(
                            math.tau * 1.7 * u
                            + parameters["right_phase"] * 0.47
                        )
                    )
                    up_velocity = (
                        parameters["up_bias"]
                        + parameters["up_amp"]
                        * math.sin(math.tau * 0.72 * u + parameters["up_phase"])
                    )
                    yaw_velocity = (
                        parameters["yaw_bias_deg_s"]
                        + parameters["yaw_amp_deg_s"]
                        * math.sin(math.tau * 0.63 * u + parameters["yaw_phase"])
                    )
                    right_step = envelope * right_velocity * interval * xy_scale
                    up_step = envelope * up_velocity * interval
                    panorama_step_deg = envelope * yaw_velocity * interval
                    fov_degrees = max(
                        40.0,
                        min(
                            85.0,
                            base_fov
                            + envelope
                            * parameters["fov_amp_deg"]
                            * math.sin(
                                math.tau * 0.55 * u + parameters["fov_phase"]
                            ),
                        ),
                    )
                    process.call_stub(
                        _trajectory_step_stub(
                            exports["IGCS_MoveCameraPanorama"],
                            exports["IGCS_MoveCameraMultishot"],
                            math.radians(panorama_step_deg),
                            right_step,
                            up_step,
                            fov_degrees,
                        )
                    )
                    pose = read_pose(config_path)
                    writer.writerow(
                        {
                            "frame_id": frame_id,
                            "timestamp_sec": f"{time.perf_counter() - started:.9f}",
                            "planned_right_step": right_step,
                            "planned_up_step": up_step,
                            "planned_panorama_step_deg": panorama_step_deg,
                            "planned_fov_deg": fov_degrees,
                            **{name: pose[name] for name in (
                                "x", "y", "z", "q0", "q1", "q2", "q3",
                                "pitch_degrees", "yaw_degrees", "roll_degrees",
                                "fov_degrees",
                            )},
                        }
                    )
                    completed_frames += 1
        except Exception:
            process.call_remote(exports["IGCS_EndScreenshotSession"])
            raise

    result = {
        "seed": actual_seed,
        "duration_requested": duration,
        "hz_requested": hz,
        "xy_scale": xy_scale,
        "frames": completed_frames,
        "elapsed_seconds": time.perf_counter() - started,
        "start_pose": start_pose,
        "final_pose": read_pose(config_path),
        "parameters": parameters,
        "csv_path": str(csv_path),
        "session_left_active": True,
    }
    manifest_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    result["manifest_path"] = str(manifest_path)
    return result


def read_pose(config_path: Path) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    fields: dict[str, dict[str, Any]] = config.get("fields", {})
    if not fields:
        raise RuntimeError("Pose config has no fields")
    pid = find_process_id()
    module = find_module(pid)
    result: dict[str, Any] = {
        "captured_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(),
        "pid": pid,
    }
    with ProcessReader(pid) as reader:
        base_cache: dict[str, int] = {}
        for name, spec in fields.items():
            base_name = str(spec["base"])
            if base_name not in base_cache:
                base_cache[base_name] = _resolve_named_base(
                    reader, module["base"], base_name
                )
            result[name] = _read_field(
                reader,
                _field_address(base_cache[base_name], spec),
                str(spec.get("type", "f32")),
            )
    if all(name in result for name in ("q0", "q1", "q2", "q3")):
        result["quaternion_norm"] = math.sqrt(
            sum(float(result[name]) ** 2 for name in ("q0", "q1", "q2", "q3"))
        )
    for source, target in (
        ("pitch_radians", "pitch_degrees"),
        ("yaw_radians", "yaw_degrees"),
        ("roll_radians", "roll_degrees"),
        ("fov_radians", "fov_degrees"),
    ):
        if source in result:
            result[target] = math.degrees(float(result[source]))
    return result


def _pitch_yaw_quaternion(pitch: float, yaw: float) -> tuple[float, float, float, float]:
    """Return the verified KCD2 q0-q3 layout for a zero-roll camera."""
    sin_pitch = math.sin(pitch * 0.5)
    cos_pitch = math.cos(pitch * 0.5)
    sin_yaw = math.sin(yaw * 0.5)
    cos_yaw = math.cos(yaw * 0.5)
    return (
        sin_pitch * cos_yaw,
        sin_pitch * sin_yaw,
        cos_pitch * sin_yaw,
        cos_pitch * cos_yaw,
    )


_TRAJECTORY_BLOCK_FIELDS = (
    ("x", 0x18, "f64"),
    ("y", 0x20, "f64"),
    ("z", 0x28, "f64"),
    ("q0", 0x30, "f32"),
    ("q1", 0x34, "f32"),
    ("q2", 0x38, "f32"),
    ("q3", 0x3C, "f32"),
    ("fov_radians", 0x40, "f32"),
    ("pitch_radians", 0x44, "f32"),
    ("yaw_radians", 0x48, "f32"),
    ("roll_radians", 0x4C, "f32"),
)


def _validated_trajectory_block_offset(config_path: Path) -> int:
    """Verify that the versioned pose config still describes one pose block."""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    fields: dict[str, dict[str, Any]] = config.get("fields", {})
    for name, expected_offset, expected_type in _TRAJECTORY_BLOCK_FIELDS:
        spec = fields.get(name)
        if not isinstance(spec, dict):
            raise RuntimeError(f"Pose config has no field named {name}")
        offset = _field_address(0, spec)
        data_type = str(spec.get("type", "f32"))
        if (
            str(spec.get("base")) != "camera"
            or offset != expected_offset
            or data_type != expected_type
        ):
            raise RuntimeError(
                "Pose config no longer matches the verified contiguous "
                f"trajectory block at field {name}"
            )
    return _TRAJECTORY_BLOCK_FIELDS[0][1]


def _pack_trajectory_pose(frame: dict[str, Any]) -> bytes:
    values = {
        name: float(frame[name])
        for name in (
            "x",
            "y",
            "z",
            "yaw_degrees",
            "pitch_degrees",
            "roll_degrees",
            "fov_degrees",
        )
    }
    if not all(math.isfinite(value) for value in values.values()):
        raise RuntimeError("Trajectory frame contains a non-finite pose value")
    if abs(math.remainder(values["roll_degrees"], 360.0)) > 1.0e-4:
        raise RuntimeError(
            "Direct trajectory playback is verified only for roll=0"
        )
    if not 1.0 <= values["fov_degrees"] <= 179.0:
        raise RuntimeError("Trajectory FOV must be between 1 and 179 degrees")
    pitch = math.radians(values["pitch_degrees"])
    yaw = math.radians(values["yaw_degrees"])
    roll = math.radians(values["roll_degrees"])
    q0, q1, q2, q3 = _pitch_yaw_quaternion(pitch, yaw)
    return struct.pack(
        "<ddd8f",
        values["x"],
        values["y"],
        values["z"],
        q0,
        q1,
        q2,
        q3,
        math.radians(values["fov_degrees"]),
        pitch,
        yaw,
        roll,
    )


def _wait_for_trajectory_deadline(
    deadline: float,
    stop_requested: Callable[[], bool] | None,
) -> bool:
    """Sleep most of the interval and use a short final spin for 60 Hz timing."""
    while True:
        if stop_requested is not None and stop_requested():
            return False
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return True
        if remaining > 0.002:
            time.sleep(remaining - 0.001)


def play_absolute_trajectory(
    config_path: Path,
    frames: Iterable[dict[str, Any]],
    *,
    timing_csv_path: Path,
    stop_requested: Callable[[], bool] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Play a dense trajectory by writing one complete CameraTools pose per frame.

    A single process handle and one contiguous 56-byte write are used for every
    frame.  This avoids the per-field/per-frame process setup used by interactive
    set-pose. The original 56-byte pose block is restored directly afterward, so
    dense playback does not depend on IGCS's stateful screenshot-session API.
    """
    if _sha256(DLL_PATH) != EXPECTED_DLL_SHA256:
        raise RuntimeError("DLL hash mismatch; refusing direct trajectory playback")
    if latest_camera_enabled_state() is not True:
        raise RuntimeError(
            "KCD2 free camera is disabled. Enable CameraTools with Insert first."
        )

    prepared: list[tuple[dict[str, Any], float, bytes]] = []
    previous_time: float | None = None
    for index, raw in enumerate(frames):
        frame = dict(raw)
        source_time = float(frame.get("time_sec", index / 60.0))
        if not math.isfinite(source_time):
            raise RuntimeError(f"Trajectory frame {index} has invalid time_sec")
        if previous_time is not None and source_time < previous_time:
            raise RuntimeError("Trajectory time_sec values must be nondecreasing")
        prepared.append((frame, source_time, _pack_trajectory_pose(frame)))
        previous_time = source_time
    if len(prepared) < 2:
        raise RuntimeError("Direct trajectory playback needs at least two frames")

    block_offset = _validated_trajectory_block_offset(config_path)
    first_source_time = prepared[0][1]
    requested_duration = prepared[-1][1] - first_source_time
    timing_csv_path = Path(timing_csv_path)
    timing_csv_path.parent.mkdir(parents=True, exist_ok=True)
    pid = find_process_id()
    module = find_module(pid)
    completed = 0
    schedule_errors_ms: list[float] = []
    cancelled = False
    started_wall = dt.datetime.now().astimezone().isoformat()
    started_perf: float | None = None

    with ProcessReader(pid, writable=True) as process:
        core = resolve_core(process, module["base"])
        block_address = core["camera"] + block_offset
        original_pose_block = process.read(
            block_address, struct.calcsize("<ddd8f")
        )
        try:
            with timing_csv_path.open(
                "w", newline="", encoding="utf-8-sig"
            ) as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=(
                        "frame_id",
                        "step",
                        "source_time_sec",
                        "target_elapsed_sec",
                        "actual_write_start_sec",
                        "actual_write_end_sec",
                        "schedule_error_ms",
                        "write_duration_ms",
                    ),
                )
                writer.writeheader()
                started_perf = time.perf_counter()
                total = len(prepared)
                for frame_id, (frame, source_time, pose_block) in enumerate(prepared):
                    target_elapsed = source_time - first_source_time
                    deadline = started_perf + target_elapsed
                    if not _wait_for_trajectory_deadline(deadline, stop_requested):
                        cancelled = True
                        break
                    write_start = time.perf_counter()
                    process.write(block_address, pose_block)
                    write_end = time.perf_counter()
                    schedule_error_ms = (write_start - deadline) * 1000.0
                    schedule_errors_ms.append(schedule_error_ms)
                    writer.writerow(
                        {
                            "frame_id": frame_id,
                            "step": frame.get("step", frame_id),
                            "source_time_sec": f"{source_time:.9f}",
                            "target_elapsed_sec": f"{target_elapsed:.9f}",
                            "actual_write_start_sec": f"{write_start - started_perf:.9f}",
                            "actual_write_end_sec": f"{write_end - started_perf:.9f}",
                            "schedule_error_ms": f"{schedule_error_ms:.6f}",
                            "write_duration_ms": f"{(write_end - write_start) * 1000.0:.6f}",
                        }
                    )
                    completed += 1
                    if frame_id % 60 == 0:
                        handle.flush()
                        if progress_callback is not None:
                            progress_callback(completed, total)
                handle.flush()
        finally:
            process.write(block_address, original_pose_block)

    finished_perf = time.perf_counter()
    time.sleep(0.15)
    restored_pose = read_pose(config_path)
    sorted_errors = sorted(schedule_errors_ms)
    percentile_index = (
        min(len(sorted_errors) - 1, math.ceil(len(sorted_errors) * 0.95) - 1)
        if sorted_errors
        else 0
    )
    return {
        "status": "cancelled" if cancelled else "completed",
        "requested_frames": len(prepared),
        "completed_frames": completed,
        "requested_duration_sec": requested_duration,
        "actual_duration_sec": (
            finished_perf - started_perf if started_perf is not None else 0.0
        ),
        "started_at": started_wall,
        "finished_at": dt.datetime.now().astimezone().isoformat(),
        "timing_csv": str(timing_csv_path),
        "restore_mode": "direct_original_pose_block",
        "schedule_error_ms": {
            "min": min(schedule_errors_ms) if schedule_errors_ms else None,
            "max": max(schedule_errors_ms) if schedule_errors_ms else None,
            "mean": (
                sum(schedule_errors_ms) / len(schedule_errors_ms)
                if schedule_errors_ms
                else None
            ),
            "p95": sorted_errors[percentile_index] if sorted_errors else None,
        },
        "restored_pose": restored_pose,
    }


def set_pose(
    config_path: Path,
    requested: dict[str, float | None],
    deltas: dict[str, float],
    *,
    dry_run: bool,
    hold_ms: int,
    write_report: bool = True,
) -> dict[str, Any]:
    if _sha256(DLL_PATH) != EXPECTED_DLL_SHA256:
        raise RuntimeError("DLL hash mismatch; refusing to write with unverified offsets")
    if latest_camera_enabled_state() is not True:
        raise RuntimeError("Enable the free camera before using set-pose")

    config = json.loads(config_path.read_text(encoding="utf-8"))
    fields: dict[str, dict[str, Any]] = config.get("fields", {})
    pid = find_process_id()
    module = find_module(pid)
    current = read_pose(config_path)
    updates: dict[str, float] = {}

    for name, value in requested.items():
        if value is not None:
            if name not in fields:
                raise RuntimeError(f"Pose config has no field named {name}")
            updates[name] = float(value)
    for name, delta in deltas.items():
        if delta:
            if name not in fields:
                raise RuntimeError(f"Pose config has no field named {name}")
            updates[name] = float(current[name]) + float(delta)

    quaternion_names = ("q0", "q1", "q2", "q3")
    angle_names = ("pitch_radians", "yaw_radians", "roll_radians")
    if any(name in updates for name in angle_names):
        target_roll = updates.get("roll_radians", float(current["roll_radians"]))
        if abs(math.remainder(target_roll, math.tau)) > 1.0e-5:
            raise RuntimeError(
                "Euler-to-quaternion synchronization is verified only for roll=0"
            )
        target_pitch = updates.get(
            "pitch_radians", float(current["pitch_radians"])
        )
        target_yaw = updates.get("yaw_radians", float(current["yaw_radians"]))
        updates.update(
            dict(
                zip(
                    quaternion_names,
                    _pitch_yaw_quaternion(target_pitch, target_yaw),
                )
            )
        )

    supplied_quaternion = [name in updates for name in quaternion_names]
    if any(supplied_quaternion) and not all(supplied_quaternion):
        raise RuntimeError("Set all of q0, q1, q2, q3 together")
    if all(supplied_quaternion):
        norm = math.sqrt(sum(updates[name] ** 2 for name in quaternion_names))
        if not 0.98 <= norm <= 1.02:
            raise RuntimeError(f"Quaternion norm must be near 1.0; got {norm}")

    for name, value in updates.items():
        if not math.isfinite(value):
            raise RuntimeError(f"{name} must be finite")
    if "fov_radians" in updates and not math.radians(1) <= updates[
        "fov_radians"
    ] <= math.radians(179):
        raise RuntimeError("FOV must be between 1 and 179 degrees")
    if not updates:
        raise RuntimeError("No pose values or deltas were supplied")
    if not 0 <= hold_ms <= 60_000:
        raise RuntimeError("hold-ms must be between 0 and 60000")

    if not dry_run:
        with ProcessReader(pid, writable=True) as reader:
            base_cache: dict[str, int] = {}
            resolved_updates: list[tuple[int, str, float]] = []
            for name, value in updates.items():
                spec = fields[name]
                base_name = str(spec["base"])
                if base_name not in base_cache:
                    base_cache[base_name] = _resolve_named_base(
                        reader, module["base"], base_name
                    )
                resolved_updates.append(
                    (
                        _field_address(base_cache[base_name], spec),
                        str(spec.get("type", "f32")),
                        value,
                    )
                )
            hold_until = time.perf_counter() + hold_ms / 1000.0
            while True:
                for address, data_type, value in resolved_updates:
                    reader.write_scalar(address, data_type, value)
                if time.perf_counter() >= hold_until:
                    break
                time.sleep(1.0 / 120.0)
        time.sleep(POSE_WRITE_READBACK_DELAY_SECONDS)

    observed = read_pose(config_path)
    report: dict[str, Any] = {
        "dry_run": dry_run,
        "hold_ms": hold_ms,
        "config": str(config_path),
        "before": current,
        "requested_updates": updates,
        "observed_after": observed,
        "readback_error": {
            name: float(observed[name]) - value for name, value in updates.items()
        },
    }
    if write_report:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        report_path = DATA_DIR / (
            f"{dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_set_pose.json"
        )
        report["report_path"] = str(report_path)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return report


def record_pose(
    config_path: Path,
    seconds: float,
    hz: float,
    output: Path | None,
) -> Path:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    fields: dict[str, dict[str, Any]] = config.get("fields", {})
    if not fields:
        raise RuntimeError("Pose config has no fields")
    pid = find_process_id()
    module = find_module(pid)
    if output is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        output = DATA_DIR / (
            f"{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}_pose.csv"
        )
    interval = 1.0 / hz
    started = time.perf_counter()
    next_tick = started
    frame_id = 0
    with ProcessReader(pid) as reader, output.open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["frame_id", "timestamp_sec", *fields.keys()],
        )
        writer.writeheader()
        while True:
            now = time.perf_counter()
            elapsed = now - started
            if elapsed >= seconds:
                break
            if now < next_tick:
                time.sleep(min(next_tick - now, 0.005))
                continue
            base_cache: dict[str, int] = {}
            row: dict[str, Any] = {
                "frame_id": frame_id,
                "timestamp_sec": f"{elapsed:.9f}",
            }
            for name, spec in fields.items():
                base_name = str(spec["base"])
                if base_name not in base_cache:
                    base_cache[base_name] = _resolve_named_base(
                        reader, module["base"], base_name
                    )
                row[name] = _read_field(
                    reader,
                    _field_address(base_cache[base_name], spec),
                    str(spec.get("type", "f32")),
                )
            writer.writerow(row)
            frame_id += 1
            next_tick += interval
    return output


def inject_camera_dll() -> dict[str, Any]:
    """Inject the verified camera DLL with remote LoadLibraryW."""
    if not DLL_PATH.exists():
        raise RuntimeError(f"Camera DLL not found: {DLL_PATH}")
    dll_hash = _sha256(DLL_PATH)
    if dll_hash != EXPECTED_DLL_SHA256:
        raise RuntimeError(
            "DLL hash mismatch; refusing to inject an unverified camera build"
        )
    pid = find_process_id()
    try:
        loaded = find_module(pid)
        return {
            "pid": pid,
            "already_loaded": True,
            "module": loaded,
            "dll_sha256": dll_hash,
        }
    except RuntimeError:
        pass

    remote_kernel32 = find_module(pid, "kernel32.dll")
    local_kernel32 = kernel32.GetModuleHandleW("kernel32.dll")
    if not local_kernel32:
        raise _last_error("GetModuleHandleW(kernel32.dll)")
    local_load_library = kernel32.GetProcAddress(local_kernel32, b"LoadLibraryW")
    if not local_load_library:
        raise _last_error("GetProcAddress(LoadLibraryW)")
    local_kernel_base = int(
        ctypes.cast(local_kernel32, ctypes.c_void_p).value
    )
    local_load_address = int(
        ctypes.cast(local_load_library, ctypes.c_void_p).value
    )
    remote_load_library = (
        int(remote_kernel32["base"])
        + local_load_address
        - local_kernel_base
    )
    dll_path_bytes = (str(DLL_PATH.resolve()) + "\0").encode("utf-16-le")

    with ProcessReader(pid, writable=True, remote_execute=True) as process:
        remote_path = kernel32.VirtualAllocEx(
            process.handle,
            None,
            len(dll_path_bytes),
            MEM_COMMIT | MEM_RESERVE,
            PAGE_READWRITE,
        )
        if not remote_path:
            raise _last_error("VirtualAllocEx(DLL path)")
        remote_path_address = int(
            ctypes.cast(remote_path, ctypes.c_void_p).value
        )
        try:
            process.write(remote_path_address, dll_path_bytes)
            load_result = process.call_remote(
                remote_load_library, remote_path_address
            )
            if load_result == 0:
                raise RuntimeError("Remote LoadLibraryW returned NULL")
        finally:
            kernel32.VirtualFreeEx(
                process.handle,
                ctypes.c_void_p(remote_path_address),
                0,
                MEM_RELEASE,
            )

    deadline = time.monotonic() + 10.0
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            loaded = find_module(pid)
            return {
                "pid": pid,
                "already_loaded": False,
                "module": loaded,
                "dll_sha256": dll_hash,
            }
        except Exception as exc:
            last_error = exc
            time.sleep(0.1)
    raise RuntimeError(f"DLL was not visible after injection: {last_error}")


def status() -> dict[str, Any]:
    dll_hash = _sha256(DLL_PATH) if DLL_PATH.exists() else None
    result: dict[str, Any] = {
        "tool_root": str(ROOT_DIR),
        "dll_exists": DLL_PATH.exists(),
        "dll_sha256": dll_hash,
        "dll_matches_v105": dll_hash == EXPECTED_DLL_SHA256,
        "latest_log_camera_enabled": latest_camera_enabled_state(),
    }
    try:
        pid = find_process_id()
        result["pid"] = pid
        module = find_module(pid)
        result["module"] = module
        with ProcessReader(pid) as reader:
            result.update(resolve_core(reader, module["base"]))
    except Exception as exc:  # status should still return useful diagnostics
        result["runtime_error"] = str(exc)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="KCD2 Camera Tools v1.0.5 pose probe and relative controller"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Check game, injected DLL, and recovered pointers")
    sub.add_parser(
        "inject",
        help="Inject the verified camera DLL without opening IGCSClient",
    )

    pose_parser = sub.add_parser("pose", help="Print the current decoded camera pose")
    pose_parser.add_argument("--config", type=Path, default=DEFAULT_POSE_CONFIG)

    set_pose_parser = sub.add_parser(
        "set-pose",
        help="Write a guarded absolute pose value or XYZ delta to the enabled camera",
    )
    set_pose_parser.add_argument("--config", type=Path, default=DEFAULT_POSE_CONFIG)
    for field_name in ("x", "y", "z", "q0", "q1", "q2", "q3"):
        set_pose_parser.add_argument(f"--{field_name}", type=float)
    for field_name in ("pitch", "yaw", "roll", "fov"):
        set_pose_parser.add_argument(f"--{field_name}-deg", type=float)
    set_pose_parser.add_argument("--dx", type=float, default=0.0)
    set_pose_parser.add_argument("--dy", type=float, default=0.0)
    set_pose_parser.add_argument("--dz", type=float, default=0.0)
    set_pose_parser.add_argument("--dry-run", action="store_true")
    set_pose_parser.add_argument(
        "--hold-ms",
        type=int,
        default=0,
        help="Keep writing the target at 120 Hz for this duration",
    )

    export_test_parser = sub.add_parser(
        "export-test",
        help="Call the DLL's own camera movement exports, then restore the session",
    )
    export_test_parser.add_argument(
        "--config", type=Path, default=DEFAULT_POSE_CONFIG
    )
    export_test_parser.add_argument("--right", type=float, default=0.0)
    export_test_parser.add_argument("--up", type=float, default=0.0)
    export_test_parser.add_argument("--fov-deg", type=float, default=0.0)
    export_test_parser.add_argument("--panorama-deg", type=float, default=0.0)
    export_test_parser.add_argument("--hold-seconds", type=float, default=2.0)

    export_goto_parser = sub.add_parser(
        "export-goto",
        help="Move to an absolute XYZ/yaw using the DLL exports and keep the session",
    )
    export_goto_parser.add_argument(
        "--config", type=Path, default=DEFAULT_POSE_CONFIG
    )
    export_goto_parser.add_argument("--x", type=float, required=True)
    export_goto_parser.add_argument("--y", type=float, required=True)
    export_goto_parser.add_argument("--z", type=float, required=True)
    export_goto_parser.add_argument("--yaw-deg", type=float, required=True)
    export_goto_parser.add_argument("--pitch-deg", type=float, default=0.0)
    export_goto_parser.add_argument("--roll-deg", type=float, default=0.0)
    export_goto_parser.add_argument("--fov-deg", type=float, required=True)

    export_end_parser = sub.add_parser(
        "export-end",
        help="End the active export session and restore its starting pose",
    )
    export_end_parser.add_argument(
        "--config", type=Path, default=DEFAULT_POSE_CONFIG
    )

    random_parser = sub.add_parser(
        "random-trajectory",
        help="Generate and execute a smooth seeded random camera trajectory",
    )
    random_parser.add_argument(
        "--config", type=Path, default=DEFAULT_POSE_CONFIG
    )
    random_parser.add_argument("--duration", type=float, default=8.0)
    random_parser.add_argument("--hz", type=float, default=20.0)
    random_parser.add_argument("--seed", type=int)
    random_parser.add_argument(
        "--xy-scale",
        type=float,
        default=1.0,
        help="Scale only the horizontal travel distance",
    )

    capture_parser = sub.add_parser(
        "capture", help="Capture feature/camera memory and pointer targets"
    )
    capture_parser.add_argument("--label", default="manual")

    diff_parser = sub.add_parser("diff", help="Compare two memory captures")
    diff_parser.add_argument("before", type=Path)
    diff_parser.add_argument("after", type=Path)
    diff_parser.add_argument("--output", type=Path)

    control_parser = sub.add_parser(
        "control", help="Send a relative control input to the active OPM camera"
    )
    control_parser.add_argument("action", choices=sorted(VK))
    control_parser.add_argument("--duration-ms", type=int, default=120)

    console_parser = sub.add_parser(
        "console", help="Send one command through the KCD2 developer console"
    )
    console_parser.add_argument("console_command", nargs="+")

    calibrate_parser = sub.add_parser(
        "calibrate",
        help="Move/rotate the enabled free camera and discover likely pose offsets",
    )
    calibrate_parser.add_argument("--duration-ms", type=int, default=250)
    calibrate_parser.add_argument("--force", action="store_true")

    record_parser = sub.add_parser(
        "record", help="Record configured pose fields to CSV"
    )
    record_parser.add_argument("--config", type=Path, default=DEFAULT_POSE_CONFIG)
    record_parser.add_argument("--seconds", type=float, default=10.0)
    record_parser.add_argument("--hz", type=float, default=30.0)
    record_parser.add_argument("--output", type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            print(json.dumps(status(), ensure_ascii=False, indent=2))
        elif args.command == "inject":
            print(json.dumps(inject_camera_dll(), ensure_ascii=False, indent=2))
        elif args.command == "pose":
            print(json.dumps(read_pose(args.config), ensure_ascii=False, indent=2))
        elif args.command == "set-pose":
            requested = {
                name: getattr(args, name) for name in ("x", "y", "z", "q0", "q1", "q2", "q3")
            }
            for name in ("pitch", "yaw", "roll", "fov"):
                value = getattr(args, f"{name}_deg")
                requested[f"{name}_radians"] = (
                    math.radians(value) if value is not None else None
                )
            print(
                json.dumps(
                    set_pose(
                        args.config,
                        requested,
                        {"x": args.dx, "y": args.dy, "z": args.dz},
                        dry_run=args.dry_run,
                        hold_ms=args.hold_ms,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "export-test":
            print(
                json.dumps(
                    exported_camera_test(
                        args.config,
                        step_left_right=args.right,
                        step_up_down=args.up,
                        fov_degrees=args.fov_deg,
                        panorama_degrees=args.panorama_deg,
                        hold_seconds=args.hold_seconds,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "export-goto":
            print(
                json.dumps(
                    exported_camera_goto(
                        args.config,
                        target_x=args.x,
                        target_y=args.y,
                        target_z=args.z,
                        target_yaw_degrees=args.yaw_deg,
                        target_pitch_degrees=args.pitch_deg,
                        target_roll_degrees=args.roll_deg,
                        target_fov_degrees=args.fov_deg,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "export-end":
            print(
                json.dumps(
                    end_export_session(args.config),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "random-trajectory":
            print(
                json.dumps(
                    random_trajectory(
                        args.config,
                        duration=args.duration,
                        hz=args.hz,
                        seed=args.seed,
                        xy_scale=args.xy_scale,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "capture":
            print(save_snapshot(args.label))
        elif args.command == "diff":
            print(save_diff(args.before, args.after, args.output))
        elif args.command == "control":
            perform_action(args.action, args.duration_ms)
            print(f"sent {args.action}")
        elif args.command == "console":
            command = " ".join(args.console_command)
            send_console_command(command)
            print(f"sent console command: {command}")
        elif args.command == "calibrate":
            print(calibrate(args.duration_ms, args.force))
        elif args.command == "record":
            print(
                record_pose(
                    args.config,
                    args.seconds,
                    args.hz,
                    args.output,
                )
            )
        else:
            raise AssertionError(args.command)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
