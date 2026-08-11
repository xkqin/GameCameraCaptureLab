from __future__ import annotations

import csv
import html
import json
import math
from pathlib import Path
import shutil
from typing import Any


def generate_capture_report(
    aligned_csv: str | Path,
    output_dir: str | Path,
    *,
    top_k: int = 30,
) -> dict[str, str]:
    rows = _read_rows(aligned_csv)
    if not rows:
        raise ValueError("Aligned CSV has no rows")
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    score_rows = [
        row for row in rows if _number(row.get("score")) is not None
    ]
    valid_rows = [
        row
        for row in rows
        if str(row.get("alignment_valid", "")).lower() in {"true", "1"}
    ]
    score_curve = output / "score_curve.png"
    camera_path = output / "camera_path.png"
    _plots(score_rows, valid_rows, score_curve, camera_path)
    top = sorted(
        score_rows,
        key=lambda row: float(row["score"]),
        reverse=True,
    )[: max(0, top_k)]
    top_dir = output / "top_frames"
    top_dir.mkdir(exist_ok=True)
    cards: list[str] = []
    for rank, row in enumerate(top, start=1):
        source = Path(str(row.get("frame_path", "")))
        destination = top_dir / (
            f"rank_{rank:03d}_score_{float(row['score']):.3f}_"
            f"t{float(row['timestamp_sec']):.3f}.jpg"
        )
        if source.exists():
            shutil.copy2(source, destination)
        cards.append(
            "<article><img src='{}'><p>Rank {} · score {:.3f} · t={:.3f}</p>"
            "<p>XYZ ({}, {}, {}) · yaw {} · pitch {} · FOV {}</p></article>".format(
                html.escape(str(Path("top_frames") / destination.name).replace("\\", "/")),
                rank,
                float(row["score"]),
                float(row["timestamp_sec"]),
                _fmt(row.get("x")),
                _fmt(row.get("y")),
                _fmt(row.get("z")),
                _fmt(row.get("yaw_degrees")),
                _fmt(row.get("pitch_degrees")),
                _fmt(row.get("fov_degrees")),
            )
        )
    scores = [float(row["score"]) for row in score_rows]
    summary = {
        "rows": len(rows),
        "aligned_rows": len(valid_rows),
        "scored_rows": len(score_rows),
        "average_score": sum(scores) / len(scores) if scores else None,
        "max_score": max(scores) if scores else None,
        "min_score": min(scores) if scores else None,
    }
    table_fields = [
        "timestamp_sec",
        "score",
        "x",
        "y",
        "z",
        "yaw_degrees",
        "pitch_degrees",
        "fov_degrees",
        "alignment_time_diff_sec",
        "alignment_valid",
    ]
    table_rows = "".join(
        "<tr>"
        + "".join(f"<td>{html.escape(str(row.get(name, '')))}</td>" for name in table_fields)
        + "</tr>"
        for row in rows[:500]
    )
    report = output / "report.html"
    report.write_text(
        f"""<!doctype html>
<html><head><meta charset="utf-8"><title>KCD2 Capture Report</title>
<style>
body{{font-family:Segoe UI,Arial;margin:32px;color:#172033;background:#f8fafc}}
h1,h2{{color:#0f172a}} .summary{{display:flex;gap:12px;flex-wrap:wrap}}
.metric,article{{background:white;border:1px solid #dbe3ef;border-radius:10px;padding:12px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}}
article img{{width:100%}} .plot{{max-width:100%;background:white;border:1px solid #ddd}}
table{{border-collapse:collapse;width:100%;background:white;font-size:12px}}
th,td{{border:1px solid #ddd;padding:5px;text-align:right}}
</style></head><body>
<h1>KCD2 Camera Capture Report</h1>
<div class="summary">{''.join(f'<div class="metric"><b>{html.escape(k)}</b><br>{html.escape(_fmt(v))}</div>' for k,v in summary.items())}</div>
<h2>Plots</h2><img class="plot" src="score_curve.png"><img class="plot" src="camera_path.png">
<h2>Top Frames</h2><div class="grid">{''.join(cards)}</div>
<h2>Aligned Samples</h2><table><tr>{''.join(f'<th>{name}</th>' for name in table_fields)}</tr>{table_rows}</table>
</body></html>""",
        encoding="utf-8",
    )
    summary_path = output / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "report": str(report),
        "summary": str(summary_path),
        "score_curve": str(score_curve),
        "camera_path": str(camera_path),
    }


def build_score_ascent_trajectory(
    aligned_csv: str | Path,
    output_json: str | Path,
    *,
    max_keyframes: int = 64,
) -> dict[str, Any]:
    rows = [
        row
        for row in _read_rows(aligned_csv)
        if _number(row.get("score")) is not None
        and all(_number(row.get(name)) is not None for name in ("x", "y", "z"))
    ]
    if len(rows) < 2:
        raise ValueError("At least two scored, aligned poses are required")
    rows.sort(key=lambda row: float(row["timestamp_sec"]))
    best = max(rows, key=lambda row: float(row["score"]))
    current = rows[0]
    path = [current]
    remaining = rows[1:]
    while current is not best and remaining and len(path) < max_keyframes:
        higher = [
            row
            for row in remaining
            if float(row["score"]) > float(current["score"])
        ]
        if not higher:
            break
        next_row = min(
            higher,
            key=lambda row: _distance(current, row)
            / max(1.0e-6, float(row["score"]) - float(current["score"])),
        )
        path.append(next_row)
        remaining.remove(next_row)
        current = next_row
    if path[-1] is not best:
        path.append(best)
    frames = []
    start_time = float(path[0]["timestamp_sec"])
    for step, row in enumerate(path):
        frames.append(
            {
                "step": step,
                "time_sec": max(0.0, float(row["timestamp_sec"]) - start_time),
                "x": float(row["x"]),
                "y": float(row["y"]),
                "z": float(row["z"]),
                "yaw_degrees": float(row.get("yaw_degrees") or 0.0),
                "pitch_degrees": float(row.get("pitch_degrees") or 0.0),
                "roll_degrees": float(row.get("roll_degrees") or 0.0),
                "fov_degrees": float(row.get("fov_degrees") or 63.0),
                "score": float(row["score"]),
                "image_path": row.get("frame_path", ""),
            }
        )
    output = Path(output_json).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "trajectory_id": output.stem,
        "coordinate_system": {"angle_unit": "degrees", "vertical_axis": "z"},
        "source_aligned_csv": str(Path(aligned_csv).resolve()),
        "keyframes": frames,
    }
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "output_json": str(output),
        "keyframe_count": len(frames),
        "start_score": frames[0]["score"],
        "end_score": frames[-1]["score"],
    }


def _plots(rows, valid_rows, score_curve: Path, camera_path: Path) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("matplotlib is required to generate report plots") from exc
    plt.figure(figsize=(11, 4.5))
    if rows:
        plt.plot(
            [float(row["timestamp_sec"]) for row in rows],
            [float(row["score"]) for row in rows],
            linewidth=1.2,
        )
    plt.xlabel("timestamp_sec")
    plt.ylabel("aesthetic score")
    plt.title("KCD2 Aesthetic Score Over Time")
    plt.tight_layout()
    plt.savefig(score_curve, dpi=150)
    plt.close()

    plot_rows = [
        row
        for row in valid_rows
        if _number(row.get("x")) is not None and _number(row.get("y")) is not None
    ]
    plt.figure(figsize=(7, 7))
    if plot_rows:
        xs = [float(row["x"]) for row in plot_rows]
        ys = [float(row["y"]) for row in plot_rows]
        colors = [
            float(row["score"]) if _number(row.get("score")) is not None else 0.0
            for row in plot_rows
        ]
        points = plt.scatter(xs, ys, c=colors, cmap="viridis", s=18)
        plt.plot(xs, ys, color="black", alpha=0.25, linewidth=0.8)
        plt.colorbar(points, label="aesthetic score")
    plt.xlabel("x")
    plt.ylabel("y")
    plt.title("KCD2 Camera Path (horizontal plane)")
    plt.axis("equal")
    plt.tight_layout()
    plt.savefig(camera_path, dpi=150)
    plt.close()


def _read_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str:
    number = _number(value)
    return f"{number:.4f}" if number is not None and math.isfinite(number) else str(value or "")


def _distance(left: dict[str, Any], right: dict[str, Any]) -> float:
    return math.dist(
        tuple(float(left[name]) for name in ("x", "y", "z")),
        tuple(float(right[name]) for name in ("x", "y", "z")),
    )
