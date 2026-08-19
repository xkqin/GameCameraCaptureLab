from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import time
from typing import Any

from .backend import CameraBackend, LivePoseRecorder
from .coordinate_scale import COORDINATE_SCALE
from .obs_bridge import OBSBridge
from .paths import RUNS_DIR, ensure_data_dirs
from .storage import safe_id


class VideoPoseSession:
    """Start and stop OBS recording with a timestamped live pose CSV."""

    def __init__(
        self,
        backend: CameraBackend,
        obs: OBSBridge,
        *,
        scene_id: str,
        pose_hz: float,
    ) -> None:
        ensure_data_dirs()
        self.backend = backend
        self.obs = obs
        self.scene_id = safe_id(scene_id)
        self.pose_hz = pose_hz
        self.pose_recorder = LivePoseRecorder(backend)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_id = f"{stamp}_{self.scene_id}"
        self.output_dir = RUNS_DIR / self.session_id
        self.manifest_path = self.output_dir / "recording_manifest.json"
        self.started = False
        self.manifest: dict[str, Any] = {}

    def start(self) -> dict[str, Any]:
        if self.started:
            raise RuntimeError("Recording session is already active")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        pose_path = self.pose_recorder.start(
            session_id=self.session_id,
            hz=self.pose_hz,
        )
        self.pose_recorder.wait_until_ready()
        pose_start_ns = self.pose_recorder.started_perf_counter_ns
        if pose_start_ns is None:
            self.pose_recorder.stop()
            raise RuntimeError("Pose recorder did not expose its monotonic start")
        obs_start_request_ns = time.perf_counter_ns()
        try:
            self.obs.start_recording()
        except Exception:
            self.pose_recorder.stop()
            raise
        obs_start_ack_ns = time.perf_counter_ns()
        now_wall = dt.datetime.now().astimezone().isoformat()
        self.manifest = {
            "session_id": self.session_id,
            "scene_id": self.scene_id,
            "coordinate_system": COORDINATE_SCALE.coordinate_system(),
            "started_at": now_wall,
            "pose_started_at": self.pose_recorder.started_wall_time,
            "pose_start_monotonic_ns": pose_start_ns,
            "obs_start_request_monotonic_ns": obs_start_request_ns,
            "obs_start_ack_monotonic_ns": obs_start_ack_ns,
            "pose_time_at_obs_start_sec": (
                obs_start_request_ns - pose_start_ns
            )
            / 1_000_000_000.0,
            "pose_hz": self.pose_hz,
            "pose_csv": str(pose_path),
            "timing_note": (
                "Pose logging starts before OBS. For a video frame timestamp t, "
                "the matching pose time is approximately "
                "t + pose_time_at_obs_start_sec. The offset uses the local "
                "OBS StartRecord request timestamp."
            ),
            "status": "recording",
        }
        self._write_manifest()
        self.started = True
        return dict(self.manifest)

    def stop(self) -> dict[str, Any]:
        if not self.started:
            raise RuntimeError("No recording session is active")
        video_path: str | None = None
        obs_error: str | None = None
        obs_stop_request_ns = time.perf_counter_ns()
        try:
            video_path = self.obs.stop_recording()
        except Exception as exc:
            obs_error = str(exc)
        pose_path = self.pose_recorder.stop()
        self.manifest.update(
            {
                "stopped_at": dt.datetime.now().astimezone().isoformat(),
                "obs_stop_request_monotonic_ns": obs_stop_request_ns,
                "stop_monotonic_ns": time.perf_counter_ns(),
                "pose_frames": self.pose_recorder.frame_count,
                "pose_csv": str(pose_path) if pose_path else None,
                "video_path": video_path,
                "pose_error": (
                    str(self.pose_recorder.last_error)
                    if self.pose_recorder.last_error
                    else None
                ),
                "obs_stop_error": obs_error,
                "status": "stopped" if not obs_error else "stopped_with_error",
            }
        )
        self._write_manifest()
        self.started = False
        if obs_error:
            raise RuntimeError(
                f"OBS stop failed, but pose logging was stopped safely: {obs_error}"
            )
        return dict(self.manifest)

    def update_metadata(self, values: dict[str, Any]) -> None:
        self.manifest.update(values)
        self._write_manifest()

    def _write_manifest(self) -> None:
        self.manifest_path.write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
