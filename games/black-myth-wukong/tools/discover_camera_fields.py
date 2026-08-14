"""Read-only Black Myth camera-field locator.

This utility is part of the clean-room standalone camera bridge work.  It does
not inject a DLL and it never writes to the target process.  It searches
readable committed pages for the current pose's contiguous XYZ float triplet,
then reports candidate addresses and the owning module/region.  A later live
calibration pass can compare two poses to identify the game's own camera
storage; this first pass deliberately remains read-only.

The current UUU bridge is used only as a pose source when the user has already
started it.  The resulting report is diagnostic data, not a dependency of the
standalone bridge.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import struct
import sys
from typing import Iterator


PAGE_READONLY = 0x02
PAGE_READWRITE = 0x04
PAGE_WRITECOPY = 0x08
PAGE_EXECUTE_READ = 0x20
PAGE_EXECUTE_READWRITE = 0x40
PAGE_EXECUTE_WRITECOPY = 0x80
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01
MEM_COMMIT = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", wintypes.LPVOID),
        ("AllocationBase", wintypes.LPVOID),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


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


READABLE_PROTECTS = {
    PAGE_READONLY,
    PAGE_READWRITE,
    PAGE_WRITECOPY,
    PAGE_EXECUTE_READ,
    PAGE_EXECUTE_READWRITE,
    PAGE_EXECUTE_WRITECOPY,
}


def _kernel32() -> ctypes.WinDLL:
    if sys.platform != "win32":
        raise RuntimeError("camera field discovery currently requires Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.VirtualQueryEx.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        ctypes.POINTER(MEMORY_BASIC_INFORMATION),
        ctypes.c_size_t,
    ]
    kernel32.VirtualQueryEx.restype = ctypes.c_size_t
    kernel32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.LPVOID,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    kernel32.ReadProcessMemory.restype = wintypes.BOOL
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
    return kernel32


def module_ranges(pid: int) -> list[dict[str, object]]:
    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000008 | 0x00000010, pid)
    invalid = ctypes.c_void_p(-1).value
    if snapshot in (None, 0, invalid):
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
    entry = MODULEENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    result: list[dict[str, object]] = []
    try:
        if not kernel32.Module32FirstW(snapshot, ctypes.byref(entry)):
            return result
        while True:
            base = ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value or 0
            result.append(
                {
                    "name": entry.szModule,
                    "path": entry.szExePath,
                    "base": base,
                    "size": int(entry.modBaseSize),
                }
            )
            if not kernel32.Module32NextW(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return result


def readable_regions(
    handle: wintypes.HANDLE,
    *,
    max_bytes: int,
) -> Iterator[tuple[int, int, int]]:
    kernel32 = _kernel32()
    address = 0
    scanned = 0
    mbi = MEMORY_BASIC_INFORMATION()
    pointer_limit = 1 << 47
    while address < pointer_limit and scanned < max_bytes:
        result = kernel32.VirtualQueryEx(
            handle,
            ctypes.c_void_p(address),
            ctypes.byref(mbi),
            ctypes.sizeof(mbi),
        )
        if result == 0:
            break
        base = ctypes.cast(mbi.BaseAddress, ctypes.c_void_p).value or 0
        size = int(mbi.RegionSize)
        if (
            mbi.State == MEM_COMMIT
            and not (mbi.Protect & PAGE_GUARD)
            and not (mbi.Protect & PAGE_NOACCESS)
            and (mbi.Protect & 0xFF) in READABLE_PROTECTS
        ):
            remaining = max_bytes - scanned
            usable = min(size, remaining)
            if usable > 0:
                yield base, usable, int(mbi.Protect)
                scanned += usable
        address = max(base + size, address + 0x1000)


def read_chunks(
    handle: wintypes.HANDLE,
    base: int,
    size: int,
    *,
    chunk_size: int = 4 * 1024 * 1024,
) -> Iterator[tuple[int, bytes]]:
    kernel32 = _kernel32()
    for offset in range(0, size, chunk_size):
        length = min(chunk_size, size - offset)
        buffer = ctypes.create_string_buffer(length)
        read = ctypes.c_size_t()
        ok = kernel32.ReadProcessMemory(
            handle,
            ctypes.c_void_p(base + offset),
            buffer,
            length,
            ctypes.byref(read),
        )
        if ok and read.value:
            yield base + offset, buffer.raw[: read.value]


def find_all(data: bytes, needle: bytes) -> Iterator[int]:
    start = 0
    while True:
        index = data.find(needle, start)
        if index < 0:
            return
        yield index
        start = index + 1


def owner_for(address: int, modules: list[dict[str, object]]) -> dict[str, object] | None:
    for module in modules:
        base = int(module["base"])
        size = int(module["size"])
        if base <= address < base + size:
            return {
                "module": module["name"],
                "module_rva": address - base,
                "module_path": module["path"],
            }
    return None


def discover(pid: int, pose: tuple[float, float, float], max_gb: float) -> dict[str, object]:
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
        False,
        pid,
    )
    if not handle:
        raise OSError(ctypes.get_last_error(), f"OpenProcess({pid}) failed")
    modules = module_ranges(pid)
    needle = struct.pack("<3f", *pose)
    hits: list[dict[str, object]] = []
    scanned = 0
    try:
        for base, size, protect in readable_regions(
            handle,
            max_bytes=max(1, int(max_gb * 1024**3)),
        ):
            scanned += size
            for chunk_base, data in read_chunks(handle, base, size):
                for offset in find_all(data, needle):
                    address = chunk_base + offset
                    hit: dict[str, object] = {
                        "address": f"0x{address:016X}",
                        "aligned": address % 4 == 0,
                        "protect": f"0x{protect:02X}",
                    }
                    hit.update(owner_for(address, modules) or {})
                    hits.append(hit)
                    if len(hits) >= 5000:
                        return {
                            "pid": pid,
                            "pose_xyz": pose,
                            "needle_hex": needle.hex(" "),
                            "scanned_bytes": scanned,
                            "modules": modules,
                            "hits_truncated": True,
                            "hits": hits,
                        }
    finally:
        kernel32.CloseHandle(handle)
    return {
        "pid": pid,
        "pose_xyz": pose,
        "needle_hex": needle.hex(" "),
        "scanned_bytes": scanned,
        "modules": modules,
        "hits_truncated": False,
        "hits": hits,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--x", type=float, required=True)
    parser.add_argument("--y", type=float, required=True)
    parser.add_argument("--z", type=float, required=True)
    parser.add_argument("--max-gb", type=float, default=4.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = discover(args.pid, (args.x, args.y, args.z), args.max_gb)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
