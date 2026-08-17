from __future__ import annotations

from pathlib import Path

import cv2
import pandas as pd
from tqdm import tqdm

from .paths import ensure_dir


def get_video_info(video_path: str | Path) -> dict[str, float | int | str]:
    path = Path(video_path)
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    duration = frame_count / fps if fps > 0 else 0.0
    capture.release()
    return {
        "video_path": str(path),
        "fps": fps,
        "frame_count": frame_count,
        "width": width,
        "height": height,
        "duration_sec": duration,
    }


def extract_frames(
    video_path: str | Path,
    output_dir: str | Path,
    target_fps: float,
    jpeg_quality: int = 95,
    overwrite: bool = False,
) -> pd.DataFrame:
    video = Path(video_path)
    out_dir = ensure_dir(output_dir)
    metadata_path = out_dir / "frame_metadata.csv"
    if metadata_path.exists() and not overwrite:
        return pd.read_csv(metadata_path)

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise RuntimeError(f"Could not open video: {video}")

    source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if source_fps <= 0:
        capture.release()
        raise RuntimeError(f"Video reports an invalid FPS: {video}")

    duration_sec = frame_count / source_fps if frame_count else 0.0
    step = 1.0 / target_fps
    rows: list[dict[str, object]] = []
    timestamp = 0.0
    extracted_index = 0
    total = int(duration_sec * target_fps) + 1 if duration_sec else None

    with tqdm(total=total, desc="Extracting frames") as progress:
        while True:
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000.0)
            ok, frame = capture.read()
            if not ok:
                break
            height, width = frame.shape[:2]
            file_name = f"frame_{extracted_index:06d}_t{timestamp:07.3f}.jpg"
            frame_path = out_dir / file_name
            if overwrite or not frame_path.exists():
                success = cv2.imwrite(str(frame_path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)])
                if not success:
                    raise RuntimeError(f"Could not write frame: {frame_path}")
            rows.append(
                {
                    "video_path": str(video),
                    "frame_path": str(frame_path),
                    "frame_index": extracted_index,
                    "timestamp_sec": round(timestamp, 6),
                    "width": width,
                    "height": height,
                }
            )
            extracted_index += 1
            timestamp += step
            progress.update(1)
            if duration_sec and timestamp > duration_sec:
                break

    capture.release()
    data = pd.DataFrame(rows)
    data.to_csv(metadata_path, index=False)
    return data
