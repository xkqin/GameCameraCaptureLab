from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parent
VECTOR_ROOT = ROOT / "vector"


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path(r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf"),
        Path(r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def candidate_number(path: Path) -> str:
    return path.name.split("_")[1]


def display_name(path: Path) -> str:
    parts = path.stem.split("_", 2)
    return parts[2].replace("_", " ").title() if len(parts) == 3 else path.stem


def render_sheet(
    files: list[Path],
    output: Path,
    *,
    title: str,
    subtitle: str,
) -> None:
    if not files:
        raise SystemExit(f"No candidates found for {output.name}")

    columns = 2
    rows = math.ceil(len(files) / columns)
    page_w = 2400
    header_h = 220
    footer_h = 70
    cell_w, cell_h = 1080, 700
    thumb_w, thumb_h = 1080, 608
    gap_x, gap_y = 60, 70
    start_x, start_y = 90, header_h
    page_h = header_h + rows * cell_h + max(0, rows - 1) * gap_y + footer_h

    page = Image.new("RGB", (page_w, page_h), "#eef2f7")
    draw = ImageDraw.Draw(page)
    draw.text((90, 55), title, font=font(62, bold=True), fill="#111827")
    draw.text((90, 130), subtitle, font=font(34), fill="#475569")

    for index, path in enumerate(files):
        col = index % columns
        row = index // columns
        x = start_x + col * (cell_w + gap_x)
        y = start_y + row * (cell_h + gap_y)
        card = Image.new("RGB", (cell_w, cell_h), "white")
        with Image.open(path) as source:
            image = source.convert("RGB")
            fitted = ImageOps.fit(image, (thumb_w, thumb_h), method=Image.Resampling.LANCZOS)
        card.paste(fitted, (0, 0))
        card_draw = ImageDraw.Draw(card)
        number = candidate_number(path)
        style = display_name(path)
        card_draw.rectangle((0, thumb_h, cell_w, cell_h), fill="#ffffff")
        card_draw.text((24, thumb_h + 18), number, font=font(42, bold=True), fill="#0f172a")
        card_draw.text((96, thumb_h + 24), style, font=font(30), fill="#334155")
        page.paste(card, (x, y))
        draw.rounded_rectangle(
            (x, y, x + cell_w, y + cell_h),
            radius=8,
            outline="#cbd5e1",
            width=3,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    page.save(output, quality=92, subsampling=0)
    print(output)


def main() -> None:
    candidates = sorted(ROOT.glob("candidate_[0-9][0-9]_*.png"))
    render_sheet(
        candidates,
        ROOT / "candidate_contact_sheet.jpg",
        title="Game Camera Capture Lab",
        subtitle=f"All visual candidates · choose 01–{len(candidates):02d}",
    )

    cinematic = [path for path in candidates if 7 <= int(candidate_number(path)) <= 12]
    render_sheet(
        cinematic,
        ROOT / "cinematic_contact_sheet_07_12.jpg",
        title="Cinematic Multiverse Series",
        subtitle="Paper teaser and cover candidates · choose 07–12",
    )

    vectors = sorted(VECTOR_ROOT.glob("vector_[0-9][0-9]_*.png"))
    render_sheet(
        vectors,
        VECTOR_ROOT / "vector_contact_sheet.jpg",
        title="Editable Vector Paper Figures",
        subtitle=f"Native SVG + vector PDF + PNG preview · choose 01–{len(vectors):02d}",
    )


if __name__ == "__main__":
    main()
