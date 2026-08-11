from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import time
from typing import Callable, Iterable, Protocol

from .models import CameraPose, CapturePoint
from .screen_capture import save_game_screenshot


class PoseReader(Protocol):
    def read_pose(self) -> CameraPose: ...


class PoseMover(Protocol):
    def move_to(
        self,
        target: CameraPose,
        *,
        stop_requested: Callable[[], bool],
        on_update: Callable[[str], None] | None,
    ) -> CameraPose: ...


@dataclass(frozen=True)
class CaptureRunResult:
    session_dir: Path
    manifest_json: Path
    manifest_csv: Path
    captured_count: int
    requested_count: int
    stopped: bool


def _safe_label(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip())
    return cleaned.strip("._")[:48] or "point"


def _sleep_interruptible(seconds: float, stop_requested: Callable[[], bool]) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        if stop_requested():
            raise InterruptedError("采集已停止")
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


def _pose_columns(prefix: str, pose: CameraPose) -> dict[str, object]:
    return {f"{prefix}_{key}": value for key, value in pose.as_dict().items()}


class CaptureRunner:
    def __init__(
        self,
        *,
        bridge: PoseReader,
        mover: PoseMover,
        pid: int,
        settle_seconds: float = 0.35,
        image_format: str = "png",
        screenshotter: Callable[[int, str | Path], Path] = save_game_screenshot,
    ) -> None:
        self.bridge = bridge
        self.mover = mover
        self.pid = pid
        self.settle_seconds = max(0.0, settle_seconds)
        self.image_format = image_format.lower().lstrip(".") or "png"
        self.screenshotter = screenshotter

    def run(
        self,
        points: Iterable[CapturePoint],
        output_root: str | Path,
        *,
        mode: str,
        stop_requested: Callable[[], bool] = lambda: False,
        on_progress: Callable[[int, int, str], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        respect_timestamps: bool = False,
    ) -> CaptureRunResult:
        values = list(points)
        if not values:
            raise ValueError("没有可采集的点位")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        session_dir = Path(output_root) / f"{mode}_{stamp}"
        images_dir = session_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=False)
        manifest_json = session_dir / "manifest.json"
        manifest_csv = session_dir / "manifest.csv"
        rows: list[dict[str, object]] = []
        stopped = False
        run_error: str | None = None
        run_started = time.monotonic()

        try:
            for ordinal, point in enumerate(values, start=1):
                if stop_requested():
                    raise InterruptedError("采集已停止")
                if respect_timestamps and point.time_sec > 0:
                    scheduled = run_started + point.time_sec
                    _sleep_interruptible(scheduled - time.monotonic(), stop_requested)

                if on_progress is not None:
                    on_progress(ordinal - 1, len(values), f"前往 {point.label}")
                actual = self.mover.move_to(
                    point.pose,
                    stop_requested=stop_requested,
                    on_update=on_log,
                )
                _sleep_interruptible(self.settle_seconds, stop_requested)
                actual = self.bridge.read_pose()
                filename = (
                    f"{ordinal:05d}_{_safe_label(point.label)}.{self.image_format}"
                )
                image_path = self.screenshotter(self.pid, images_dir / filename)
                captured_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
                row: dict[str, object] = {
                    "sequence": ordinal,
                    "point_index": point.index,
                    "label": point.label,
                    "time_sec": point.time_sec,
                    "image": str(Path("images") / image_path.name),
                    "captured_at": captured_at,
                    **_pose_columns("target", point.pose),
                    **_pose_columns("actual", actual),
                }
                rows.append(row)
                if on_progress is not None:
                    on_progress(ordinal, len(values), f"已保存 {filename}")
        except InterruptedError:
            stopped = True
        except Exception as exc:
            run_error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            payload = {
                "format": "bmw-uuu-capture-manifest-v1",
                "mode": mode,
                "status": "failed" if run_error else ("stopped" if stopped else "complete"),
                "error": run_error,
                "requested_count": len(values),
                "captured_count": len(rows),
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "frames": rows,
            }
            manifest_json.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if rows:
                with manifest_csv.open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(rows)
            else:
                manifest_csv.write_text("sequence,label,image\n", encoding="utf-8-sig")

        return CaptureRunResult(
            session_dir=session_dir,
            manifest_json=manifest_json,
            manifest_csv=manifest_csv,
            captured_count=len(rows),
            requested_count=len(values),
            stopped=stopped,
        )
