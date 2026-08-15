from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "assets" / "v2" / "spatialvid_samples"
FRAMES = SOURCE / "frames"
OUTPUT = ROOT / "spatialvid_real_samples_contact_sheet.jpg"


def font(size: int, *, bold: bool = False):
    choices = [
        Path(rf"C:\Windows\Fonts\{'arialbd.ttf' if bold else 'arial.ttf'}"),
        Path(rf"C:\Windows\Fonts\{'segoeuib.ttf' if bold else 'segoeui.ttf'}"),
    ]
    for path in choices:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def duration(video: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(video),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def extract(video: Path, output: Path) -> float:
    seconds = duration(video)
    timestamp = max(0.0, seconds * 0.50)
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-y", "-ss", f"{timestamp:.3f}",
            "-i", str(video), "-frames:v", "1", "-q:v", "2", str(output),
        ],
        check=True,
    )
    return seconds


def main() -> None:
    FRAMES.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((SOURCE / "selection_manifest.json").read_text(encoding="utf-8"))
    videos = sorted((SOURCE / "videos").glob("*"))
    items: list[tuple[str, float, Image.Image]] = []
    for video in videos:
        frame = FRAMES / f"{video.stem}_mid.jpg"
        seconds = extract(video, frame)
        items.append((video.stem, seconds, Image.open(frame).convert("RGB")))

    sheet = Image.new("RGB", (2400, 1540), "#EDF4F7")
    draw = ImageDraw.Draw(sheet)
    draw.text((80, 48), "SpatialVID-HQ samples inside /data/EZCAM2/dataset.zip", font=font(49, bold=True), fill="#10243A")
    draw.text((80, 108), "Seven real internet-video samples; middle frames only", font=font(28), fill="#526A80")
    colors = ["#0B91B8", "#56A878", "#E39C3B", "#8A77CF", "#D565A9", "#3789C7", "#78A62E"]
    card_w, card_h = 710, 550
    for index, (group, seconds, image) in enumerate(items):
        row, col = divmod(index, 3)
        x, y = 80 + col * 770, 180 + row * 610
        thumb = ImageOps.fit(image, (card_w, 420), method=Image.Resampling.LANCZOS)
        sheet.paste(thumb, (x, y))
        draw.rectangle((x, y, x + card_w, y + card_h), fill=None, outline="#B7CBD8", width=3)
        draw.rectangle((x, y + 420, x + card_w, y + card_h), fill="#FFFFFF")
        draw.rectangle((x, y + 420, x + 9, y + card_h), fill=colors[index])
        draw.text((x + 28, y + 450), group, font=font(28, bold=True), fill="#142B43")
        draw.text((x + 28, y + 493), f"video length {seconds:.1f} s", font=font(22), fill="#5B7085")
        draw.text((x + 28, y + 523), "estimated pose + intrinsics + caption + score", font=font(19), fill="#0C789A")

    note_x, note_y = 850, 1400
    draw.rounded_rectangle((note_x, note_y, 2320, 1498), radius=20, fill="#E4F6FB", outline="#A7D8E7", width=2)
    draw.text((note_x + 30, note_y + 23), "Source: FelixYuan/SpatialVID-HQ (gated), CC BY-NC-SA 4.0.", font=font(22, bold=True), fill="#176C88")
    draw.text((note_x + 30, note_y + 58), "Pose is annotation/estimation, not sensor ground truth.", font=font(21), fill="#526A80")
    sheet.save(OUTPUT, quality=94, subsampling=0)
    print(OUTPUT)


if __name__ == "__main__":
    main()
