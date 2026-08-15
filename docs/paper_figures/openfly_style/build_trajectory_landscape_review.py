from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from reportlab.lib.pagesizes import landscape
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets" / "trajectory_landscapes"
SHEET = ROOT / "trajectory_landscapes_contact_sheet.jpg"
PDF = ROOT / "trajectory_landscapes_iclr_review.pdf"

ITEMS = [
    ("01", "Medieval valley / historical adapter", "01_medieval_valley_trajectory.png"),
    ("02", "Eastern peaks / mythic adapter", "02_eastern_peaks_trajectory.png"),
    ("03", "Alpine research complex / RE-style adapter", "03_alpine_research_complex_trajectory.png"),
    ("04", "Unified multi-engine world / platform teaser", "04_multiengine_world_trajectory.png"),
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidate = Path(
        rf"C:\Windows\Fonts\{'arialbd.ttf' if bold else 'arial.ttf'}"
    )
    return ImageFont.truetype(str(candidate), size)


def build_sheet() -> None:
    sheet = Image.new("RGB", (2800, 1840), "#EEF4F7")
    draw = ImageDraw.Draw(sheet)
    draw.text((70, 45), "Camera-trajectory landscape candidates",
              font=font(48, True), fill="#12263B")
    draw.text(
        (70, 105),
        "Concept visuals for a multi-game camera pose, point, trajectory, and synchronized capture platform",
        font=font(25), fill="#587086",
    )
    positions = [(70, 180), (1435, 180), (70, 1000), (1435, 1000)]
    for (number, title, filename), (x, y) in zip(ITEMS, positions):
        source = Image.open(ASSETS / filename).convert("RGB")
        thumb = ImageOps.fit(source, (1295, 729), Image.Resampling.LANCZOS)
        card = Image.new("RGB", (1295, 790), "white")
        card.paste(thumb, (0, 0))
        cdraw = ImageDraw.Draw(card)
        cdraw.rectangle((0, 729, 1295, 790), fill="#FFFFFF")
        cdraw.text((20, 746), number, font=font(24, True), fill="#1597C3")
        cdraw.text((70, 746), title, font=font(23, True), fill="#12263B")
        sheet.paste(card, (x, y))
        draw.rectangle((x, y, x + 1295, y + 790), outline="#B8D1DC", width=3)
    sheet.save(SHEET, quality=95, subsampling=0)


def build_pdf() -> None:
    page_size = landscape((720, 405))
    document = canvas.Canvas(str(PDF), pagesize=page_size)
    page_w, page_h = page_size
    for number, title, filename in ITEMS:
        document.setFillColorRGB(1, 1, 1)
        document.rect(0, 0, page_w, page_h, fill=1, stroke=0)
        document.drawImage(str(ASSETS / filename), 0, 0, width=page_w,
                           height=page_h, preserveAspectRatio=True,
                           anchor="c", mask="auto")
        document.setFillColorRGB(0.025, 0.075, 0.115, alpha=0.74)
        document.roundRect(18, 16, 338, 38, 8, fill=1, stroke=0)
        document.setFillColorRGB(1, 1, 1)
        document.setFont("Helvetica-Bold", 13)
        document.drawString(31, 38, f"{number}  {title}")
        document.setFont("Helvetica-Oblique", 8.8)
        document.drawString(31, 24, "Generated concept visual - not an experimental result")
        document.showPage()
    document.save()


def main() -> None:
    for _, _, filename in ITEMS:
        if not (ASSETS / filename).exists():
            raise FileNotFoundError(ASSETS / filename)
    build_sheet()
    build_pdf()
    print(SHEET)
    print(PDF)


if __name__ == "__main__":
    main()
