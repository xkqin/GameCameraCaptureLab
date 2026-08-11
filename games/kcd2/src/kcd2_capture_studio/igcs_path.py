from __future__ import annotations

import datetime as dt
import json
from threading import Event
from typing import Any, Callable, Iterable

from .backend import CameraBackend
from .models import TrajectoryKeyframe
from .paths import TRAJECTORIES_DIR, ensure_data_dirs
from .pose_control import ClosedLoopPoseController, PoseTarget
from .storage import safe_id


class IGCSCameraPathBuilder:
    """Create an in-DLL Camera Path by positioning the camera and adding nodes."""

    def __init__(
        self,
        backend: CameraBackend,
        controller: ClosedLoopPoseController,
        *,
        stop_event: Event | None = None,
    ) -> None:
        self.backend = backend
        self.controller = controller
        self.stop_event = stop_event or Event()

    def stop(self) -> None:
        self.stop_event.set()

    def build(
        self,
        frames: Iterable[TrajectoryKeyframe],
        *,
        trajectory_id: str,
        strict_pose: bool = True,
        max_nodes: int = 128,
        progress_callback: Callable[
            [TrajectoryKeyframe, int, int, dict[str, Any]], None
        ]
        | None = None,
    ) -> dict[str, Any]:
        selected = list(frames)
        if len(selected) < 2:
            raise ValueError("Camera Path requires at least two keyframes")
        if len(selected) > max_nodes:
            raise ValueError(
                f"Camera Path has {len(selected)} nodes; current safety limit is "
                f"{max_nodes}. Resample the trajectory first."
            )
        ensure_data_dirs()
        safe_trajectory = safe_id(trajectory_id, "trajectory")
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = (
            TRAJECTORIES_DIR / f"{stamp}_{safe_trajectory}_igcs_path_build.json"
        )
        report: dict[str, Any] = {
            "trajectory_id": safe_trajectory,
            "started_at": dt.datetime.now().astimezone().isoformat(),
            "requested_nodes": len(selected),
            "completed_nodes": 0,
            "status": "building",
            "timing_note": (
                "Node positions, orientation and FOV are copied into the IGCS "
                "Camera Path. Playback duration/interpolation remain controlled "
                "by the in-game IGCS path settings."
            ),
            "nodes": [],
        }
        caught: Exception | None = None
        try:
            self.backend.send_action("path_add", 80)
            for index, frame in enumerate(selected, start=1):
                if self.stop_event.is_set():
                    report["status"] = "stopped"
                    break
                target = PoseTarget(
                    x=frame.x,
                    y=frame.y,
                    z=frame.z,
                    yaw_degrees=frame.yaw_degrees,
                    pitch_degrees=frame.pitch_degrees,
                    roll_degrees=frame.roll_degrees,
                    fov_degrees=frame.fov_degrees,
                )
                control_report = self.controller.move_to(
                    target,
                    strict=strict_pose,
                )
                self.backend.send_action("path_add_node", 80)
                report["nodes"].append(
                    {
                        "node_index": index,
                        "source_keyframe": frame.as_dict(),
                        "control": control_report,
                    }
                )
                report["completed_nodes"] = index
                report_path.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                if progress_callback is not None:
                    progress_callback(frame, index, len(selected), control_report)
            else:
                report["status"] = "completed"
        except Exception as exc:
            caught = exc
            report["status"] = "failed"
            report["error"] = str(exc)
        finally:
            try:
                report["restored_start"] = self.controller.restore_start()
            except Exception as restore_exc:
                report["restore_error"] = str(restore_exc)
                if caught is None:
                    caught = restore_exc
                    report["status"] = "failed"
            report["finished_at"] = dt.datetime.now().astimezone().isoformat()
            report["report_path"] = str(report_path)
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if caught is not None:
            raise caught
        return report

    def play_pause(self) -> None:
        self.backend.send_action("path_play_pause", 80)

    def stop_playback(self) -> None:
        self.backend.send_action("path_stop", 80)
