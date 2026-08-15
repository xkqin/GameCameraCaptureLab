from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parent
PDFIMAGES_CANDIDATES = [
    Path(r"C:\Users\hder\AppData\Local\Programs\MiKTeX\miktex\bin\x64\pdfimages.exe"),
]


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path(r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf"),
        Path(r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def pdfimages_binary() -> str | None:
    found = shutil.which("pdfimages")
    if found:
        return found
    for candidate in PDFIMAGES_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    return None


def embedded_image_count(pdf: Path, binary: str) -> int:
    result = subprocess.run(
        [binary, "-list", str(pdf)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines = result.stdout.splitlines()[2:]
    return sum(1 for line in lines if line.strip()[:1].isdigit())


def render_pdf(pdf: Path) -> Image.Image:
    document = pdfium.PdfDocument(str(pdf))
    if len(document) != 1:
        raise ValueError(f"Expected one page in {pdf.name}, found {len(document)}")
    page = document[0]
    image = page.render(scale=1.35).to_pil().convert("RGB")
    page.close()
    document.close()
    return image


def contact_sheet(images: list[tuple[str, Image.Image]], output: Path) -> None:
    page = Image.new("RGB", (2400, 2520), "#eef2f7")
    draw = ImageDraw.Draw(page)
    draw.text((90, 50), "Vector PDF Verification", font=font(60, bold=True), fill="#111827")
    draw.text(
        (90, 126),
        "Rendered from the final PDF files · zero embedded raster images",
        font=font(32),
        fill="#475569",
    )
    for index, (number, image) in enumerate(images):
        col = index % 2
        row = index // 2
        x = 90 + col * 1140
        y = 210 + row * 745
        thumb = ImageOps.contain(image, (1080, 608), method=Image.Resampling.LANCZOS)
        card = Image.new("RGB", (1080, 680), "white")
        card.paste(thumb, ((1080 - thumb.width) // 2, 0))
        card_draw = ImageDraw.Draw(card)
        card_draw.text((24, 625), number, font=font(38, bold=True), fill="#0f172a")
        card_draw.text((94, 629), "PDF render", font=font(28), fill="#334155")
        page.paste(card, (x, y))
        draw.rounded_rectangle((x, y, x + 1080, y + 680), radius=8, outline="#cbd5e1", width=3)
    page.save(output, quality=92, subsampling=0)


def main() -> None:
    svg_files = sorted(ROOT.glob("vector_[0-9][0-9]_*.svg"))
    pdf_files = sorted(ROOT.glob("vector_[0-9][0-9]_*.pdf"))
    if len(svg_files) != 6 or len(pdf_files) != 6:
        raise SystemExit(f"Expected 6 SVG and 6 PDF files, found {len(svg_files)} and {len(pdf_files)}")

    for svg in svg_files:
        ElementTree.parse(svg)
        source = svg.read_text(encoding="utf-8").lower()
        if "<image" in source:
            raise ValueError(f"Embedded raster image found in {svg.name}")

    binary = pdfimages_binary()
    if binary is None:
        raise SystemExit("pdfimages is required for vector-PDF verification")

    rendered: list[tuple[str, Image.Image]] = []
    for pdf in pdf_files:
        count = embedded_image_count(pdf, binary)
        if count != 0:
            raise ValueError(f"{pdf.name} contains {count} embedded raster images")
        number = pdf.name.split("_")[1]
        rendered.append((number, render_pdf(pdf)))
        print(f"OK {pdf.name}: 0 embedded images")

    output = ROOT / "vector_pdf_verification.jpg"
    contact_sheet(rendered, output)
    print(output)


if __name__ == "__main__":
    main()
