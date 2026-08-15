from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parent
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


def file_url(path: Path) -> str:
    return "file:///" + quote(path.resolve().as_posix(), safe="/:()")


def wrapper(svg: Path) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: 18in 9.8in; margin: 0; }}
html, body {{ width: 1800px; height: 980px; margin: 0; overflow: hidden; }}
object {{ width: 1800px; height: 980px; display: block; border: 0; }}
</style></head><body><object type="image/svg+xml" data="{svg.name}"></object></body></html>"""


def run(arguments: list[str]) -> None:
    subprocess.run([
        str(CHROME), "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--run-all-compositor-stages-before-draw", "--virtual-time-budget=3000",
        *arguments,
    ], cwd=ROOT, check=True)


def main() -> None:
    svgs = sorted(ROOT.glob("openfly_0[789]_*.svg"))
    if len(svgs) != 3:
        raise SystemExit(f"Expected 3 V2 SVGs, found {len(svgs)}")
    for svg in svgs:
        html = svg.with_suffix(".render.html")
        html.write_text(wrapper(svg), encoding="utf-8")
        try:
            with tempfile.TemporaryDirectory(prefix="openfly-v2-") as profile:
                common = [
                    f"--user-data-dir={profile}",
                    "--allow-file-access-from-files",
                ]
                run([
                    *common, "--window-size=1800,980",
                    "--force-device-scale-factor=1",
                    f"--screenshot={svg.with_suffix('.png')}", file_url(html),
                ])
                run([
                    *common, "--no-pdf-header-footer",
                    "--print-to-pdf-no-header",
                    f"--print-to-pdf={svg.with_suffix('.pdf')}", file_url(html),
                ])
        finally:
            html.unlink(missing_ok=True)
        print(svg.name)


if __name__ == "__main__":
    main()
