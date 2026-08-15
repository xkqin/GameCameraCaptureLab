from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pypdfium2 as pdfium
from PIL import ImageStat


ROOT = Path(__file__).resolve().parent
STEM = "openfly_07_multisource_teaser_v4"


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
    svg_text = svg.read_text(encoding="utf-8")
    if svg_text.count('stroke-width="2.15"') != 4:
        raise ValueError("Expected four thin transparent route cores")
    if svg_text.count('opacity="0.52"') < 10:
        raise ValueError("Expected transparent wireframe camera frusta")

    listing = subprocess.run(
        [executable("pdfimages"), "-list", str(pdf)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    raster_objects = sum(
        1 for row in listing.splitlines()[2:] if row.strip()[:1].isdigit()
    )
    if raster_objects < 4:
        raise ValueError("Expected a hybrid vector/raster PDF")

    document = pdfium.PdfDocument(str(pdf))
    if len(document) != 1:
        raise ValueError("Expected one teaser page")
    page = document[0]
    rendered = page.render(scale=2.0).to_pil().convert("RGB")
    page.close()
    document.close()
    qa = ROOT / "qa_pdf" / f"{STEM}_from_pdf.png"
    qa.parent.mkdir(parents=True, exist_ok=True)
    rendered.save(qa)
    if ImageStat.Stat(rendered).mean[0] < 25:
        raise ValueError("PDF render is unexpectedly dark")

    report = {
        "svg_parse": "ok",
        "pdf_pages": 1,
        "thin_transparent_routes": 4,
        "wireframe_camera_groups": svg_text.count('opacity="0.52"'),
        "embedded_raster_objects": raster_objects,
        "pdf_render_size": list(rendered.size),
        "pdf_render": str(qa),
        "hybrid_vector_raster": True,
    }
    (ROOT / "verification_v4.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
