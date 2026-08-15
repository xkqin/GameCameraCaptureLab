from __future__ import annotations

from html import escape
from pathlib import Path


ROOT = Path(__file__).resolve().parent
W, H = 1600, 900


def attrs(**values: object) -> str:
    rendered: list[str] = []
    for key, value in values.items():
        if value is None:
            continue
        key = key.rstrip("_").replace("_", "-")
        rendered.append(f'{key}="{escape(str(value), quote=True)}"')
    return " ".join(rendered)


def rect(
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    fill: str = "none",
    stroke: str = "none",
    stroke_width: float = 1,
    rx: float = 0,
    opacity: float | None = None,
    cls: str | None = None,
) -> str:
    attributes = attrs(
        x=x,
        y=y,
        width=width,
        height=height,
        rx=rx,
        fill=fill,
        stroke=stroke,
        stroke_width=stroke_width,
        opacity=opacity,
        class_=cls,
    )
    return f'<rect {attributes}/>'


def circle(
    cx: float,
    cy: float,
    r: float,
    *,
    fill: str = "none",
    stroke: str = "none",
    stroke_width: float = 1,
    opacity: float | None = None,
    cls: str | None = None,
) -> str:
    attributes = attrs(
        cx=cx,
        cy=cy,
        r=r,
        fill=fill,
        stroke=stroke,
        stroke_width=stroke_width,
        opacity=opacity,
        class_=cls,
    )
    return f'<circle {attributes}/>'


def line(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    stroke: str,
    stroke_width: float = 2,
    dash: str | None = None,
    marker_end: str | None = None,
    opacity: float | None = None,
) -> str:
    attributes = attrs(
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
        stroke=stroke,
        stroke_width=stroke_width,
        stroke_dasharray=dash,
        marker_end=marker_end,
        opacity=opacity,
        fill="none",
    )
    return f'<line {attributes}/>'


def path(
    d: str,
    *,
    fill: str = "none",
    stroke: str = "none",
    stroke_width: float = 1,
    dash: str | None = None,
    marker_end: str | None = None,
    opacity: float | None = None,
) -> str:
    attributes = attrs(
        d=d,
        fill=fill,
        stroke=stroke,
        stroke_width=stroke_width,
        stroke_dasharray=dash,
        marker_end=marker_end,
        opacity=opacity,
    )
    return f'<path {attributes}/>'


def polygon(
    points: str,
    *,
    fill: str = "none",
    stroke: str = "none",
    stroke_width: float = 1,
    opacity: float | None = None,
) -> str:
    attributes = attrs(
        points=points,
        fill=fill,
        stroke=stroke,
        stroke_width=stroke_width,
        opacity=opacity,
    )
    return f'<polygon {attributes}/>'


def text(
    x: float,
    y: float,
    value: str,
    *,
    size: float = 24,
    fill: str = "#12233A",
    weight: int = 400,
    anchor: str = "start",
    family: str = "Times New Roman",
    italic: bool = False,
    opacity: float | None = None,
    letter_spacing: float | None = None,
) -> str:
    attributes = attrs(
        x=x,
        y=y,
        font_size=size,
        fill=fill,
        font_weight=weight,
        text_anchor=anchor,
        font_family=family,
        font_style="italic" if italic else None,
        opacity=opacity,
        letter_spacing=letter_spacing,
    )
    return f'<text {attributes}>{escape(value)}</text>'


def multiline(
    x: float,
    y: float,
    lines: list[str],
    *,
    size: float = 22,
    fill: str = "#12233A",
    weight: int = 400,
    anchor: str = "start",
    line_height: float = 1.32,
    family: str = "Times New Roman",
) -> str:
    spans = []
    for index, value in enumerate(lines):
        dy = 0 if index == 0 else size * line_height
        spans.append(
            f'<tspan x="{x}" dy="{dy}">{escape(value)}</tspan>'
        )
    attributes = attrs(
        x=x,
        y=y,
        font_size=size,
        fill=fill,
        font_weight=weight,
        text_anchor=anchor,
        font_family=family,
    )
    return f'<text {attributes}>{"".join(spans)}</text>'


def pill(
    x: float,
    y: float,
    width: float,
    label: str,
    *,
    fill: str,
    text_fill: str,
    stroke: str = "none",
) -> str:
    return "".join(
        [
            rect(x, y, width, 34, fill=fill, stroke=stroke, rx=17),
            text(x + width / 2, y + 23, label, size=17, fill=text_fill, weight=600, anchor="middle"),
        ]
    )


def camera_frustum(
    x: float,
    y: float,
    *,
    scale: float = 1,
    color: str = "#13B8D4",
    angle: float = 0,
    opacity: float = 1,
) -> str:
    body = rect(-16, -11, 32, 22, fill="none", stroke=color, stroke_width=2, rx=4)
    lens = circle(0, 0, 5, fill="none", stroke=color, stroke_width=2)
    rays = "".join(
        [
            line(16, -9, 58, -30, stroke=color, stroke_width=2),
            line(16, 9, 58, 30, stroke=color, stroke_width=2),
            line(58, -30, 58, 30, stroke=color, stroke_width=2),
            line(16, 0, 58, 0, stroke=color, stroke_width=1, dash="5 5", opacity=0.65),
        ]
    )
    axes = "".join(
        [
            line(-18, 13, 6, 13, stroke="#F24B4B", stroke_width=2),
            line(-18, 13, -18, -11, stroke="#22C55E", stroke_width=2),
            line(-18, 13, -31, 25, stroke="#2B79FF", stroke_width=2),
        ]
    )
    return (
        f'<g transform="translate({x} {y}) rotate({angle}) scale({scale})" '
        f'opacity="{opacity}">{body}{lens}{rays}{axes}</g>'
    )


def axis_triad(x: float, y: float, *, scale: float = 1) -> str:
    return (
        f'<g transform="translate({x} {y}) scale({scale})">'
        + circle(0, 0, 4, fill="#12233A")
        + line(0, 0, 52, 0, stroke="#EF4444", stroke_width=3, marker_end="url(#arrow-red)")
        + line(0, 0, 0, -52, stroke="#22C55E", stroke_width=3, marker_end="url(#arrow-green)")
        + line(0, 0, -34, 30, stroke="#2563EB", stroke_width=3, marker_end="url(#arrow-blue-small)")
        + text(60, 6, "x", size=18, fill="#EF4444", italic=True)
        + text(-4, -62, "z", size=18, fill="#22C55E", italic=True)
        + text(-49, 44, "y", size=18, fill="#2563EB", italic=True)
        + "</g>"
    )


def top_title(title_value: str, subtitle: str, *, dark: bool = False, number: str = "") -> str:
    primary = "#F8FBFF" if dark else "#10233F"
    secondary = "#A9BDD5" if dark else "#556A86"
    accent = "#35D1F2" if dark else "#087EA4"
    return "".join(
        [
            text(70, 70, number, size=18, fill=accent, weight=700, letter_spacing=2.5),
            text(70, 116, title_value, size=40, fill=primary, weight=700),
            text(70, 151, subtitle, size=21, fill=secondary),
            line(70, 174, 1530, 174, stroke="#34506F" if dark else "#D4DEE9", stroke_width=1),
        ]
    )


def footer(label: str, *, dark: bool = False) -> str:
    return "".join(
        [
            line(70, 846, 1530, 846, stroke="#2C4665" if dark else "#D7E1EC", stroke_width=1),
            text(70, 875, "GAME CAMERA CAPTURE LAB", size=14, fill="#6F88A5" if dark else "#657B95", weight=700, letter_spacing=2),
            text(1530, 875, label, size=15, fill="#6F88A5" if dark else "#657B95", anchor="end"),
        ]
    )


def document(title_value: str, description: str, body: str, *, background: str) -> str:
    defs = """
  <defs>
    <linearGradient id="dark-bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#07101F"/>
      <stop offset="1" stop-color="#122744"/>
    </linearGradient>
    <linearGradient id="blue-panel" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0B7494"/>
      <stop offset="1" stop-color="#17B6D3"/>
    </linearGradient>
    <linearGradient id="violet-panel" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#5D4ACB"/>
      <stop offset="1" stop-color="#8776EC"/>
    </linearGradient>
    <linearGradient id="warm-panel" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#E88236"/>
      <stop offset="1" stop-color="#F4B15D"/>
    </linearGradient>
    <marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#169BB8"/>
    </marker>
    <marker id="arrow-light" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#7DE8FA"/>
    </marker>
    <marker id="arrow-gray" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto" markerUnits="strokeWidth">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#6D829A"/>
    </marker>
    <marker id="arrow-red" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
      <path d="M 0 0 L 8 4 L 0 8 z" fill="#EF4444"/>
    </marker>
    <marker id="arrow-green" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
      <path d="M 0 0 L 8 4 L 0 8 z" fill="#22C55E"/>
    </marker>
    <marker id="arrow-blue-small" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
      <path d="M 0 0 L 8 4 L 0 8 z" fill="#2563EB"/>
    </marker>
    <pattern id="dot-grid" width="24" height="24" patternUnits="userSpaceOnUse">
      <circle cx="2" cy="2" r="1.5" fill="#70BFD0" opacity="0.22"/>
    </pattern>
  </defs>
"""
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" aria-labelledby="title desc">\n'
        f'<title id="title">{escape(title_value)}</title>\n'
        f'<desc id="desc">{escape(description)}</desc>\n'
        f'{defs}<rect width="{W}" height="{H}" fill="{background}"/>\n{body}\n</svg>\n'
    )


def figure_01_system_overview() -> str:
    parts: list[str] = [top_title(
        "Unified Multi-Game Camera Capture Framework",
        "Dynamic adapters map engine-specific camera control to shared pose, point-set, and trajectory contracts.",
        number="FIGURE 01 / SYSTEM OVERVIEW",
    )]

    parts += [
        text(82, 222, "GAME ADAPTERS", size=17, fill="#51708F", weight=700, letter_spacing=2),
        text(580, 222, "SHARED CAPTURE CORE", size=17, fill="#51708F", weight=700, letter_spacing=2),
        text(1240, 222, "DATA PRODUCTS", size=17, fill="#51708F", weight=700, letter_spacing=2),
    ]

    adapters = [
        ("RE Engine", "Lua pose bridge", "STABLE", "#E9F8F3", "#17815F"),
        ("CryEngine", "Camera Tools bridge", "BETA", "#FFF4E6", "#B76A18"),
        ("Unreal Engine 5", "UUU Connector", "EXPERIMENTAL", "#F1EDFF", "#6652B9"),
        ("Future engine", "game.json + adapter", "PLUG-IN", "#EDF5FA", "#39748F"),
    ]
    for index, (name, bridge, status, bg, color) in enumerate(adapters):
        y = 258 + index * 126
        parts.append(rect(70, y, 390, 100, fill=bg, stroke="#CEDCE8", stroke_width=1.5, rx=16))
        parts.append(rect(70, y, 10, 100, fill=color, rx=5))
        parts.append(text(102, y + 36, name, size=24, fill="#132A46", weight=700))
        parts.append(text(102, y + 68, bridge, size=19, fill="#61758D"))
        parts.append(pill(302, y + 18, 138, status, fill="#FFFFFF", text_fill=color, stroke="#C7D6E3"))
        if index == 3:
            parts.append(line(91, y + 86, 440, y + 86, stroke="#78A0B6", stroke_width=1.5, dash="7 7"))

    parts += [
        path("M 460 449 C 510 449 513 449 555 449", stroke="#169BB8", stroke_width=4, marker_end="url(#arrow)"),
        text(507, 430, "discover", size=17, fill="#4D6B84", anchor="middle", italic=True),
        rect(565, 254, 548, 506, fill="#FFFFFF", stroke="#B9CDDC", stroke_width=2, rx=24),
        rect(595, 286, 488, 82, fill="#E7F7FB", stroke="#9CCFDC", stroke_width=1.5, rx=15),
        text(620, 323, "Dynamic registry", size=25, fill="#0E5670", weight=700),
        text(620, 351, "discovers games/*/game.json and actions", size=18, fill="#4C7180"),
        pill(931, 309, 124, "N ADAPTERS", fill="#CDEEF5", text_fill="#0B6A83"),
        line(839, 369, 839, 403, stroke="#169BB8", stroke_width=3, marker_end="url(#arrow)"),
        text(596, 424, "VERSIONED REPRESENTATION", size=16, fill="#51708F", weight=700, letter_spacing=1.5),
    ]

    schema_cards = [
        ("Pose", ["camera-pose", "v1"], ["XYZ + rotation", "quaternion (opt.)", "field of view"], "#E9F5FF", "#2B79B7"),
        ("Point set", ["camera-point-set", "v1"], ["scene poses", "explicit units", "and coordinates"], "#EEF9F4", "#268568"),
        ("Trajectory", ["camera-trajectory", "v1"], ["time-ordered", "pose keyframes"], "#F3EFFF", "#6655B7"),
    ]
    for index, (name, schema_lines, note_lines, bg, color) in enumerate(schema_cards):
        x = 594 + index * 162
        parts.append(rect(x, 448, 146, 174, fill=bg, stroke="#C8D7E4", stroke_width=1.2, rx=14))
        parts.append(circle(x + 27, 476, 10, fill=color))
        parts.append(text(x + 45, 483, name, size=20, fill="#172E4A", weight=700))
        parts.append(multiline(x + 18, 521, schema_lines, size=13.5, fill=color, weight=700, line_height=1.15, family="Consolas"))
        parts.append(multiline(x + 18, 570, note_lines, size=13.5, fill="#5B718A", line_height=1.18))

    parts += [
        line(839, 625, 839, 655, stroke="#169BB8", stroke_width=3, marker_end="url(#arrow)"),
        rect(595, 668, 488, 63, fill="#122F4B", stroke="#122F4B", rx=14),
        text(839, 695, "PLAN  ->  CONTROL  ->  CAPTURE  ->  VALIDATE", size=19, fill="#E9F8FD", weight=700, anchor="middle", letter_spacing=1.2),
        text(839, 719, "engine-neutral orchestration with adapter-specific execution", size=15, fill="#A8C5D7", anchor="middle"),
        path("M 1113 449 C 1160 449 1165 449 1207 449", stroke="#169BB8", stroke_width=4, marker_end="url(#arrow)"),
    ]

    products = [
        ("Still scans", "multi-view RGB frames", "#EAF7FB", "#0A809A"),
        ("Trajectory clips", "time-aligned frame sequences", "#F1EEFF", "#6654B6"),
        ("Pose metadata", "auditable coordinates and units", "#FFF4E8", "#B96B21"),
    ]
    for index, (name, note, bg, color) in enumerate(products):
        y = 279 + index * 150
        parts.append(rect(1220, y, 310, 118, fill=bg, stroke="#CAD8E4", stroke_width=1.3, rx=16))
        parts.append(circle(1254, y + 34, 14, fill=color))
        parts.append(text(1280, y + 42, name, size=23, fill="#142C48", weight=700))
        parts.append(text(1254, y + 82, note, size=17, fill="#63778F"))
        if index == 0:
            for j in range(4):
                parts.append(rect(1450 + (j % 2) * 27, y + 69 + (j // 2) * 20, 20, 14, fill="#A8DCE7", rx=2))
        elif index == 1:
            parts.append(path(f"M 1445 {y+83} C 1460 {y+55} 1490 {y+103} 1511 {y+65}", stroke=color, stroke_width=3))
        else:
            parts.append(text(1450, y + 91, "{ x, y, z }", size=16, fill=color, family="Consolas", weight=700))

    parts += [
        rect(1220, 731, 310, 57, fill="#F7FAFC", stroke="#CAD8E4", stroke_width=1.2, rx=12),
        text(1238, 756, "Evidence states remain explicit", size=18, fill="#2F4E6B", weight=700),
        text(1238, 778, "verified  /  pending  /  experimental", size=15, fill="#71869C"),
        footer("Architecture overview", dark=False),
    ]
    return document(
        "Unified multi-game camera capture framework",
        "Architecture overview showing dynamically discovered game adapters, shared versioned camera schemas, capture orchestration, and dataset products.",
        "".join(parts),
        background="#F7F9FC",
    )


def figure_02_pose_contract() -> str:
    parts: list[str] = [top_title(
        "A Coordinate-Explicit Camera Pose Contract",
        "Every adapter declares its native coordinates before mapping position, orientation, and field of view.",
        number="FIGURE 02 / DATA REPRESENTATION",
    )]

    parts += [
        text(80, 222, "ENGINE-NATIVE INPUT", size=17, fill="#55708B", weight=700, letter_spacing=2),
        text(620, 222, "CAMERA-POSE/V1", size=17, fill="#55708B", weight=700, letter_spacing=2),
        text(1240, 222, "COMPOSABLE CONTAINERS", size=17, fill="#55708B", weight=700, letter_spacing=2),
    ]

    native_cards = [
        ("Engine A", "left-handed", "z-up", "degrees", "#EAF7FB", "#1185A0"),
        ("Engine B", "right-handed", "y-up", "degrees", "#F0EDFF", "#6A58BE"),
        ("Engine C", "unknown hand", "z-up", "radians", "#FFF3E7", "#B97025"),
    ]
    for index, (name, hand, up, angle, bg, color) in enumerate(native_cards):
        y = 264 + index * 160
        parts.append(rect(70, y, 410, 132, fill=bg, stroke="#CBD9E5", stroke_width=1.3, rx=16))
        parts.append(text(96, y + 36, name, size=23, fill="#152D49", weight=700))
        parts.append(pill(342, y + 14, 112, up.upper(), fill="#FFFFFF", text_fill=color, stroke="#C6D6E2"))
        parts.append(text(96, y + 73, f"handedness: {hand}", size=18, fill="#536E87", family="Consolas"))
        parts.append(text(96, y + 101, f"angle_unit: {angle}", size=18, fill="#536E87", family="Consolas"))
        parts.append(axis_triad(410, y + 98, scale=0.42))

    parts += [
        path("M 480 475 C 525 475 535 475 575 475", stroke="#169BB8", stroke_width=4, marker_end="url(#arrow)"),
        text(527, 452, "map", size=18, fill="#53738A", anchor="middle", italic=True),
        rect(590, 252, 530, 515, fill="#FFFFFF", stroke="#AFC8D9", stroke_width=2, rx=24),
        rect(590, 252, 530, 74, fill="url(#blue-panel)", rx=24),
        rect(590, 302, 530, 24, fill="url(#blue-panel)"),
        text(620, 298, "One complete camera pose", size=27, fill="#FFFFFF", weight=700),
        pill(952, 272, 137, "VERSIONED", fill="#D8F6FB", text_fill="#08728B"),
        camera_frustum(973, 434, scale=1.45, color="#169BB8", angle=-8),
        axis_triad(858, 465, scale=0.9),
    ]

    fields = [
        ("position", "x, y, z", "required", "#E8F5FB", "#0D7993"),
        ("rotation", "yaw, pitch, roll", "required", "#EEF7F2", "#287A62"),
        ("quaternion", "x, y, z, w", "optional", "#F3EFFF", "#6656B6"),
        ("fov_degrees", "scalar", "required", "#FFF3E7", "#AF6822"),
    ]
    for index, (name, value, state, bg, color) in enumerate(fields):
        x = 620 + (index % 2) * 235
        y = 552 + (index // 2) * 94
        parts.append(rect(x, y, 215, 75, fill=bg, stroke="#CAD9E5", stroke_width=1.1, rx=12))
        parts.append(text(x + 16, y + 28, name, size=18, fill=color, weight=700, family="Consolas"))
        parts.append(text(x + 16, y + 55, value, size=17, fill="#405A72", family="Consolas"))
        parts.append(text(x + 198, y + 28, state, size=13, fill="#75899E", anchor="end", weight=700))

    parts += [
        rect(620, 739, 470, 1, fill="#D7E1EA"),
        text(855, 760, "coordinate_system is stored with every pose container", size=16, fill="#526D86", anchor="middle", italic=True),
        path("M 1120 475 C 1170 475 1175 475 1212 475", stroke="#169BB8", stroke_width=4, marker_end="url(#arrow)"),
    ]

    # Point-set container.
    parts += [
        rect(1225, 276, 305, 210, fill="#EFF8F4", stroke="#BFD8CD", stroke_width=1.5, rx=18),
        text(1250, 314, "Point set", size=25, fill="#1B6652", weight=700),
        text(1250, 343, "camera-point-set/v1", size=17, fill="#39806C", family="Consolas"),
    ]
    for px, py in [(1268, 396), (1322, 376), (1373, 420), (1430, 383), (1480, 431)]:
        parts.append(circle(px, py, 8, fill="#37A27D", stroke="#FFFFFF", stroke_width=2))
    parts.append(path("M 1268 396 C 1322 340 1373 451 1430 383 S 1480 431 1501 399", stroke="#65B79A", stroke_width=2, dash="6 6"))
    parts.append(text(1250, 464, "N labeled scene poses", size=17, fill="#58756C"))

    # Trajectory container.
    parts += [
        rect(1225, 524, 305, 210, fill="#F2EEFF", stroke="#CEC5ED", stroke_width=1.5, rx=18),
        text(1250, 562, "Trajectory", size=25, fill="#5C4AA8", weight=700),
        text(1250, 591, "camera-trajectory/v1", size=17, fill="#7563B9", family="Consolas"),
        path("M 1260 668 C 1310 614 1375 705 1422 642 S 1485 620 1507 654", stroke="#7C66D5", stroke_width=4),
    ]
    for px, py, label in [(1260, 668, "t0"), (1353, 670, "t1"), (1422, 642, "t2"), (1507, 654, "tK")]:
        parts.append(circle(px, py, 7, fill="#7C66D5", stroke="#FFFFFF", stroke_width=2))
        parts.append(text(px, py + 28, label, size=15, fill="#675A89", anchor="middle", italic=True))

    parts += [
        rect(70, 765, 410, 54, fill="#132F4C", rx=12),
        text(275, 788, "Explicit invariants", size=17, fill="#FFFFFF", weight=700, anchor="middle"),
        text(275, 810, "handedness  |  vertical axis  |  units", size=15, fill="#B8CDDE", anchor="middle"),
        footer("Pose and container schemas", dark=False),
    ]
    return document(
        "Coordinate-explicit camera pose contract",
        "Data representation diagram showing engine-native coordinate systems mapped into a versioned camera pose and composed into point sets and trajectories.",
        "".join(parts),
        background="#F8FAFC",
    )


def figure_03_layered_scan() -> str:
    dark = True
    parts: list[str] = [top_title(
        "Boundary-Aware Layered Still-Scan Planning",
        "Sparse boundary observations define a 3D volume; 4-6 layers place camera poses before multi-view capture.",
        dark=dark,
        number="FIGURE 03 / SPATIAL SAMPLING",
    )]

    parts += [
        rect(58, 204, 1082, 600, fill="#0B1930", stroke="#284868", stroke_width=1.5, rx=24),
        rect(58, 204, 1082, 600, fill="url(#dot-grid)", opacity=0.75, rx=24),
        text(90, 241, "PLANNING VOLUME", size=16, fill="#79A5C8", weight=700, letter_spacing=2),
    ]

    # Perspective wireframe bounds.
    back = [(330, 285), (910, 285), (1010, 525), (430, 525)]
    front = [(210, 410), (790, 410), (890, 650), (310, 650)]
    parts.append(polygon(" ".join(f"{x},{y}" for x, y in back), stroke="#4F86AB", stroke_width=2, opacity=0.7))
    parts.append(polygon(" ".join(f"{x},{y}" for x, y in front), stroke="#67D8ED", stroke_width=2.4, opacity=0.9))
    for (x1, y1), (x2, y2) in zip(back, front):
        parts.append(line(x1, y1, x2, y2, stroke="#4F86AB", stroke_width=2, dash="9 7", opacity=0.75))

    # Six translucent layers.
    layer_colors = ["#116A8A", "#157C9B", "#198FAC", "#239FB9", "#35AEC2", "#53BDD0"]
    layer_points: list[list[tuple[float, float]]] = []
    for index in range(6):
        t = index / 5
        pts = [
            (330 - 120 * t, 285 + 125 * t),
            (910 - 120 * t, 285 + 125 * t),
            (1010 - 120 * t, 525 + 125 * t),
            (430 - 120 * t, 525 + 125 * t),
        ]
        layer_points.append(pts)
        parts.append(polygon(" ".join(f"{x:.1f},{y:.1f}" for x, y in pts), fill=layer_colors[index], stroke="#7AE5F5", stroke_width=1.3, opacity=0.13 + index * 0.025))
        parts.append(text(1020 - 120 * t, 520 + 125 * t, f"L{index + 1}", size=14, fill="#76D9EA", weight=700))

    # Boundary polygon and raw samples on the lowest visible layer.
    boundary = [(285, 580), (390, 474), (610, 451), (807, 520), (822, 604), (612, 626), (405, 620)]
    parts.append(polygon(" ".join(f"{x},{y}" for x, y in boundary), fill="#4C3A9B", stroke="#B7A9FF", stroke_width=3, opacity=0.32))
    for index, (px, py) in enumerate(boundary):
        parts.append(circle(px, py, 8, fill="#C6B9FF", stroke="#FFFFFF", stroke_width=2))
        parts.append(text(px + 11, py - 9, f"b{index + 1}", size=13, fill="#D6CEFF", italic=True))

    # Generated points across layers.
    generated = [
        (396, 355), (541, 332), (705, 345), (847, 380),
        (360, 420), (520, 395), (670, 420), (790, 445),
        (342, 488), (485, 480), (631, 500), (758, 525),
        (330, 553), (465, 560), (600, 572), (735, 582),
    ]
    for index, (px, py) in enumerate(generated):
        parts.append(circle(px, py, 6.5, fill="#F9C74F", stroke="#FFF4B8", stroke_width=2))
        if index in {1, 6, 11, 14}:
            parts.append(camera_frustum(px - 16, py - 24, scale=0.42, color="#7DE8FA", angle=-18 + index * 2, opacity=0.9))

    # View target and rays.
    parts.append(circle(602, 520, 19, fill="#10263D", stroke="#7DE8FA", stroke_width=2.5))
    parts.append(circle(602, 520, 6, fill="#7DE8FA"))
    for px, py in [(396, 355), (705, 345), (342, 488), (735, 582)]:
        parts.append(line(px, py, 602, 520, stroke="#5BC8DB", stroke_width=1.2, dash="5 6", opacity=0.55))
    parts.append(text(626, 516, "view target", size=15, fill="#9DD8E3", italic=True))

    # Side process.
    parts += [
        text(1185, 222, "PLANNER", size=17, fill="#79A5C8", weight=700, letter_spacing=2),
    ]
    steps = [
        ("01", "Record boundary", ["Sparse XYZ samples delimit", "the scene volume."]),
        ("02", "Generate 4-6 layers", ["Interpolate valid positions", "inside the boundaries."]),
        ("03", "Assign view directions", ["Orient poses toward targets", "or angular sectors."]),
        ("04", "Capture and validate", ["Save RGB frames with", "measured camera poses."]),
    ]
    for index, (num, name, note_lines) in enumerate(steps):
        y = 262 + index * 126
        parts.append(circle(1207, y + 28, 24, fill="#173957", stroke="#5DCBE0", stroke_width=2))
        parts.append(text(1207, y + 35, num, size=17, fill="#8DE6F4", weight=700, anchor="middle"))
        parts.append(text(1250, y + 27, name, size=22, fill="#F1F7FC", weight=700))
        parts.append(multiline(1250, y + 57, note_lines, size=16, fill="#9CB4C9", line_height=1.2))
        if index < len(steps) - 1:
            parts.append(line(1207, y + 55, 1207, y + 102, stroke="#3C728F", stroke_width=2, dash="5 5"))

    parts += [
        rect(1180, 761, 350, 56, fill="#112F49", stroke="#2F6683", stroke_width=1.2, rx=12),
        text(1355, 785, "N positions x K view directions", size=18, fill="#DDF8FC", weight=700, anchor="middle"),
        text(1355, 808, "dataset scale remains configurable", size=15, fill="#8DB1C8", anchor="middle"),
        footer("Layered scan planner", dark=True),
    ]
    return document(
        "Boundary-aware layered still-scan planning",
        "A 3D sampling schematic where sparse boundary points define a scene volume, six layers place camera positions, and multiple view directions produce still scans.",
        "".join(parts),
        background="url(#dark-bg)",
    )


def figure_04_trajectory_feedback() -> str:
    dark = True
    parts: list[str] = [top_title(
        "Trajectory Execution with Measured-Pose Feedback",
        "The same trajectory contract supports atomic pose control where available and closed-loop relative control elsewhere.",
        dark=dark,
        number="FIGURE 04 / TRAJECTORY CONTROL",
    )]

    parts += [
        text(82, 218, "TIME-PARAMETERIZED CAMERA PATH", size=17, fill="#79A5C8", weight=700, letter_spacing=2),
        path("M 95 366 C 250 232 380 450 540 318 S 820 244 970 352 S 1260 451 1505 276", stroke="#163550", stroke_width=11, opacity=0.9),
        path("M 95 366 C 250 232 380 450 540 318 S 820 244 970 352 S 1260 451 1505 276", stroke="#65DDF0", stroke_width=3.5, marker_end="url(#arrow-light)"),
    ]
    keyframes = [
        (95, 366, "t0", -20),
        (328, 347, "t1", 13),
        (540, 318, "t2", -13),
        (754, 285, "t3", 8),
        (970, 352, "t4", 18),
        (1236, 381, "t5", -8),
        (1505, 276, "tK", -24),
    ]
    for index, (px, py, label, angle) in enumerate(keyframes):
        parts.append(circle(px, py, 10, fill="#7DE8FA", stroke="#E8FCFF", stroke_width=3))
        parts.append(text(px, py + (31 if index % 2 == 0 else -22), label, size=16, fill="#B7DFE8", anchor="middle", italic=True))
        if index in {0, 2, 4, 6}:
            parts.append(camera_frustum(px, py - 45, scale=0.54, color="#6AD9EC", angle=angle, opacity=0.95))

    parts += [
        line(390, 427, 390, 466, stroke="#5596B2", stroke_width=2, marker_end="url(#arrow-light)"),
        line(804, 402, 804, 466, stroke="#5596B2", stroke_width=2, marker_end="url(#arrow-light)"),
        line(1216, 431, 1216, 466, stroke="#5596B2", stroke_width=2, marker_end="url(#arrow-light)"),
        text(80, 489, "EXECUTION AND SYNCHRONIZATION", size=17, fill="#79A5C8", weight=700, letter_spacing=2),
    ]

    blocks = [
        (80, 525, 230, "Trajectory file", "keyframes + time_sec", "#153654", "#79DDF0"),
        (355, 525, 250, "Time sampler", "interpolation + rate", "#153654", "#79DDF0"),
        (650, 525, 300, "Pose controller", "engine-specific execution", "#263761", "#B5A8FF"),
        (995, 525, 230, "Game camera", "commanded motion", "#153654", "#79DDF0"),
        (1270, 525, 250, "Frame capture", "RGB + timestamp", "#3D2B43", "#F2B46A"),
    ]
    for x, y, width, name, note, bg, color in blocks:
        parts.append(rect(x, y, width, 102, fill=bg, stroke=color, stroke_width=1.5, rx=14))
        parts.append(text(x + width / 2, y + 42, name, size=22, fill="#F5FAFE", weight=700, anchor="middle"))
        parts.append(text(x + width / 2, y + 72, note, size=16, fill="#A9C1D3", anchor="middle"))
    for x1, x2 in [(310, 355), (605, 650), (950, 995), (1225, 1270)]:
        parts.append(line(x1, 576, x2 - 8, 576, stroke="#65DDF0", stroke_width=3, marker_end="url(#arrow-light)"))

    # Controller branches.
    parts += [
        rect(650, 661, 142, 74, fill="#142F4C", stroke="#5ED1E6", stroke_width=1.2, rx=11),
        text(721, 689, "Atomic setPose", size=17, fill="#E5FAFD", weight=700, anchor="middle"),
        text(721, 715, "where available", size=14, fill="#89AEC4", anchor="middle"),
        rect(808, 661, 142, 74, fill="#302C58", stroke="#A999F2", stroke_width=1.2, rx=11),
        text(879, 689, "Relative steps", size=17, fill="#F4F1FF", weight=700, anchor="middle"),
        text(879, 715, "+ pose feedback", size=14, fill="#BDB3E5", anchor="middle"),
        line(721, 627, 721, 653, stroke="#63D5E9", stroke_width=2, marker_end="url(#arrow-light)"),
        line(879, 627, 879, 653, stroke="#A999F2", stroke_width=2, marker_end="url(#arrow-light)"),
    ]

    # Feedback loop and alignment.
    parts += [
        rect(995, 681, 230, 54, fill="#10283E", stroke="#4B85A0", stroke_width=1.2, rx=10),
        text(1110, 714, "Measured pose stream", size=17, fill="#BCECF4", weight=700, anchor="middle"),
        path("M 1110 681 L 1110 650 C 1110 642 1100 642 1092 642 L 970 642 C 960 642 958 652 958 662 L 958 761 C 958 775 947 783 933 783 L 858 783 C 843 783 836 771 836 745", stroke="#9E91E7", stroke_width=3, dash="7 6", marker_end="url(#arrow-light)"),
        text(1010, 777, "feedback correction", size=15, fill="#B2A8E2", italic=True),
        rect(1270, 681, 250, 54, fill="#3A2B2D", stroke="#D4934D", stroke_width=1.2, rx=10),
        text(1395, 714, "Frame-pose alignment", size=17, fill="#FFE8C9", weight=700, anchor="middle"),
        line(1225, 708, 1262, 708, stroke="#E5A55A", stroke_width=2.5, marker_end="url(#arrow-light)"),
        rect(80, 681, 525, 90, fill="#0E2338", stroke="#315876", stroke_width=1.2, rx=13),
        text(105, 711, "Verification ladder", size=18, fill="#F0F7FC", weight=700),
        text(105, 744, "command issued", size=16, fill="#8FAFC5"),
        line(234, 739, 291, 739, stroke="#4D829C", stroke_width=2, marker_end="url(#arrow-light)"),
        text(310, 744, "pose changed", size=16, fill="#8FAFC5"),
        line(414, 739, 468, 739, stroke="#4D829C", stroke_width=2, marker_end="url(#arrow-light)"),
        text(487, 744, "visual acceptance", size=16, fill="#8FAFC5"),
        footer("Trajectory replay and feedback", dark=True),
    ]
    return document(
        "Trajectory execution with measured-pose feedback",
        "A trajectory-control diagram showing keyframes, interpolation, atomic setPose when available, relative pose feedback as a fallback, and synchronized frame capture.",
        "".join(parts),
        background="url(#dark-bg)",
    )


def figure_05_adapter_architecture() -> str:
    parts: list[str] = [top_title(
        "An Extensible Adapter Architecture",
        "New games are discovered from manifests; the launcher never hard-codes game count or engine branches.",
        number="FIGURE 05 / EXTENSIBILITY",
    )]

    # Central core.
    parts += [
        circle(800, 470, 166, fill="#E9F6FA", stroke="#79BCCE", stroke_width=2.5),
        circle(800, 470, 126, fill="#FFFFFF", stroke="#B5D3DE", stroke_width=1.5),
        text(800, 431, "SHARED", size=18, fill="#26758D", weight=700, anchor="middle", letter_spacing=2),
        text(800, 470, "Capture Core", size=31, fill="#14314D", weight=700, anchor="middle"),
        text(800, 504, "registry + schemas", size=18, fill="#5E748B", anchor="middle"),
        text(800, 529, "validation + launcher", size=18, fill="#5E748B", anchor="middle"),
    ]

    cards = [
        (190, 255, 340, 150, "RE Engine adapter", "Lua bridge", "stable", "#EAF8F3", "#258368", (650, 393)),
        (1070, 255, 340, 150, "CryEngine adapter", "Camera Tools bridge", "beta", "#FFF4E7", "#B76D24", (950, 393)),
        (190, 574, 340, 150, "UE5 adapter", "UUU Connector", "experimental", "#F2EEFF", "#6754B8", (650, 547)),
        (1070, 574, 340, 150, "Future adapter N", "manifest + bridge", "plug-in", "#EDF5FA", "#39758F", (950, 547)),
    ]
    for x, y, width, height, name, bridge, state, bg, color, target in cards:
        parts.append(rect(x, y, width, height, fill=bg, stroke="#C6D7E3", stroke_width=1.5, rx=18))
        parts.append(rect(x, y, 9, height, fill=color, rx=4))
        parts.append(text(x + 30, y + 40, name, size=24, fill="#152E4A", weight=700))
        parts.append(text(x + 30, y + 74, bridge, size=18, fill="#5B7188"))
        parts.append(pill(x + 30, y + 96, 144, state.upper(), fill="#FFFFFF", text_fill=color, stroke="#CBD8E3"))
        parts.append(text(x + 188, y + 119, "game.json", size=15, fill=color, family="Consolas", weight=700))
        start_x = x + width if x < 800 else x
        start_y = y + height / 2
        parts.append(path(f"M {start_x} {start_y} C {target[0]} {start_y} {target[0]} {target[1]} {target[0]} {target[1]}", stroke=color, stroke_width=3, dash="7 6", marker_end="url(#arrow)"))

    # Orbit detail labels.
    orbit_labels = [
        (800, 251, 150, "capabilities"),
        (1010, 470, 110, "actions"),
        (800, 699, 110, "examples"),
        (590, 470, 100, "docs"),
    ]
    for x, y, width, label in orbit_labels:
        parts.append(pill(x - width / 2, y - 17, width, label.upper(), fill="#153A57", text_fill="#EAF8FC"))

    parts += [
        text(70, 217, "ADAPTER CONTRACT", size=17, fill="#55708B", weight=700, letter_spacing=2),
        text(1530, 217, "DYNAMIC DISCOVERY", size=17, fill="#55708B", weight=700, letter_spacing=2, anchor="end"),
        rect(570, 753, 460, 63, fill="#14314D", rx=14),
        text(800, 780, "games/*/game.json  ->  registry  ->  launcher", size=19, fill="#F1F8FC", weight=700, anchor="middle", family="Consolas"),
        text(800, 805, "adding an adapter does not modify the shared hub", size=15, fill="#ABC2D2", anchor="middle"),
    ]

    # Dotted future sockets.
    for cx, cy in [(606, 263), (994, 263), (606, 690), (994, 690)]:
        parts.append(circle(cx, cy, 14, fill="#F7F9FC", stroke="#7FA5B9", stroke_width=2, opacity=0.9))
        parts.append(line(cx - 6, cy, cx + 6, cy, stroke="#5A859A", stroke_width=2))
        parts.append(line(cx, cy - 6, cx, cy + 6, stroke="#5A859A", stroke_width=2))

    parts += [
        footer("Manifest-driven adapter system", dark=False),
    ]
    return document(
        "Extensible adapter architecture",
        "A hub-and-spoke adapter architecture where current and future game engines register through manifests and share a common capture core without hard-coded game branches.",
        "".join(parts),
        background="#F8FAFC",
    )


def figure_06_dataset_lifecycle() -> str:
    parts: list[str] = [top_title(
        "From Camera Plans to Auditable Capture Datasets",
        "Still scans and trajectories share synchronized RGB, measured pose metadata, and explicit provenance.",
        number="FIGURE 06 / DATASET GENERATION",
    )]

    column_x = [70, 575, 1080]
    headers = [
        ("1", "CAPTURE PLANS", "#EAF7FB", "#0D7D97"),
        ("2", "SYNCHRONIZED ACQUISITION", "#F1EEFF", "#6655B5"),
        ("3", "DATASET PRODUCTS", "#FFF3E7", "#B96A21"),
    ]
    for x, (num, label, bg, color) in zip(column_x, headers):
        parts.append(rect(x, 215, 450, 56, fill=bg, stroke="#CAD8E4", stroke_width=1.2, rx=13))
        parts.append(circle(x + 30, 243, 17, fill=color))
        parts.append(text(x + 30, 249, num, size=17, fill="#FFFFFF", weight=700, anchor="middle"))
        parts.append(text(x + 61, 250, label, size=18, fill=color, weight=700, letter_spacing=1.3))

    # Column 1: plans.
    parts += [
        rect(70, 296, 450, 485, fill="#FFFFFF", stroke="#CAD8E4", stroke_width=1.4, rx=18),
        text(96, 335, "Point-set plan", size=24, fill="#18324F", weight=700),
        text(96, 365, "camera-point-set/v1", size=16, fill="#37819A", family="Consolas"),
        rect(96, 390, 398, 136, fill="#EAF6F9", stroke="#B8D8E2", stroke_width=1.1, rx=12),
    ]
    point_positions = [(130, 483), (176, 438), (236, 464), (293, 420), (348, 474), (418, 434), (464, 486)]
    parts.append(path("M 130 483 C 170 421 232 488 293 420 S 420 438 464 486", stroke="#42A7BC", stroke_width=2, dash="6 5"))
    for px, py in point_positions:
        parts.append(circle(px, py, 7, fill="#1595AF", stroke="#FFFFFF", stroke_width=2))
        parts.append(camera_frustum(px, py - 19, scale=0.26, color="#128BA5", angle=-12, opacity=0.8))

    parts += [
        line(96, 554, 494, 554, stroke="#D7E1EA", stroke_width=1),
        text(96, 590, "Trajectory plan", size=24, fill="#18324F", weight=700),
        text(96, 620, "camera-trajectory/v1", size=16, fill="#6C58B8", family="Consolas"),
        rect(96, 645, 398, 108, fill="#F2EFFF", stroke="#D0C7EE", stroke_width=1.1, rx=12),
        path("M 122 714 C 178 654 234 738 298 682 S 407 662 470 701", stroke="#7562CE", stroke_width=4),
    ]
    for px, py, label in [(122, 714, "t0"), (234, 707, "t1"), (342, 668, "t2"), (470, 701, "tK")]:
        parts.append(circle(px, py, 7, fill="#7562CE", stroke="#FFFFFF", stroke_width=2))
        parts.append(text(px, py + 25, label, size=13, fill="#6B5D8C", anchor="middle", italic=True))

    # Flow arrows.
    parts += [
        line(520, 527, 559, 527, stroke="#169BB8", stroke_width=4, marker_end="url(#arrow)"),
        line(1025, 527, 1064, 527, stroke="#169BB8", stroke_width=4, marker_end="url(#arrow)"),
    ]

    # Column 2: synchronized acquisition.
    parts += [
        rect(575, 296, 450, 485, fill="#FFFFFF", stroke="#CAD8E4", stroke_width=1.4, rx=18),
        text(601, 335, "In-game camera", size=24, fill="#18324F", weight=700),
        rect(601, 363, 398, 164, fill="#102A44", stroke="#385B78", stroke_width=1.2, rx=12),
        polygon("615,505 690,423 752,467 819,399 900,474 985,416 985,513 615,513", fill="#1B526C", stroke="#4D8BA2", stroke_width=1.4),
        circle(917, 403, 24, fill="#F4B85E", opacity=0.9),
        camera_frustum(781, 448, scale=0.65, color="#78E2F3", angle=-7),
        text(619, 389, "RGB frame", size=15, fill="#BDEBF3", weight=700),
        line(601, 554, 999, 554, stroke="#D7E1EA", stroke_width=1),
    ]

    streams = [
        ("RGB stream", "frame_000123.png", "#EAF7FB", "#0D819B"),
        ("Pose stream", "x y z  |  yaw pitch roll", "#F1EEFF", "#6655B5"),
        ("Clock stream", "timestamp + frame index", "#FFF3E7", "#B96A21"),
    ]
    for index, (name, note, bg, color) in enumerate(streams):
        y = 578 + index * 57
        parts.append(rect(601, y, 398, 45, fill=bg, stroke="#D0DCE6", stroke_width=1, rx=9))
        parts.append(circle(621, y + 22.5, 7, fill=color))
        parts.append(text(640, y + 20, name, size=17, fill="#18324F", weight=700))
        parts.append(text(982, y + 20, note, size=14, fill=color, anchor="end", family="Consolas"))
        if index < 2:
            parts.append(line(800, y + 45, 800, y + 55, stroke="#7A8FA4", stroke_width=1.5, dash="3 3"))
    parts += [
        rect(601, 750, 398, 1, fill="#D7E1EA"),
        text(800, 770, "align by timestamp, retain measured pose", size=15, fill="#63788F", anchor="middle", italic=True),
    ]

    # Column 3: dataset products.
    parts += [
        rect(1080, 296, 450, 485, fill="#FFFFFF", stroke="#CAD8E4", stroke_width=1.4, rx=18),
        text(1106, 335, "Dataset package", size=24, fill="#18324F", weight=700),
        text(1106, 365, "portable, versioned, auditable", size=17, fill="#6D8197"),
    ]

    # Vector-only thumbnail grid.
    thumb_colors = ["#39677D", "#9B7546", "#496F58", "#4D506E", "#8A5D4E", "#376C82"]
    for index, color in enumerate(thumb_colors):
        x = 1106 + (index % 3) * 128
        y = 392 + (index // 3) * 94
        parts.append(rect(x, y, 112, 78, fill="#EAF0F4", stroke="#C8D6E0", stroke_width=1, rx=6))
        parts.append(polygon(f"{x+5},{y+70} {x+35},{y+38} {x+58},{y+55} {x+82},{y+26} {x+107},{y+70}", fill=color, stroke="none"))
        parts.append(circle(x + 91, y + 18, 9, fill="#F0BB69"))
        parts.append(text(x + 8, y + 18, f"{index + 1:03d}", size=12, fill="#314D64", family="Consolas", weight=700))

    parts += [
        line(1106, 593, 1504, 593, stroke="#D7E1EA", stroke_width=1),
        text(1106, 625, "scene_id/", size=17, fill="#B06725", family="Consolas", weight=700),
        text(1134, 655, "frames/", size=16, fill="#506B82", family="Consolas"),
        text(1134, 682, "poses.jsonl", size=16, fill="#506B82", family="Consolas"),
        text(1134, 709, "trajectory.json", size=16, fill="#506B82", family="Consolas"),
        text(1134, 736, "manifest.json", size=16, fill="#506B82", family="Consolas"),
        line(1270, 642, 1270, 746, stroke="#D7E1EA", stroke_width=1),
        text(1293, 626, "Provenance", size=18, fill="#18324F", weight=700),
        text(1293, 655, "game_id", size=15, fill="#687D92", family="Consolas"),
        text(1293, 682, "coordinate_system", size=15, fill="#687D92", family="Consolas"),
        text(1293, 709, "schema_version", size=15, fill="#687D92", family="Consolas"),
        text(1293, 736, "capture_time", size=15, fill="#687D92", family="Consolas"),
        footer("Synchronized dataset lifecycle", dark=False),
    ]
    return document(
        "From camera plans to auditable capture datasets",
        "A dataset lifecycle showing point sets and trajectories, synchronized RGB-pose acquisition, and versioned output packages with provenance.",
        "".join(parts),
        background="#F8FAFC",
    )


FIGURES = {
    "vector_01_system_overview.svg": figure_01_system_overview,
    "vector_02_pose_contract.svg": figure_02_pose_contract,
    "vector_03_layered_scan.svg": figure_03_layered_scan,
    "vector_04_trajectory_feedback.svg": figure_04_trajectory_feedback,
    "vector_05_adapter_architecture.svg": figure_05_adapter_architecture,
    "vector_06_dataset_lifecycle.svg": figure_06_dataset_lifecycle,
}


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    for filename, builder in FIGURES.items():
        target = ROOT / filename
        target.write_text(builder(), encoding="utf-8", newline="\n")
        print(target)


if __name__ == "__main__":
    main()
