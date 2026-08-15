from __future__ import annotations

import base64
import json
import mimetypes
from html import escape
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets" / "v2"
SPATIAL = ASSETS / "spatialvid_samples"
FRAMES = SPATIAL / "frames"

W, H = 1800, 980
INK = "#11263E"
MUTED = "#597087"
BLUE = "#078DB8"
CYAN = "#31C6E4"
LIME = "#A8D63A"
GREEN = "#2DA27A"
AMBER = "#E7A13B"
MAGENTA = "#D45FC1"
PURPLE = "#806FD0"
GRID = "#C5D9E5"


def attrs(**values: object) -> str:
    result: list[str] = []
    for key, value in values.items():
        if value is None:
            continue
        key = key.rstrip("_").replace("_", "-")
        result.append(f'{key}="{escape(str(value), quote=True)}"')
    return " ".join(result)


def rect(x: float, y: float, w: float, h: float, *, fill: str = "none",
         stroke: str = "none", sw: float = 1, rx: float = 0,
         opacity: float | None = None, clip: str | None = None) -> str:
    return f'<rect {attrs(x=x, y=y, width=w, height=h, fill=fill, stroke=stroke, stroke_width=sw, rx=rx, opacity=opacity, clip_path=clip)}/>'


def circle(cx: float, cy: float, r: float, *, fill: str = "none",
           stroke: str = "none", sw: float = 1,
           opacity: float | None = None) -> str:
    return f'<circle {attrs(cx=cx, cy=cy, r=r, fill=fill, stroke=stroke, stroke_width=sw, opacity=opacity)}/>'


def line(x1: float, y1: float, x2: float, y2: float, *,
         stroke: str = INK, sw: float = 2, dash: str | None = None,
         marker: str | None = None, opacity: float | None = None) -> str:
    return f'<line {attrs(x1=x1, y1=y1, x2=x2, y2=y2, stroke=stroke, stroke_width=sw, stroke_dasharray=dash, marker_end=marker, opacity=opacity, fill="none")}/>'


def path(d: str, *, fill: str = "none", stroke: str = INK,
         sw: float = 2, dash: str | None = None,
         marker: str | None = None, opacity: float | None = None,
         filter_: str | None = None) -> str:
    return f'<path {attrs(d=d, fill=fill, stroke=stroke, stroke_width=sw, stroke_dasharray=dash, marker_end=marker, opacity=opacity, filter=filter_)}/>'


def txt(x: float, y: float, value: object, *, size: float = 24,
        fill: str = INK, weight: int = 400, anchor: str = "start",
        family: str = "Arial", italic: bool = False,
        letter: float | None = None, opacity: float | None = None) -> str:
    return f'<text {attrs(x=x, y=y, font_size=size, fill=fill, font_weight=weight, text_anchor=anchor, font_family=family, font_style="italic" if italic else None, letter_spacing=letter, opacity=opacity)}>{escape(str(value))}</text>'


def data_uri(source: Path) -> str:
    mime = mimetypes.guess_type(source.name)[0] or "image/png"
    encoded = base64.b64encode(source.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def image(source: Path, x: float, y: float, w: float, h: float,
          *, clip: str | None = None, opacity: float | None = None) -> str:
    return f'<image {attrs(href=data_uri(source), x=x, y=y, width=w, height=h, preserveAspectRatio="xMidYMid slice", clip_path=clip, opacity=opacity)}/>'


def clip(clip_id: str, x: float, y: float, w: float, h: float,
         rx: float = 0) -> str:
    return f'<clipPath id="{clip_id}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}"/></clipPath>'


def definitions(extra: str = "") -> str:
    return f"""
<defs>
  <linearGradient id="glass" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#FFFFFF" stop-opacity="0"/>
    <stop offset="0.20" stop-color="#FFFFFF" stop-opacity="0.86"/>
    <stop offset="0.50" stop-color="#FFFFFF" stop-opacity="0.97"/>
    <stop offset="0.80" stop-color="#FFFFFF" stop-opacity="0.86"/>
    <stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/>
  </linearGradient>
  <linearGradient id="pale" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#E8F7FD"/>
    <stop offset="1" stop-color="#FAFDFE"/>
  </linearGradient>
  <linearGradient id="contract" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#DDF4FA"/>
    <stop offset="0.55" stop-color="#F7FCFE"/>
    <stop offset="1" stop-color="#E7F6ED"/>
  </linearGradient>
  <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
    <feGaussianBlur stdDeviation="5" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
    <feDropShadow dx="0" dy="7" stdDeviation="10" flood-color="#0B3045" flood-opacity="0.20"/>
  </filter>
  <marker id="a-blue" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto"><path d="M0,0 L12,6 L0,12 z" fill="{BLUE}"/></marker>
  <marker id="a-cyan" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto"><path d="M0,0 L12,6 L0,12 z" fill="{CYAN}"/></marker>
  <marker id="a-lime" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto"><path d="M0,0 L12,6 L0,12 z" fill="{LIME}"/></marker>
  <marker id="a-magenta" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto"><path d="M0,0 L12,6 L0,12 z" fill="{MAGENTA}"/></marker>
  {extra}
</defs>
"""


def document(title_value: str, description: str, body: str, *,
             extra_defs: str = "", background: str = "#FFFFFF") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-labelledby="title desc">'
        f'<title id="title">{escape(title_value)}</title>'
        f'<desc id="desc">{escape(description)}</desc>'
        f'{definitions(extra_defs)}{rect(0, 0, W, H, fill=background)}{body}</svg>\n'
    )


def camera(x: float, y: float, color: str, *, angle: float = 0,
           scale: float = 1) -> str:
    return f'<g transform="translate({x} {y}) rotate({angle}) scale({scale})">' + "".join([
        circle(0, 0, 9, fill="#FFFFFF", stroke=color, sw=4),
        path("M11,-7 L35,-17 L35,17 L11,7 Z",
             fill=color, stroke="#FFFFFF", sw=1),
    ]) + "</g>"


def dark_label(x: float, y: float, title_value: str, subtitle: str,
               color: str, width: float = 390) -> str:
    return "".join([
        rect(x, y, width, 68, fill="#071B29", opacity=0.80),
        rect(x, y, 8, 68, fill=color),
        txt(x + 23, y + 28, title_value, size=15, fill="#FFFFFF",
            weight=700, letter=1.0),
        txt(x + 23, y + 52, subtitle, size=13, fill="#D7E9F0"),
    ])


def title_band(number: str, title_value: str, subtitle: str) -> str:
    return "".join([
        txt(67, 54, number, size=16, fill=BLUE, weight=700, letter=2.2),
        txt(67, 100, title_value, size=39, fill=INK, weight=700,
            family="Times New Roman"),
        txt(67, 135, subtitle, size=18, fill=MUTED),
        line(67, 158, 1733, 158, stroke=GRID, sw=1.5),
    ])


def stage(x: float, y: float, number: str, title_value: str,
          color: str) -> str:
    return "".join([
        circle(x, y, 18, fill=color),
        txt(x, y + 6, number, size=15, fill="#FFFFFF", weight=700,
            anchor="middle"),
        txt(x + 31, y + 7, title_value, size=20, fill=INK, weight=700),
    ])


def teaser() -> str:
    internet_a = FRAMES / "group_0012_mid.jpg"
    internet_b = FRAMES / "group_0036_mid.jpg"
    internet_c = FRAMES / "group_0048_mid.jpg"
    game_a = ASSETS / "game-medieval-concept.png"
    game_b = ASSETS / "game-fantasy-concept.png"
    scene_3d = SPATIAL / "3dgs" / "preview_01.png"
    clips = "".join([
        clip("t-a", 0, 0, 600, 490),
        clip("t-b", 600, 0, 600, 490),
        clip("t-c", 1200, 0, 600, 490),
        clip("t-d", 0, 490, 720, 490),
        clip("t-e", 720, 490, 720, 490),
        clip("t-f", 1440, 490, 360, 490),
    ])
    parts = [
        image(internet_a, 0, 0, 600, 490, clip="url(#t-a)"),
        image(internet_b, 600, 0, 600, 490, clip="url(#t-b)"),
        image(internet_c, 1200, 0, 600, 490, clip="url(#t-c)"),
        image(game_a, 0, 490, 720, 490, clip="url(#t-d)"),
        image(game_b, 720, 490, 720, 490, clip="url(#t-e)"),
        image(scene_3d, 1440, 490, 360, 490, clip="url(#t-f)"),
        line(600, 0, 600, 490, stroke="#FFFFFF", sw=5),
        line(1200, 0, 1200, 490, stroke="#FFFFFF", sw=5),
        line(720, 490, 720, 980, stroke="#FFFFFF", sw=5),
        line(1440, 490, 1440, 980, stroke="#FFFFFF", sw=5),
        line(0, 490, 1800, 490, stroke="#FFFFFF", sw=6),
    ]
    routes = [
        ("M55,405 C181,333 265,376 392,273 C466,213 518,187 566,121",
         CYAN, "url(#a-cyan)"),
        ("M648,420 C754,354 835,390 935,304 C1032,222 1082,227 1166,153",
         LIME, "url(#a-lime)"),
        ("M1237,404 C1331,355 1419,362 1502,294 C1592,219 1662,224 1751,157",
         AMBER, "url(#a-blue)"),
        ("M53,897 C200,845 321,817 422,713 C507,627 599,647 685,552",
         CYAN, "url(#a-cyan)"),
        ("M769,897 C902,842 997,830 1087,733 C1194,617 1272,658 1400,549",
         MAGENTA, "url(#a-magenta)"),
        ("M1468,889 C1511,822 1538,811 1579,730 C1621,646 1665,627 1760,557",
         LIME, "url(#a-lime)"),
    ]
    for route, color, marker in routes:
        parts += [
            path(route, stroke="#FFFFFF", sw=10, opacity=0.68),
            path(route, stroke=color, sw=5, marker=marker,
                 filter_="url(#glow)"),
        ]
    nodes = [
        (128,365,CYAN,-20),(375,284,CYAN,-30),(496,202,CYAN,-22),
        (708,386,LIME,-20),(941,300,LIME,-28),(1093,214,LIME,-20),
        (1302,370,AMBER,-20),(1515,284,AMBER,-28),(1681,212,AMBER,-18),
        (145,859,CYAN,-15),(419,714,CYAN,-30),(605,623,CYAN,-28),
        (857,858,MAGENTA,-18),(1085,733,MAGENTA,-35),(1305,626,MAGENTA,-25),
        (1511,828,LIME,-55),(1591,713,LIME,-55),(1702,608,LIME,-35),
    ]
    for x, y, color, angle in nodes:
        parts.append(camera(x, y, color, angle=angle, scale=0.62))
    parts += [
        dark_label(22, 21, "INTERNET VIDEO",
                   "real scenes + estimated camera geometry", CYAN),
        dark_label(22, 892, "CONTROLLABLE GAME WORLDS",
                   "measured pose, point sets, replay", MAGENTA, 420),
        dark_label(1368, 892, "PUBLIC 3D SCENES",
                   "internet 3DGS assets and provenance", LIME, 410),
        rect(568, 126, 664, 722, fill="url(#glass)"),
        txt(900, 270, "REAL + VIRTUAL", size=21, fill=BLUE, weight=700,
            anchor="middle", letter=4.2),
        txt(900, 350, "Camera-Aware", size=59, fill=INK, weight=700,
            anchor="middle", family="Times New Roman"),
        txt(900, 414, "Visual Data", size=59, fill=INK, weight=700,
            anchor="middle", family="Times New Roman"),
        line(726, 452, 1074, 452, stroke=BLUE, sw=3),
        txt(900, 499, "Internet videos and 3D scenes", size=27, fill=INK,
            anchor="middle", family="Times New Roman"),
        txt(900, 539, "+", size=25, fill=BLUE, weight=700,
            anchor="middle"),
        txt(900, 582, "Controllable game environments", size=27, fill=INK,
            anchor="middle", family="Times New Roman"),
        txt(900, 648,
            "RGB / VIDEO  -  CAMERA  -  TIME  -  PROVENANCE",
            size=16, fill=INK, weight=700, anchor="middle", letter=1.1),
        txt(900, 691,
            "estimated and measured poses remain explicitly separated",
            size=17, fill=MUTED, anchor="middle", italic=True),
        rect(548, 944, 704, 26, fill="#071A28", opacity=0.78),
        txt(900, 963,
            "SpatialVID-HQ: CC BY-NC-SA 4.0; generated game scenes are concept visuals",
            size=12.5, fill="#FFFFFF", anchor="middle"),
    ]
    return document(
        "Camera-aware real and virtual data teaser",
        "Full-bleed teaser combining real internet-video frames, public 3D scene previews, original game-world concepts, and editable camera trajectories.",
        "".join(parts), extra_defs=clips, background="#071A28",
    )


def framework() -> str:
    real_a = FRAMES / "group_0012_mid.jpg"
    real_b = FRAMES / "group_0036_mid.jpg"
    real_c = FRAMES / "group_0048_mid.jpg"
    game_a = ASSETS / "game-medieval-concept.png"
    game_b = ASSETS / "game-fantasy-concept.png"
    scene_3d = SPATIAL / "3dgs" / "preview_01.png"
    source_specs = [
        (60, 90, real_a, CYAN, "INTERNET VIDEO", "estimated pose + intrinsics"),
        (325, 90, real_b, CYAN, "REAL-WORLD VIDEO", "caption + motion + score"),
        (60, 307, real_c, CYAN, "DIVERSE REAL SCENES", "natural / urban / indoor"),
        (325, 307, game_a, MAGENTA, "GAME ADAPTER A", "runtime measured pose"),
        (60, 524, game_b, PURPLE, "GAME ADAPTER B", "engine-specific control"),
        (325, 524, scene_3d, LIME, "PUBLIC 3DGS", "scene assets + provenance"),
    ]
    clips = "".join(
        clip(f"f-{index}", x, y, 250, 175, 12)
        for index, (x, y, _, _, _, _) in enumerate(source_specs)
    )
    clips += "".join([
        clip("f-route", 1246, 112, 482, 236, 14),
        clip("f-out1", 1302, 570, 116, 78, 8),
        clip("f-out2", 1434, 570, 116, 78, 8),
        clip("f-out3", 1566, 570, 116, 78, 8),
    ])
    parts = [
        rect(30, 28, 575, 760, fill="url(#pale)",
             stroke="#A8D4E5", sw=2, rx=22),
        txt(318, 65, "Heterogeneous visual sources", size=26, fill=INK,
            weight=700, anchor="middle", family="Times New Roman"),
    ]
    for index, (x, y, source, color, title_value, subtitle) in enumerate(source_specs):
        parts += [
            image(source, x, y, 250, 175, clip=f"url(#f-{index})"),
            rect(x, y + 147, 250, 28, fill="#071C2B", opacity=0.86),
            rect(x, y + 147, 6, 28, fill=color),
            txt(x + 15, y + 159, title_value, size=10.5,
                fill="#FFFFFF", weight=700, letter=0.55),
            txt(x + 15, y + 171, subtitle, size=9.7, fill="#D9EAF1"),
        ]
    parts += [
        rect(58, 720, 519, 48, fill="#FFFFFF", stroke=GRID, sw=1.2,
             rx=10),
        txt(77, 741, "SOURCE-SPECIFIC INTERFACES", size=12, fill=BLUE,
            weight=700, letter=1.0),
        txt(77, 758, "decode / license / estimate / inject / read back",
            size=12, fill=MUTED),
        line(605, 407, 650, 407, stroke=BLUE, sw=4,
             marker="url(#a-blue)"),
        rect(657, 28, 1113, 760, fill="url(#pale)",
             stroke="#8CCBE0", sw=2.3, rx=22),
        txt(1214, 67, "Camera-aware automatic curation", size=29,
            fill=INK, weight=700, anchor="middle",
            family="Times New Roman"),
        txt(1214, 92,
            "image-led toolchain for real, reconstructed, and virtual data",
            size=15, fill=MUTED, anchor="middle", italic=True),
        stage(696, 137, "1", "Audit source", CYAN),
        stage(952, 137, "2", "Recover camera", AMBER),
        stage(1280, 137, "3", "Plan samples", GREEN),
        rect(681, 171, 203, 177, fill="#FFFFFF", stroke=GRID, sw=1.3,
             rx=12),
        txt(703, 203, "media decode", size=16, fill=INK, weight=700),
        txt(703, 237, "URL / dataset ID", size=14, fill=MUTED),
        txt(703, 267, "license + access", size=14, fill=MUTED),
        txt(703, 297, "integrity + dedup", size=14, fill=MUTED),
        rect(703, 318, 156, 18, fill="#E5F6FB", rx=9),
        txt(781, 331, "provenance retained", size=10.5, fill=BLUE,
            weight=700, anchor="middle"),
        line(894, 258, 925, 258, stroke=BLUE, sw=3,
             marker="url(#a-blue)"),
        rect(932, 171, 280, 177, fill="#FFFFFF", stroke=GRID, sw=1.3,
             rx=12),
    ]
    for x, y, color, dash in [
        (981, 224, GREEN, None),
        (1084, 205, AMBER, "7 5"),
        (1140, 269, AMBER, "7 5"),
    ]:
        parts += [
            camera(x, y, color, angle=-18, scale=0.52),
            line(x + 12, y, 1064, 268, stroke=color, sw=2, dash=dash),
        ]
    parts += [
        circle(1056, 263, 5, fill=CYAN),
        circle(1073, 279, 4, fill=BLUE),
        circle(1090, 257, 4, fill=LIME),
        circle(1038, 282, 3, fill=CYAN),
        txt(954, 321, "MEASURED", size=10.5, fill=GREEN, weight=700),
        line(1024, 317, 1056, 317, stroke=GREEN, sw=3),
        txt(1070, 321, "ESTIMATED", size=10.5, fill=AMBER, weight=700),
        line(1140, 317, 1172, 317, stroke=AMBER, sw=3, dash="7 5"),
        line(1220, 258, 1248, 258, stroke=BLUE, sw=3,
             marker="url(#a-blue)"),
        image(real_b, 1246, 112, 482, 236, clip="url(#f-route)"),
        rect(1246, 112, 482, 236, fill="#071927", opacity=0.08,
             clip="url(#f-route)"),
        path("M1280,315 C1357,274 1402,305 1469,233 C1536,161 1601,219 1691,131",
             stroke="#FFFFFF", sw=9, opacity=0.68),
        path("M1280,315 C1357,274 1402,305 1469,233 C1536,161 1601,219 1691,131",
             stroke=LIME, sw=4, marker="url(#a-lime)",
             filter_="url(#glow)"),
        camera(1351, 278, LIME, angle=-25, scale=0.48),
        camera(1470, 232, LIME, angle=-35, scale=0.48),
        camera(1606, 189, LIME, angle=-25, scale=0.48),
        rect(1246, 320, 482, 28, fill="#061B29", opacity=0.82),
        txt(1260, 340,
            "aesthetic target + spatial diversity + motion",
            size=12.5, fill="#FFFFFF", weight=700, letter=0.5),
        line(1214, 358, 1214, 395, stroke=BLUE, sw=3,
             marker="url(#a-blue)"),
        rect(681, 410, 316, 292, fill="#FFFFFF", stroke=GRID, sw=1.4,
             rx=14),
        stage(710, 444, "4", "Multi-view points", BLUE),
    ]
    for yy, color, layer in [
        (505, MAGENTA, "L4"), (548, PURPLE, "L3"),
        (591, BLUE, "L2"), (634, CYAN, "L1"),
    ]:
        parts += [
            path(f"M750,{yy} L934,{yy-22} L968,{yy+1} L784,{yy+23} Z",
                 fill=color, opacity=0.17),
            txt(727, yy + 6, layer, size=12, fill=color, weight=700,
                anchor="middle"),
        ]
        for index in range(6):
            parts.append(circle(798 + index * 26, yy + 8 - index * 3,
                                3.6, fill=color))
    parts += [
        txt(839, 682, "boundary -> hull -> layers -> views", size=13,
            fill=MUTED, anchor="middle"),
        rect(1018, 410, 235, 292, fill="#FFFFFF", stroke=GRID, sw=1.4,
             rx=14),
        stage(1047, 444, "5", "Trajectories", MAGENTA),
        path("M1050,649 C1081,593 1099,617 1121,550 C1142,486 1178,568 1218,497",
             stroke="#E7EEF2", sw=12),
        path("M1050,649 C1081,593 1099,617 1121,550 C1142,486 1178,568 1218,497",
             stroke=MAGENTA, sw=4, marker="url(#a-magenta)"),
        camera(1080, 598, MAGENTA, angle=-45, scale=0.42),
        camera(1122, 550, MAGENTA, angle=-55, scale=0.42),
        camera(1180, 543, MAGENTA, angle=-15, scale=0.42),
        txt(1135, 682, "pose + time + score", size=13, fill=MUTED,
            anchor="middle"),
        rect(1274, 410, 454, 292, fill="#FFFFFF", stroke=GRID, sw=1.4,
             rx=14),
        stage(1303, 444, "6", "Validate and export", GREEN),
    ]
    for index, check in enumerate([
        "pose status / residual",
        "frame-time alignment",
        "source and rights record",
    ]):
        yy = 491 + index * 29
        parts += [
            circle(1320, yy - 4, 7, fill="#E7F6EE", stroke=GREEN, sw=1.5),
            path(f"M1316,{yy-4} l3,3 l6,-8", stroke=GREEN, sw=1.6),
            txt(1337, yy, check, size=13, fill=MUTED),
        ]
    parts += [
        image(real_a, 1302, 570, 116, 78, clip="url(#f-out1)"),
        image(game_a, 1434, 570, 116, 78, clip="url(#f-out2)"),
        image(scene_3d, 1566, 570, 116, 78, clip="url(#f-out3)"),
        txt(1492, 681, "stills  |  clips  |  3D scenes", size=13,
            fill=MUTED, anchor="middle"),
        line(1214, 702, 1214, 733, stroke=BLUE, sw=3,
             marker="url(#a-blue)"),
        rect(98, 824, 1604, 111, fill="url(#contract)",
             stroke="#9ACFDE", sw=1.8, rx=17),
        txt(128, 859, "Unified camera-aware record", size=22,
            fill="#0D6784", weight=700, family="Times New Roman"),
    ]
    contract_fields = [
        (128, "RGB / video"), (286, "intrinsics"),
        (419, "pose + confidence"), (612, "timestamp"),
        (748, "caption / action"), (926, "aesthetic score"),
        (1103, "provenance / license"), (1342, "quality evidence"),
    ]
    for x, value in contract_fields:
        parts.append(txt(x, 893, value, size=15, fill=INK, weight=700))
    parts += [
        txt(128, 921,
            "The shared schema normalizes fields without erasing the difference between estimated internet-video pose and measured game-camera pose.",
            size=13, fill=MUTED),
        txt(900, 965,
            "SpatialVID-HQ: CC BY-NC-SA 4.0; generated game panels are concept visuals",
            size=12.5, fill=MUTED, anchor="middle", italic=True),
    ]
    return document(
        "Heterogeneous camera-aware visual-data framework",
        "Image-led pipeline integrating gated internet videos, public 3D scenes, and controllable game worlds while separating estimated and measured camera pose.",
        "".join(parts), extra_defs=clips,
    )


def score_values(path_value: Path) -> list[float]:
    values = []
    for row in path_value.read_text(encoding="utf-8").splitlines():
        if row.strip():
            values.append(float(json.loads(row)["aesthetic_score"]))
    return values


def annotation_figure() -> str:
    groups = ["group_0012", "group_0036", "group_0048"]
    colors = [GREEN, PURPLE, MAGENTA]
    frame_paths = [FRAMES / f"{group}_mid.jpg" for group in groups]
    clips = "".join(
        clip(f"s-{index}", 75 + index * 568, 198, 530, 294, 14)
        for index in range(3)
    )
    parts = [
        title_band(
            "FIGURE 09 / INTERNET VIDEO DATA",
            "Internet Videos Already Carry Camera-Aware Supervision",
            "Three real SpatialVID-HQ samples from /data/EZCAM2/dataset.zip; poses are estimated annotations, not sensor ground truth.",
        )
    ]
    for index, (group, color, frame) in enumerate(
        zip(groups, colors, frame_paths)
    ):
        x = 75 + index * 568
        annotation_dir = SPATIAL / "annotations" / group
        poses = np.load(annotation_dir / "poses.npy")
        intrinsics = np.load(annotation_dir / "intrinsics.npy")
        caption = json.loads(
            (annotation_dir / "caption.json").read_text(encoding="utf-8")
        )
        scores = score_values(
            annotation_dir / "aesthetic_scores_0p01s.jsonl"
        )
        instructions = json.loads(
            (annotation_dir / "instructions.json").read_text(encoding="utf-8")
        )
        xy = poses[:, [0, 2]]
        minimum = xy.min(axis=0)
        maximum = xy.max(axis=0)
        span = np.maximum(maximum - minimum, 1e-6)
        plot = [
            (
                x + 53 + float((point[0] - minimum[0]) / span[0]) * 205,
                713 - float((point[1] - minimum[1]) / span[1]) * 132,
            )
            for point in xy
        ]
        points = " ".join(f"{px:.2f},{py:.2f}" for px, py in plot)
        scene_type = caption["CategoryTags"]["sceneType"]["second"]
        parts += [
            image(frame, x, 198, 530, 294, clip=f"url(#s-{index})"),
            rect(x, 198, 530, 294, fill="#071A28", opacity=0.04,
                 clip=f"url(#s-{index})"),
            rect(x, 454, 530, 38, fill="#071B29", opacity=0.82),
            txt(x + 17, 479, group, size=15, fill="#FFFFFF",
                weight=700, letter=0.8),
            txt(x + 513, 479, scene_type, size=14, fill="#D9EDF4",
                anchor="end"),
            rect(x, 520, 530, 267, fill="#FFFFFF", stroke=GRID, sw=1.4,
                 rx=13),
            txt(x + 24, 553, "estimated camera path", size=16,
                fill=color, weight=700),
            rect(x + 23, 576, 246, 160, fill="#F8FBFD",
                 stroke="#D8E4EB", sw=1),
            f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>',
            circle(plot[0][0], plot[0][1], 6, fill="#FFFFFF",
                   stroke=color, sw=3),
            circle(plot[-1][0], plot[-1][1], 7, fill=color,
                   stroke="#FFFFFF", sw=2),
            txt(x + 291, 553, "sample metadata", size=16,
                fill=INK, weight=700),
            txt(x + 291, 586, f"pose: {poses.shape[0]} x 7",
                size=14, fill=MUTED),
            txt(x + 291, 613, f"intrinsics: {intrinsics.shape[0]} x 4",
                size=14, fill=MUTED),
            txt(x + 291, 640,
                f"score range: {min(scores):.2f} - {max(scores):.2f}",
                size=14, fill=MUTED),
            txt(x + 291, 667, f"score mean: {sum(scores)/len(scores):.2f}",
                size=14, fill=MUTED),
            txt(x + 291, 694, f"motion spans: {len(instructions)}",
                size=14, fill=MUTED),
            txt(x + 291, 721, "pose confidence", size=13, fill=MUTED),
            rect(x + 291, 737, 112, 24, fill="#FFF5E7",
                 stroke="#EAC486", sw=1, rx=12),
            txt(x + 347, 754, "ESTIMATED", size=11.5,
                fill="#9A6517", weight=700, anchor="middle", letter=0.7),
        ]
        if index < 2:
            parts.append(
                line(x + 544, 643, x + 560, 643, stroke=BLUE, sw=2.5,
                     marker="url(#a-blue)")
            )
    parts += [
        rect(75, 823, 1660, 96, fill="#EAF7FB",
             stroke="#ADD6E4", sw=1.4, rx=14),
        txt(101, 855, "Each matched sample contains", size=16,
            fill="#0C6B88", weight=700),
        txt(101, 885,
            "video  +  N x 7 pose  +  N x 4 intrinsics  +  frame indexes  +  dynamic masks  +  caption  +  camera instructions  +  per-frame aesthetic scores",
            size=15, fill=INK, weight=700),
        txt(101, 909,
            "Archive index: 347,111 complete annotation bundles across 74 groups. Source: FelixYuan/SpatialVID-HQ, gated, CC BY-NC-SA 4.0.",
            size=13, fill=MUTED),
        txt(900, 963,
            "Real samples and real annotations; no claim of sensor-calibrated ground truth",
            size=12.5, fill=MUTED, anchor="middle", italic=True),
    ]
    return document(
        "Internet video camera-aware supervision",
        "Three real SpatialVID-HQ frames with estimated pose trajectories and annotation dimensions read from the cluster dataset archive.",
        "".join(parts), extra_defs=clips,
    )


FIGURES = {
    "openfly_07_multisource_teaser_v2.svg": teaser,
    "openfly_08_heterogeneous_framework_v2.svg": framework,
    "openfly_09_spatialvid_camera_supervision.svg": annotation_figure,
}


def main() -> None:
    required = [
        FRAMES / "group_0012_mid.jpg",
        FRAMES / "group_0036_mid.jpg",
        FRAMES / "group_0048_mid.jpg",
        ASSETS / "game-medieval-concept.png",
        ASSETS / "game-fantasy-concept.png",
        SPATIAL / "3dgs" / "preview_01.png",
    ]
    missing = [path_value for path_value in required if not path_value.exists()]
    if missing:
        raise SystemExit("Missing assets: " + ", ".join(map(str, missing)))
    for filename, builder in FIGURES.items():
        target = ROOT / filename
        target.write_text(builder(), encoding="utf-8", newline="\n")
        print(target)


if __name__ == "__main__":
    main()
