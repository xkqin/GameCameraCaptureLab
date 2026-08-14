"""Read-only inspection helper for the current Black Myth camera session.

This is a calibration/diagnostic tool for the clean-room BMW Camera Bridge.
It reads the live process and prints pointer-shaped fields around the known
UUU camera wrapper.  It never writes memory, injects code, or changes camera
state.  The UUU addresses here are only used to locate a reference object
during one-time research; the standalone bridge will not load UUU.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import math
import struct
import sys
from typing import Any


PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

UUU_CONTROLLER_GLOBAL_RVA = 0x2FF958
UUU_CAMERA_FEATURE_OFFSET = 0x1C48
UUU_CAMERA_VTABLE_RVA = 0x2A8C20
UUU_IMAGE_SIZE = 0x32E000


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


def kernel32() -> ctypes.WinDLL:
    if sys.platform != "win32":
        raise RuntimeError("live camera inspection requires Windows")
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    dll.OpenProcess.restype = wintypes.HANDLE
    dll.CloseHandle.argtypes = [wintypes.HANDLE]
    dll.CloseHandle.restype = wintypes.BOOL
    dll.ReadProcessMemory.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.LPVOID,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    dll.ReadProcessMemory.restype = wintypes.BOOL
    dll.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    dll.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    dll.Module32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(MODULEENTRY32W),
    ]
    dll.Module32FirstW.restype = wintypes.BOOL
    dll.Module32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(MODULEENTRY32W),
    ]
    dll.Module32NextW.restype = wintypes.BOOL
    return dll


def modules(pid: int) -> dict[str, dict[str, Any]]:
    dll = kernel32()
    snapshot = dll.CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32,
        pid,
    )
    if snapshot in (None, 0, INVALID_HANDLE_VALUE):
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
    entry = MODULEENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    result: dict[str, dict[str, Any]] = {}
    try:
        if not dll.Module32FirstW(snapshot, ctypes.byref(entry)):
            return result
        while True:
            base = ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value or 0
            result[entry.szModule.lower()] = {
                "name": entry.szModule,
                "path": entry.szExePath,
                "base": base,
                "size": int(entry.modBaseSize),
            }
            if not dll.Module32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        dll.CloseHandle(snapshot)
    return result


def read(handle: wintypes.HANDLE, address: int, size: int) -> bytes:
    dll = kernel32()
    buffer = ctypes.create_string_buffer(size)
    count = ctypes.c_size_t()
    if not dll.ReadProcessMemory(
        handle,
        ctypes.c_void_p(address),
        buffer,
        size,
        ctypes.byref(count),
    ) or count.value != size:
        raise OSError(ctypes.get_last_error(), f"ReadProcessMemory(0x{address:X}) failed")
    return buffer.raw


def pointer(handle: wintypes.HANDLE, address: int) -> int:
    return struct.unpack("<Q", read(handle, address, 8))[0]


def describe_pointer(
    address: int,
    *,
    module_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {"address": f"0x{address:016X}"}
    for module in module_index.values():
        base = int(module["base"])
        size = int(module["size"])
        if base <= address < base + size:
            result.update(
                {
                    "module": module["name"],
                    "module_rva": f"0x{address - base:X}",
                    "path": module["path"],
                }
            )
            break
    return result


def float_summary(raw: bytes) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for offset in range(0, len(raw) - 3, 4):
        value = struct.unpack_from("<f", raw, offset)[0]
        if math.isfinite(value) and abs(value) < 1.0e8:
            result.append({"offset": f"+0x{offset:X}", "value": value})
    return result


def inspect(pid: int, *, field_bytes: int) -> dict[str, Any]:
    dll = kernel32()
    handle = dll.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), f"OpenProcess({pid}) failed")
    module_index = modules(pid)
    try:
        uuu = module_index.get("universalue5unlocker.dll")
        if not uuu:
            raise RuntimeError("UniversalUE5Unlocker.dll is not loaded in the target process")
        base = int(uuu["base"])
        controller_global = base + UUU_CONTROLLER_GLOBAL_RVA
        controller = pointer(handle, controller_global)
        feature_pointer_address = controller + UUU_CAMERA_FEATURE_OFFSET
        feature = pointer(handle, feature_pointer_address) if controller else 0
        report: dict[str, Any] = {
            "pid": pid,
            "uuu": {
                **uuu,
                "controller_global": f"0x{controller_global:016X}",
                "controller": describe_pointer(controller, module_index=module_index),
                "camera_feature_pointer_address": f"0x{feature_pointer_address:016X}",
                "camera_feature": describe_pointer(feature, module_index=module_index),
            },
            "feature_fields": [],
            "vtable": [],
        }
        if not feature:
            return report
        feature_raw = read(handle, feature, field_bytes)
        if len(feature_raw) >= 0x44:
            report["reference_camera_state"] = {
                "coordinate_storage": "ue5_lwc_three_contiguous_float64",
                "x": struct.unpack_from("<d", feature_raw, 0x18)[0],
                "y": struct.unpack_from("<d", feature_raw, 0x20)[0],
                "z": struct.unpack_from("<d", feature_raw, 0x28)[0],
                "quaternion_xyzw": list(struct.unpack_from("<4f", feature_raw, 0x30)),
                "fov_degrees": struct.unpack_from("<f", feature_raw, 0x40)[0],
            }
        fields: list[dict[str, Any]] = []
        for offset in range(0, field_bytes - 7, 8):
            value = struct.unpack_from("<Q", feature_raw, offset)[0]
            if value == 0:
                continue
            item = {"offset": f"+0x{offset:X}"}
            item.update(describe_pointer(value, module_index=module_index))
            fields.append(item)
        report["feature_fields"] = fields
        vtable = pointer(handle, feature)
        report["vtable_address"] = describe_pointer(vtable, module_index=module_index)
        for index in range(0, 16):
            method = pointer(handle, vtable + index * 8)
            report["vtable"].append(
                {
                    "index": index,
                    "entry": describe_pointer(method, module_index=module_index),
                    "inside_uuu": base <= method < base + UUU_IMAGE_SIZE,
                }
            )
        report["feature_bytes_floats"] = float_summary(feature_raw)
        return report
    finally:
        dll.CloseHandle(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--field-bytes", type=int, default=0x180)
    parser.add_argument("--output", type=str)
    args = parser.parse_args()
    report = inspect(args.pid, field_bytes=max(0x40, min(args.field_bytes, 0x1000)))
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        path = open(args.output, "w", encoding="utf-8")
        try:
            path.write(encoded + "\n")
        finally:
            path.close()
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
