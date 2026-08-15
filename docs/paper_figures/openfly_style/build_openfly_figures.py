from __future__ import annotations

import base64
import csv
import json
import math
import mimetypes
import shutil
import subprocess
from collections import Counter
from html import escape
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
KCD2_SET = REPO / "games/kcd2/examples/trajectory_sets/scene_1_fixed_global_max_1000"
SCAN_PLAN = REPO / "games/kcd2/examples/scan_plans/scene_1_5layer_hull_plan.json"
SCAN_POSITIONS = REPO / "games/kcd2/examples/scan_plans/scene_1_5layer_hull_positions.csv"
TRAJECTORY_JSON = KCD2_SET / "20260810_191147_scene_1_auto22_true_gain2_no_backtrack_balanced_1000_trajectories.json"
TRAJECTORY_CSV = KCD2_SET / "trajectory_summary.csv"
VALIDATION_JSON = KCD2_SET / "validation.json"
CAPTURE_VALIDATION_JSON = KCD2_SET / "capture_import_validation.json"
DEMO_VIDEO = REPO / "docs/assets/game-camera-capture-demo.mp4"

W, H = 1800, 980
FONT = "Times New Roman"
SANS = "Arial"
INK = "#13233A"
MUTED = "#607189"
BLUE = "#0A7EAA"
CYAN = "#41BCE0"
LIGHT_BLUE = "#E9F7FC"
PALE_BLUE = "#F4FAFD"
GREEN = "#2D9D78"
ORANGE = "#E8893D"
PURPLE = "#7868C7"
RED = "#D84D4D"
GRID = "#D5E0E9"


def a(**values: object) -> str:
    out: list[str] = []
    for key, value in values.items():
        if value is None:
            continue
        key = key.rstrip("_").replace("_", "-")
        out.append(f'{key}="{escape(str(value), quote=True)}"')
    return " ".join(out)


def rect(x: float, y: float, w: float, h: float, *, fill: str = "none", stroke: str = "none", sw: float = 1, rx: float = 0, opacity: float | None = None) -> str:
    return f'<rect {a(x=x, y=y, width=w, height=h, fill=fill, stroke=stroke, stroke_width=sw, rx=rx, opacity=opacity)}/>'


def circle(cx: float, cy: float, r: float, *, fill: str = "none", stroke: str = "none", sw: float = 1, opacity: float | None = None) -> str:
    return f'<circle {a(cx=cx, cy=cy, r=r, fill=fill, stroke=stroke, stroke_width=sw, opacity=opacity)}/>'


def line(x1: float, y1: float, x2: float, y2: float, *, stroke: str = INK, sw: float = 2, dash: str | None = None, marker: str | None = None, opacity: float | None = None) -> str:
    return f'<line {a(x1=x1, y1=y1, x2=x2, y2=y2, stroke=stroke, stroke_width=sw, stroke_dasharray=dash, marker_end=marker, opacity=opacity, fill="none")}/>'


def path(d: str, *, fill: str = "none", stroke: str = INK, sw: float = 2, dash: str | None = None, marker: str | None = None, opacity: float | None = None) -> str:
    return f'<path {a(d=d, fill=fill, stroke=stroke, stroke_width=sw, stroke_dasharray=dash, marker_end=marker, opacity=opacity)}/>'


def polygon(points: str, *, fill: str = "none", stroke: str = "none", sw: float = 1, opacity: float | None = None) -> str:
    return f'<polygon {a(points=points, fill=fill, stroke=stroke, stroke_width=sw, opacity=opacity)}/>'


def txt(x: float, y: float, value: str, *, size: float = 26, fill: str = INK, weight: int = 400, anchor: str = "start", family: str = FONT, italic: bool = False, opacity: float | None = None, letter: float | None = None) -> str:
    return f'<text {a(x=x, y=y, font_size=size, fill=fill, font_weight=weight, text_anchor=anchor, font_family=family, font_style="italic" if italic else None, opacity=opacity, letter_spacing=letter)}>{escape(value)}</text>'


def multiline(x: float, y: float, values: list[str], *, size: float = 22, fill: str = INK, weight: int = 400, anchor: str = "start", family: str = FONT, line_height: float = 1.28) -> str:
    spans = []
    for index, value in enumerate(values):
        spans.append(f'<tspan x="{x}" dy="{0 if index == 0 else size * line_height}">{escape(value)}</tspan>')
    return f'<text {a(x=x, y=y, font_size=size, fill=fill, font_weight=weight, text_anchor=anchor, font_family=family)}>{"".join(spans)}</text>'


def image_data(path_value: Path) -> str:
    mime = mimetypes.guess_type(path_value.name)[0] or "image/png"
    encoded = base64.b64encode(path_value.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def image_tag(path_value: Path, x: float, y: float, w: float, h: float, *, preserve: str = "xMidYMid slice", opacity: float | None = None, clip: str | None = None) -> str:
    return f'<image {a(href=image_data(path_value), x=x, y=y, width=w, height=h, preserveAspectRatio=preserve, opacity=opacity, clip_path=clip)}/>'


def crop_clip(clip_id: str, x: float, y: float, w: float, h: float, rx: float = 0) -> str:
    return f'<clipPath id="{clip_id}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}"/></clipPath>'


def defs(extra: str = "") -> str:
    return f"""
<defs>
  <marker id="arrow-blue" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{BLUE}"/></marker>
  <marker id="arrow-cyan" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="{CYAN}"/></marker>
  <marker id="arrow-gray" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#72849A"/></marker>
  <linearGradient id="teaser-shade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#091827" stop-opacity="0.04"/><stop offset="1" stop-color="#091827" stop-opacity="0.76"/></linearGradient>
  <linearGradient id="blue-flow" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#0B7FA9"/><stop offset="1" stop-color="#45C2E4"/></linearGradient>
  <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="0" dy="8" stdDeviation="12" flood-color="#0B263A" flood-opacity="0.22"/></filter>
  {extra}
</defs>
"""


def document(title_value: str, description: str, body: str, *, extra_defs: str = "", background: str = "#FFFFFF") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="title desc">\n'
        f'<title id="title">{escape(title_value)}</title><desc id="desc">{escape(description)}</desc>\n'
        f'{defs(extra_defs)}{rect(0, 0, W, H, fill=background)}{body}</svg>\n'
    )


def title_band(number: str, title_value: str, subtitle: str) -> str:
    return "".join([
        txt(70, 58, number, size=18, fill=BLUE, family=SANS, weight=700, letter=2.4),
        txt(70, 104, title_value, size=40, fill=INK, weight=700),
        txt(70, 139, subtitle, size=20, fill=MUTED, family=SANS),
        line(70, 162, 1730, 162, stroke=GRID, sw=1.5),
    ])


def footer(label: str) -> str:
    return "".join([
        line(70, 936, 1730, 936, stroke=GRID, sw=1),
        txt(70, 963, "GAME CAMERA CAPTURE LAB", size=13, fill="#71839A", family=SANS, weight=700, letter=1.8),
        txt(1730, 963, label, size=14, fill="#71839A", family=SANS, anchor="end"),
    ])


def camera_icon(x: float, y: float, *, scale: float = 1, color: str = BLUE) -> str:
    return f'<g transform="translate({x} {y}) scale({scale})">' + "".join([
        rect(-20, -13, 40, 26, fill="#FFFFFF", stroke=color, sw=2, rx=4),
        circle(0, 0, 6, fill="none", stroke=color, sw=2),
        line(20, -10, 66, -32, stroke=color, sw=2),
        line(20, 10, 66, 32, stroke=color, sw=2),
        line(66, -32, 66, 32, stroke=color, sw=2),
        line(20, 0, 66, 0, stroke=color, sw=1, dash="5 5", opacity=0.65),
    ]) + "</g>"


def extract_demo_frames() -> list[Path]:
    out = ROOT / "assets/demo_frames"
    out.mkdir(parents=True, exist_ok=True)
    times = [6.5, 7.8, 9.2, 10.6, 11.5, 12.3]
    targets: list[Path] = []
    for index, time_sec in enumerate(times, start=1):
        target = out / f"kcd2_demo_{index:02d}.jpg"
        targets.append(target)
        if target.exists():
            continue
        subprocess.run([
            "ffmpeg", "-loglevel", "error", "-y", "-ss", str(time_sec), "-i", str(DEMO_VIDEO),
            "-frames:v", "1", "-q:v", "2", str(target),
        ], check=True)
    return targets


def load_data() -> dict[str, object]:
    scan = json.loads(SCAN_PLAN.read_text(encoding="utf-8"))
    validation = json.loads(VALIDATION_JSON.read_text(encoding="utf-8"))
    capture_validation = json.loads(CAPTURE_VALIDATION_JSON.read_text(encoding="utf-8"))
    trajectory_payload = json.loads(TRAJECTORY_JSON.read_text(encoding="utf-8"))
    with TRAJECTORY_CSV.open(encoding="utf-8", newline="") as stream:
        summary = list(csv.DictReader(stream))
    with SCAN_POSITIONS.open(encoding="utf-8", newline="") as stream:
        positions = list(csv.DictReader(stream))
    keyframe_counts = Counter(int(float(row["keyframe_count"])) for row in summary)
    path_lengths = [float(row["path_xyz_length"]) for row in summary]
    score_gains = [float(row["score_gain"]) for row in summary]
    mean_x = sum(path_lengths) / len(path_lengths)
    mean_y = sum(score_gains) / len(score_gains)
    corr = sum((x - mean_x) * (y - mean_y) for x, y in zip(path_lengths, score_gains)) / math.sqrt(
        sum((x - mean_x) ** 2 for x in path_lengths) * sum((y - mean_y) ** 2 for y in score_gains)
    )
    trajectory = next(t for t in trajectory_payload["trajectories"] if t["trajectory_id"].endswith("00750"))
    return {
        "scan": scan,
        "validation": validation,
        "capture_validation": capture_validation,
        "trajectory_payload": trajectory_payload,
        "summary": summary,
        "positions": positions,
        "keyframe_counts": keyframe_counts,
        "corr": corr,
        "trajectory": trajectory,
        "frames": extract_demo_frames(),
    }


DATA = load_data()


def teaser() -> str:
    hero = REPO / "docs/assets/hero-multigame.png"
    real = REPO / "docs/assets/game-camera-capture-demo-poster.jpg"
    interface = REPO / "docs/assets/interface-overview.png"
    dataset = REPO / "docs/assets/dataset-preview.png"
    clips = "".join([
        crop_clip("teaser-a", 0, 0, 900, 490),
        crop_clip("teaser-b", 900, 0, 900, 490),
        crop_clip("teaser-c", 0, 490, 900, 490),
        crop_clip("teaser-d", 900, 490, 900, 490),
    ])
    body = "".join([
        image_tag(real, 0, 0, 900, 490, clip="url(#teaser-a)"),
        image_tag(hero, 900, 0, 900, 490, clip="url(#teaser-b)"),
        image_tag(interface, 0, 490, 900, 490, clip="url(#teaser-c)"),
        image_tag(dataset, 900, 490, 900, 490, clip="url(#teaser-d)"),
        rect(0, 0, W, H, fill="url(#teaser-shade)"),
        line(900, 0, 900, 980, stroke="#FFFFFF", sw=3, opacity=0.75),
        line(0, 490, 1800, 490, stroke="#FFFFFF", sw=3, opacity=0.75),
        rect(425, 310, 950, 360, fill="#07192A", stroke="#BEEFFC", sw=2, rx=22, opacity=0.88),
        txt(900, 382, "GAME CAMERA CAPTURE LAB", size=20, fill="#8EE4F7", family=SANS, weight=700, anchor="middle", letter=3.0),
        txt(900, 450, "A Unified Camera Data Platform", size=48, fill="#FFFFFF", weight=700, anchor="middle"),
        txt(900, 493, "for Controllable Virtual Worlds", size=46, fill="#FFFFFF", weight=700, anchor="middle"),
        txt(900, 542, "pose  |  layered scan  |  trajectory  |  synchronized capture", size=23, fill="#D7EEF5", family=SANS, anchor="middle"),
        line(575, 582, 1225, 582, stroke="#4DC6E3", sw=2),
        txt(900, 622, "Concept teaser - visual language inspired by OpenFly, not an experimental result", size=16, fill="#B9D1DC", family=SANS, anchor="middle", italic=True),
        rect(28, 28, 255, 40, fill="#091C2B", rx=20, opacity=0.76),
        txt(155, 55, "REAL KCD2 FRAME", size=14, fill="#FFFFFF", family=SANS, weight=700, anchor="middle", letter=1.1),
        rect(1510, 28, 262, 40, fill="#091C2B", rx=20, opacity=0.76),
        txt(1641, 55, "CONCEPT MULTIVERSE", size=14, fill="#FFFFFF", family=SANS, weight=700, anchor="middle", letter=1.1),
        rect(28, 910, 238, 40, fill="#091C2B", rx=20, opacity=0.76),
        txt(147, 937, "CAPTURE INTERFACE", size=14, fill="#FFFFFF", family=SANS, weight=700, anchor="middle", letter=1.1),
        rect(1512, 910, 260, 40, fill="#091C2B", rx=20, opacity=0.76),
        txt(1642, 937, "DATASET WORKSPACE", size=14, fill="#FFFFFF", family=SANS, weight=700, anchor="middle", letter=1.1),
    ])
    return document("Game Camera Capture Lab teaser", "Four-panel conceptual teaser combining a real KCD2 frame, a conceptual multi-world illustration, and project interface visualizations.", body, extra_defs=clips, background="#07192A")


def pipeline() -> str:
    parts = [title_band("FIGURE 02 / AUTOMATIC DATA GENERATION", "A Unified Toolchain for Game-Camera Data Collection", "Engine-specific camera access is normalized before planning, control, capture, validation, and export.")]
    adapters = [
        ("RE Engine", "REFramework Lua", GREEN),
        ("CryEngine", "IGCS / Camera Tools", ORANGE),
        ("Unreal Engine 5", "UUU + native bridge", PURPLE),
        ("Future game", "game.json adapter", BLUE),
    ]
    for index, (name, bridge, color) in enumerate(adapters):
        x = 70 + index * 325
        parts += [
            rect(x, 205, 285, 92, fill="#FFFFFF", stroke=GRID, sw=1.5, rx=14),
            rect(x, 205, 9, 92, fill=color, rx=4),
            txt(x + 30, 243, name, size=22, weight=700),
            txt(x + 30, 274, bridge, size=16, fill=MUTED, family=SANS),
            line(x + 143, 297, x + 143, 336, stroke="#72849A", sw=2, marker="url(#arrow-gray)"),
        ]
    parts += [
        rect(1405, 205, 325, 92, fill=PALE_BLUE, stroke="#A9D6E5", sw=1.5, rx=14),
        txt(1568, 242, "Dynamic registry", size=22, fill="#0B6888", weight=700, anchor="middle"),
        txt(1568, 274, "discovers games/*/game.json", size=16, fill=MUTED, family=SANS, anchor="middle"),
        line(1568, 297, 1568, 336, stroke=BLUE, sw=2, marker="url(#arrow-blue)"),
        rect(70, 348, 1660, 120, fill=LIGHT_BLUE, stroke="#90CEE0", sw=2, rx=20),
        txt(900, 390, "Unified camera contract", size=29, fill="#0D5E7A", weight=700, anchor="middle"),
        txt(900, 426, "camera-pose/v1  +  camera-point-set/v1  +  camera-trajectory/v1", size=21, fill="#247994", family="Consolas", anchor="middle"),
        txt(900, 453, "explicit units, axes, handedness, position, rotation, time, and field of view", size=16, fill=MUTED, family=SANS, anchor="middle"),
        line(900, 468, 900, 510, stroke=BLUE, sw=3, marker="url(#arrow-blue)"),
    ]
    modules = [
        ("1", "Scene sampling", ["boundary poses", "4-6 spatial layers", "multi-view allocation"], GREEN),
        ("2", "Trajectory planning", ["measured keyframes", "spatial diversity", "smooth timing"], PURPLE),
        ("3", "Camera execution", ["absolute setPose", "relative input fallback", "pose feedback"], BLUE),
        ("4", "RGB acquisition", ["OBS / screenshots", "frame timestamps", "original pixels"], ORANGE),
        ("5", "Quality validation", ["pose reachability", "dropped-frame audit", "collision smoke test"], RED),
    ]
    for index, (number, name, notes, color) in enumerate(modules):
        x = 70 + index * 332
        parts += [
            rect(x, 522, 300, 226, fill="#FFFFFF", stroke=GRID, sw=1.5, rx=18),
            circle(x + 38, 560, 22, fill=color),
            txt(x + 38, 568, number, size=20, fill="#FFFFFF", family=SANS, weight=700, anchor="middle"),
            txt(x + 72, 568, name, size=22, weight=700),
            line(x + 26, 594, x + 274, 594, stroke=GRID, sw=1),
            multiline(x + 30, 634, [f"- {n}" for n in notes], size=18, fill=MUTED, family=SANS, line_height=1.55),
        ]
        if index < len(modules) - 1:
            parts.append(line(x + 300, 635, x + 328, 635, stroke=BLUE, sw=2.5, marker="url(#arrow-blue)"))
    products = [
        ("RGB + pose", "time-aligned frames and measured camera metadata"),
        ("Still scans", "layered viewpoints with reproducible orientation sets"),
        ("Trajectory clips", "keyframes, timing, source evidence, and manifests"),
    ]
    for index, (name, note) in enumerate(products):
        x = 182 + index * 500
        parts += [
            line(x + 145, 748, x + 145, 788, stroke=BLUE, sw=2, marker="url(#arrow-blue)"),
            rect(x, 802, 430, 92, fill=PALE_BLUE, stroke="#B4DCE8", sw=1.4, rx=14),
            txt(x + 25, 840, name, size=22, fill="#0B6888", weight=700),
            txt(x + 25, 871, note, size=15, fill=MUTED, family=SANS),
        ]
    parts.append(footer("Pure vector method figure"))
    return document("Unified game-camera data generation toolchain", "OpenFly-inspired modular pipeline showing game adapters, a unified camera contract, sampling, planning, execution, capture, validation, and dataset products.", "".join(parts))


def chart_frame(x: float, y: float, w: float, h: float, title_value: str, tag: str) -> list[str]:
    return [
        txt(x, y - 19, tag, size=17, fill=BLUE, family=SANS, weight=700),
        txt(x + 34, y - 19, title_value, size=22, weight=700),
        rect(x, y, w, h, fill="#FFFFFF", stroke="#BFCEDA", sw=1.5),
    ]


def histogram(values: list[float], bins: int, x: float, y: float, w: float, h: float, *, color: str, x_label: str, y_label: str) -> str:
    lo, hi = min(values), max(values)
    step = (hi - lo) / bins if hi > lo else 1
    counts = [0] * bins
    for value in values:
        index = min(bins - 1, int((value - lo) / step))
        counts[index] += 1
    max_count = max(counts)
    parts: list[str] = []
    for index, count in enumerate(counts):
        bw = w / bins
        bh = (h - 58) * count / max_count
        parts.append(rect(x + index * bw + 2, y + h - 43 - bh, max(2, bw - 4), bh, fill=color, opacity=0.8))
    for frac in [0, 0.5, 1]:
        px = x + frac * w
        value = lo + frac * (hi - lo)
        parts += [line(px, y + h - 43, px, y + h - 36, stroke=INK, sw=1), txt(px, y + h - 16, f"{value:.1f}", size=13, fill=MUTED, family=SANS, anchor="middle")]
    parts += [
        line(x, y + h - 43, x + w, y + h - 43, stroke=INK, sw=1.3),
        txt(x + w / 2, y + h + 10, x_label, size=15, fill=MUTED, family=SANS, anchor="middle"),
        txt(x - 31, y + h / 2, y_label, size=15, fill=MUTED, family=SANS, anchor="middle"),
    ]
    return "".join(parts)


def empirical_stats() -> str:
    rows: list[dict[str, str]] = DATA["summary"]  # type: ignore[assignment]
    validation: dict[str, object] = DATA["validation"]  # type: ignore[assignment]
    keyframe_counts: Counter[int] = DATA["keyframe_counts"]  # type: ignore[assignment]
    parts = [title_band("FIGURE 03 / EMPIRICAL DATA STATISTICS", "KCD2 Scene 1: Audited Trajectory-Set Statistics", "All values below are computed from the committed 1000-trajectory CSV/JSON evidence; runtime interpolation is excluded.")]
    boxes = [(70, 214), (915, 214), (70, 563), (915, 563)]
    # A: keyframe count distribution.
    x, y = boxes[0]
    parts += chart_frame(x, y, 785, 280, "Measured keyframes per trajectory", "(a)")
    max_count = max(keyframe_counts.values())
    for idx, key in enumerate(sorted(keyframe_counts)):
        bx = x + 80 + idx * 106
        bh = 177 * keyframe_counts[key] / max_count
        parts += [
            rect(bx, y + 223 - bh, 66, bh, fill=BLUE, opacity=0.82),
            txt(bx + 33, y + 214 - bh, str(keyframe_counts[key]), size=14, fill=INK, family=SANS, anchor="middle", weight=700),
            txt(bx + 33, y + 251, str(key), size=15, fill=MUTED, family=SANS, anchor="middle"),
        ]
    parts += [line(x + 58, y + 223, x + 740, y + 223, stroke=INK, sw=1.2), txt(x + 399, y + 272, "real scored control points", size=15, fill=MUTED, family=SANS, anchor="middle")]

    # B: score gain distribution.
    x, y = boxes[1]
    parts += chart_frame(x, y, 815, 280, "Aesthetic-score gain", "(b)")
    gains = [float(row["score_gain"]) for row in rows]
    parts.append(histogram(gains, 18, x + 62, y + 18, 700, 222, color=GREEN, x_label="measured score gain", y_label="count"))
    gain_stat = validation["score_gain"]  # type: ignore[index]
    parts.append(txt(x + 785, y + 42, f"mean {gain_stat['mean']:.3f}", size=15, fill=GREEN, family=SANS, weight=700, anchor="end"))

    # C: path length distribution.
    x, y = boxes[2]
    parts += chart_frame(x, y, 785, 280, "Spatial path length", "(c)")
    lengths = [float(row["path_xyz_length"]) for row in rows]
    parts.append(histogram(lengths, 18, x + 62, y + 18, 660, 222, color=PURPLE, x_label="path XYZ length (world units)", y_label="count"))
    corr = float(DATA["corr"])
    parts.append(txt(x + 745, y + 42, f"corr(gain, length) = {corr:.3f}", size=15, fill=PURPLE, family=SANS, weight=700, anchor="end"))

    # D: evidence summary with directly audited values.
    x, y = boxes[3]
    parts += chart_frame(x, y, 815, 280, "Validated set-level properties", "(d)")
    diversity = validation["set_diversity"]  # type: ignore[index]
    metrics = [
        ("1,000 / 1,000", "trajectories passed validation", BLUE),
        ("8,152", "real scored control keyframes", GREEN),
        ("100", "unique physical start positions", ORANGE),
        ("98.814%", "sampled pairs <= 0.70 overlap", PURPLE),
    ]
    for idx, (value, label, color) in enumerate(metrics):
        mx = x + 36 + (idx % 2) * 390
        my = y + 38 + (idx // 2) * 105
        parts += [
            rect(mx, my, 350, 82, fill=PALE_BLUE, stroke="#C6DDE6", sw=1, rx=11),
            txt(mx + 18, my + 34, value, size=27, fill=color, family=SANS, weight=700),
            txt(mx + 18, my + 62, label, size=14, fill=MUTED, family=SANS),
        ]
    parts.append(txt(x + 36, y + 266, f"Exact max route overlap: {diversity['pairwise_route_overlap_max']:.6f}; collision clearance and runtime-frame scores remain unverified.", size=14, fill="#7D5060", family=SANS))
    parts.append(footer("Pure vector, data-derived statistics"))
    return document("KCD2 audited trajectory statistics", "Four-panel statistics figure generated from the committed KCD2 Scene 1 trajectory CSV and validation JSON.", "".join(parts))


def layered_scan() -> str:
    scan: dict[str, object] = DATA["scan"]  # type: ignore[assignment]
    positions: list[dict[str, str]] = DATA["positions"]  # type: ignore[assignment]
    boundary_payload = json.loads((REPO / "games/kcd2/examples/scene_points/scene_1_boundary_space.json").read_text(encoding="utf-8"))
    boundary = boundary_payload["points"]
    parts = [title_band("FIGURE 04 / LAYERED SCENE SAMPLING", "From Sparse Boundary Poses to a Multi-View Scan Plan", "The KCD2 Scene 1 plan uses a convex-hull model, five vertical layers, and 22 orientations per retained spatial point.")]
    panel_w = 388
    panel_xs = [70, 492, 914, 1336]
    panel_titles = ["Boundary acquisition", "Convex-hull space", "Five-layer packing", "Multi-view output"]
    for index, (x, title_value) in enumerate(zip(panel_xs, panel_titles), start=1):
        parts += [
            txt(x, 208, f"({chr(96 + index)})", size=17, fill=BLUE, family=SANS, weight=700),
            txt(x + 34, 208, title_value, size=22, weight=700),
            rect(x, 228, panel_w, 570, fill="#FFFFFF", stroke=GRID, sw=1.5, rx=16),
        ]
        if index < 4:
            parts.append(line(x + panel_w + 8, 514, x + panel_w + 28, 514, stroke=BLUE, sw=2.5, marker="url(#arrow-blue)"))

    # Panel A: sparse boundary pose scatter.
    x = panel_xs[0]
    bx = [p[0] for p in boundary]; by = [p[1] for p in boundary]; bz = [p[2] for p in boundary]
    xmin, xmax = min(bx), max(bx); ymin, ymax = min(by), max(by); zmin, zmax = min(bz), max(bz)
    def project(px: float, py: float, pz: float, ox: float, oy: float) -> tuple[float, float]:
        nx = (px - xmin) / (xmax - xmin); ny = (py - ymin) / (ymax - ymin); nz = (pz - zmin) / (zmax - zmin)
        return ox + 255 * nx + 70 * ny, oy + 250 - 155 * ny - 85 * nz
    projected = [project(*point, x + 25, 302) for point in boundary]
    for i in range(len(projected) - 1):
        parts.append(line(*projected[i], *projected[i + 1], stroke="#AFC6D3", sw=1.2, opacity=0.8))
    for px, py in projected:
        parts.append(circle(px, py, 5.5, fill=BLUE, stroke="#FFFFFF", sw=1.2))
    parts += [
        camera_icon(x + 104, 620, scale=0.55, color=GREEN), camera_icon(x + 247, 555, scale=0.48, color=ORANGE),
        txt(x + 28, 696, f"{boundary_payload['source_record_count']} records", size=21, fill=INK, family=SANS, weight=700),
        txt(x + 28, 728, f"{boundary_payload['unique_xyz_count']} unique XYZ", size=17, fill=MUTED, family=SANS),
        txt(x + 28, 759, "z is the vertical axis", size=17, fill=MUTED, family=SANS),
    ]

    # Panel B: stylized convex hull wireframe using real projected boundary points.
    x = panel_xs[1]
    projected2 = [project(*point, x + 25, 302) for point in boundary]
    triangles = boundary_payload["triangles"]
    for tri in triangles:
        p0, p1, p2 = [projected2[j] for j in tri]
        parts.append(polygon(f"{p0[0]},{p0[1]} {p1[0]},{p1[1]} {p2[0]},{p2[1]}", fill="#BCE9F5", stroke=BLUE, sw=0.8, opacity=0.15))
    for px, py in projected2:
        parts.append(circle(px, py, 3.4, fill=BLUE))
    parts += [
        txt(x + 28, 696, f"{boundary_payload['hull_vertex_count']} hull vertices", size=21, fill=INK, family=SANS, weight=700),
        txt(x + 28, 728, f"{boundary_payload['triangle_count']} triangular faces", size=17, fill=MUTED, family=SANS),
        txt(x + 28, 759, "safety margin: 2.0", size=17, fill=MUTED, family=SANS),
    ]

    # Panel C: five real layers and retained positions.
    x = panel_xs[2]
    colors = ["#2D9D78", "#30A9C7", "#4C83D6", "#7868C7", "#D17FB0"]
    layer_groups: dict[int, list[dict[str, str]]] = {}
    for row in positions:
        layer_groups.setdefault(int(row["layer_index"]), []).append(row)
    for layer_idx, rows in sorted(layer_groups.items(), reverse=True):
        base_y = 315 + (5 - layer_idx) * 72
        parts.append(polygon(f"{x+48},{base_y+30} {x+275},{base_y+30} {x+337},{base_y-5} {x+110},{base_y-5}", fill=colors[layer_idx-1], stroke=colors[layer_idx-1], sw=1, opacity=0.12))
        for row in rows:
            nx = (float(row["x"]) - scan["bounds"]["x_min"]) / (scan["bounds"]["x_max"] - scan["bounds"]["x_min"])  # type: ignore[index]
            ny = (float(row["y"]) - scan["bounds"]["y_min"]) / (scan["bounds"]["y_max"] - scan["bounds"]["y_min"])  # type: ignore[index]
            px = x + 62 + nx * 245 + ny * 35
            py = base_y + 25 - ny * 28
            parts.append(circle(px, py, 3.1, fill=colors[layer_idx-1], opacity=0.9))
        parts.append(txt(x + 30, base_y + 17, f"L{layer_idx}", size=14, fill=colors[layer_idx-1], family=SANS, weight=700))
    parts += [
        txt(x + 28, 696, f"{scan['layer_count']} layers / {scan['spatial_point_count']} poses", size=21, fill=INK, family=SANS, weight=700),
        txt(x + 28, 728, "counts: 17, 36, 38, 29, 11", size=17, fill=MUTED, family=SANS),
        txt(x + 28, 759, "serpentine layer ordering", size=17, fill=MUTED, family=SANS),
    ]

    # Panel D: orientation fan and output cards.
    x = panel_xs[3]
    cx, cy = x + 194, 430
    parts.append(circle(cx, cy, 12, fill=INK))
    for yaw in range(0, 360, 45):
        rad = math.radians(yaw)
        ex, ey = cx + 125 * math.cos(rad), cy + 84 * math.sin(rad)
        parts += [line(cx, cy, ex, ey, stroke=BLUE, sw=2, marker="url(#arrow-blue)", opacity=0.8), circle(ex, ey, 5, fill=BLUE)]
    for index, (pitch, color) in enumerate([("+90", PURPLE), ("+45", GREEN), ("0", BLUE), ("-45", ORANGE), ("-90", RED)]):
        yy = 572 + index * 25
        parts += [circle(x + 42, yy, 5, fill=color), txt(x + 58, yy + 5, f"pitch {pitch} deg", size=14, fill=MUTED, family=SANS)]
    parts += [
        rect(x + 190, 563, 160, 119, fill=PALE_BLUE, stroke="#B8DAE6", sw=1, rx=10),
        txt(x + 270, 596, "22 views", size=25, fill=BLUE, family=SANS, weight=700, anchor="middle"),
        txt(x + 270, 628, "per spatial pose", size=15, fill=MUTED, family=SANS, anchor="middle"),
        txt(x + 270, 661, "FOV 63 deg", size=15, fill=MUTED, family=SANS, anchor="middle"),
        txt(x + 28, 721, f"{scan['image_count']:,} planned images", size=24, fill=INK, family=SANS, weight=700),
        txt(x + 28, 757, "positions x orientations", size=17, fill=MUTED, family=SANS),
    ]
    parts += [
        rect(70, 835, 1660, 70, fill=PALE_BLUE, stroke="#B9DBE6", sw=1.2, rx=12),
        txt(900, 867, "27 unique boundary XYZ  ->  19 hull vertices  ->  131 safe positions  ->  2,882 explicit camera samples", size=22, fill="#0B6888", family=SANS, weight=700, anchor="middle"),
        txt(900, 893, "All counts and layer heights are read directly from scene_1_5layer_hull_plan.json.", size=14, fill=MUTED, family=SANS, anchor="middle", italic=True),
        footer("Pure vector, data-derived sampling figure"),
    ]
    return document("KCD2 layered scene sampling", "Four-panel method figure showing boundary poses, a convex hull, five-layer point packing, and multi-view orientation allocation using committed KCD2 scan-plan data.", "".join(parts))


def keyframe_sequence() -> str:
    frames: list[Path] = DATA["frames"]  # type: ignore[assignment]
    times = [6.5, 7.8, 9.2, 10.6, 11.5, 12.3]
    parts = [title_band("FIGURE 05 / RECORDED CAMERA SEQUENCE", "A Real KCD2 Free-Camera Move from Water to the Castle", "Six frames sampled from the 13.4 s recorded demo; this video has no synchronized pose log, so the sequence is illustrative rather than pose-aligned.")]
    frame_w, frame_h = 260, 310
    clips = []
    for index in range(6):
        x = 70 + index * 286
        clips.append(crop_clip(f"seq-{index}", x, 228, frame_w, frame_h, 8))
        parts += [
            image_tag(frames[index], x, 228, frame_w, frame_h, clip=f"url(#seq-{index})"),
            rect(x, 228, frame_w, frame_h, fill="none", stroke="#FFFFFF", sw=3, rx=8),
            rect(x + 12, 242, 76, 30, fill="#07192A", rx=15, opacity=0.78),
            txt(x + 50, 263, f"t={times[index]:.1f}s", size=13, fill="#FFFFFF", family=SANS, weight=700, anchor="middle"),
            txt(x + frame_w / 2, 572, f"Frame {index + 1}", size=18, fill=INK, family=SANS, weight=700, anchor="middle"),
        ]
        if index < 5:
            parts.append(line(x + frame_w + 5, 384, x + frame_w + 23, 384, stroke=BLUE, sw=3, marker="url(#arrow-blue)"))
    # Narrative axis.
    parts += [
        line(100, 653, 1700, 653, stroke="#9CCDDD", sw=5),
        circle(100, 653, 11, fill=BLUE), circle(1700, 653, 11, fill=GREEN),
        txt(100, 690, "water-dominant view", size=18, fill=BLUE, family=SANS, weight=700),
        txt(1700, 690, "stable castle composition", size=18, fill=GREEN, family=SANS, weight=700, anchor="end"),
    ]
    observations = [
        (200, "tilt / translation", "subject begins to enter"),
        (585, "reveal", "castle becomes dominant"),
        (970, "reframe", "horizon and facade stabilize"),
        (1360, "settle", "hero view is held"),
    ]
    for ox, name, note in observations:
        parts += [
            line(ox, 653, ox, 727, stroke="#91A5B7", sw=1.5, dash="5 5"),
            txt(ox, 754, name, size=18, fill=INK, family=SANS, weight=700, anchor="middle"),
            txt(ox, 781, note, size=14, fill=MUTED, family=SANS, anchor="middle"),
        ]
    parts += [
        rect(70, 827, 1660, 78, fill="#FFF8F0", stroke="#EDCBA8", sw=1.2, rx=12),
        txt(95, 859, "Evidence boundary", size=17, fill="#A75A1E", family=SANS, weight=700),
        txt(95, 886, "The frames are genuine video samples. No claim is made that they correspond to the offline trajectory keyframes or to exact XYZ/yaw/pitch values.", size=15, fill="#795B45", family=SANS),
        footer("Hybrid vector layout + six raster video frames"),
    ]
    return document("KCD2 recorded camera sequence", "Six real frames from the KCD2 demo video laid out as an illustrative camera sequence with an explicit evidence boundary.", "".join(parts), extra_defs="".join(clips))


def trajectory_pose() -> str:
    trajectory: dict[str, object] = DATA["trajectory"]  # type: ignore[assignment]
    keyframes: list[dict[str, object]] = trajectory["keyframes"]  # type: ignore[assignment]
    parts = [title_band("FIGURE 06 / POSE-TRAJECTORY REPRESENTATION", "Measured Pose Keyframes Define an Auditable Camera Path", "Trajectory 00750 is selected because it has the maximum measured score gain in the 1000-trajectory set.")]
    # Left: top-view trajectory.
    parts += [
        txt(70, 213, "(a)", size=17, fill=BLUE, family=SANS, weight=700), txt(105, 213, "top-view path and orientation", size=22, weight=700),
        rect(70, 232, 1010, 570, fill="#FFFFFF", stroke=GRID, sw=1.5),
    ]
    xs = [float(k["x"]) for k in keyframes]; ys = [float(k["y"]) for k in keyframes]
    xmin, xmax = min(xs), max(xs); ymin, ymax = min(ys), max(ys)
    pad_x = max(1, (xmax - xmin) * 0.12); pad_y = max(1, (ymax - ymin) * 0.12)
    xmin -= pad_x; xmax += pad_x; ymin -= pad_y; ymax += pad_y
    def map_xy(px: float, py: float) -> tuple[float, float]:
        return 130 + (px - xmin) / (xmax - xmin) * 860, 742 - (py - ymin) / (ymax - ymin) * 430
    points = [map_xy(float(k["x"]), float(k["y"])) for k in keyframes]
    for frac in [0, 0.25, 0.5, 0.75, 1]:
        gx = 130 + frac * 860; gy = 312 + frac * 430
        parts += [line(gx, 312, gx, 742, stroke=GRID, sw=1), line(130, gy, 990, gy, stroke=GRID, sw=1)]
    polyline = " ".join(f"{x},{y}" for x, y in points)
    parts.append(f'<polyline points="{polyline}" fill="none" stroke="{BLUE}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/>')
    scores = [float(k["score"]) for k in keyframes]
    slo, shi = min(scores), max(scores)
    for index, ((px, py), k) in enumerate(zip(points, keyframes)):
        score = float(k["score"]); t = (score - slo) / (shi - slo)
        color = f"rgb({int(48 + 176*t)},{int(167 - 80*t)},{int(203 - 135*t)})"
        parts += [circle(px, py, 12 if index in {0, len(points)-1} else 8, fill=color, stroke="#FFFFFF", sw=2), txt(px, py - 18, str(index + 1), size=13, fill=INK, family=SANS, weight=700, anchor="middle")]
        yaw = math.radians(float(k["yaw"]))
        parts.append(line(px, py, px + 38 * math.cos(yaw), py - 38 * math.sin(yaw), stroke=color, sw=2, marker="url(#arrow-gray)"))
    parts += [
        txt(130, 780, "world x", size=16, fill=MUTED, family=SANS), txt(990, 780, f"{xmin:.1f} to {xmax:.1f}", size=14, fill=MUTED, family=SANS, anchor="end"),
        txt(95, 500, "world y", size=16, fill=MUTED, family=SANS, anchor="middle"),
    ]
    # Right: keyframe table / score progression.
    parts += [
        txt(1135, 213, "(b)", size=17, fill=BLUE, family=SANS, weight=700), txt(1170, 213, "measured pose sequence", size=22, weight=700),
        rect(1135, 232, 595, 570, fill="#FFFFFF", stroke=GRID, sw=1.5),
        rect(1155, 254, 555, 47, fill=LIGHT_BLUE, stroke="none", rx=7),
    ]
    headers = [(1172, "k"), (1215, "t(s)"), (1282, "x"), (1357, "y"), (1432, "z"), (1507, "yaw"), (1578, "pitch"), (1668, "score")]
    for hx, label in headers:
        parts.append(txt(hx, 284, label, size=14, fill="#0B6888", family=SANS, weight=700, anchor="middle"))
    for index, k in enumerate(keyframes):
        yy = 330 + index * 42
        if index % 2 == 0:
            parts.append(rect(1155, yy - 25, 555, 36, fill="#F8FBFD"))
        values = [
            str(index + 1), f"{float(k['time_sec']):.1f}", f"{float(k['x']):.1f}", f"{float(k['y']):.1f}", f"{float(k['z']):.1f}",
            f"{float(k['yaw']):.0f}", f"{float(k['pitch']):.0f}", f"{float(k['score']):.2f}",
        ]
        for (hx, _), value in zip(headers, values):
            parts.append(txt(hx, yy, value, size=13.5, fill=INK, family="Consolas", anchor="middle"))
    parts += [
        line(1160, 737, 1705, 737, stroke=GRID, sw=1),
        txt(1160, 767, f"gain  {trajectory['score_gain']:.3f}", size=18, fill=GREEN, family=SANS, weight=700),
        txt(1335, 767, f"duration  {trajectory['duration_sec']:.2f} s", size=18, fill=BLUE, family=SANS, weight=700),
        txt(1555, 767, f"length  {trajectory['path_xyz_length']:.1f}", size=18, fill=PURPLE, family=SANS, weight=700),
    ]
    parts += [
        rect(70, 835, 1660, 70, fill=PALE_BLUE, stroke="#B9DBE6", sw=1.2, rx=12),
        txt(94, 866, "Offline evidence", size=17, fill="#0B6888", family=SANS, weight=700),
        txt(94, 892, "All nine control poses are real scored source poses and strictly increase to score 7.101753. Smooth runtime frames and collision clearance are not established by this figure.", size=15, fill=MUTED, family=SANS),
        footer("Pure vector, pose values from trajectory JSON"),
    ]
    return document("KCD2 measured pose trajectory", "A pure-vector top-view pose path and keyframe table for the maximum-gain KCD2 trajectory, using values read directly from the committed trajectory JSON.", "".join(parts))


FIGURES = {
    "openfly_01_multigame_teaser.svg": teaser,
    "openfly_02_toolchain_framework.svg": pipeline,
    "openfly_03_kcd2_statistics.svg": empirical_stats,
    "openfly_04_layered_sampling.svg": layered_scan,
    "openfly_05_recorded_frame_sequence.svg": keyframe_sequence,
    "openfly_06_pose_trajectory.svg": trajectory_pose,
}


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    for filename, builder in FIGURES.items():
        target = ROOT / filename
        target.write_text(builder(), encoding="utf-8", newline="\n")
        print(target)


if __name__ == "__main__":
    main()
