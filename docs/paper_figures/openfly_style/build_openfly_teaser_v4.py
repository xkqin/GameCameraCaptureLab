from __future__ import annotations

import math
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
ASSETS = ROOT / "assets" / "v2"
FRAMES = ASSETS / "spatialvid_samples" / "frames"
OUTPUT = ROOT / "openfly_07_multisource_teaser_v4.svg"

INK = "#111820"
MUTED = "#536675"
CYAN = "#43D9FF"
PINK = "#F08EBE"
GREEN = "#64D98C"
LIME = "#D2E85B"


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


def vertical_text(x: float, y: float, value: str, *, fill: str = INK,
                  size: float = 18) -> str:
    return (
        f'<text x="{x}" y="{y}" fill="{fill}" font-size="{size}" '
        'font-family="Arial" font-weight="700" text-anchor="middle" '
        f'letter-spacing="1.2" transform="rotate(-90 {x} {y})">{value}</text>'
    )


def panel_tag(x: float, y: float, title: str, subtitle: str,
              color: str, width: float) -> str:
    return "".join([
        rect(x, y, width, 48, fill="#071725", stroke="#FFFFFF", sw=0.7,
             rx=8, opacity=0.66),
        rect(x, y, 5, 48, fill=color, rx=2, opacity=0.85),
        txt(x + 17, y + 20, title, size=12.5, fill="#FFFFFF", weight=700,
            letter=0.9),
        txt(x + 17, y + 38, subtitle, size=10.2, fill="#DCE9EE"),
    ])


def waypoint(x: float, y: float, color: str, scale: float = 1.0) -> str:
    rings = "".join([
        circle(x, y, 6.4 * scale, fill="none", stroke=color, sw=1.1,
               opacity=0.24),
        circle(x, y, 3.7 * scale, fill="#F8FEFF", stroke=color, sw=1.1,
               opacity=0.88),
        circle(x, y, 1.25 * scale, fill="#FFFFFF", opacity=0.98),
    ])
    return group(rings, filter_id="v4-dot-glow")


def wire_camera(x: float, y: float, angle: float, color: str,
                scale: float = 1.0) -> str:
    # Open, unfilled frustum with a tiny RGB pose triad, matching the supplied
    # transparent camera-path reference rather than a solid camera icon.
    geometry = "".join([
        f'<path d="M0,0 L27,-16 L53,-11 L53,11 L27,16 Z M27,-16 L27,16 '
        f'M27,-16 L53,11 M27,16 L53,-11" fill="none" stroke="{color}" '
        'stroke-width="1.25" stroke-linecap="round" '
        'stroke-linejoin="round"/>',
        '<circle cx="0" cy="0" r="2.1" fill="#F8FEFF" '
        f'stroke="{color}" stroke-width="1"/>',
        '<path d="M0,0 L18,0" stroke="#F05A5A" stroke-width="1.35"/>',
        '<path d="M18,0 L14,-2.5 L14,2.5 Z" fill="#F05A5A"/>',
        '<path d="M0,0 L0,-17" stroke="#58D56E" stroke-width="1.35"/>',
        '<path d="M0,-17 L-2.5,-13 L2.5,-13 Z" fill="#58D56E"/>',
        '<path d="M0,0 L-10,14" stroke="#5E8DFF" stroke-width="1.35"/>',
        '<path d="M-10,14 L-9,9 L-5,12 Z" fill="#5E8DFF"/>',
    ])
    return group(
        geometry,
        transform=f"translate({x} {y}) rotate({angle}) scale({scale})",
        opacity=0.52,
        filter_id="v4-camera-glow",
    )


def transparent_route(d: str, color: str,
                      nodes: list[tuple[float, float]],
                      cameras: list[tuple[float, float, float, float]]) -> str:
    parts = [
        path(d, stroke=color, sw=7.0, opacity=0.10,
             filter_="url(#v4-route-blur)"),
        path(d, stroke=color, sw=2.15, opacity=0.68),
        path(d, stroke="#F8FEFF", sw=0.62, opacity=0.48),
    ]
    for index, (x, y) in enumerate(nodes):
        parts.append(waypoint(x, y, color, 1.22 if index in (0, len(nodes) - 1) else 0.82))
    for x, y, angle, scale in cameras:
        parts.append(wire_camera(x, y, angle, color, scale))
    return "".join(parts)


def vector_point_cloud(cx: float, cy: float) -> str:
    points: list[str] = []
    palette = [CYAN, "#6FCFF0", GREEN, LIME, PINK]
    for i in range(128):
        angle = i * 2.399963229728653
        radius = 13 + (i % 29) * 2.15
        x = cx + math.cos(angle) * radius * (1.34 + 0.15 * math.sin(i * 0.7))
        y = cy + math.sin(angle) * radius * (0.54 + 0.08 * math.cos(i * 0.43))
        r = 0.75 + (i % 5) * 0.15
        points.append(circle(x, y, r, fill=palette[i % len(palette)],
                             opacity=0.34 + (i % 4) * 0.08))
    links = [
        line(cx - 68, cy + 3, cx - 24, cy - 22, stroke=CYAN, sw=0.8,
             opacity=0.28),
        line(cx - 24, cy - 22, cx + 26, cy + 15, stroke=GREEN, sw=0.8,
             opacity=0.28),
        line(cx + 26, cy + 15, cx + 74, cy - 8, stroke=PINK, sw=0.8,
             opacity=0.28),
    ]
    return group("".join(links + points), filter_id="v4-dot-glow")


def feature_row(x: float, y: float, color: str, title: str,
                subtitle: str) -> str:
    return "".join([
        circle(x, y - 4, 5.3, fill=color, opacity=0.90),
        txt(x + 15, y, title, size=13.2, fill=INK, weight=700,
            letter=0.25),
        txt(x + 15, y + 17, subtitle, size=10.8, fill=MUTED),
    ])


def teaser() -> str:
    real_video = FRAMES / "group_0012_mid.jpg"
    reconstruction = ASSETS / "internet-media-reconstruction-concept.png"
    game_a = ASSETS / "game-medieval-concept.png"
    game_b = ASSETS / "game-fantasy-concept.png"
    required = [real_video, reconstruction, game_a, game_b]
    missing = [str(item) for item in required if not item.exists()]
    if missing:
        raise FileNotFoundError("Missing V4 assets: " + ", ".join(missing))

    extra_defs = "".join([
        clip("v4-tl", 102, 50, 798, 440),
        clip("v4-tr", 900, 50, 798, 440),
        clip("v4-bl", 102, 490, 798, 440),
        clip("v4-br", 900, 490, 798, 440),
        """
  <linearGradient id="v4-center" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#FFFFFF" stop-opacity="0"/>
    <stop offset="0.14" stop-color="#FFFFFF" stop-opacity="0.79"/>
    <stop offset="0.29" stop-color="#FFFFFF" stop-opacity="0.93"/>
    <stop offset="0.71" stop-color="#FFFFFF" stop-opacity="0.93"/>
    <stop offset="0.86" stop-color="#FFFFFF" stop-opacity="0.79"/>
    <stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/>
  </linearGradient>
  <linearGradient id="v4-center-vertical" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#FFFFFF" stop-opacity="0"/>
    <stop offset="0.16" stop-color="#FFFFFF" stop-opacity="0.26"/>
    <stop offset="0.50" stop-color="#FFFFFF" stop-opacity="0.50"/>
    <stop offset="0.84" stop-color="#FFFFFF" stop-opacity="0.26"/>
    <stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/>
  </linearGradient>
  <linearGradient id="v4-panel-shade" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#071725" stop-opacity="0.02"/>
    <stop offset="0.72" stop-color="#071725" stop-opacity="0.02"/>
    <stop offset="1" stop-color="#071725" stop-opacity="0.20"/>
  </linearGradient>
  <filter id="v4-route-blur" x="-25%" y="-35%" width="150%" height="170%">
    <feGaussianBlur stdDeviation="4.2"/>
  </filter>
  <filter id="v4-dot-glow" x="-120%" y="-120%" width="340%" height="340%">
    <feGaussianBlur stdDeviation="1.5" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="v4-camera-glow" x="-60%" y="-70%" width="220%" height="240%">
    <feGaussianBlur stdDeviation="0.7" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="v4-center-shadow" x="-20%" y="-20%" width="140%" height="150%">
    <feDropShadow dx="0" dy="6" stdDeviation="11" flood-color="#223546" flood-opacity="0.16"/>
  </filter>
        """,
    ])

    parts: list[str] = [
        rect(0, 0, W, H, fill="#F7FBFD"),
        # Four OpenFly-style scene quadrants.
        image(real_video, 102, 50, 798, 440, clip="url(#v4-tl)"),
        image(reconstruction, 900, 50, 798, 440, clip="url(#v4-tr)"),
        image(game_a, 102, 490, 798, 440, clip="url(#v4-bl)"),
        image(game_b, 900, 490, 798, 440, clip="url(#v4-br)"),
        rect(102, 50, 1596, 880, fill="url(#v4-panel-shade)"),
        line(900, 50, 900, 930, stroke="#FFFFFF", sw=3.0, opacity=0.88),
        line(102, 490, 1698, 490, stroke="#FFFFFF", sw=3.0, opacity=0.88),
        # Pastel vertical engine/source labels reproduce the paper's hierarchy.
        rect(50, 50, 52, 440, fill="#DDF2F8"),
        rect(50, 490, 52, 440, fill="#DFECF8"),
        rect(1698, 50, 52, 440, fill="#E3F4EA"),
        rect(1698, 490, 52, 440, fill="#EBF5D9"),
        vertical_text(80, 270, "INTERNET VIDEO", fill="#315665", size=17),
        vertical_text(80, 710, "GAME ADAPTER A", fill="#35556B", size=17),
        vertical_text(1720, 270, "3D RECONSTRUCTION", fill="#3D6650", size=16),
        vertical_text(1720, 710, "GAME ADAPTER B", fill="#59672B", size=17),
    ]

    # Transparent flight paths are drawn before the center glass, as in OpenFly.
    parts += [
        transparent_route(
            "M126,422 C223,375 276,388 352,326 C430,261 493,298 566,235 C640,172 728,191 866,106",
            CYAN,
            [(140,416),(224,375),(352,326),(471,286),(566,235),(690,185),(854,113)],
            [(266,365,-18,0.72),(526,263,-25,0.72),(746,170,-23,0.67)],
        ),
        transparent_route(
            "M934,414 C1017,370 1088,390 1164,331 C1241,271 1311,303 1390,246 C1485,178 1557,214 1670,121",
            PINK,
            [(948,407),(1040,366),(1164,331),(1280,286),(1390,246),(1531,195),(1658,130)],
            [(1056,360,-18,0.68),(1348,266,-21,0.72),(1576,176,-25,0.66)],
        ),
        transparent_route(
            "M126,882 C225,826 297,852 375,793 C456,733 520,770 596,696 C676,619 760,651 865,548",
            GREEN,
            [(142,874),(237,826),(375,793),(493,754),(596,696),(714,633),(852,559)],
            [(275,817,-17,0.71),(545,732,-29,0.72),(761,620,-25,0.68)],
        ),
        transparent_route(
            "M936,882 C1024,829 1091,847 1171,783 C1250,718 1318,742 1394,674 C1475,601 1567,648 1670,548",
            LIME,
            [(950,874),(1037,827),(1171,783),(1282,728),(1394,674),(1535,622),(1657,559)],
            [(1062,821,-16,0.68),(1346,696,-27,0.72),(1581,606,-24,0.68)],
        ),
    ]

    # Small, low-contrast provenance tags keep real samples and concepts distinct.
    parts += [
        panel_tag(124, 70, "REAL INTERNET SAMPLE",
                  "SpatialVID-HQ / estimated geometry", CYAN, 285),
        panel_tag(1394, 70, "CONCEPT VISUAL",
                  "internet media to reconstruction", PINK, 280),
        panel_tag(124, 862, "CONCEPT VISUAL",
                  "controllable medieval world", GREEN, 260),
        panel_tag(1418, 862, "CONCEPT VISUAL",
                  "controllable fantasy world", LIME, 258),
    ]

    # OpenFly-like translucent center strip and concise platform summary.
    center = "".join([
        rect(552, 82, 696, 816, fill="url(#v4-center)", opacity=0.98),
        rect(468, 242, 864, 500, fill="url(#v4-center-vertical)", opacity=0.62),
        txt(900, 266, "MULTI-SOURCE CAMERA DATA PLATFORM", size=13.2,
            fill="#2A7F9A", weight=700, anchor="middle", letter=2.1),
        txt(900, 332, "Game Camera", size=49, fill=INK, weight=700,
            anchor="middle", family="Arial"),
        txt(900, 382, "Capture Lab", size=49, fill=INK, weight=700,
            anchor="middle", family="Arial"),
        line(748, 404, 1052, 404, stroke="#4DBAD4", sw=2.0,
             opacity=0.76),
        vector_point_cloud(900, 482),
        wire_camera(819, 481, -9, CYAN, 0.46),
        wire_camera(967, 470, 14, PINK, 0.46),
        feature_row(704, 573, CYAN, "UNIFIED ADAPTERS",
                    "engine-specific pose and control"),
        feature_row(704, 626, GREEN, "6-DOF CAMERA POSE",
                    "measured or estimated, never conflated"),
        feature_row(934, 573, PINK, "POINT + TRAJECTORY",
                    "keyframes, paths, and replay"),
        feature_row(934, 626, LIME, "SYNCHRONIZED CAPTURE",
                    "RGB / video / time / provenance"),
        rect(700, 682, 400, 43, fill="#EEF7F9", stroke="#B8DDE5",
             sw=0.8, rx=9, opacity=0.88),
        txt(900, 700, "REAL DATA  +  CONTROLLABLE GAME DATA", size=11.8,
            fill="#254957", weight=700, anchor="middle", letter=0.85),
        txt(900, 717, "dataset-ready camera supervision", size=10.8,
            fill=MUTED, anchor="middle", italic=True),
    ])
    parts.append(group(center, filter_id="v4-center-shadow"))

    # Thin outer keyline gives the composition a paper-figure finish.
    parts += [
        rect(50, 50, 1700, 880, fill="none", stroke="#C4D9E1", sw=1.3,
             rx=4),
        txt(900, 958,
            "Real internet samples and generated concept scenes are explicitly labeled; camera paths and frusta are editable vectors.",
            size=11.5, fill="#657985", anchor="middle", italic=True),
    ]

    return document(
        "OpenFly-style multi-source camera-data teaser V4",
        "A two-row academic teaser modeled on OpenFly's source-panel composition, with transparent thin camera trajectories, small glowing waypoints, and unfilled camera frusta.",
        "".join(parts),
        extra_defs=extra_defs,
        background="#F7FBFD",
    )


def main() -> None:
    OUTPUT.write_text(teaser(), encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
