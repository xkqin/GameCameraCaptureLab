from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class VideoStutterReport:
    video_path: Path
    width: int
    height: int
    fps: float
    frame_count: int
    duration_sec: float
    repeat_like_ratio: float
    freeze_event_count: int
    longest_freeze_sec: float
    motion_cv: float
    motion_p95_over_p50: float
    motion_p99_over_p50: float
    cadence_jitter: float
    stutter_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_path": str(self.video_path),
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "frame_count": self.frame_count,
            "duration_sec": self.duration_sec,
            "repeat_like_ratio": self.repeat_like_ratio,
            "freeze_event_count": self.freeze_event_count,
            "longest_freeze_sec": self.longest_freeze_sec,
            "motion_cv": self.motion_cv,
            "motion_p95_over_p50": self.motion_p95_over_p50,
            "motion_p99_over_p50": self.motion_p99_over_p50,
            "cadence_jitter": self.cadence_jitter,
            "stutter_score": self.stutter_score,
        }


def analyze_video_stutter(
    video_path: str | Path,
    output_json: str | Path | None = None,
    max_width: int = 480,
    duplicate_threshold: float = 0.25,
) -> VideoStutterReport:
    """Estimate dropped-frame/stutter artifacts from encoded video frames.

    The metrics are visual estimates. They catch repeated frames and uneven frame-to-frame
    motion even when exact encoder timestamps are not available.
    """
    path = Path(video_path)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    duration_sec = frame_count / fps if fps > 0 else 0.0
    scale = min(1.0, max_width / max(1, width))
    resized_size = (max(1, int(width * scale)), max(1, int(height * scale)))

    prev: np.ndarray | None = None
    diffs: list[float] = []
    flow_mags: list[float] = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, resized_size, interpolation=cv2.INTER_AREA)
        if prev is not None:
            diffs.append(float(np.mean(cv2.absdiff(gray, prev))))
            flow = cv2.calcOpticalFlowFarneback(
                prev,
                gray,
                None,
                pyr_scale=0.5,
                levels=2,
                winsize=15,
                iterations=2,
                poly_n=5,
                poly_sigma=1.1,
                flags=0,
            )
            mag = np.sqrt(flow[..., 0] * flow[..., 0] + flow[..., 1] * flow[..., 1])
            flow_mags.append(float(np.median(mag)))
        prev = gray
    cap.release()

    diff_arr = np.asarray(diffs, dtype=np.float64)
    flow_arr = np.asarray(flow_mags, dtype=np.float64)
    if diff_arr.size == 0:
        report = VideoStutterReport(path, width, height, fps, frame_count, duration_sec, 0.0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    else:
        repeat_like = diff_arr < duplicate_threshold
        freeze_events, longest_freeze_frames = _count_runs(repeat_like)
        motion = flow_arr if flow_arr.size else diff_arr
        motion_positive = motion[motion > 1e-6]
        if motion_positive.size == 0:
            motion_positive = motion + 1e-6
        median = float(np.percentile(motion_positive, 50))
        p95 = float(np.percentile(motion_positive, 95))
        p99 = float(np.percentile(motion_positive, 99))
        mean = float(np.mean(motion_positive))
        std = float(np.std(motion_positive))
        motion_cv = std / max(mean, 1e-6)
        rolling = pd.Series(motion).rolling(window=9, center=True, min_periods=1).median().to_numpy()
        cadence_jitter = float(np.mean(np.abs(motion - rolling)) / max(float(np.median(rolling)), 1e-6))
        repeat_ratio = float(np.mean(repeat_like))
        p95_over_p50 = p95 / max(median, 1e-6)
        p99_over_p50 = p99 / max(median, 1e-6)
        stutter_score = float(
            repeat_ratio * 100.0
            + max(0.0, p95_over_p50 - 2.0) * 10.0
            + cadence_jitter * 10.0
            + motion_cv * 5.0
        )
        report = VideoStutterReport(
            video_path=path,
            width=width,
            height=height,
            fps=fps,
            frame_count=frame_count,
            duration_sec=duration_sec,
            repeat_like_ratio=repeat_ratio,
            freeze_event_count=freeze_events,
            longest_freeze_sec=(longest_freeze_frames / fps) if fps > 0 else 0.0,
            motion_cv=motion_cv,
            motion_p95_over_p50=p95_over_p50,
            motion_p99_over_p50=p99_over_p50,
            cadence_jitter=cadence_jitter,
            stutter_score=stutter_score,
        )

    if output_json is not None:
        out = Path(output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return report


def analyze_pose_smoothness(pose_log: str | Path, output_json: str | Path | None = None) -> dict[str, Any]:
    """Measure whether recorded camera pose changes have large discontinuities."""
    path = Path(pose_log)
    df = pd.read_csv(path)
    if len(df) < 3:
        result: dict[str, Any] = {"pose_log": str(path), "rows": len(df), "error": "not enough rows"}
    else:
        t = df["timestamp_sec"].to_numpy(dtype=float)
        dt = np.diff(t)
        result = {
            "pose_log": str(path),
            "rows": int(len(df)),
            "duration_sec": float(t[-1] - t[0]),
            "sample_dt_p50": float(np.percentile(dt, 50)),
            "sample_dt_p95": float(np.percentile(dt, 95)),
            "sample_dt_max": float(np.max(dt)),
        }
        for column in ["x", "y", "z", "yaw", "pitch", "fov"]:
            if column not in df.columns:
                continue
            values = df[column].to_numpy(dtype=float)
            velocity = np.diff(values) / np.maximum(dt, 1e-6)
            accel = np.diff(velocity) / np.maximum(dt[1:], 1e-6)
            result[f"{column}_velocity_p95"] = float(np.percentile(np.abs(velocity), 95))
            result[f"{column}_velocity_max"] = float(np.max(np.abs(velocity)))
            result[f"{column}_accel_p95"] = float(np.percentile(np.abs(accel), 95)) if accel.size else 0.0
            result[f"{column}_jump_count"] = int(np.sum(np.abs(velocity) > max(np.percentile(np.abs(velocity), 95) * 3.0, 1e-6)))
    if output_json is not None:
        out = Path(output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def _count_runs(mask: np.ndarray) -> tuple[int, int]:
    count = 0
    longest = 0
    current = 0
    for value in mask:
        if bool(value):
            current += 1
            if current == 1:
                count += 1
            longest = max(longest, current)
        else:
            current = 0
    return count, longest
