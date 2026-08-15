from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parent
DEFAULT_CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


def file_url(path: Path) -> str:
    return "file:///" + quote(path.resolve().as_posix(), safe="/:()")


def wrapper(svg: Path) -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><style>
@page {{ size: 18in 9.8in; margin: 0; }}
html, body {{ width: 1800px; height: 980px; margin: 0; padding: 0; overflow: hidden; }}
object {{ display: block; width: 1800px; height: 980px; border: 0; }}
</style></head><body><object type="image/svg+xml" data="{svg.name}"></object></body></html>
"""


def run(chrome: Path, arguments: list[str], *, cwd: Path) -> None:
    subprocess.run([
        str(chrome), "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--run-all-compositor-stages-before-draw", "--virtual-time-budget=2500", *arguments,
    ], check=True, cwd=cwd)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render OpenFly-style SVG figures to PNG and PDF.")
    parser.add_argument("--chrome", type=Path, default=DEFAULT_CHROME)
    args = parser.parse_args()
    if not args.chrome.exists():
        raise SystemExit(f"Chrome not found: {args.chrome}")
    svg_files = sorted(ROOT.glob("openfly_[0-9][0-9]_*.svg"))
    if not svg_files:
        raise SystemExit("No OpenFly-style SVG files found")
    for svg in svg_files:
        html = svg.with_suffix(".render.html")
        html.write_text(wrapper(svg), encoding="utf-8", newline="\n")
        with tempfile.TemporaryDirectory(prefix="openfly-figure-") as profile:
            common = [f"--user-data-dir={profile}", "--allow-file-access-from-files"]
            run(args.chrome, [*common, "--window-size=1800,980", "--force-device-scale-factor=1", f"--screenshot={svg.with_suffix('.png')}", file_url(html)], cwd=ROOT)
            run(args.chrome, [*common, "--no-pdf-header-footer", "--print-to-pdf-no-header", f"--print-to-pdf={svg.with_suffix('.pdf')}", file_url(html)], cwd=ROOT)
        html.unlink()
        print(f"rendered {svg.name}")


if __name__ == "__main__":
    main()
