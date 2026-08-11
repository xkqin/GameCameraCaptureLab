from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest

from kcd2_capture_studio.reports import (
    build_score_ascent_trajectory,
    generate_capture_report,
)
from kcd2_capture_studio.video_analysis import align_frames_with_pose


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class VideoAnalysisTests(unittest.TestCase):
    def test_manifest_offset_aligns_video_time_to_pose_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            frames = root / "scores.csv"
            poses = root / "pose.csv"
            manifest = root / "recording_manifest.json"
            aligned = root / "aligned.csv"
            write_csv(
                frames,
                [
                    {
                        "video_path": "video.mkv",
                        "frame_path": "a.jpg",
                        "file_name": "a.jpg",
                        "frame_index": 0,
                        "timestamp_sec": 0.0,
                        "width": 640,
                        "height": 360,
                        "score": 5.0,
                    },
                    {
                        "video_path": "video.mkv",
                        "frame_path": "b.jpg",
                        "file_name": "b.jpg",
                        "frame_index": 1,
                        "timestamp_sec": 0.1,
                        "width": 640,
                        "height": 360,
                        "score": 6.0,
                    },
                ],
            )
            write_csv(
                poses,
                [
                    {
                        "timestamp_sec": 0.0,
                        "x": 0,
                        "y": 0,
                        "z": 0,
                        "q0": 0,
                        "q1": 0,
                        "q2": 0,
                        "q3": 1,
                        "yaw_degrees": 0,
                        "pitch_degrees": 0,
                        "roll_degrees": 0,
                        "fov_degrees": 63,
                    },
                    {
                        "timestamp_sec": 0.1,
                        "x": 1,
                        "y": 2,
                        "z": 3,
                        "q0": 0,
                        "q1": 0,
                        "q2": 0,
                        "q3": 1,
                        "yaw_degrees": 10,
                        "pitch_degrees": 5,
                        "roll_degrees": 0,
                        "fov_degrees": 63,
                    },
                    {
                        "timestamp_sec": 0.2,
                        "x": 2,
                        "y": 4,
                        "z": 6,
                        "q0": 0,
                        "q1": 0,
                        "q2": 0,
                        "q3": 1,
                        "yaw_degrees": 20,
                        "pitch_degrees": 10,
                        "roll_degrees": 0,
                        "fov_degrees": 63,
                    },
                ],
            )
            manifest.write_text(
                json.dumps({"pose_time_at_obs_start_sec": 0.1}),
                encoding="utf-8",
            )
            result = align_frames_with_pose(
                frames,
                poses,
                aligned,
                recording_manifest=manifest,
                max_time_diff_sec=0.01,
            )
            with aligned.open("r", newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(result["aligned_count"], 2)
        self.assertEqual(float(rows[0]["x"]), 1.0)
        self.assertEqual(float(rows[1]["x"]), 2.0)

    def test_report_and_score_ascent_trajectory(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            image_a = root / "a.jpg"
            image_b = root / "b.jpg"
            Image.new("RGB", (16, 16), (255, 0, 0)).save(image_a)
            Image.new("RGB", (16, 16), (0, 255, 0)).save(image_b)
            aligned = root / "aligned.csv"
            write_csv(
                aligned,
                [
                    {
                        "video_path": "video.mkv",
                        "frame_path": str(image_a),
                        "timestamp_sec": 0.0,
                        "score": 4.0,
                        "x": 0,
                        "y": 0,
                        "z": 0,
                        "yaw_degrees": 0,
                        "pitch_degrees": 0,
                        "roll_degrees": 0,
                        "fov_degrees": 63,
                        "alignment_time_diff_sec": 0.0,
                        "alignment_valid": True,
                    },
                    {
                        "video_path": "video.mkv",
                        "frame_path": str(image_b),
                        "timestamp_sec": 1.0,
                        "score": 7.0,
                        "x": 10,
                        "y": 5,
                        "z": 2,
                        "yaw_degrees": 30,
                        "pitch_degrees": 5,
                        "roll_degrees": 0,
                        "fov_degrees": 65,
                        "alignment_time_diff_sec": 0.0,
                        "alignment_valid": True,
                    },
                ],
            )
            report = generate_capture_report(aligned, root / "report", top_k=2)
            trajectory = build_score_ascent_trajectory(
                aligned,
                root / "trajectory.json",
            )
            payload = json.loads(
                Path(trajectory["output_json"]).read_text(encoding="utf-8")
            )
            self.assertTrue(Path(report["report"]).exists())
            self.assertTrue(Path(report["score_curve"]).exists())
            self.assertTrue(Path(report["camera_path"]).exists())
        self.assertEqual(trajectory["keyframe_count"], 2)
        self.assertEqual(payload["keyframes"][-1]["score"], 7.0)


if __name__ == "__main__":
    unittest.main()
