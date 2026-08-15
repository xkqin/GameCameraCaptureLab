from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parent
DEFAULT_CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


def file_url(path: Path) -> str:
    normalized = path.resolve().as_posix()
    return "file:///" + quote(normalized, safe="/:()")


def wrapper(svg: Path) -> str:
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    @page {{ size: 16in 9in; margin: 0; }}
    html, body {{ width: 1600px; height: 900px; margin: 0; padding: 0; overflow: hidden; }}
    object {{ display: block; width: 1600px; height: 900px; border: 0; }}
  </style>
</head>
<body><object type="image/svg+xml" data="{svg.name}"></object></body>
</html>
"""


def run(chrome: Path, arguments: list[str]) -> None:
    command = [
        str(chrome),
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=2000",
        *arguments,
    ]
    subprocess.run(command, check=True, cwd=ROOT)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render editable SVG figures to PDF and PNG previews.")
    parser.add_argument("--chrome", type=Path, default=DEFAULT_CHROME)
    args = parser.parse_args()
    if not args.chrome.exists():
        raise SystemExit(f"Chrome not found: {args.chrome}")

    svg_files = sorted(ROOT.glob("vector_[0-9][0-9]_*.svg"))
    if not svg_files:
        raise SystemExit("No vector figures found")

    with tempfile.TemporaryDirectory(prefix="game-camera-vector-") as profile:
        for svg in svg_files:
            html = svg.with_suffix(".render.html")
            png = svg.with_suffix(".png")
            pdf = svg.with_suffix(".pdf")
            html.write_text(wrapper(svg), encoding="utf-8", newline="\n")
            url = file_url(html)
            common = [f"--user-data-dir={profile}", "--allow-file-access-from-files"]
            run(
                args.chrome,
                [
                    *common,
                    "--window-size=1600,900",
                    "--force-device-scale-factor=1",
                    f"--screenshot={png}",
                    url,
                ],
            )
            run(
                args.chrome,
                [
                    *common,
                    "--no-pdf-header-footer",
                    "--print-to-pdf-no-header",
                    f"--print-to-pdf={pdf}",
                    url,
                ],
            )
            html.unlink()
            print(f"rendered {svg.name}")


if __name__ == "__main__":
    main()
