from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path
import shutil
from threading import Event
import time
from typing import Any, Callable

from .models import StillSample
from .obs_bridge import OBSBridge
from .paths import STILLS_DIR, ensure_data_dirs
from .pose_control import ClosedLoopPoseController, PoseTarget
from .storage import safe_id


def load_scan_samples(manifest_path: str | Path) -> tuple[dict[str, Any], list[StillSample]]:
    manifest_file = Path(manifest_path).resolve()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    samples_value = manifest.get("samples_csv")
    if not samples_value:
        raise ValueError("Scan manifest has no samples_csv")
    samples_path = Path(str(samples_value))
    if not samples_path.is_absolute():
        samples_path = manifest_file.parent / samples_path
    if not samples_path.exists():
        raise FileNotFoundError(f"Scan sample CSV not found: {samples_path}")

    samples: list[StillSample] = []
    with samples_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            samples.append(
                StillSample(
                    sample_index=int(row["sample_index"]),
                    point_index=int(row["point_index"]),
                    pattern=str(row["pattern"]),
                    x=float(row["x"]),
                    y=float(row["y"]),
                    z=float(row["z"]),
                    yaw_degrees=float(row["yaw_degrees"]),
                    pitch_degrees=float(row["pitch_degrees"]),
                    fov_degrees=float(row["fov_degrees"]),
                )
            )
    expected = int(manifest.get("image_count") or len(samples))
    if len(samples) != expected:
        raise ValueError(
            f"Manifest expects {expected} samples, but CSV contains {len(samples)}"
        )
    return manifest, samples


class AutomatedStillScan:
    FIELDNAMES = [
        "session_id",
        "sample_index",
        "point_index",
        "pattern",
        "target_x",
        "target_y",
        "target_z",
        "target_yaw_degrees",
        "target_pitch_degrees",
        "target_roll_degrees",
        "target_fov_degrees",
        "observed_x",
        "observed_y",
        "observed_z",
        "observed_yaw_degrees",
        "observed_pitch_degrees",
        "observed_roll_degrees",
        "observed_fov_degrees",
        "position_error",
        "yaw_error_degrees",
        "pitch_error_degrees",
        "roll_error_degrees",
        "fov_error_degrees",
        "image_path",
        "obs_source",
        "captured_at",
    ]

    def __init__(
        self,
        controller: ClosedLoopPoseController,
        obs: OBSBridge,
        *,
        stop_event: Event | None = None,
    ) -> None:
        self.controller = controller
        self.obs = obs
        self.stop_event = stop_event or Event()

    def stop(self) -> None:
        self.stop_event.set()

    def run(
        self,
        manifest_path: str | Path,
        *,
        scene_id: str,
        source_name: str,
        image_format: str,
        width: int,
        height: int,
        quality: int,
        settle_seconds: float = 0.05,
        start_sample: int = 1,
        end_sample: int | None = None,
        strict_pose: bool = True,
        progress_callback: Callable[
            [StillSample, int, int, Path, dict[str, Any]], None
        ]
        | None = None,
    ) -> dict[str, Any]:
        ensure_data_dirs()
        source_manifest, samples = load_scan_samples(manifest_path)
        selected = [
            sample
            for sample in samples
            if sample.sample_index >= start_sample
            and (end_sample is None or sample.sample_index <= end_sample)
        ]
        if not selected:
            raise ValueError("Selected scan sample range is empty")

        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        session_id = f"{stamp}_{safe_id(scene_id)}_auto22"
        output_dir = STILLS_DIR / session_id
        images_dir = output_dir / "images"
        output_dir.mkdir(parents=True, exist_ok=True)
        images_dir.mkdir(parents=True, exist_ok=True)
        source_copy = output_dir / "source_scan_plan.json"
        shutil.copy2(Path(manifest_path).resolve(), source_copy)
        samples_csv = output_dir / "samples.csv"
        run_manifest = output_dir / "run_manifest.json"
        control_reports = output_dir / "pose_control_reports.jsonl"

        state: dict[str, Any] = {
            "session_id": session_id,
            "scene_id": safe_id(scene_id),
            "started_at": dt.datetime.now().astimezone().isoformat(),
            "source_manifest": str(Path(manifest_path).resolve()),
            "source_manifest_copy": str(source_copy),
            "selected_start_sample": selected[0].sample_index,
            "selected_end_sample": selected[-1].sample_index,
            "selected_count": len(selected),
            "completed_count": 0,
            "status": "running",
            "strict_pose": strict_pose,
            "settle_seconds": settle_seconds,
            "output_dir": str(output_dir),
            "samples_csv": str(samples_csv),
            "control_reports": str(control_reports),
            "source_plan": source_manifest,
        }
        self._write_manifest(run_manifest, state)
        extension = (
            "jpg"
            if image_format.lower().lstrip(".") in {"jpg", "jpeg"}
            else "png"
        )

        caught: Exception | None = None
        restored: dict[str, Any] | None = None
        try:
            with (
                samples_csv.open("w", newline="", encoding="utf-8-sig") as csv_handle,
                control_reports.open("w", encoding="utf-8") as report_handle,
            ):
                writer = csv.DictWriter(csv_handle, fieldnames=self.FIELDNAMES)
                writer.writeheader()
                total = len(selected)
                for completed, sample in enumerate(selected, start=1):
                    if self.stop_event.is_set():
                        state["status"] = "stopped"
                        break
                    target = PoseTarget(
                        x=sample.x,
                        y=sample.y,
                        z=sample.z,
                        yaw_degrees=sample.yaw_degrees,
                        pitch_degrees=sample.pitch_degrees,
                        roll_degrees=0.0,
                        fov_degrees=sample.fov_degrees,
                    )
                    control_report = self.controller.move_to(
                        target,
                        strict=strict_pose,
                    )
                    if settle_seconds > 0:
                        time.sleep(settle_seconds)
                    observed = self.controller.backend.pose()
                    error = control_report["error"]
                    image_path = images_dir / (
                        f"s{sample.sample_index:06d}_p{sample.point_index:05d}_"
                        f"{safe_id(sample.pattern, 'view')}_"
                        f"yaw{sample.yaw_degrees:+07.2f}_"
                        f"pitch{sample.pitch_degrees:+06.2f}.{extension}"
                    )
                    obs_source = self.obs.save_screenshot(
                        image_path,
                        source_name=source_name,
                        image_format=extension,
                        width=width,
                        height=height,
                        quality=quality,
                    )
                    row = {
                        "session_id": session_id,
                        "sample_index": sample.sample_index,
                        "point_index": sample.point_index,
                        "pattern": sample.pattern,
                        "target_x": sample.x,
                        "target_y": sample.y,
                        "target_z": sample.z,
                        "target_yaw_degrees": sample.yaw_degrees,
                        "target_pitch_degrees": sample.pitch_degrees,
                        "target_roll_degrees": 0.0,
                        "target_fov_degrees": sample.fov_degrees,
                        "observed_x": observed.x,
                        "observed_y": observed.y,
                        "observed_z": observed.z,
                        "observed_yaw_degrees": observed.yaw_degrees,
                        "observed_pitch_degrees": observed.pitch_degrees,
                        "observed_roll_degrees": observed.roll_degrees,
                        "observed_fov_degrees": observed.fov_degrees,
                        "position_error": error["position"],
                        "yaw_error_degrees": error["yaw_degrees"],
                        "pitch_error_degrees": error["pitch_degrees"],
                        "roll_error_degrees": error["roll_degrees"],
                        "fov_error_degrees": error["fov_degrees"],
                        "image_path": str(image_path),
                        "obs_source": obs_source,
                        "captured_at": dt.datetime.now().astimezone().isoformat(),
                    }
                    writer.writerow(row)
                    csv_handle.flush()
                    report_handle.write(
                        json.dumps(control_report, ensure_ascii=False) + "\n"
                    )
                    report_handle.flush()
                    state["completed_count"] = completed
                    state["last_sample_index"] = sample.sample_index
                    state["last_image_path"] = str(image_path)
                    self._write_manifest(run_manifest, state)
                    if progress_callback is not None:
                        progress_callback(
                            sample,
                            completed,
                            total,
                            image_path,
                            control_report,
                        )
                else:
                    state["status"] = "completed"
        except Exception as exc:
            caught = exc
            state["status"] = "failed"
            state["error"] = str(exc)
        finally:
            try:
                restored = self.controller.restore_start()
            except Exception as restore_exc:
                state["restore_error"] = str(restore_exc)
                if caught is None:
                    caught = restore_exc
                    state["status"] = "failed"
            state["restored_start"] = restored
            state["finished_at"] = dt.datetime.now().astimezone().isoformat()
            self._write_manifest(run_manifest, state)

        if caught is not None:
            raise caught
        state["run_manifest"] = str(run_manifest)
        return state

    @staticmethod
    def _write_manifest(path: Path, state: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
