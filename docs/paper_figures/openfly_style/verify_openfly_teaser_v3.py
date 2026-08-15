from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pypdfium2 as pdfium
from PIL import Image, ImageStat


ROOT = Path(__file__).resolve().parent
STEM = "openfly_07_multisource_teaser_v3"


def tool(name: str) -> str:
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

    info = subprocess.run(
        [tool("pdfimages"), "-list", str(pdf)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout
    raster_objects = sum(
        1 for row in info.splitlines()[2:] if row.strip()[:1].isdigit()
    )
    if raster_objects < 5:
        raise ValueError(
            f"Expected a hybrid vector/raster PDF, found {raster_objects} image objects"
        )

    document = pdfium.PdfDocument(str(pdf))
    if len(document) != 1:
        raise ValueError("Teaser PDF must have exactly one page")
    page = document[0]
    rendered = page.render(scale=2.0).to_pil().convert("RGB")
    page.close()
    document.close()
    qa_png = ROOT / "qa_pdf" / f"{STEM}_from_pdf.png"
    qa_png.parent.mkdir(parents=True, exist_ok=True)
    rendered.save(qa_png)

    expected_ratio = 1800 / 980
    actual_ratio = rendered.width / rendered.height
    if abs(expected_ratio - actual_ratio) > 0.01:
        raise ValueError(f"Unexpected page ratio: {actual_ratio:.4f}")
    if ImageStat.Stat(rendered).mean[0] < 8:
        raise ValueError("Rendered PDF appears nearly black")

    report = {
        "svg_parse": "ok",
        "pdf_pages": 1,
        "embedded_raster_objects": raster_objects,
        "pdf_render_size": list(rendered.size),
        "pdf_render": str(qa_png),
        "hybrid_vector_raster": True,
    }
    (ROOT / "verification_v3.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
