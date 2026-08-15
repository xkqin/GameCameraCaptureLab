from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pypdfium2 as pdfium
from PIL import Image, ImageOps, ImageStat


ROOT = Path(__file__).resolve().parent
STEM = "openfly_10_multigame_capture_framework_iclr"


def executable(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    candidate = Path(
        rf"C:\Users\hder\AppData\Local\Programs\MiKTeX\miktex\bin\x64\{name}.exe"
    )
    if candidate.exists():
        return str(candidate)
    raise SystemExit(f"{name} not found")


def main() -> None:
    svg = ROOT / f"{STEM}.svg"
    pdf = ROOT / f"{STEM}.pdf"
    png = ROOT / f"{STEM}.png"
    for item in (svg, pdf, png):
        if not item.exists() or item.stat().st_size == 0:
            raise FileNotFoundError(item)
    ElementTree.parse(svg)

    source = svg.read_text(encoding="utf-8")
    sizes = [float(value) for value in re.findall(r'font-size="([0-9.]+)"', source)]
    if not sizes or min(sizes) < 8.5:
        raise ValueError("Unexpectedly small or missing SVG text")
    for required in [
        "Unified adapter interface",
        "Automatic camera-data toolchain",
        "Unified camera-aware record",
        "measured / est.",
    ]:
        if required not in source:
            raise ValueError(f"Missing required method label: {required}")

    listing = subprocess.run(
        [executable("pdfimages"), "-list", str(pdf)],
        check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    ).stdout
    raster_objects = sum(
        1 for row in listing.splitlines()[2:] if row.strip()[:1].isdigit()
    )
    if raster_objects < 7:
        raise ValueError("Expected a hybrid vector/raster method PDF")

    document = pdfium.PdfDocument(str(pdf))
    if len(document) != 1:
        raise ValueError("Method figure PDF must be one page")
    page = document[0]
    rendered = page.render(scale=2.0).to_pil().convert("RGB")
    page.close()
    document.close()
    if ImageStat.Stat(rendered).mean[0] < 120:
        raise ValueError("Method figure render is unexpectedly dark")

    qa_dir = ROOT / "qa_pdf"
    qa_dir.mkdir(parents=True, exist_ok=True)
    qa_full = qa_dir / f"{STEM}_from_pdf.png"
    qa_iclr = qa_dir / f"{STEM}_iclr_width.png"
    rendered.save(qa_full)
    scaled = ImageOps.contain(rendered, (1350, 735), Image.Resampling.LANCZOS)
    scaled.save(qa_iclr)

    report = {
        "svg_parse": "ok",
        "pdf_pages": 1,
        "embedded_raster_objects": raster_objects,
        "svg_font_range": [min(sizes), max(sizes)],
        "pdf_render_size": list(rendered.size),
        "full_render": str(qa_full),
        "iclr_review_render": str(qa_iclr),
        "hybrid_vector_raster": True,
    }
    (ROOT / "verification_framework_iclr.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
