from __future__ import annotations

import csv
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "core" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from re9_pose_recorder.stutter_analyzer import analyze_pose_smoothness, analyze_video_stutter


VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".flv", ".avi"}


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_ROOT / "data" / "videos" / "trajectories" / "scene_1.1_low_to_high"
    root = root.resolve()
    videos = sorted(path for path in root.rglob("*") if path.suffix.lower() in VIDEO_SUFFIXES)
    out_dir = root / "stutter_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []

    print(f"videos={len(videos)}")
    for index, video in enumerate(videos, start=1):
        relative = video.relative_to(root)
        safe_stem = "_".join(relative.with_suffix("").parts).replace(" ", "_").replace(":", "_")
        out_json = out_dir / f"{safe_stem}_stutter.json"
        print(f"[{index}/{len(videos)}] {relative}")

        report = analyze_video_stutter(video, output_json=out_json).to_dict()
        pose_log = video.parent.parent / "pose_log.csv"
        if pose_log.exists():
            pose_json = out_dir / f"{safe_stem}_pose.json"
            pose_report = analyze_pose_smoothness(pose_log, output_json=pose_json)
            for key, value in pose_report.items():
                if key != "pose_log":
                    report[f"pose_{key}"] = value
            report["pose_log"] = str(pose_log)
            report["pose_report_json"] = str(pose_json)
        else:
            report["pose_log"] = ""
            report["pose_report_json"] = ""

        report["relative_path"] = str(relative)
        report["report_json"] = str(out_json)
        rows.append(report)

    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)

    out_csv = out_dir / "all_videos_stutter_summary.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)

    ranked = sorted(rows, key=lambda row: float(row.get("stutter_score") or 0.0), reverse=True)
    print("\nTOP stutter_score:")
    for row in ranked[:15]:
        print(
            f"{float(row.get('stutter_score') or 0.0):7.3f} "
            f"repeat={float(row.get('repeat_like_ratio') or 0.0):.3f} "
            f"freeze={row.get('freeze_event_count')} "
            f"longest={float(row.get('longest_freeze_sec') or 0.0):.3f}s "
            f"{row['relative_path']}"
        )
    print(f"\nsummary_csv={out_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
