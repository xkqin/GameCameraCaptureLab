"""One-time clean-room pose-change calibration for Black Myth.

The script compares two live pose snapshots and intersects exact XYZ matches in
the target process.  The reference movement is issued through the already
running bridge only for calibration; this utility never writes arbitrary
process memory.  A candidate is useful only after a later controlled write
test and render-hook test confirm that it is the game's camera, not a stale
copy or the bridge's shared-memory snapshot.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import struct
import sys
import time

from discover_camera_fields import (
    MEM_COMMIT,
    PAGE_GUARD,
    PAGE_NOACCESS,
    READABLE_PROTECTS,
    MEMORY_BASIC_INFORMATION,
    _kernel32,
    module_ranges,
    read_chunks,
    find_all,
)
from inspect_live_camera import (
    UUU_CAMERA_FEATURE_OFFSET,
    UUU_CONTROLLER_GLOBAL_RVA,
    modules,
    pointer,
    read,
)


PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010


def scan_xyz(
    handle: wintypes.HANDLE,
    pose: tuple[float, float, float],
    max_gb: float,
) -> set[int]:
    kernel32 = _kernel32()
    # Black Myth uses UE5 Large World Coordinates.  The authoritative camera
    # position is three contiguous doubles; scanning the rounded Connector
    # floats creates hundreds of unrelated matches in transform buffers.
    needle = struct.pack("<3d", *pose)
    hits: set[int] = set()
    address = 0
    scanned = 0
    max_bytes = max(1, int(max_gb * 1024**3))
    mbi = MEMORY_BASIC_INFORMATION()
    while address < (1 << 47) and scanned < max_bytes:
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
            usable = min(size, max_bytes - scanned)
            if usable > 0:
                for chunk_base, data in read_chunks(handle, base, usable):
                    hits.update(chunk_base + offset for offset in find_all(data, needle))
                scanned += usable
        address = max(base + size, address + 0x1000)
    return hits


def read_reference_pose(
    handle: wintypes.HANDLE,
    pid: int,
) -> tuple[tuple[float, float, float], int]:
    """Read UUU's exact LWC position during one-time clean-room calibration."""

    module_index = modules(pid)
    uuu = module_index.get("universalue5unlocker.dll")
    if not uuu:
        raise RuntimeError("UUU must be loaded only for this one-time calibration pass")
    uuu_base = int(uuu["base"])
    controller = pointer(handle, uuu_base + UUU_CONTROLLER_GLOBAL_RVA)
    if not controller:
        raise RuntimeError("UUU controller is not ready")
    feature = pointer(handle, controller + UUU_CAMERA_FEATURE_OFFSET)
    if not feature:
        raise RuntimeError("UUU camera feature is not ready; enable its camera first")
    pose = struct.unpack("<3d", read(handle, feature + 0x18, 24))
    return (float(pose[0]), float(pose[1]), float(pose[2])), feature


def calibrate(pid: int, *, movement: float, settle_seconds: float, max_gb: float) -> dict[str, object]:
    kernel32 = _kernel32()
    handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        raise OSError(ctypes.get_last_error(), f"OpenProcess({pid}) failed")
    bridge = None
    before: tuple[float, float, float] | None = None
    original_pose = None
    try:
        before, feature_before = read_reference_pose(handle, pid)
        before_hits = scan_xyz(handle, before, max_gb)
        from bmw_capture_studio.bridge import create_pose_bridge

        bridge = create_pose_bridge()
        original_pose = bridge.read_pose()
        bridge.apply_native_step(move_forward=movement)
        time.sleep(max(0.05, settle_seconds))
        after, feature_after = read_reference_pose(handle, pid)
        after_hits = scan_xyz(handle, after, max_gb)
        candidates = sorted(before_hits & after_hits)
        return {
            "pid": pid,
            "coordinate_storage": "ue5_lwc_three_contiguous_float64",
            "before_xyz": before,
            "after_xyz": after,
            "delta_xyz": tuple(after[index] - before[index] for index in range(3)),
            "before_hit_count": len(before_hits),
            "after_hit_count": len(after_hits),
            "changed_xyz_candidates": [f"0x{address:016X}" for address in candidates],
            "reference_camera_feature_before": f"0x{feature_before:016X}",
            "reference_camera_feature_after": f"0x{feature_after:016X}",
            "movement_command": movement,
            "warning": "Candidates require an independent write/render validation before being used by the standalone bridge.",
            "modules": module_ranges(pid),
        }
    finally:
        # Restore the complete saved pose through feedback instead of issuing
        # one inverse pulse. UUU smooths relative commands, so inverse pulses
        # do not cancel exactly and used to leave calibration sessions offset.
        if bridge is not None and original_pose is not None:
            try:
                from bmw_capture_studio.input_control import ClosedLoopMover

                mover = ClosedLoopMover(
                    bridge,
                    position_tolerance=0.1,
                    angle_tolerance=0.05,
                    fov_tolerance=0.05,
                    move_pulse_sec=0.02,
                    rotate_pulse_sec=0.02,
                    max_seconds=60.0,
                    focus_game=lambda: None,
                    prefer_native=True,
                    allow_hotkey_fallback=False,
                    feedback_timeout_sec=max(0.5, settle_seconds * 2.0),
                )
                mover.move_to(original_pose)
            except Exception:
                pass
        kernel32.CloseHandle(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--movement", type=float, default=50.0)
    parser.add_argument("--settle-seconds", type=float, default=0.35)
    parser.add_argument("--max-gb", type=float, default=2.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = calibrate(
        args.pid,
        movement=args.movement,
        settle_seconds=args.settle_seconds,
        max_gb=args.max_gb,
    )
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
