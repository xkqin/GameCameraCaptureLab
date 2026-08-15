from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parent
SVG = ROOT / "openfly_10_multigame_capture_framework_iclr.svg"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


def file_url(path: Path) -> str:
    return "file:///" + quote(path.resolve().as_posix(), safe="/:()")


def run(args: list[str]) -> None:
    subprocess.run(
        [
            str(CHROME), "--headless=new", "--disable-gpu",
            "--hide-scrollbars", "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=3500", *args,
        ],
        cwd=ROOT,
        check=True,
    )


def main() -> None:
    if not SVG.exists():
        raise SystemExit(f"Build the SVG first: {SVG}")
    html = SVG.with_suffix(".render.html")
    html.write_text(
        """<!doctype html><html><head><meta charset="utf-8"><style>
@page { size: 18in 9.8in; margin: 0; }
html, body { width: 1800px; height: 980px; margin: 0; overflow: hidden; background: white; }
object { width: 1800px; height: 980px; display: block; border: 0; }
</style></head><body><object type="image/svg+xml" data="openfly_10_multigame_capture_framework_iclr.svg"></object></body></html>""",
        encoding="utf-8",
    )
    try:
        with tempfile.TemporaryDirectory(prefix="openfly-method-") as profile:
            common = [f"--user-data-dir={profile}",
                      "--allow-file-access-from-files"]
            run([
                *common, "--window-size=1800,980",
                "--force-device-scale-factor=1",
                f"--screenshot={SVG.with_suffix('.png')}", file_url(html),
            ])
            run([
                *common, "--no-pdf-header-footer",
                "--print-to-pdf-no-header",
                f"--print-to-pdf={SVG.with_suffix('.pdf')}", file_url(html),
            ])
    finally:
        html.unlink(missing_ok=True)
    print(SVG.with_suffix(".png"))
    print(SVG.with_suffix(".pdf"))


if __name__ == "__main__":
    main()
