from __future__ import annotations

import html
import shutil
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from jinja2 import Template

from .paths import ensure_dir


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>RE9 FreeCam Aesthetic Pose Report</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 32px; color: #202020; }
    table { border-collapse: collapse; width: 100%; font-size: 13px; }
    th, td { border: 1px solid #ddd; padding: 6px 8px; text-align: right; }
    th:first-child, td:first-child { text-align: left; }
    th { background: #f2f2f2; }
    img.plot { max-width: 100%; border: 1px solid #ddd; }
    .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }
    .frame img { width: 100%; display: block; }
    .frame { border: 1px solid #ddd; padding: 8px; }
  </style>
</head>
<body>
  <h1>RE9 FreeCam Aesthetic Pose Report</h1>
  <h2>Summary</h2>
  <table>
    {% for key, value in summary.items() %}
    <tr><th>{{ key }}</th><td>{{ value }}</td></tr>
    {% endfor %}
  </table>
  <h2>Plots</h2>
  <p><img class="plot" src="score_curve.png" alt="Aesthetic score over time"></p>
  <p><img class="plot" src="camera_path.png" alt="Camera path"></p>
  <h2>Top Frames</h2>
  <div class="grid">
    {% for row in top_rows %}
    <div class="frame">
      <img src="{{ row.relative_path }}" alt="Top frame {{ loop.index }}">
      <p>Rank {{ loop.index }} | score {{ "%.3f"|format(row.score) }} | t={{ "%.3f"|format(row.timestamp_sec) }}</p>
      <p>x={{ row.x }} y={{ row.y }} z={{ row.z }} yaw={{ row.yaw }} pitch={{ row.pitch }} fov={{ row.fov }}</p>
    </div>
    {% endfor %}
  </div>
  <h2>Aligned Samples</h2>
  {{ table_html }}
</body>
</html>
"""


def generate_report(
    scores_with_pose_csv: str | Path,
    output_dir: str | Path,
    top_k: int = 50,
    copy_top_frames: bool = True,
    smooth_window: int = 0,
    session_id: str = "",
    extraction_fps: float | None = None,
    pose_log_csv: str | Path | None = None,
) -> dict[str, Path]:
    out_dir = ensure_dir(output_dir)
    data = pd.read_csv(scores_with_pose_csv)
    score_curve = out_dir / "score_curve.png"
    camera_path = out_dir / "camera_path.png"
    html_path = out_dir / "report.html"

    _plot_score_curve(data, score_curve, smooth_window=smooth_window)
    _plot_camera_path(data, camera_path)
    top_rows = _copy_top_frames(data, out_dir, top_k=top_k, enabled=copy_top_frames)
    summary = _summary(data, session_id=session_id, extraction_fps=extraction_fps, pose_log_csv=pose_log_csv)
    table_cols = [
        "timestamp_sec",
        "score",
        "x",
        "y",
        "z",
        "yaw",
        "pitch",
        "fov",
        "alignment_valid",
    ]
    table_html = data[table_cols].head(500).to_html(index=False, escape=True)
    html_path.write_text(
        Template(HTML_TEMPLATE).render(summary=summary, top_rows=top_rows, table_html=table_html),
        encoding="utf-8",
    )
    return {"score_curve": score_curve, "camera_path": camera_path, "report": html_path}


def _plot_score_curve(data: pd.DataFrame, output_path: Path, smooth_window: int = 0) -> None:
    plt.figure(figsize=(12, 5))
    plt.plot(data["timestamp_sec"], data["score"], label="Score", linewidth=1.2)
    if smooth_window and smooth_window > 1:
        plt.plot(
            data["timestamp_sec"],
            data["score"].rolling(smooth_window, min_periods=1).mean(),
            label=f"Rolling average ({smooth_window})",
            linewidth=2.0,
        )
        plt.legend()
    plt.xlabel("timestamp_sec")
    plt.ylabel("aesthetic score")
    plt.title("Aesthetic Score Over Time")
    plt.tight_layout()
    plt.savefig(output_path, dpi=160)
    plt.close()


def _plot_camera_path(data: pd.DataFrame, output_path: Path) -> None:
    if "alignment_valid" in data.columns:
        valid = data[data["alignment_valid"].fillna(False) == True].copy()  # noqa: E712
    else:
        valid = data.iloc[0:0].copy()
    plt.figure(figsize=(7, 7))
    if not valid.empty and {"x", "z", "score"}.issubset(valid.columns):
        points = plt.scatter(valid["x"], valid["z"], c=valid["score"], cmap="viridis", s=20)
        plt.colorbar(points, label="aesthetic score")
        plt.plot(valid["x"], valid["z"], color="black", alpha=0.25, linewidth=0.8)
    plt.xlabel("x")
    plt.ylabel("z")
    plt.title("Camera Path")
    plt.axis("equal")
    plt.tight_layout()
    plt.savefig(output_path, dpi=160)
    plt.close()


def _copy_top_frames(data: pd.DataFrame, output_dir: Path, top_k: int, enabled: bool) -> list[dict[str, object]]:
    top = data.sort_values("score", ascending=False).head(top_k).copy()
    top_dir = output_dir / "top_frames"
    if enabled:
        top_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for rank, (_, row) in enumerate(top.iterrows(), start=1):
        source = Path(str(row["frame_path"]))
        name = (
            f"rank_{rank:03d}_score_{float(row['score']):.3f}_t{float(row['timestamp_sec']):07.3f}_"
            f"x{_fmt(row.get('x'))}_y{_fmt(row.get('y'))}_z{_fmt(row.get('z'))}.jpg"
        )
        destination = top_dir / _safe_name(name)
        if enabled and source.exists():
            shutil.copy2(source, destination)
        item = row.to_dict()
        item["relative_path"] = html.escape(str(Path("top_frames") / destination.name).replace("\\", "/"))
        rows.append(item)
    return rows


def _summary(
    data: pd.DataFrame,
    session_id: str = "",
    extraction_fps: float | None = None,
    pose_log_csv: str | Path | None = None,
) -> dict[str, object]:
    video_name = Path(str(data["video_path"].iloc[0])).name if "video_path" in data and len(data) else ""
    pose_rows_count = ""
    if pose_log_csv and Path(pose_log_csv).exists():
        pose_rows_count = len(pd.read_csv(pose_log_csv))
    return {
        "Video name": video_name,
        "Session id": session_id,
        "Video duration": float(data["timestamp_sec"].max()) if len(data) else 0,
        "Frame extraction FPS": extraction_fps if extraction_fps is not None else "",
        "Number of scored frames": len(data),
        "Pose rows count": pose_rows_count,
        "Number of aligned rows": int(data.get("alignment_valid", pd.Series(dtype=bool)).fillna(False).sum()),
        "Average score": round(float(data["score"].mean()), 4) if len(data) else "",
        "Median score": round(float(data["score"].median()), 4) if len(data) else "",
        "Max score": round(float(data["score"].max()), 4) if len(data) else "",
        "Min score": round(float(data["score"].min()), 4) if len(data) else "",
    }


def _fmt(value: object) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "na"


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value)
