"""Rank live UE5-LWC XYZ candidates by nearby camera-like data (read-only)."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import math
import struct
from pathlib import Path

from discover_camera_fields import (
    MEMORY_BASIC_INFORMATION,
    PROCESS_QUERY_INFORMATION,
    PROCESS_VM_READ,
    _kernel32,
)
from calibrate_camera_memory import read_reference_pose


def read(handle: wintypes.HANDLE, address: int, size: int) -> bytes:
    kernel32 = _kernel32()
    buffer = ctypes.create_string_buffer(size)
    count = ctypes.c_size_t()
    if not kernel32.ReadProcessMemory(
        handle,
        ctypes.c_void_p(address),
        buffer,
        size,
        ctypes.byref(count),
    ) or count.value != size:
        raise OSError(ctypes.get_last_error(), f"ReadProcessMemory(0x{address:X}) failed")
    return buffer.raw


def region_info(handle: wintypes.HANDLE, address: int) -> dict[str, object]:
    kernel32 = _kernel32()
    mbi = MEMORY_BASIC_INFORMATION()
    result = kernel32.VirtualQueryEx(
        handle,
        ctypes.c_void_p(address),
        ctypes.byref(mbi),
        ctypes.sizeof(mbi),
    )
    if not result:
        return {}
    return {
        "region_base": f"0x{(ctypes.cast(mbi.BaseAddress, ctypes.c_void_p).value or 0):016X}",
        "region_size": int(mbi.RegionSize),
        "protect": f"0x{int(mbi.Protect):X}",
        "type": f"0x{int(mbi.Type):X}",
    }


def finite(value: float) -> bool:
    return math.isfinite(value) and abs(value) < 1.0e8


def vector_norm(values: tuple[float, float, float]) -> float:
    return math.sqrt(sum(value * value for value in values))


def inspect_candidate(
    handle: wintypes.HANDLE,
    address: int,
    current_pose: tuple[float, float, float],
) -> dict[str, object]:
    # A little context is enough to recognize FTransform/FMinimalViewInfo-like
    # layouts without assuming the game's final structure. Black Myth stores
    # FVector as three doubles because Large World Coordinates are enabled.
    base = max(0, address - 0x60)
    try:
        raw = read(handle, base, 0x260)
    except OSError:
        return {"address": f"0x{address:016X}", "score": -1}
    target_offset = address - base
    observed_xyz = struct.unpack_from("<3d", raw, target_offset)
    xyz_error = math.sqrt(
        sum((observed_xyz[index] - current_pose[index]) ** 2 for index in range(3))
    )
    live = xyz_error <= 1.0e-6
    floats: list[float] = []
    for offset in range(0, len(raw) - 3, 4):
        value = struct.unpack_from("<f", raw, offset)[0]
        floats.append(value if finite(value) else float("nan"))
    score = 100 if live else 0
    fov_hits: list[str] = []
    unit_vectors: list[str] = []
    for index, value in enumerate(floats):
        if finite(value) and 1.0 <= value <= 179.0 and abs(value - 65.0) < 0.01:
            score += 1
            fov_hits.append(f"+0x{index * 4 - 0x60:X}")
        if index + 2 < len(floats):
            vector = (floats[index], floats[index + 1], floats[index + 2])
            if all(finite(item) for item in vector):
                norm = vector_norm(vector)
                if 0.97 <= norm <= 1.03:
                    score += 1
                    unit_vectors.append(f"+0x{index * 4 - 0x60:X}")
    # Stronger signal: LWC XYZ followed by UUU-like quaternion+FOV or by a
    # plausible UE FRotator made of three doubles.
    local_index = target_offset // 4
    quaternion: tuple[float, ...] = ()
    if 0 <= local_index + 9 < len(floats):
        quaternion = tuple(floats[local_index + 6 : local_index + 10])
        qnorm = math.sqrt(sum(value * value for value in quaternion))
        if all(finite(value) for value in quaternion) and 0.97 <= qnorm <= 1.03:
            score += 24
    immediate_fov = struct.unpack_from("<f", raw, target_offset + 40)[0]
    if math.isfinite(immediate_fov) and abs(immediate_fov - 65.0) <= 0.01:
        score += 12
    rotation_doubles = struct.unpack_from("<3d", raw, target_offset + 24)
    if all(math.isfinite(value) and abs(value) <= 720.0 for value in rotation_doubles):
        if any(abs(value) >= 0.01 for value in rotation_doubles):
            score += 14
    neighbors: list[dict[str, object]] = []
    for delta in range(-0x10, 0x80, 4):
        index = local_index + delta // 4
        if 0 <= index < len(floats) and finite(floats[index]):
            neighbors.append({"offset": f"{delta:+#x}", "value": floats[index]})
    result: dict[str, object] = {
        "address": f"0x{address:016X}",
        "score": score,
        "live_exact_pose": live,
        "xyz_error": xyz_error,
        "observed_xyz": list(observed_xyz),
        "quaternion_after_xyz": list(quaternion),
        "immediate_fov": immediate_fov,
        "rotation_doubles_after_xyz": list(rotation_doubles),
        "fov65_offsets": fov_hits[:20],
        "unit_vector_offsets": unit_vectors[:30],
        "neighbors": neighbors,
    }
    result.update(region_info(handle, address))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    calibration = json.loads(args.calibration.read_text(encoding="utf-8"))
    addresses = [int(value, 16) for value in calibration.get("changed_xyz_candidates", [])]
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, args.pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), f"OpenProcess({args.pid}) failed")
    try:
        current_pose, reference_feature = read_reference_pose(handle, args.pid)
        ranked = [
            inspect_candidate(handle, address, current_pose)
            for address in addresses[: args.limit]
        ]
    finally:
        kernel32.CloseHandle(handle)
    ranked.sort(key=lambda item: int(item.get("score", -1)), reverse=True)
    result = {
        "pid": args.pid,
        "source": str(args.calibration),
        "candidate_count": len(addresses),
        "current_reference_pose": list(current_pose),
        "reference_camera_feature": f"0x{reference_feature:016X}",
        "ranked": ranked,
    }
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
