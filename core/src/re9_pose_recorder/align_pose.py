from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


POSE_COLUMNS = ["x", "y", "z", "yaw", "pitch", "fov", "freecam_mode", "user_has_rotated"]
OUTPUT_COLUMNS = [
    "video_path",
    "frame_path",
    "file_name",
    "frame_index",
    "timestamp_sec",
    "score",
    "width",
    "height",
    "pose_timestamp_sec",
    "x",
    "y",
    "z",
    "yaw",
    "pitch",
    "fov",
    "freecam_mode",
    "user_has_rotated",
    "alignment_time_diff_sec",
    "alignment_valid",
]


def align_pose(
    scores_csv: str | Path,
    pose_log_csv: str | Path,
    output_csv: str | Path,
    method: str = "nearest",
    max_time_diff_sec: float = 0.25,
) -> pd.DataFrame:
    scores = pd.read_csv(scores_csv)
    pose = pd.read_csv(pose_log_csv)
    if "timestamp_sec" not in scores.columns:
        raise ValueError("scores.csv must contain timestamp_sec")
    if "timestamp_sec" not in pose.columns:
        raise ValueError("pose log must contain timestamp_sec")

    scores = scores.sort_values("timestamp_sec").reset_index(drop=True)
    pose = pose.sort_values("timestamp_sec").reset_index(drop=True)
    if method == "linear":
        aligned = _linear_align(scores, pose, max_time_diff_sec)
    elif method == "nearest":
        aligned = _nearest_align(scores, pose, max_time_diff_sec)
    else:
        raise ValueError("alignment method must be 'nearest' or 'linear'")

    for column in OUTPUT_COLUMNS:
        if column not in aligned.columns:
            aligned[column] = ""
    aligned = aligned[OUTPUT_COLUMNS]
    Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
    aligned.to_csv(output_csv, index=False)
    return aligned


def _nearest_align(scores: pd.DataFrame, pose: pd.DataFrame, max_time_diff_sec: float) -> pd.DataFrame:
    pose_renamed = pose.rename(columns={"timestamp_sec": "pose_timestamp_sec"})
    merged = pd.merge_asof(
        scores,
        pose_renamed,
        left_on="timestamp_sec",
        right_on="pose_timestamp_sec",
        direction="nearest",
        tolerance=max_time_diff_sec,
    )
    merged["alignment_time_diff_sec"] = (merged["timestamp_sec"] - merged["pose_timestamp_sec"]).abs()
    merged["alignment_valid"] = merged["pose_timestamp_sec"].notna()
    return merged


def _linear_align(scores: pd.DataFrame, pose: pd.DataFrame, max_time_diff_sec: float) -> pd.DataFrame:
    result = scores.copy()
    times = pose["timestamp_sec"].to_numpy(dtype=float)
    frame_times = result["timestamp_sec"].to_numpy(dtype=float)
    nearest_indices = np.searchsorted(times, frame_times)
    diffs: list[float] = []
    valid: list[bool] = []
    pose_times: list[float | None] = []

    for column in POSE_COLUMNS:
        result[column] = np.nan

    for index, frame_time in enumerate(frame_times):
        right = int(nearest_indices[index])
        left = max(0, right - 1)
        right = min(len(times) - 1, right)
        nearest = left if abs(times[left] - frame_time) <= abs(times[right] - frame_time) else right
        diff = abs(times[nearest] - frame_time)
        is_valid = diff <= max_time_diff_sec
        diffs.append(diff)
        valid.append(is_valid)
        pose_times.append(float(times[nearest]) if is_valid else None)
        if not is_valid:
            continue

        for column in ["x", "y", "z", "pitch", "fov"]:
            if column in pose.columns:
                result.loc[index, column] = np.interp(frame_time, times, pose[column].astype(float).to_numpy())
        for column in ["yaw", "freecam_mode", "user_has_rotated"]:
            if column in pose.columns:
                result.loc[index, column] = pose.iloc[nearest][column]

    result["pose_timestamp_sec"] = pose_times
    result["alignment_time_diff_sec"] = diffs
    result["alignment_valid"] = valid
    return result
