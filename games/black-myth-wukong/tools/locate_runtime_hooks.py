"""Locate runtime code hooks in the live Black Myth executable.

This clean-room diagnostic compares executable PE sections on disk with the
same sections in the running process.  It reports patched byte ranges and
decodes the common x64 jump stubs used by camera tools.  The report is used to
identify the game's camera update function; the standalone bridge does not
load or call the third-party module.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import struct

from inspect_live_camera import kernel32, modules, read


IMAGE_SCN_MEM_EXECUTE = 0x20000000
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010


def pe_sections(image: bytes) -> tuple[int, list[dict[str, int | str]]]:
    if image[:2] != b"MZ":
        raise ValueError("not a PE image")
    pe_offset = struct.unpack_from("<I", image, 0x3C)[0]
    if image[pe_offset : pe_offset + 4] != b"PE\0\0":
        raise ValueError("invalid PE signature")
    file_header = pe_offset + 4
    section_count = struct.unpack_from("<H", image, file_header + 2)[0]
    optional_size = struct.unpack_from("<H", image, file_header + 16)[0]
    optional_header = file_header + 20
    image_base = struct.unpack_from("<Q", image, optional_header + 24)[0]
    section_table = optional_header + optional_size
    sections: list[dict[str, int | str]] = []
    for index in range(section_count):
        offset = section_table + index * 40
        name = image[offset : offset + 8].split(b"\0", 1)[0].decode("ascii", "replace")
        virtual_size, virtual_address, raw_size, raw_offset = struct.unpack_from(
            "<IIII", image, offset + 8
        )
        characteristics = struct.unpack_from("<I", image, offset + 36)[0]
        sections.append(
            {
                "name": name,
                "virtual_size": virtual_size,
                "rva": virtual_address,
                "raw_size": raw_size,
                "raw_offset": raw_offset,
                "characteristics": characteristics,
            }
        )
    return image_base, sections


def containing_module(address: int, module_index: dict[str, dict[str, object]]) -> dict[str, object]:
    for item in module_index.values():
        base = int(item["base"])
        size = int(item["size"])
        if base <= address < base + size:
            return {
                "target_module": item["name"],
                "target_module_rva": f"0x{address - base:X}",
                "target_module_path": item["path"],
            }
    return {}


def decode_jump(address: int, data: bytes) -> int | None:
    if len(data) >= 5 and data[0] == 0xE9:
        displacement = struct.unpack_from("<i", data, 1)[0]
        return address + 5 + displacement
    if len(data) >= 14 and data[:6] == b"\xFF\x25\x00\x00\x00\x00":
        return struct.unpack_from("<Q", data, 6)[0]
    if len(data) >= 12 and data[:2] == b"\x48\xB8" and data[10:12] == b"\xFF\xE0":
        return struct.unpack_from("<Q", data, 2)[0]
    return None


def changed_runs(disk: bytes, live: bytes, *, merge_gap: int = 0) -> list[tuple[int, int]]:
    changed = [index for index, (left, right) in enumerate(zip(disk, live)) if left != right]
    if not changed:
        return []
    runs: list[tuple[int, int]] = []
    start = previous = changed[0]
    for index in changed[1:]:
        if index <= previous + merge_gap + 1:
            previous = index
            continue
        runs.append((start, previous + 1))
        start = previous = index
    runs.append((start, previous + 1))
    return runs


def locate(pid: int, executable: Path) -> dict[str, object]:
    module_index = modules(pid)
    exe_module = module_index.get(executable.name.lower())
    if not exe_module:
        raise RuntimeError(f"{executable.name} is not loaded in PID {pid}")
    live_base = int(exe_module["base"])
    image = executable.read_bytes()
    preferred_base, sections = pe_sections(image)
    dll = kernel32()
    handle = dll.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), f"OpenProcess({pid}) failed")
    patches: list[dict[str, object]] = []
    try:
        for section in sections:
            if not int(section["characteristics"]) & IMAGE_SCN_MEM_EXECUTE:
                continue
            raw_size = int(section["raw_size"])
            virtual_size = int(section["virtual_size"])
            compare_size = min(raw_size, virtual_size)
            if compare_size <= 0:
                continue
            raw_offset = int(section["raw_offset"])
            rva = int(section["rva"])
            disk = image[raw_offset : raw_offset + compare_size]
            live = read(handle, live_base + rva, compare_size)
            for start, end in changed_runs(disk, live):
                # Include a small suffix so jump stubs whose unchanged zeroes
                # split the exact diff still decode as one instruction.
                context_end = min(compare_size, max(end, start + 32))
                address = live_base + rva + start
                live_context = live[start:context_end]
                disk_context = disk[start:context_end]
                target = decode_jump(address, live_context)
                item: dict[str, object] = {
                    "section": section["name"],
                    "address": f"0x{address:016X}",
                    "rva": f"0x{rva + start:X}",
                    "changed_length": end - start,
                    "disk_hex": disk_context.hex(" "),
                    "live_hex": live_context.hex(" "),
                }
                if target is not None:
                    item["jump_target"] = f"0x{target:016X}"
                    item.update(containing_module(target, module_index))
                patches.append(item)
    finally:
        dll.CloseHandle(handle)
    return {
        "pid": pid,
        "executable": str(executable),
        "preferred_image_base": f"0x{preferred_base:016X}",
        "live_image_base": f"0x{live_base:016X}",
        "patch_count": len(patches),
        "patches": patches,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--exe", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = locate(args.pid, args.exe.resolve())
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
