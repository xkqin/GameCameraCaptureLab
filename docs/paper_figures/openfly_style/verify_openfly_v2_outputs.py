from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parent


def font(size: int, bold: bool = False):
    choices = [
        Path(rf"C:\Windows\Fonts\{'arialbd.ttf' if bold else 'arial.ttf'}"),
        Path(rf"C:\Windows\Fonts\{'segoeuib.ttf' if bold else 'segoeui.ttf'}"),
    ]
    for path in choices:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def pdfimages_binary() -> str:
    found = shutil.which("pdfimages")
    if found:
        return found
    candidate = Path(
        r"C:\Users\hder\AppData\Local\Programs\MiKTeX\miktex\bin\x64\pdfimages.exe"
    )
    if candidate.exists():
        return str(candidate)
    raise SystemExit("pdfimages not found")


def embedded_images(pdf: Path, binary: str) -> int:
    result = subprocess.run(
        [binary, "-list", str(pdf)],
        check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return sum(
        1 for row in result.stdout.splitlines()[2:]
        if row.strip()[:1].isdigit()
    )


def render_pdf(pdf: Path) -> Image.Image:
    document = pdfium.PdfDocument(str(pdf))
    if len(document) != 1:
        raise ValueError(f"Expected one page: {pdf.name}")
    page = document[0]
    image = page.render(scale=1.35).to_pil().convert("RGB")
    page.close()
    document.close()
    return image


def contact_sheet(items: list[tuple[str, Image.Image]], target: Path) -> None:
    canvas = Image.new("RGB", (2500, 1510), "#EDF4F7")
    draw = ImageDraw.Draw(canvas)
    draw.text(
        (80, 45), "OpenFly-calibrated camera-data figures V2",
        font=font(49, True), fill="#10243A",
    )
    draw.text(
        (80, 104),
        "Real internet video + public 3D scenes + controllable game worlds",
        font=font(27), fill="#526A80",
    )
    positions = [(80, 170), (1280, 170), (680, 835)]
    for (name, image), (x, y) in zip(items, positions):
        thumb = ImageOps.contain(
            image, (1140, 620), Image.Resampling.LANCZOS
        )
        card = Image.new("RGB", (1140, 665), "white")
        card.paste(thumb, ((1140 - thumb.width) // 2, 0))
        ImageDraw.Draw(card).text(
            (18, 628), name, font=font(23, True), fill="#152C44"
        )
        canvas.paste(card, (x, y))
        draw.rectangle(
            (x, y, x + 1140, y + 665), outline="#B8CEDA", width=3
        )
    canvas.save(target, quality=94, subsampling=0)


def main() -> None:
    svgs = sorted(ROOT.glob("openfly_0[789]_*.svg"))
    pdfs = sorted(ROOT.glob("openfly_0[789]_*.pdf"))
    pngs = sorted(ROOT.glob("openfly_0[789]_*.png"))
    if not (len(svgs) == len(pdfs) == len(pngs) == 3):
        raise SystemExit(
            f"Expected 3 SVG/PDF/PNG, got {len(svgs)}/{len(pdfs)}/{len(pngs)}"
        )
    for svg in svgs:
        ElementTree.parse(svg)
    binary = pdfimages_binary()
    report: dict[str, object] = {}
    rendered: list[tuple[str, Image.Image]] = []
    for pdf in pdfs:
        count = embedded_images(pdf, binary)
        if count < 3:
            raise ValueError(
                f"Expected a hybrid PDF, found only {count} image objects: {pdf.name}"
            )
        image = render_pdf(pdf)
        report[pdf.name] = {
            "embedded_raster_objects": count,
            "render_size": list(image.size),
        }
        rendered.append((pdf.stem, image))
        print(f"OK {pdf.name}: {count} image objects, {image.size}")
    contact_sheet(rendered, ROOT / "openfly_v2_contact_sheet.jpg")
    (ROOT / "verification_v2.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
