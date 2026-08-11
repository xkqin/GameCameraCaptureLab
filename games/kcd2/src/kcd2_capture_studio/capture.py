from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any

from .backend import CameraBackend
from .obs_bridge import OBSBridge
from .paths import STILLS_DIR, ensure_data_dirs
from .storage import safe_id


SAMPLE_FIELDS = [
    "sample_index",
    "scene_id",
    "label",
    "captured_at",
    "image_path",
    "obs_source",
    "x",
    "y",
    "z",
    "q0",
    "q1",
    "q2",
    "q3",
    "yaw_degrees",
    "pitch_degrees",
    "roll_degrees",
    "fov_degrees",
]


class StillCaptureSession:
    def __init__(
        self,
        backend: CameraBackend,
        obs: OBSBridge,
        *,
        scene_id: str,
    ) -> None:
        ensure_data_dirs()
        self.backend = backend
        self.obs = obs
        self.scene_id = safe_id(scene_id)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_dir = STILLS_DIR / f"{stamp}_{self.scene_id}"
        self.images_dir = self.output_dir / "images"
        self.samples_csv = self.output_dir / "samples.csv"
        self.samples_json = self.output_dir / "samples.json"
        self.rows: list[dict[str, Any]] = []

    def capture_current(
        self,
        *,
        label: str,
        source_name: str,
        image_format: str,
        width: int,
        height: int,
        quality: int,
    ) -> dict[str, Any]:
        pose = self.backend.pose()
        sample_index = len(self.rows) + 1
        extension = "jpg" if image_format.lower() in {"jpg", "jpeg"} else "png"
        image_path = self.images_dir / (
            f"{sample_index:06d}_{safe_id(label, 'current')}"
            f"_x{pose.x:.3f}_y{pose.y:.3f}_z{pose.z:.3f}"
            f"_yaw{pose.yaw_degrees:.2f}_pitch{pose.pitch_degrees:.2f}.{extension}"
        )
        source = self.obs.save_screenshot(
            image_path,
            source_name=source_name,
            image_format=extension,
            width=width,
            height=height,
            quality=quality,
        )
        row = {
            "sample_index": sample_index,
            "scene_id": self.scene_id,
            "label": label,
            "captured_at": pose.captured_at,
            "image_path": str(image_path),
            "obs_source": source,
            **pose.as_dict(),
        }
        self.rows.append(row)
        self._write_metadata()
        return row

    def _write_metadata(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.samples_csv.open(
            "w", newline="", encoding="utf-8-sig"
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=SAMPLE_FIELDS)
            writer.writeheader()
            for row in self.rows:
                writer.writerow(
                    {name: row.get(name, "") for name in SAMPLE_FIELDS}
                )
        self.samples_json.write_text(
            json.dumps(
                {
                    "scene_id": self.scene_id,
                    "count": len(self.rows),
                    "samples": self.rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
