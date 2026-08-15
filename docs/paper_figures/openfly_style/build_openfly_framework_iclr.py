from __future__ import annotations

from pathlib import Path

from build_openfly_v2_figures import (
    H,
    W,
    circle,
    clip,
    document,
    image,
    line,
    path,
    rect,
    txt,
)


ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
ASSETS = ROOT / "assets" / "v2"
SPATIAL = ASSETS / "spatialvid_samples"
FRAMES = SPATIAL / "frames"
DOC_ASSETS = REPO / "docs" / "assets"
OUTPUT = ROOT / "openfly_10_multigame_capture_framework_iclr.svg"

INK = "#12263B"
MUTED = "#587086"
BLUE = "#1597C3"
CYAN = "#35C8E6"
GREEN = "#39AD7A"
AMBER = "#ECA338"
MAGENTA = "#D764BE"
PURPLE = "#8174D6"
GRID = "#BED8E4"
PALE = "#EAF6FB"


def group(content: str, *, transform: str | None = None,
          opacity: float | None = None, filter_id: str | None = None) -> str:
    values: list[str] = []
    if transform:
        values.append(f'transform="{transform}"')
    if opacity is not None:
        values.append(f'opacity="{opacity}"')
    if filter_id:
        values.append(f'filter="url(#{filter_id})"')
    return f"<g {' '.join(values)}>{content}</g>"


def pill(x: float, y: float, w: float, label: str, color: str,
         *, dark_text: bool = False) -> str:
    return "".join([
        rect(x, y, w, 25, fill=color, rx=12.5, opacity=0.94),
        txt(x + w / 2, y + 17.5, label, size=14.2,
            fill=INK if dark_text else "#FFFFFF", weight=700,
            anchor="middle", letter=0.45),
    ])


def engine_card(x: float, y: float, code: str, title: str, status: str,
                color: str, status_color: str) -> str:
    return "".join([
        rect(x, y, 126, 112, fill="#FFFFFF", stroke=GRID, sw=1.1,
             rx=13),
        circle(x + 63, y + 35, 24, fill=color, opacity=0.14),
        circle(x + 63, y + 35, 23, fill="none", stroke=color, sw=1.5),
        txt(x + 63, y + 42, code, size=16.5, fill=color, weight=700,
            anchor="middle", letter=0.3),
        txt(x + 63, y + 76, title, size=15.5, fill=INK, weight=700,
            anchor="middle"),
        pill(x + 13, y + 84, 100, status, status_color,
             dark_text=status_color in ("#D8E9F0", "#F4E8C9")),
    ])


def interface_cell(x: float, y: float, number: str, title: str,
                   subtitle: str, color: str, glyph: str) -> str:
    return "".join([
        rect(x, y, 252, 90, fill="#FFFFFF", stroke=GRID, sw=1.15,
             rx=12),
        circle(x + 29, y + 28, 17, fill=color),
        txt(x + 29, y + 34, number, size=15, fill="#FFFFFF", weight=700,
            anchor="middle"),
        txt(x + 55, y + 25, title, size=18, fill=INK, weight=700),
        txt(x + 55, y + 48, subtitle, size=14.3, fill=MUTED),
        txt(x + 226, y + 68, glyph, size=24, fill=color, weight=700,
            anchor="end", family="Consolas", opacity=0.76),
    ])


def stage_header(x: float, y: float, number: str, title: str,
                 color: str) -> str:
    return "".join([
        circle(x, y, 17, fill=color),
        txt(x, y + 6, number, size=15, fill="#FFFFFF", weight=700,
            anchor="middle"),
        txt(x + 28, y + 6, title, size=20, fill=INK, weight=700),
    ])


def check(x: float, y: float, label: str, color: str = GREEN) -> str:
    return "".join([
        circle(x, y - 4, 7, fill="#E9F7F0", stroke=color, sw=1.2),
        path(f"M{x-3.5},{y-4} l2.5,2.6 l5,-6", stroke=color, sw=1.6),
        txt(x + 14, y, label, size=14.5, fill=MUTED),
    ])


def wire_camera(x: float, y: float, angle: float, color: str,
                scale: float = 1.0) -> str:
    body = "".join([
        f'<path d="M0,0 L23,-13 L45,-9 L45,9 L23,13 Z M23,-13 L23,13 '
        f'M23,-13 L45,9 M23,13 L45,-9" fill="none" stroke="{color}" '
        'stroke-width="1.2" stroke-linecap="round" '
        'stroke-linejoin="round"/>',
        '<circle cx="0" cy="0" r="2" fill="#FFFFFF" '
        f'stroke="{color}" stroke-width="1"/>',
    ])
    return group(body,
                 transform=f"translate({x} {y}) rotate({angle}) scale({scale})",
                 opacity=0.54,
                 filter_id="m-glow")


def thin_route(d: str, color: str,
               nodes: list[tuple[float, float]],
               cameras: list[tuple[float, float, float]]) -> str:
    result = [
        path(d, stroke=color, sw=7, opacity=0.10,
             filter_="url(#m-route-blur)"),
        path(d, stroke=color, sw=2.15, opacity=0.69),
        path(d, stroke="#FFFFFF", sw=0.65, opacity=0.53),
    ]
    for index, (x, y) in enumerate(nodes):
        result += [
            circle(x, y, 5.6 if index in (0, len(nodes)-1) else 3.5,
                   fill="#FFFFFF", stroke=color, sw=1.25, opacity=0.92),
            circle(x, y, 1.3, fill="#FFFFFF"),
        ]
    for x, y, angle in cameras:
        result.append(wire_camera(x, y, angle, color, 0.62))
    return "".join(result)


def field_pill(x: float, y: float, w: float, title: str,
               subtitle: str, color: str) -> str:
    return "".join([
        rect(x, y, w, 50, fill="#FFFFFF", stroke="#B9D5E0", sw=1,
             rx=10),
        rect(x, y, 5, 50, fill=color, rx=2),
        txt(x + 17, y + 21, title, size=16.5, fill=INK, weight=700),
        txt(x + 17, y + 40, subtitle, size=13.2, fill=MUTED),
    ])


def framework() -> str:
    real_video = FRAMES / "group_0012_mid.jpg"
    re9_ui = DOC_ASSETS / "interface-overview.png"
    kcd2_frame = DOC_ASSETS / "game-camera-capture-demo-poster.jpg"
    bmw_concept = ASSETS / "game-fantasy-concept.png"
    trajectory_ui = DOC_ASSETS / "trajectory-replay.png"
    dataset_ui = DOC_ASSETS / "dataset-preview.png"
    scene_3d = SPATIAL / "3dgs" / "preview_01.png"
    required = [
        real_video, re9_ui, kcd2_frame, bmw_concept,
        trajectory_ui, dataset_ui, scene_3d,
    ]
    missing = [str(item) for item in required if not item.exists()]
    if missing:
        raise FileNotFoundError("Missing framework assets: " + ", ".join(missing))

    source_x = [72, 210, 348, 486]
    clips = "".join([
        clip("m-src-0", source_x[0], 284, 126, 90, 8),
        clip("m-src-1", source_x[1], 284, 126, 90, 8),
        clip("m-src-2", source_x[2], 284, 126, 90, 8),
        clip("m-src-3", source_x[3], 284, 126, 90, 8),
        clip("m-exec", 1332, 188, 376, 164, 10),
        clip("m-out-0", 730, 569, 67, 55, 6),
        clip("m-out-1", 810, 569, 67, 55, 6),
        clip("m-out-2", 890, 569, 67, 55, 6),
    ])
    extra_defs = clips + """
  <linearGradient id="m-left" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#EAF7FC"/>
    <stop offset="1" stop-color="#F7FCFE"/>
  </linearGradient>
  <linearGradient id="m-right" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#EDF8FC"/>
    <stop offset="1" stop-color="#F5FBFD"/>
  </linearGradient>
  <linearGradient id="m-record" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#E3F5FB"/>
    <stop offset="0.52" stop-color="#F8FCFD"/>
    <stop offset="1" stop-color="#EAF7EE"/>
  </linearGradient>
  <filter id="m-route-blur" x="-25%" y="-40%" width="150%" height="180%">
    <feGaussianBlur stdDeviation="3.8"/>
  </filter>
  <filter id="m-glow" x="-80%" y="-80%" width="260%" height="260%">
    <feGaussianBlur stdDeviation="1.2" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <marker id="m-arrow" markerWidth="10" markerHeight="10" refX="8.5" refY="5" orient="auto">
    <path d="M0,0 L10,5 L0,10 Z" fill="#1597C3"/>
  </marker>
  <marker id="m-arrow-left" markerWidth="10" markerHeight="10" refX="1.5" refY="5" orient="auto">
    <path d="M10,0 L0,5 L10,10 Z" fill="#1597C3"/>
  </marker>
    """

    parts: list[str] = [
        rect(0, 0, W, H, fill="#FFFFFF"),
        # Left block: sources and a capability-aware common interface.
        rect(30, 30, 615, 752, fill="url(#m-left)", stroke="#91CCE1",
             sw=2, rx=23),
        txt(337, 73, "Source environments", size=29, fill=INK,
            weight=700, anchor="middle", family="Times New Roman",
            italic=True),
        txt(337, 102, "heterogeneous engines, data, and control semantics",
            size=16, fill=MUTED, anchor="middle", italic=True),
    ]

    engine_specs = [
        (72, "WEB", "Internet", "ESTIMATED", CYAN, "#D8E9F0"),
        (210, "RE", "RE Engine", "STABLE", GREEN, GREEN),
        (348, "CRY", "CryEngine", "BETA", BLUE, BLUE),
        (486, "UE5", "Unreal 5", "EXPERIMENT", PURPLE, PURPLE),
    ]
    for x, code, title, status, color, status_color in engine_specs:
        parts.append(engine_card(x, 126, code, title, status, color,
                                 status_color))

    source_specs = [
        (source_x[0], real_video, "real video", "estimated pose", CYAN),
        (source_x[1], re9_ui, "RE9 adapter", "verified workflow", GREEN),
        (source_x[2], kcd2_frame, "KCD2 frame", "relative control", BLUE),
        (source_x[3], bmw_concept, "Black Myth", "concept / experimental", PURPLE),
    ]
    for index, (x, source, title, subtitle, color) in enumerate(source_specs):
        parts += [
            image(source, x, 284, 126, 90, clip=f"url(#m-src-{index})"),
            rect(x, 346, 126, 28, fill="#061A29", opacity=0.76,
                 clip=f"url(#m-src-{index})"),
            rect(x, 346, 4, 28, fill=color),
            txt(x + 10, 358, title.upper(), size=9.6, fill="#FFFFFF",
                weight=700, letter=0.4),
            txt(x + 10, 370, subtitle, size=8.8, fill="#D8E8EF"),
        ]

    parts += [
        txt(337, 421, "Unified adapter interface", size=25, fill=INK,
            weight=700, anchor="middle", family="Times New Roman",
            italic=True),
        interface_cell(72, 447, "1", "Pose I/O",
                       "6-DoF + FOV + status", CYAN, "SE(3)"),
        interface_cell(348, 447, "2", "Frame capture",
                       "still / video + timestamp", GREEN, "RGB"),
        interface_cell(72, 554, "3", "Point plans",
                       "boundary / layers / views", BLUE, "XYZ"),
        interface_cell(348, 554, "4", "Camera paths",
                       "keyframes / feedback / replay", MAGENTA, "PATH"),
        rect(72, 672, 528, 72, fill="#FFFFFF", stroke=GRID, sw=1.15,
             rx=12),
        txt(90, 699, "COMMON CONTRACT, EXPLICIT CAPABILITIES", size=14.5,
            fill=BLUE, weight=700, letter=0.75),
        txt(90, 724,
            "The schema is shared; native setPose, relative control, and estimated pose remain distinct.",
            size=12.8, fill=MUTED),
        # Transition into the toolchain.
        line(645, 405, 674, 405, stroke=BLUE, sw=3,
             marker="url(#m-arrow)"),
        # Right block: OpenFly Figure 2-like snake toolchain.
        rect(675, 30, 1095, 752, fill="url(#m-right)",
             stroke="#80C6DF", sw=2.2, rx=23),
        txt(1222, 73, "Automatic camera-data toolchain", size=30,
            fill=INK, weight=700, anchor="middle",
            family="Times New Roman", italic=True),
        txt(1222, 102,
            "pose-aware planning, synchronized capture, and auditable export",
            size=16, fill=MUTED, anchor="middle", italic=True),
    ]

    # Six cards form a readable snake: 1 -> 2 -> 3, down, 4 -> 5 -> 6.
    cards = [
        (700, 128, 272, 280),
        (997, 128, 288, 280),
        (1310, 128, 430, 280),
        (1310, 451, 430, 286),
        (997, 451, 288, 286),
        (700, 451, 272, 286),
    ]
    for x, y, w, h in cards:
        parts.append(rect(x, y, w, h, fill="#FFFFFF", stroke=GRID,
                          sw=1.25, rx=14))

    parts += [
        stage_header(729, 161, "1", "Read + verify", CYAN),
        txt(727, 205, "pose", size=14.5, fill=MUTED, weight=700),
        txt(865, 205, "[x y z qx qy qz qw]", size=13.2, fill=INK,
            anchor="middle", family="Consolas"),
        txt(727, 237, "FOV", size=14.5, fill=MUTED, weight=700),
        txt(936, 237, "58.2 deg", size=13.2, fill=INK,
            anchor="end", family="Consolas"),
        txt(727, 269, "status", size=14.5, fill=MUTED, weight=700),
        pill(823, 250, 115, "MEASURED", GREEN),
        line(731, 316, 912, 316, stroke="#CADCE4", sw=1.1),
        circle(749, 343, 4, fill=CYAN),
        circle(785, 333, 4, fill=CYAN),
        circle(821, 347, 4, fill=CYAN),
        circle(857, 326, 4, fill=CYAN),
        circle(893, 340, 4, fill=CYAN),
        path("M749,343 L785,333 L821,347 L857,326 L893,340",
             stroke=CYAN, sw=1.6, opacity=0.72),
        txt(836, 380, "handshake + confidence", size=13.8, fill=MUTED,
            anchor="middle"),

        stage_header(1026, 161, "2", "Plan views", AMBER),
    ]
    for yy, color, label in [
        (220, MAGENTA, "L4"),
        (258, PURPLE, "L3"),
        (296, BLUE, "L2"),
        (334, CYAN, "L1"),
    ]:
        parts += [
            path(f"M1053,{yy} L1197,{yy-17} L1227,{yy+3} L1083,{yy+20} Z",
                 fill=color, opacity=0.15, stroke=color, sw=0.6),
            txt(1039, yy + 8, label, size=12.2, fill=color, weight=700,
                anchor="middle"),
        ]
        for index in range(5):
            parts.append(circle(1093 + index * 25, yy + 8 - index * 3,
                                3.3, fill=color))
    parts.append(txt(1141, 380, "boundary -> layers -> directions",
                     size=13.6, fill=MUTED, anchor="middle"))

    parts += [
        stage_header(1339, 161, "3", "Execute path", MAGENTA),
        image(kcd2_frame, 1332, 188, 376, 164, clip="url(#m-exec)"),
        rect(1332, 188, 376, 164, fill="#061624", opacity=0.06,
             clip="url(#m-exec)"),
        thin_route(
            "M1349,331 C1413,296 1452,311 1502,269 C1557,223 1615,249 1690,195",
            CYAN,
            [(1354,329),(1420,294),(1502,269),(1581,238),(1684,200)],
            [(1453,301,-19),(1582,238,-20)],
        ),
        rect(1332, 352, 376, 38, fill="#071A29", rx=0, opacity=0.80),
        txt(1350, 376, "KCD2 REAL FRAME", size=13.4, fill="#FFFFFF",
            weight=700, letter=0.6),
        txt(1692, 376, "relative path control", size=12.2,
            fill="#D5E8F0", anchor="end"),

        stage_header(1339, 484, "4", "Synchronize", BLUE),
        txt(1340, 526, "RGB", size=13.5, fill=INK, weight=700),
        txt(1340, 579, "POSE", size=13.5, fill=INK, weight=700),
    ]
    for index in range(7):
        xx = 1407 + index * 42
        parts += [
            line(xx, 515, xx, 606, stroke="#B8CBD5", sw=0.7,
                 dash="3 4", opacity=0.64),
            circle(xx, 522 + (index % 2) * 4, 4.2, fill=CYAN),
            circle(xx, 575 - (index % 3) * 3, 4.2, fill=GREEN),
        ]
    parts += [
        line(1407, 524, 1659, 526, stroke=CYAN, sw=1.7),
        line(1407, 575, 1659, 570, stroke=GREEN, sw=1.7),
        rect(1362, 626, 316, 54, fill="#F3FAFC", stroke="#C3DCE5",
             sw=1, rx=9),
        txt(1520, 648, "nearest-time match + interpolation guard",
            size=13.3, fill=INK, weight=700, anchor="middle"),
        txt(1520, 668, "dropped frames remain auditable",
            size=12.4, fill=MUTED, anchor="middle"),

        stage_header(1026, 484, "5", "Validate", GREEN),
        check(1038, 530, "pose residual"),
        check(1038, 564, "frame alignment"),
        check(1038, 598, "source rights"),
        check(1038, 632, "schema validity"),
        rect(1034, 666, 210, 34, fill="#EAF7F1", stroke="#B9DEC9",
             sw=1, rx=9),
        txt(1139, 688, "PASS / FLAG / REJECT", size=13.5,
            fill=GREEN, weight=700, anchor="middle", letter=0.55),

        stage_header(729, 484, "6", "Package data", PURPLE),
        image(real_video, 730, 569, 67, 55, clip="url(#m-out-0)"),
        image(kcd2_frame, 810, 569, 67, 55, clip="url(#m-out-1)"),
        image(scene_3d, 890, 569, 67, 55, clip="url(#m-out-2)"),
        txt(836, 651, "frames / videos / 3D scenes", size=13.4,
            fill=MUTED, anchor="middle"),
        field_pill(721, 670, 113, "poses", ".csv / .json", CYAN),
        field_pill(844, 670, 113, "manifest", "schema + rights", PURPLE),
    ]

    # Snake arrows: top left-to-right, down, then bottom right-to-left.
    parts += [
        line(974, 270, 990, 270, stroke=BLUE, sw=2.7,
             marker="url(#m-arrow)"),
        line(1287, 270, 1303, 270, stroke=BLUE, sw=2.7,
             marker="url(#m-arrow)"),
        line(1721, 410, 1721, 442, stroke=BLUE, sw=2.7,
             marker="url(#m-arrow)"),
        line(1304, 594, 1290, 594, stroke=BLUE, sw=2.7,
             marker="url(#m-arrow)"),
        line(991, 594, 977, 594, stroke=BLUE, sw=2.7,
             marker="url(#m-arrow)"),
        # Unified record strip, mirroring OpenFly's common interface contract.
        rect(98, 814, 1604, 122, fill="url(#m-record)",
             stroke="#8FCBDD", sw=1.8, rx=18),
        txt(126, 850, "Unified camera-aware record", size=24, fill="#126E8A",
            weight=700, family="Times New Roman", italic=True),
    ]

    record_fields = [
        (126, 104, "frame", "RGB / video", CYAN),
        (242, 112, "time", "timestamp", BLUE),
        (366, 149, "intrinsics", "FOV / K", AMBER),
        (527, 151, "camera pose", "SE(3)", GREEN),
        (690, 164, "pose status", "measured / est.", MAGENTA),
        (866, 147, "adapter", "engine ID", PURPLE),
        (1025, 177, "task", "point / path", CYAN),
        (1214, 171, "quality", "checks / score", GREEN),
        (1397, 272, "provenance", "source / license / config", AMBER),
    ]
    for x, width, title, subtitle, color in record_fields:
        parts.append(field_pill(x, 865, width, title, subtitle, color))

    parts += [
        txt(900, 962,
            "A shared schema normalizes outputs without erasing engine-specific capability and pose provenance.",
            size=13.5, fill=MUTED, anchor="middle", italic=True),
    ]

    return document(
        "OpenFly-style multi-game camera capture framework",
        "An ICLR-oriented method figure showing heterogeneous source environments, a capability-aware adapter interface, a six-stage automatic capture toolchain, and a unified camera-aware data record.",
        "".join(parts),
        extra_defs=extra_defs,
        background="#FFFFFF",
    )


def main() -> None:
    OUTPUT.write_text(framework(), encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
