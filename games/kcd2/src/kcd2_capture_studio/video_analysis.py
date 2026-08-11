from __future__ import annotations

from bisect import bisect_left
import csv
import json
from pathlib import Path
from threading import Event
from typing import Any, Callable


def extract_video_frames(
    video_path: str | Path,
    output_dir: str | Path,
    *,
    target_fps: float = 2.0,
    jpeg_quality: int = 95,
    overwrite: bool = False,
    stop_event: Event | None = None,
    progress_callback: Callable[[int, int, Path], None] | None = None,
) -> dict[str, Any]:
    if target_fps <= 0:
        raise ValueError("target_fps must be positive")
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError(
            "OpenCV is unavailable. Launch with the project/RE9 environment."
        ) from exc

    video = Path(video_path).resolve()
    if not video.exists():
        raise FileNotFoundError(video)
    frames_dir = Path(output_dir).resolve() / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    metadata_csv = frames_dir / "frame_metadata.csv"
    if metadata_csv.exists() and not overwrite:
        with metadata_csv.open("r", newline="", encoding="utf-8-sig") as handle:
            count = sum(1 for _ in csv.DictReader(handle))
        return {
            "video_path": str(video),
            "frames_dir": str(frames_dir),
            "metadata_csv": str(metadata_csv),
            "frame_count": count,
            "reused": True,
        }

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {video}")
    source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
    source_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if source_fps <= 0:
        capture.release()
        raise RuntimeError(f"Video reports invalid FPS: {video}")
    duration = source_count / source_fps if source_count else 0.0
    expected = max(1, int(duration * target_fps) + 1)
    step = 1.0 / target_fps
    rows: list[dict[str, Any]] = []
    timestamp = 0.0
    index = 0
    try:
        while not duration or timestamp <= duration + 1.0e-6:
            if stop_event is not None and stop_event.is_set():
                break
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000.0)
            ok, frame = capture.read()
            if not ok:
                break
            file_name = f"frame_{index:06d}_t{timestamp:09.3f}.jpg"
            frame_path = frames_dir / file_name
            success = cv2.imwrite(
                str(frame_path),
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)],
            )
            if not success:
                raise RuntimeError(f"Could not write frame: {frame_path}")
            rows.append(
                {
                    "video_path": str(video),
                    "frame_path": str(frame_path),
                    "file_name": file_name,
                    "frame_index": index,
                    "timestamp_sec": f"{timestamp:.6f}",
                    "width": width,
                    "height": height,
                }
            )
            index += 1
            timestamp += step
            if progress_callback is not None:
                progress_callback(index, expected, frame_path)
    finally:
        capture.release()

    _write_rows(
        metadata_csv,
        rows,
        [
            "video_path",
            "frame_path",
            "file_name",
            "frame_index",
            "timestamp_sec",
            "width",
            "height",
        ],
    )
    return {
        "video_path": str(video),
        "source_fps": source_fps,
        "source_frame_count": source_count,
        "duration_sec": duration,
        "frames_dir": str(frames_dir),
        "metadata_csv": str(metadata_csv),
        "frame_count": len(rows),
        "stopped": bool(stop_event and stop_event.is_set()),
        "reused": False,
    }


def align_frames_with_pose(
    frame_or_score_csv: str | Path,
    pose_csv: str | Path,
    output_csv: str | Path,
    *,
    recording_manifest: str | Path | None = None,
    max_time_diff_sec: float = 0.25,
) -> dict[str, Any]:
    frames = _read_rows(frame_or_score_csv)
    poses = _read_rows(pose_csv)
    if not frames or "timestamp_sec" not in frames[0]:
        raise ValueError("Frame/score CSV must contain timestamp_sec")
    if not poses or "timestamp_sec" not in poses[0]:
        raise ValueError("Pose CSV must contain timestamp_sec")
    offset = 0.0
    if recording_manifest:
        manifest = json.loads(Path(recording_manifest).read_text(encoding="utf-8"))
        offset = float(manifest.get("pose_time_at_obs_start_sec") or 0.0)

    poses.sort(key=lambda row: float(row["timestamp_sec"]))
    pose_times = [float(row["timestamp_sec"]) for row in poses]
    output: list[dict[str, Any]] = []
    valid_count = 0
    for frame in sorted(frames, key=lambda row: float(row["timestamp_sec"])):
        frame_time = float(frame["timestamp_sec"])
        target_pose_time = frame_time + offset
        right = bisect_left(pose_times, target_pose_time)
        candidates = [
            index
            for index in (right - 1, right)
            if 0 <= index < len(pose_times)
        ]
        nearest = min(
            candidates,
            key=lambda index: abs(pose_times[index] - target_pose_time),
        )
        pose = poses[nearest]
        difference = abs(pose_times[nearest] - target_pose_time)
        valid = difference <= max_time_diff_sec
        if valid:
            valid_count += 1
        row = dict(frame)
        row.update(
            {
                "pose_timestamp_sec": pose["timestamp_sec"] if valid else "",
                "pose_time_at_obs_start_sec": offset,
                "alignment_time_diff_sec": f"{difference:.9f}",
                "alignment_valid": valid,
            }
        )
        for name in (
            "x",
            "y",
            "z",
            "q0",
            "q1",
            "q2",
            "q3",
            "yaw_degrees",
            "pitch_degrees",
            "roll_degrees",
            "fov_degrees",
        ):
            row[name] = pose.get(name, "") if valid else ""
        output.append(row)

    fields = list(output[0]) if output else []
    _write_rows(Path(output_csv), output, fields)
    return {
        "output_csv": str(Path(output_csv).resolve()),
        "frame_count": len(output),
        "aligned_count": valid_count,
        "invalid_count": len(output) - valid_count,
        "pose_time_at_obs_start_sec": offset,
        "max_time_diff_sec": max_time_diff_sec,
    }


def _read_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _write_rows(
    path: Path,
    rows: list[dict[str, Any]],
    fields: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
