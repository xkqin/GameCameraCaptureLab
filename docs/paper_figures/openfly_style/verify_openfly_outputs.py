from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parent
PURE_VECTOR = {2, 3, 4, 6}
HYBRID = {1: 4, 5: 6}
PDFIMAGES_CANDIDATES = [Path(r"C:\Users\hder\AppData\Local\Programs\MiKTeX\miktex\bin\x64\pdfimages.exe")]


def font(size: int, *, bold: bool = False):
    choices = [Path(r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf"), Path(r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf")]
    for path in choices:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def pdfimages_binary() -> str:
    found = shutil.which("pdfimages")
    if found:
        return found
    for path in PDFIMAGES_CANDIDATES:
        if path.exists():
            return str(path)
    raise SystemExit("pdfimages is required")


def embedded_images(pdf: Path, binary: str) -> int:
    result = subprocess.run([binary, "-list", str(pdf)], check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return sum(1 for line in result.stdout.splitlines()[2:] if line.strip()[:1].isdigit())


def render_pdf(pdf: Path) -> Image.Image:
    document = pdfium.PdfDocument(str(pdf))
    if len(document) != 1:
        raise ValueError(f"Expected one page: {pdf.name}")
    page = document[0]
    image = page.render(scale=1.2).to_pil().convert("RGB")
    page.close(); document.close()
    return image


def contact_sheet(images: list[tuple[str, Image.Image]], output: Path) -> None:
    sheet = Image.new("RGB", (2500, 2350), "#edf3f7")
    draw = ImageDraw.Draw(sheet)
    draw.text((90, 52), "OpenFly-inspired paper figure candidates", font=font(56, bold=True), fill="#10233a")
    draw.text((90, 122), "Rendered from final PDFs; figures 02/03/04/06 are pure vector", font=font(28), fill="#52677e")
    for index, (name, image) in enumerate(images):
        col, row = index % 2, index // 2
        x, y = 90 + col * 1160, 190 + row * 690
        thumb = ImageOps.contain(image, (1090, 594), Image.Resampling.LANCZOS)
        card = Image.new("RGB", (1090, 635), "white")
        card.paste(thumb, ((1090 - thumb.width) // 2, 0))
        ImageDraw.Draw(card).text((18, 600), name, font=font(24, bold=True), fill="#152b45")
        sheet.paste(card, (x, y))
        draw.rectangle((x, y, x + 1090, y + 635), outline="#bed0dd", width=3)
    sheet.save(output, quality=93, subsampling=0)


def main() -> None:
    svgs = sorted(ROOT.glob("openfly_[0-9][0-9]_*.svg"))
    pdfs = sorted(ROOT.glob("openfly_[0-9][0-9]_*.pdf"))
    pngs = sorted(ROOT.glob("openfly_[0-9][0-9]_*.png"))
    if not (len(svgs) == len(pdfs) == len(pngs) == 6):
        raise SystemExit(f"Expected 6 SVG/PDF/PNG files, found {len(svgs)}/{len(pdfs)}/{len(pngs)}")
    for svg in svgs:
        ElementTree.parse(svg)
    binary = pdfimages_binary()
    report = {"pure_vector": {}, "hybrid": {}}
    rendered = []
    for pdf in pdfs:
        number = int(pdf.name.split("_")[1])
        count = embedded_images(pdf, binary)
        if number in PURE_VECTOR:
            if count != 0:
                raise ValueError(f"{pdf.name} should be pure vector but contains {count} raster images")
            report["pure_vector"][pdf.name] = count
        else:
            if count < HYBRID[number]:
                raise ValueError(f"{pdf.name} should contain at least {HYBRID[number]} raster images; found {count}")
            report["hybrid"][pdf.name] = count
        rendered.append((pdf.stem, render_pdf(pdf)))
        print(f"OK {pdf.name}: {count} embedded raster image objects")
    contact_sheet(rendered, ROOT / "openfly_contact_sheet.jpg")
    (ROOT / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
