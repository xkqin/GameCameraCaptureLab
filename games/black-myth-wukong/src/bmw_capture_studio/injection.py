from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

from .paths import BRIDGE_PATH, INJECTOR_PATH
from .game_context import AUTO_DETECT, GAME_NAME, PROCESS_NAMES


BRIDGE_MODULE_NAME = "UeCameraRuntime.dll"
CAMERA_RUNTIME_MODULES = {"UeCameraRuntime.dll", "BmwCameraBridge.dll"}
CONFLICTING_MODULES = {"UniversalUE5Unlocker.dll", "IgcsConnector.addon64"}

TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
TH32CS_SNAPPROCESS = 0x00000002
PROCESS_CREATE_THREAD = 0x0002
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_READ = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04
WAIT_OBJECT_0 = 0x00000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class CameraIntegrationError(RuntimeError):
    pass


def _require_windows() -> None:
    if sys.platform != "win32":
        raise CameraIntegrationError(
            "游戏内 Runtime 注入需要 Windows/Proton；Linux 请配置 Relay。 / "
            "In-game injection requires Windows/Proton; configure the relay on Linux."
        )


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_byte)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * 260),
    ]


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


def _kernel32() -> ctypes.WinDLL:
    _require_windows()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
    kernel32.Module32FirstW.restype = wintypes.BOOL
    kernel32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
    kernel32.Module32NextW.restype = wintypes.BOOL
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.VirtualAllocEx.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    kernel32.VirtualAllocEx.restype = wintypes.LPVOID
    kernel32.WriteProcessMemory.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.LPCVOID,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.WriteProcessMemory.restype = wintypes.BOOL
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetProcAddress.argtypes = [wintypes.HMODULE, wintypes.LPCSTR]
    kernel32.GetProcAddress.restype = wintypes.LPVOID
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
    kernel32.GetExitCodeThread.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeThread.restype = wintypes.BOOL
    kernel32.VirtualFreeEx.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        ctypes.c_size_t,
        wintypes.DWORD,
    ]
    kernel32.VirtualFreeEx.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return kernel32


def list_processes() -> dict[str, list[int]]:
    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot in (None, 0, INVALID_HANDLE_VALUE):
        raise CameraIntegrationError(
            f"无法枚举系统进程 / Process enumeration failed (Windows {ctypes.get_last_error()})"
        )
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    result: dict[str, list[int]] = {}
    try:
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise CameraIntegrationError(
                f"无法读取系统进程 / Process read failed (Windows {ctypes.get_last_error()})"
            )
        while True:
            result.setdefault(entry.szExeFile.lower(), []).append(entry.th32ProcessID)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return result


def find_game_pid() -> int:
    processes = list_processes()
    matches: list[tuple[str, int]] = []
    for name in PROCESS_NAMES:
        values = processes.get(name.lower(), [])
        matches.extend((name, pid) for pid in values)
    unique_pids = {pid for _name, pid in matches}
    if AUTO_DETECT and len(unique_pids) > 1:
        raise CameraIntegrationError(
            "检测到多个受支持游戏，请只保留一个游戏运行或从采集中心明确选择。 / "
            "Multiple supported games detected; keep one running or select an adapter explicitly."
        )
    if matches:
        return matches[0][1]
    raise CameraIntegrationError(
        "没有检测到受支持的游戏进程。 / No supported game process was detected."
    )


def process_executable_path(pid: int) -> Path:
    """Return the actual executable backing a running game process."""
    kernel32 = _kernel32()
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not process:
        raise CameraIntegrationError(
            "无法读取游戏 EXE；请保持工具与游戏权限一致。 / Unable to read the game EXE; use matching privileges."
        )
    try:
        capacity = 32768
        buffer = ctypes.create_unicode_buffer(capacity)
        size = wintypes.DWORD(capacity)
        if not kernel32.QueryFullProcessImageNameW(
            process, 0, buffer, ctypes.byref(size)
        ):
            raise CameraIntegrationError(
                f"无法定位游戏 EXE / EXE lookup failed (Windows {ctypes.get_last_error()})"
            )
        return Path(buffer.value).resolve()
    finally:
        kernel32.CloseHandle(process)


def list_modules(pid: int) -> list[str]:
    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid
    )
    if snapshot in (None, 0, INVALID_HANDLE_VALUE):
        raise CameraIntegrationError(
            f"无法读取游戏模块 / Module read failed (Windows {ctypes.get_last_error()}); "
            "请保持权限一致 / use matching privileges."
        )
    entry = MODULEENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    names: list[str] = []
    try:
        if not kernel32.Module32FirstW(snapshot, ctypes.byref(entry)):
            raise CameraIntegrationError(
                f"无法枚举游戏模块 / Module enumeration failed (Windows {ctypes.get_last_error()})"
            )
        while True:
            names.append(entry.szModule)
            if not kernel32.Module32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return names


def _inject_bridge_with_helper(bridge_path: Path) -> dict[str, object]:
    bridge = bridge_path.resolve()
    injector = INJECTOR_PATH.resolve()
    if not bridge.is_file() or not injector.is_file():
        raise CameraIntegrationError(
            "缺少 Runtime 或 Injector；请先构建。 / Runtime or injector is missing; build native components first."
        )
    configured = os.environ.get("BMW_CAMERA_INJECT_COMMAND", "").strip()
    if configured:
        command = [
            token.format(injector=str(injector), bridge=str(bridge))
            for token in shlex.split(configured)
        ]
    else:
        proton = os.environ.get("BMW_PROTON_COMMAND", "").strip()
        if proton:
            command = [*shlex.split(proton), "run", str(injector)]
        else:
            wine = shutil.which("wine64") or shutil.which("wine")
            if not wine:
                raise CameraIntegrationError(
                    "Linux 注入需要 BMW_PROTON_COMMAND 或 BMW_CAMERA_INJECT_COMMAND。 / "
                    "Linux injection requires a Proton or custom injector command."
                )
            command = [wine, str(injector)]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
    if completed.returncode != 0:
        raise CameraIntegrationError(output or "UeCameraInjector 执行失败 / Injector failed")
    match = re.search(r"\bpid=(\d+)\b", output)
    return {
        "pid": int(match.group(1)) if match else 0,
        "already_loaded": "already_loaded=1" in output,
        "bridge": str(bridge),
        "injector": str(injector),
        "linux_helper": True,
    }


def inject_bridge(
    pid: int | None = None,
    bridge_path: Path = BRIDGE_PATH,
) -> dict[str, object]:
    if sys.platform != "win32":
        return _inject_bridge_with_helper(bridge_path)
    if pid is None or pid <= 0:
        pid = find_game_pid()
    bridge = bridge_path.resolve()
    if not bridge.is_file():
        raise CameraIntegrationError(f"Camera Runtime 不存在 / Runtime not found: {bridge}")
    existing = {name.lower() for name in list_modules(pid)}
    conflicts = sorted(name for name in existing if name in {v.lower() for v in CONFLICTING_MODULES})
    if conflicts:
        raise CameraIntegrationError(
            "已加载第三方或旧相机模块，不能叠加 Hook。 / "
            "Another camera module is loaded; restart the game before injection."
        )
    if any(module.casefold() in existing for module in CAMERA_RUNTIME_MODULES):
        return {"pid": pid, "already_loaded": True, "bridge": str(bridge)}

    kernel32 = _kernel32()
    access = (
        PROCESS_CREATE_THREAD
        | PROCESS_QUERY_INFORMATION
        | PROCESS_VM_OPERATION
        | PROCESS_VM_WRITE
        | PROCESS_VM_READ
    )
    process = kernel32.OpenProcess(access, False, pid)
    if not process:
        raise CameraIntegrationError(
            "无法打开游戏进程；请保持权限一致。 / Unable to open the game process; use matching privileges."
        )
    encoded = (str(bridge) + "\0").encode("utf-16-le")
    remote = None
    thread = None
    try:
        remote = kernel32.VirtualAllocEx(
            process, None, len(encoded), MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE
        )
        if not remote:
            raise CameraIntegrationError("无法分配 Runtime 路径 / Failed to allocate the runtime path")
        written = ctypes.c_size_t()
        payload = ctypes.create_string_buffer(encoded)
        if not kernel32.WriteProcessMemory(
            process,
            remote,
            payload,
            len(encoded),
            ctypes.byref(written),
        ) or written.value != len(encoded):
            raise CameraIntegrationError("无法写入 Runtime 路径 / Failed to write the runtime path")
        kernel_module = kernel32.GetModuleHandleW("kernel32.dll")
        load_library = kernel32.GetProcAddress(kernel_module, b"LoadLibraryW")
        if not load_library:
            raise CameraIntegrationError("无法定位 LoadLibraryW / LoadLibraryW not found")
        thread = kernel32.CreateRemoteThread(
            process, None, 0, load_library, remote, 0, None
        )
        if not thread:
            raise CameraIntegrationError("无法创建 Runtime 加载线程 / Failed to create the runtime loader thread")
        if kernel32.WaitForSingleObject(thread, 15000) != WAIT_OBJECT_0:
            raise CameraIntegrationError("Runtime 加载超时 / Runtime load timed out")
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeThread(thread, ctypes.byref(exit_code)):
            raise CameraIntegrationError("无法读取 Runtime 加载结果 / Failed to read the runtime load result")
        if exit_code.value == 0:
            raise CameraIntegrationError("Runtime 加载失败 / LoadLibraryW returned 0")
    finally:
        if thread:
            kernel32.CloseHandle(thread)
        if remote:
            kernel32.VirtualFreeEx(process, remote, 0, MEM_RELEASE)
        kernel32.CloseHandle(process)

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if bridge.name.lower() in {name.lower() for name in list_modules(pid)}:
            return {"pid": pid, "already_loaded": False, "bridge": str(bridge)}
        time.sleep(0.1)
    raise CameraIntegrationError("Runtime 已注入但验证失败 / Runtime injection verification failed")


def integration_status() -> dict[str, object]:
    if sys.platform != "win32":
        endpoint = os.environ.get("BMW_BRIDGE_ENDPOINT", "").strip()
        if endpoint:
            return {
                "platform_unsupported": False,
                "platform": sys.platform,
                "linux_relay": True,
                "game_running": False,
                "module_scan_ok": False,
                "bridge_loaded": False,
                "conflicting_camera_tool": False,
                "message": f"Linux/Proton Runtime Relay 已配置 / configured ({endpoint})",
            }
        return {
            "platform_unsupported": True,
            "platform": sys.platform,
            "game_running": False,
            "module_scan_ok": False,
            "bridge_loaded": False,
            "conflicting_camera_tool": False,
            "message": "Linux 需要配置 BMW_BRIDGE_ENDPOINT。 / Configure BMW_BRIDGE_ENDPOINT on Linux.",
        }
    try:
        pid = find_game_pid()
    except CameraIntegrationError as exc:
        return {"game_running": False, "message": str(exc)}
    try:
        modules = {name.lower() for name in list_modules(pid)}
    except CameraIntegrationError as exc:
        return {
            "game_running": True,
            "pid": pid,
            "module_scan_ok": False,
            "bridge_loaded": False,
            "conflicting_camera_tool": False,
            "message": str(exc),
        }
    conflicts = sorted(
        name for name in modules if name in {value.lower() for value in CONFLICTING_MODULES}
    )
    return {
        "game_running": True,
        "pid": pid,
        "module_scan_ok": True,
        "bridge_loaded": BRIDGE_MODULE_NAME.lower() in modules,
        "native_artifacts_ready": BRIDGE_PATH.is_file(),
        "bridge_path": str(BRIDGE_PATH),
        "conflicting_camera_tool": bool(conflicts),
        "conflicting_modules": conflicts,
    }
