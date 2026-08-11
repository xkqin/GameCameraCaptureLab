from __future__ import annotations

import argparse
import json
from pathlib import Path


def smootherstep7(value: float) -> float:
    u = min(1.0, max(0.0, value))
    return u**4 * (35.0 - 84.0 * u + 70.0 * u**2 - 20.0 * u**3)


def remap(time_sec: float, start: float, end: float) -> float:
    return smootherstep7((time_sec - start) / (end - start))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--duration", type=float, default=13.2)
    parser.add_argument("--fps", type=float, default=60.0)
    args = parser.parse_args()

    payload = json.loads(args.source.read_text(encoding="utf-8"))
    source_trajectory = payload["trajectories"][0]
    source_frames = source_trajectory.get("keyframes") or source_trajectory["frames"]
    first = source_frames[0]
    last = source_frames[-1]
    duration = float(args.duration)
    fps = float(args.fps)
    frame_count = round(duration * fps) + 1
    trajectory_id = "kcd2_scene1_public_demo_silky_fast_v2_60fps"

    frames = []
    for step in range(frame_count):
        time_sec = step / fps
        # Overlap the descent, lateral move, and tilt. Seventh-order
        # smootherstep gives zero velocity, acceleration, and jerk at every
        # transition boundary, avoiding the full stops in the source path.
        z_mix = remap(time_sec, 0.0, duration * 0.34)
        x_mix = remap(time_sec, duration * 0.18, duration * 0.86)
        pitch_mix = remap(time_sec, duration * 0.035, duration * 0.965)
        frames.append(
            {
                "step": step,
                "time_sec": round(time_sec, 6),
                "x": first["x"] + (last["x"] - first["x"]) * x_mix,
                "y": first["y"] + (last["y"] - first["y"]) * x_mix,
                "z": first["z"] + (last["z"] - first["z"]) * z_mix,
                "yaw": first.get("yaw", 0.0)
                + (last.get("yaw", 0.0) - first.get("yaw", 0.0)) * pitch_mix,
                "pitch": first.get("pitch", 0.0)
                + (last.get("pitch", 0.0) - first.get("pitch", 0.0))
                * pitch_mix,
                "roll": first.get("roll", 0.0)
                + (last.get("roll", 0.0) - first.get("roll", 0.0)) * pitch_mix,
                "fov": first.get("fov", 63.0)
                + (last.get("fov", 63.0) - first.get("fov", 63.0))
                * pitch_mix,
            }
        )

    output = {
        "version": 1,
        "scene_id": payload.get("scene_id", "scene_1"),
        "trajectory_count": 1,
        "trajectories": [
            {
                "trajectory_id": trajectory_id,
                "keyframe_count": frame_count,
                "duration_sec": duration,
                "fps": fps,
                "generation": {
                    "source": str(args.source.resolve()),
                    "profile": "overlapped_seventh_order_smootherstep",
                    "source_duration_sec": source_trajectory.get("duration_sec"),
                    "speedup_ratio": source_trajectory.get("duration_sec", duration)
                    / duration,
                    "note": (
                        "Position and tilt transitions overlap. Each transition "
                        "has zero endpoint velocity, acceleration, and jerk."
                    ),
                },
                "keyframes": frames,
            }
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(args.output.resolve())
    print(f"frames={frame_count} duration={duration:.3f}s fps={fps:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
