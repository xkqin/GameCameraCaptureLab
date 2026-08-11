from __future__ import annotations

import datetime as dt
from typing import Any

from .backend import CameraBackend
from .obs_bridge import OBSBridge
from .recording import VideoPoseSession


class PathPlaybackRecorder:
    """One-click IGCS Camera Path playback with OBS and continuous pose."""

    def __init__(
        self,
        backend: CameraBackend,
        obs: OBSBridge,
        *,
        scene_id: str,
        trajectory_id: str,
        pose_hz: float,
    ) -> None:
        self.backend = backend
        self.trajectory_id = trajectory_id
        self.session = VideoPoseSession(
            backend,
            obs,
            scene_id=scene_id,
            pose_hz=pose_hz,
        )
        self.active = False

    def start(self) -> dict[str, Any]:
        manifest = self.session.start()
        try:
            self.backend.send_action("path_play_pause", 80)
        except Exception:
            self.session.stop()
            raise
        self.active = True
        self.session.update_metadata(
            {
                "capture_mode": "igcs_camera_path",
                "trajectory_id": self.trajectory_id,
                "path_play_requested_at": dt.datetime.now()
                .astimezone()
                .isoformat(),
                "path_timing_note": (
                    "IGCS Camera Path controls interpolation and playback duration. "
                    "The pose CSV records the observed runtime path."
                ),
            }
        )
        return dict(self.session.manifest)

    def stop(self) -> dict[str, Any]:
        if not self.active:
            raise RuntimeError("IGCS path recording is not active")
        path_stop_error: str | None = None
        try:
            self.backend.send_action("path_stop", 80)
        except Exception as exc:
            path_stop_error = str(exc)
        try:
            result = self.session.stop()
        finally:
            self.active = False
        self.session.update_metadata(
            {
                "path_stop_requested_at": dt.datetime.now()
                .astimezone()
                .isoformat(),
                "path_stop_error": path_stop_error,
            }
        )
        result = dict(self.session.manifest)
        if path_stop_error:
            raise RuntimeError(
                "OBS/Pose stopped, but IGCS path stop hotkey failed: "
                f"{path_stop_error}"
            )
        return result
