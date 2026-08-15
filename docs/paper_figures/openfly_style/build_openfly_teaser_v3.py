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
V2 = ROOT / "assets" / "v2"
V3 = ROOT / "assets" / "v3"
FRAMES = V2 / "spatialvid_samples" / "frames"
OUTPUT = ROOT / "openfly_07_multisource_teaser_v3.svg"


CYAN = "#37D9FF"
ICE = "#DDFBFF"
AMBER = "#FFBF66"
MAGENTA = "#F072D7"
VIOLET = "#9D8BFF"
NAVY = "#061828"


def group(content: str, *, transform: str | None = None,
          opacity: float | None = None, filter_id: str | None = None,
          clip_id: str | None = None) -> str:
    attrs: list[str] = []
    if transform:
        attrs.append(f'transform="{transform}"')
    if opacity is not None:
        attrs.append(f'opacity="{opacity}"')
    if filter_id:
        attrs.append(f'filter="url(#{filter_id})"')
    if clip_id:
        attrs.append(f'clip-path="url(#{clip_id})"')
    return f"<g {' '.join(attrs)}>{content}</g>"


def rounded_photo(source: Path, x: float, y: float, w: float, h: float,
                  clip_id: str, *, opacity: float = 1.0) -> str:
    return "".join([
        rect(x - 3, y - 3, w + 6, h + 6, fill="#E8FBFF", rx=17,
             opacity=0.96),
        image(source, x, y, w, h, clip=f"url(#{clip_id})", opacity=opacity),
        rect(x, y, w, h, fill="none", stroke="#C9F4FF", sw=1.4, rx=14,
             opacity=0.78),
    ])


def camera_frustum(x: float, y: float, angle: float, color: str,
                   scale: float = 1.0) -> str:
    content = "".join([
        '<rect x="-5" y="-5" width="10" height="10" rx="2" '
        f'fill="{NAVY}" stroke="#FFFFFF" stroke-width="1.6"/>',
        '<path d="M6,-5 L29,-13 M6,5 L29,13 M29,-13 L29,13" '
        f'fill="none" stroke="{color}" stroke-width="2.2" '
        'stroke-linecap="round" stroke-linejoin="round"/>',
        '<circle cx="0" cy="0" r="2.4" fill="#FFFFFF"/>',
    ])
    return group(
        content,
        transform=f"translate({x} {y}) rotate({angle}) scale({scale})",
        filter_id="v3-soft-glow",
    )


def waypoint(x: float, y: float, color: str, *, pulse: float = 1.0) -> str:
    return "".join([
        circle(x, y, 13 * pulse, fill="none", stroke=color, sw=1.2,
               opacity=0.24),
        circle(x, y, 7.5, fill=NAVY, stroke="#FFFFFF", sw=2.1,
               opacity=0.96),
        circle(x, y, 3.0, fill=color),
    ])


def chevron(x: float, y: float, angle: float, color: str) -> str:
    content = (
        f'<path d="M-8,-7 L1,0 L-8,7" fill="none" stroke="{color}" '
        'stroke-width="2.2" stroke-linecap="round" '
        'stroke-linejoin="round"/>'
    )
    return group(content, transform=f"translate({x} {y}) rotate({angle})",
                 filter_id="v3-soft-glow")


def source_badge(x: float, y: float, number: str, title: str,
                 subtitle: str, color: str, width: float) -> str:
    return "".join([
        rect(x, y, width, 58, fill="#071B2C", stroke="#8CDFF0", sw=1,
             rx=14, opacity=0.88),
        circle(x + 29, y + 29, 15, fill=color, opacity=0.96),
        txt(x + 29, y + 34.5, number, size=13, fill=NAVY, weight=700,
            anchor="middle"),
        txt(x + 55, y + 24, title, size=14.5, fill="#FFFFFF", weight=700,
            letter=1.0),
        txt(x + 55, y + 44, subtitle, size=11.8, fill="#C9DFE9"),
    ])


def teaser() -> str:
    background = V3 / "cinematic-multisource-world-concept.png"
    internet_a = FRAMES / "group_0012_mid.jpg"
    internet_b = FRAMES / "group_0036_mid.jpg"
    internet_c = FRAMES / "group_0048_mid.jpg"
    scene_3d = V2 / "spatialvid_samples" / "3dgs" / "preview_01.png"

    required = [background, internet_a, internet_b, internet_c, scene_3d]
    missing = [str(item) for item in required if not item.exists()]
    if missing:
        raise FileNotFoundError("Missing teaser assets: " + ", ".join(missing))

    extra_defs = "".join([
        clip("v3-real-back", 82, 220, 332, 192, 14),
        clip("v3-real-mid", 68, 232, 348, 202, 14),
        clip("v3-real-front", 52, 246, 368, 216, 14),
        clip("v3-scene", 1450, 692, 296, 166, 14),
        """
  <linearGradient id="v3-route" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#31D9FF"/>
    <stop offset="0.30" stop-color="#7BE9FF"/>
    <stop offset="0.53" stop-color="#FFD071"/>
    <stop offset="0.75" stop-color="#F072D7"/>
    <stop offset="1" stop-color="#9D8BFF"/>
  </linearGradient>
  <linearGradient id="v3-sky-shade" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#020A16" stop-opacity="0.55"/>
    <stop offset="0.28" stop-color="#071426" stop-opacity="0.06"/>
    <stop offset="0.70" stop-color="#071426" stop-opacity="0.04"/>
    <stop offset="1" stop-color="#020914" stop-opacity="0.78"/>
  </linearGradient>
  <radialGradient id="v3-vignette" cx="50%" cy="46%" r="73%">
    <stop offset="58%" stop-color="#03101D" stop-opacity="0"/>
    <stop offset="100%" stop-color="#020711" stop-opacity="0.68"/>
  </radialGradient>
  <linearGradient id="v3-glass" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#06192B" stop-opacity="0.89"/>
    <stop offset="0.52" stop-color="#0B2940" stop-opacity="0.74"/>
    <stop offset="1" stop-color="#071522" stop-opacity="0.88"/>
  </linearGradient>
  <linearGradient id="v3-title-line" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#37D9FF" stop-opacity="0"/>
    <stop offset="0.24" stop-color="#37D9FF"/>
    <stop offset="0.55" stop-color="#FFBF66"/>
    <stop offset="0.82" stop-color="#F072D7"/>
    <stop offset="1" stop-color="#9D8BFF" stop-opacity="0"/>
  </linearGradient>
  <pattern id="v3-grid" width="48" height="48" patternUnits="userSpaceOnUse">
    <path d="M48 0H0V48" fill="none" stroke="#95EFFF" stroke-opacity="0.10" stroke-width="0.8"/>
  </pattern>
  <filter id="v3-route-bloom" x="-30%" y="-40%" width="160%" height="180%">
    <feGaussianBlur stdDeviation="8"/>
  </filter>
  <filter id="v3-soft-glow" x="-80%" y="-80%" width="260%" height="260%">
    <feGaussianBlur stdDeviation="2.4" result="blur"/>
    <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <filter id="v3-panel-shadow" x="-20%" y="-25%" width="140%" height="160%">
    <feDropShadow dx="0" dy="12" stdDeviation="18" flood-color="#000814" flood-opacity="0.48"/>
  </filter>
  <filter id="v3-text-shadow" x="-30%" y="-50%" width="160%" height="200%">
    <feDropShadow dx="0" dy="3" stdDeviation="4" flood-color="#00050B" flood-opacity="0.82"/>
  </filter>
        """,
    ])

    main_route = (
        "M82,806 "
        "C210,770 309,730 407,674 "
        "C503,619 590,628 704,654 "
        "C811,678 881,628 972,550 "
        "C1068,468 1156,496 1254,449 "
        "C1357,400 1428,332 1512,303 "
        "C1602,271 1670,235 1743,178"
    )
    ghost_route_a = (
        "M250,872 C421,811 515,742 638,754 "
        "C771,767 862,721 950,656"
    )
    ghost_route_b = (
        "M1118,631 C1268,579 1341,492 1448,467 "
        "C1571,439 1658,385 1787,305"
    )

    parts: list[str] = [
        image(background, 0, 0, W, H),
        rect(0, 0, W, H, fill="url(#v3-sky-shade)"),
        rect(0, 0, W, H, fill="url(#v3-vignette)"),
        rect(1190, 0, 610, 980, fill="url(#v3-grid)", opacity=0.25),
        path("M1190,0 L1190,980", stroke="#94EEFF", sw=1,
             opacity=0.12),
    ]

    # Faint secondary flight traces create depth without competing with the hero path.
    parts += [
        path(ghost_route_a, stroke="#8CEBFF", sw=1.3, dash="3 10",
             opacity=0.28),
        path(ghost_route_b, stroke="#D9A4FF", sw=1.3, dash="3 10",
             opacity=0.30),
    ]

    # Real internet-video samples remain visibly distinct from the generated backdrop.
    photo_stack = "".join([
        rounded_photo(internet_c, 82, 220, 332, 192, "v3-real-back",
                      opacity=0.78),
        rounded_photo(internet_b, 68, 232, 348, 202, "v3-real-mid",
                      opacity=0.90),
        rounded_photo(internet_a, 52, 246, 368, 216, "v3-real-front"),
        rect(52, 408, 368, 54, fill="#061827", rx=0, opacity=0.86),
        txt(72, 431, "REAL INTERNET VIDEO", size=14.5, fill="#FFFFFF",
            weight=700, letter=1.2),
        txt(72, 451, "SpatialVID-HQ sample - estimated camera geometry",
            size=11.5, fill="#CFE8F0"),
        circle(394, 434, 5, fill=CYAN),
    ])
    parts.append(group(photo_stack, filter_id="v3-panel-shadow"))

    # Public 3D scene preview, also separated from the conceptual panorama.
    scene_card = "".join([
        rect(1438, 680, 320, 222, fill="#071A2A", stroke="#A8EAF6",
             sw=1.1, rx=18, opacity=0.91),
        rounded_photo(scene_3d, 1450, 692, 296, 166, "v3-scene"),
        txt(1452, 883, "PUBLIC 3D SCENE", size=13.5, fill="#FFFFFF",
            weight=700, letter=1.05),
        txt(1738, 883, "asset preview + provenance", size=11,
            fill="#C8DEE8", anchor="end"),
    ])
    parts.append(group(scene_card, filter_id="v3-panel-shadow"))

    # Compact glass title card keeps the landscape visible.
    title_card = "".join([
        rect(472, 42, 856, 177, fill="url(#v3-glass)",
             stroke="#9AEAF8", sw=1.1, rx=28, opacity=0.94),
        rect(725, 60, 350, 30, fill="#0A3048", stroke="#65DFF4",
             sw=0.8, rx=15, opacity=0.92),
        txt(900, 80.5, "MULTI-SOURCE CAMERA DATA", size=13.5,
            fill="#BDF6FF", weight=700, anchor="middle", letter=2.4),
        txt(900, 143, "Camera-Aware Visual Worlds", size=50,
            fill="#FFFFFF", weight=700, anchor="middle",
            family="Times New Roman"),
        rect(624, 162, 552, 2.5, fill="url(#v3-title-line)", rx=1.2),
        txt(900, 192,
            "real internet video  +  controllable game worlds  +  public 3D scenes",
            size=15.5, fill="#D8EDF3", anchor="middle", letter=0.7),
    ])
    parts.append(group(title_card, filter_id="v3-panel-shadow"))

    # One continuous cinematic trajectory: dark seat, bloom, color, white core.
    parts += [
        path(main_route, stroke="#020914", sw=17, opacity=0.48),
        path(main_route, stroke="url(#v3-route)", sw=15, opacity=0.34,
             filter_="url(#v3-route-bloom)"),
        path(main_route, stroke="url(#v3-route)", sw=6.2, opacity=0.98,
             filter_="url(#v3-soft-glow)"),
        path(main_route, stroke="#F5FEFF", sw=1.65, opacity=0.94),
    ]

    waypoint_specs = [
        (151, 783, CYAN, 1.16),
        (405, 675, CYAN, 0.92),
        (704, 654, ICE, 1.05),
        (971, 551, AMBER, 0.92),
        (1253, 449, MAGENTA, 1.08),
        (1512, 303, VIOLET, 0.92),
        (1710, 204, VIOLET, 1.12),
    ]
    for x, y, color, pulse in waypoint_specs:
        parts.append(waypoint(x, y, color, pulse=pulse))

    for x, y, angle, color in [
        (300, 735, -25, CYAN),
        (823, 660, -8, ICE),
        (1128, 481, -24, AMBER),
        (1396, 369, -31, MAGENTA),
        (1631, 255, -28, VIOLET),
    ]:
        parts.append(chevron(x, y, angle, color))

    parts += [
        camera_frustum(405, 675, -27, CYAN, 0.78),
        camera_frustum(971, 551, -23, AMBER, 0.78),
        camera_frustum(1512, 303, -25, VIOLET, 0.78),
    ]

    # Lightweight data callouts and source badges.
    parts += [
        line(198, 779, 242, 742, stroke=CYAN, sw=1.0, opacity=0.65),
        rect(236, 712, 138, 34, fill="#071A29", stroke=CYAN, sw=0.8,
             rx=10, opacity=0.84),
        txt(305, 734, "p(t)  /  R(t)", size=12.5, fill="#E8FCFF",
            anchor="middle", family="Consolas"),
        line(1260, 436, 1323, 411, stroke=MAGENTA, sw=1.0, opacity=0.65),
        rect(1315, 383, 154, 35, fill="#071A29", stroke=MAGENTA, sw=0.8,
             rx=10, opacity=0.84),
        txt(1392, 406, "SE(3) replay", size=12.5, fill="#FFF0FC",
            anchor="middle", family="Consolas"),
        source_badge(48, 490, "1", "INTERNET VIDEO",
                     "real samples / estimated pose", CYAN, 326),
        source_badge(731, 781, "2", "GAME WORLDS",
                     "concept visual / measured capture", AMBER, 354),
        source_badge(1434, 914, "3", "3D SCENES",
                     "public preview / provenance", VIOLET, 318),
    ]

    # Discreet scientific contract and provenance line.
    contract = "".join([
        rect(520, 903, 760, 50, fill="#061522", stroke="#7CDDEA", sw=0.8,
             rx=18, opacity=0.88),
        txt(900, 925, "RGB / VIDEO   -   CAMERA POSE   -   TIME   -   PROVENANCE",
            size=13.5, fill="#E8F8FB", weight=700, anchor="middle",
            letter=1.15),
        txt(900, 944,
            "real samples and generated concept imagery remain explicitly separated",
            size=11.4, fill="#AFCAD5", anchor="middle", italic=True),
    ])
    parts.append(group(contract, filter_id="v3-panel-shadow"))

    # Small corner accents give the full-bleed plate a finished figure edge.
    parts += [
        line(24, 24, 116, 24, stroke=CYAN, sw=2, opacity=0.7),
        line(24, 24, 24, 92, stroke=CYAN, sw=2, opacity=0.7),
        line(1776, 956, 1684, 956, stroke=VIOLET, sw=2, opacity=0.7),
        line(1776, 956, 1776, 888, stroke=VIOLET, sw=2, opacity=0.7),
        txt(48, 42, "TEASER", size=11.5, fill="#BFF6FF", weight=700,
            letter=2.0),
    ]

    return document(
        "Camera-aware multi-source visual data teaser V3",
        "A cinematic full-bleed teaser with a continuous editable camera trajectory, real SpatialVID-HQ sample cards, a clearly labeled generated game-world concept panorama, and a public 3D scene preview.",
        "".join(parts),
        extra_defs=extra_defs,
        background=NAVY,
    )


def main() -> None:
    OUTPUT.write_text(teaser(), encoding="utf-8")
    print(OUTPUT)


if __name__ == "__main__":
    main()
