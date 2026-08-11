from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
import shutil
import threading
from typing import Any, Callable, Sequence

from .backend import CameraBackend
from .models import TrajectoryKeyframe
from .obs_bridge import OBSBridge
from .recording import VideoPoseSession
from .storage import safe_id


class ImportedTrajectoryRecorder:
    """Capture one imported dense trajectory with OBS and observed pose data."""

    def __init__(
        self,
        backend: CameraBackend,
        obs: OBSBridge,
        *,
        scene_id: str,
        trajectory_id: str,
        pose_hz: float = 60.0,
    ) -> None:
        self.backend = backend
        self.trajectory_id = safe_id(trajectory_id)
        self.session = VideoPoseSession(
            backend,
            obs,
            scene_id=scene_id,
            pose_hz=pose_hz,
        )
        self.stop_event = threading.Event()
        self.active = False

    def capture(
        self,
        frames: Sequence[TrajectoryKeyframe],
        *,
        source_path: str | Path,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        if self.active:
            raise RuntimeError("Imported trajectory capture is already active")
        if len(frames) < 2:
            raise RuntimeError("Import a trajectory with at least two frames first")
        source = Path(source_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"Trajectory source does not exist: {source}")

        self.stop_event.clear()
        self.session.start()
        self.active = True
        copied_source = self.session.output_dir / "trajectory_source.json"
        shutil.copy2(source, copied_source)
        timing_csv = self.session.output_dir / "trajectory_timing.csv"
        self.session.update_metadata(
            {
                "capture_mode": "direct_absolute_trajectory_60fps",
                "trajectory_id": self.trajectory_id,
                "trajectory_source": str(source),
                "trajectory_source_copy": str(copied_source),
                "trajectory_source_sha256": hashlib.sha256(
                    source.read_bytes()
                ).hexdigest(),
                "trajectory_requested_frames": len(frames),
                "trajectory_requested_duration_sec": (
                    frames[-1].time_sec - frames[0].time_sec
                ),
                "trajectory_play_requested_at": dt.datetime.now()
                .astimezone()
                .isoformat(),
                "trajectory_timing_csv": str(timing_csv),
            }
        )

        playback_error: Exception | None = None
        try:
            playback = self.backend.run_imported_trajectory(
                frames,
                timing_csv_path=timing_csv,
                stop_requested=self.stop_event.is_set,
                progress_callback=progress_callback,
            )
            self.session.update_metadata({"trajectory_playback": playback})
        except Exception as exc:
            playback_error = exc
            self.session.update_metadata(
                {
                    "trajectory_playback_error": str(exc),
                    "trajectory_playback_failed_at": dt.datetime.now()
                    .astimezone()
                    .isoformat(),
                }
            )
        finally:
            stop_error: Exception | None = None
            try:
                if self.session.started:
                    self.session.stop()
            except Exception as exc:
                stop_error = exc
            finally:
                self.active = False
            if playback_error is not None:
                if stop_error is not None:
                    raise RuntimeError(
                        f"{playback_error}; recording stop also failed: {stop_error}"
                    ) from playback_error
                raise playback_error
            if stop_error is not None:
                raise stop_error

        self.session.update_metadata(
            {
                "trajectory_capture_finished_at": dt.datetime.now()
                .astimezone()
                .isoformat(),
            }
        )
        return dict(self.session.manifest)

    def request_stop(self) -> None:
        if not self.active:
            raise RuntimeError("Imported trajectory capture is not active")
        self.stop_event.set()
