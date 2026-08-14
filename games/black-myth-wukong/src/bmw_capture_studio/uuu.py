from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

from .paths import BRIDGE_PATH


PROCESS_NAMES = ("b1-Win64-Shipping.exe", "BlackMythWukong.exe")

TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
TH32CS_SNAPPROCESS = 0x00000002
PROCESS_CREATE_THREAD = 0x0002
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04
WAIT_OBJECT_0 = 0x00000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class UuuIntegrationError(RuntimeError):
    pass


def _require_windows() -> None:
    if sys.platform != "win32":
        raise UuuIntegrationError(
            "UUU 5.8.21 注入和 Native Bridge 目前仅支持 Windows；Linux 只能使用兼容界面、文件和 OBS 功能。"
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
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
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
    kernel32.GetExitCodeThread.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
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
    _require_windows()
    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot in (None, 0, INVALID_HANDLE_VALUE):
        error = ctypes.get_last_error()
        raise UuuIntegrationError(f"无法枚举系统进程（Windows 错误 {error}）")
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    result: dict[str, list[int]] = {}
    try:
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            error = ctypes.get_last_error()
            raise UuuIntegrationError(f"无法读取系统进程（Windows 错误 {error}）")
        while True:
            result.setdefault(entry.szExeFile.lower(), []).append(entry.th32ProcessID)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return result


def find_process_pid(process_name: str) -> int | None:
    values = list_processes().get(process_name.lower(), [])
    return values[0] if values else None


def find_game_pid() -> int:
    candidates = list_processes()
    for name in PROCESS_NAMES:
        values = candidates.get(name.lower(), [])
        if values:
            return values[0]
    raise UuuIntegrationError("没有检测到《黑神话：悟空》游戏进程")


def list_modules(pid: int) -> list[str]:
    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid
    )
    if snapshot in (None, 0, INVALID_HANDLE_VALUE):
        error = ctypes.get_last_error()
        raise UuuIntegrationError(
            f"无法读取游戏模块（Windows 错误 {error}）。"
            "如果游戏以管理员身份运行，请也以管理员身份启动本工具。"
        )
    entry = MODULEENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    names: list[str] = []
    try:
        if not kernel32.Module32FirstW(snapshot, ctypes.byref(entry)):
            error = ctypes.get_last_error()
            raise UuuIntegrationError(f"无法枚举游戏模块（Windows 错误 {error}）")
        while True:
            names.append(entry.szModule)
            if not kernel32.Module32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return names


def inject_bridge(pid: int, bridge_path: Path = BRIDGE_PATH) -> dict[str, object]:
    _require_windows()
    bridge = bridge_path.resolve()
    if not bridge.exists():
        raise UuuIntegrationError(f"位姿桥不存在：{bridge}")
    existing = {name.lower() for name in list_modules(pid)}
    if bridge.name.lower() in existing:
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
        raise UuuIntegrationError(
            "无法打开游戏进程。若游戏以管理员身份运行，"
            "请也以管理员身份启动本工具。"
        )

    encoded = (str(bridge) + "\0").encode("utf-16-le")
    remote = None
    thread = None
    try:
        remote = kernel32.VirtualAllocEx(
            process,
            None,
            len(encoded),
            MEM_COMMIT | MEM_RESERVE,
            PAGE_READWRITE,
        )
        if not remote:
            raise UuuIntegrationError("无法在游戏进程中分配位姿桥路径")
        encoded_buffer = ctypes.create_string_buffer(encoded)
        written = ctypes.c_size_t()
        if not kernel32.WriteProcessMemory(
            process,
            remote,
            encoded_buffer,
            len(encoded),
            ctypes.byref(written),
        ) or written.value != len(encoded):
            raise UuuIntegrationError("无法把位姿桥路径写入游戏进程")

        kernel_module = kernel32.GetModuleHandleW("kernel32.dll")
        load_library = kernel32.GetProcAddress(kernel_module, b"LoadLibraryW")
        if not load_library:
            raise UuuIntegrationError("无法定位 LoadLibraryW")
        thread = kernel32.CreateRemoteThread(
            process,
            None,
            0,
            load_library,
            remote,
            0,
            None,
        )
        if not thread:
            raise UuuIntegrationError("无法创建位姿桥加载线程")
        wait_result = kernel32.WaitForSingleObject(thread, 15000)
        if wait_result != WAIT_OBJECT_0:
            raise UuuIntegrationError("等待位姿桥加载超时")
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeThread(thread, ctypes.byref(exit_code)):
            raise UuuIntegrationError("无法读取位姿桥加载结果")
        if exit_code.value == 0:
            raise UuuIntegrationError("位姿桥加载失败，LoadLibraryW 返回 0")
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
    raise UuuIntegrationError("位姿桥已注入，但模块验证失败")


def launch_uuu_client(uuu_dir: str | Path) -> dict[str, object]:
    directory = Path(uuu_dir)
    client = directory / "IGCSClient.exe"
    dll = directory / "UniversalUE5Unlocker.dll"
    if not client.exists() or not dll.exists():
        raise UuuIntegrationError(f"UUU 文件夹不完整：{directory}")
    if sys.platform != "win32":
        wine = shutil.which("wine")
        configured = os.environ.get("BMW_UUU_COMMAND", "").strip()
        if configured:
            launch_command = [*shlex.split(configured), str(client)]
            launcher = "BMW_UUU_COMMAND"
        elif wine:
            launch_command = [wine, str(client)]
            launcher = "wine"
        else:
            raise UuuIntegrationError(
                "Linux/Proton 未找到 wine；请设置 BMW_UUU_COMMAND，或安装 Wine 后再打开 UUU。"
            )
        process = subprocess.Popen(
            launch_command,
            cwd=str(directory),
            start_new_session=True,
        )
        return {
            "pid": process.pid,
            "already_running": False,
            "client": str(client),
            "launcher": launcher,
        }
    existing_pid = find_process_pid("IGCSClient.exe")
    if existing_pid is not None:
        return {"pid": existing_pid, "already_running": True, "client": str(client)}
    process = subprocess.Popen(
        [str(client)],
        cwd=str(directory),
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    return {"pid": process.pid, "already_running": False, "client": str(client)}


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
                "uuu_loaded": False,
                "message": (
                    f"Linux/Proton Bridge Relay 已配置 ({endpoint})；"
                    "等待游戏内 Bridge DLL 启动并监听 Relay。"
                ),
            }
        return {
            "platform_unsupported": True,
            "platform": sys.platform,
            "game_running": False,
            "module_scan_ok": False,
            "bridge_loaded": False,
            "uuu_loaded": False,
            "message": (
                "Linux 兼容模式：界面、点位/轨迹文件和 OBS 可用；"
                "黑神话 UUU 原生位姿控制需要 Windows。"
            ),
        }
    try:
        pid = find_game_pid()
    except UuuIntegrationError as exc:
        return {"game_running": False, "message": str(exc)}
    try:
        modules = {name.lower() for name in list_modules(pid)}
    except UuuIntegrationError as exc:
        return {
            "game_running": True,
            "pid": pid,
            "module_scan_ok": False,
            "bridge_loaded": False,
            "uuu_loaded": False,
            "message": str(exc),
        }
    return {
        "game_running": True,
        "pid": pid,
        "module_scan_ok": True,
        "bridge_loaded": "igcsconnector.addon64" in modules,
        "uuu_loaded": "universalue5unlocker.dll" in modules,
    }
