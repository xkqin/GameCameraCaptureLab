from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Any, Callable, Protocol, Sequence

from .bridge import METADATA_VERSION
from .models import CameraPose, CapturePoint, ImportedTrajectory
from .paths import TRAJECTORY_CAPTURES_DIR


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".mov", ".flv", ".avi"}


class PoseReader(Protocol):
    def read_pose(self) -> CameraPose: ...


class SmoothTrajectoryController(Protocol):
    def start_native_trajectory(
        self,
        points: Sequence[CapturePoint],
        *,
        playback_hz: float = 60.0,
        timeout_seconds: float = 1.0,
    ) -> Any: ...

    def read_trajectory_status(self) -> Any: ...

    def stop_native_trajectory(self, *, timeout_seconds: float = 1.0) -> Any: ...


class PoseMover(Protocol):
    def move_to(
        self,
        target: CameraPose,
        *,
        stop_requested: Callable[[], bool],
        on_update: Callable[[str], None] | None,
    ) -> CameraPose: ...


class RecordingOBS(Protocol):
    def set_record_directory(self, directory: str | Path) -> Path: ...
    def mute_all_audio_inputs(self) -> int: ...
    def restore_audio_inputs(self) -> None: ...
    def start_recording(self) -> None: ...
    def stop_recording(self) -> str | None: ...
    def recording_status(self) -> dict[str, Any]: ...
    def close(self) -> None: ...


def safe_id(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip())
    return cleaned.strip("._")[:80] or "scene"


def _pose_row(pose: CameraPose) -> dict[str, Any]:
    return pose.as_dict()


def _wrap_degrees(value: float) -> float:
    return (float(value) + 180.0) % 360.0 - 180.0


def _pose_delta(first: CameraPose, second: CameraPose) -> tuple[float, float, float]:
    dx = second.x - first.x
    dy = second.y - first.y
    dz = second.z - first.z
    position = (dx * dx + dy * dy + dz * dz) ** 0.5
    angle = max(
        abs(_wrap_degrees(second.yaw_degrees - first.yaw_degrees)),
        abs(_wrap_degrees(second.pitch_degrees - first.pitch_degrees)),
        abs(_wrap_degrees(second.roll_degrees - first.roll_degrees)),
    )
    return position, angle, abs(second.fov_degrees - first.fov_degrees)


def _pose_within(
    current: CameraPose,
    target: CameraPose,
    *,
    position_tolerance: float,
    angle_tolerance: float,
    fov_tolerance: float,
) -> bool:
    position, angle, fov = _pose_delta(current, target)
    return (
        position <= position_tolerance
        and angle <= angle_tolerance
        and fov <= fov_tolerance
    )


def _wait_for_stable_pose(
    bridge: PoseReader,
    stop_event: threading.Event,
    *,
    stable_seconds: float,
    timeout_seconds: float,
) -> CameraPose:
    required = max(0.0, float(stable_seconds))
    anchor = bridge.read_pose()
    if required <= 0.0:
        return anchor
    stable_since = time.monotonic()
    deadline = stable_since + max(required + 0.5, float(timeout_seconds))
    while True:
        if stop_event.wait(0.02):
            raise InterruptedError
        current = bridge.read_pose()
        now = time.monotonic()
        position, angle, fov = _pose_delta(anchor, current)
        if position > 0.25 or angle > 0.05 or fov > 0.02:
            anchor = current
            stable_since = now
        elif now - stable_since >= required:
            return current
        if now >= deadline:
            raise TimeoutError("Camera did not become stable before recording")


def _write_csv(path: Path, rows: Sequence[dict[str, Any]], fieldnames: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _find_video(directory: Path) -> Path | None:
    candidates = [
        path
        for root in (directory / "raw", directory)
        if root.is_dir()
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in VIDEO_EXTENSIONS
        and path.stat().st_size > 0
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def trajectory_capture_complete(output_dir: str | Path) -> bool:
    root = Path(output_dir)
    required = (
        root / "source_keyframes.csv",
        root / "playback_plan.csv",
        root / "observed_pose.csv",
        root / "trajectory_timing.csv",
        root / "recording_manifest.json",
    )
    if not all(path.is_file() for path in required):
        return False
    try:
        manifest = json.loads((root / "recording_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return manifest.get("status") == "completed" and _find_video(root) is not None


def find_latest_resumable_batch(scene_id: str) -> dict[str, Any] | None:
    scene_root = TRAJECTORY_CAPTURES_DIR / safe_id(scene_id)
    if not scene_root.is_dir():
        return None
    manifests = sorted(
        scene_root.glob("*/run_manifest.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for manifest_path in manifests:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        source_value = str(manifest.get("source_copy") or "")
        source_copy = (
            Path(source_value)
            if source_value
            else manifest_path.parent / "trajectory_set_source.json"
        )
        if not source_copy.is_file():
            copies = sorted(manifest_path.parent.glob("trajectory_set_source.*"))
            source_copy = copies[0] if copies else source_copy
        if not source_copy.is_file():
            continue
        try:
            total = int(manifest.get("total_trajectories") or 0)
        except (TypeError, ValueError):
            continue
        if total <= 0:
            continue
        raw_planned = manifest.get("planned_indices")
        planned: list[int] = []
        if isinstance(raw_planned, list):
            for value in raw_planned:
                try:
                    index = int(value) - 1
                except (TypeError, ValueError):
                    continue
                if 0 <= index < total:
                    planned.append(index)
        else:
            planned = list(range(total))
        pending = [
            index
            for index in planned
            if not trajectory_capture_complete(manifest_path.parent / f"traj_{index + 1:04d}")
        ]
        if pending:
            return {
                "batch_dir": manifest_path.parent.resolve(),
                "manifest_path": manifest_path.resolve(),
                "source_path": source_copy.resolve(),
                "manifest": manifest,
                "total": total,
                "pending_indices": pending,
            }
    return None


@dataclass(frozen=True)
class TrajectoryCaptureResult:
    output_dir: Path
    manifest_path: Path
    video_path: Path
    completed_points: int
    requested_points: int
    stopped: bool
    video_paths: tuple[Path, ...] = ()
    obs_restart_count: int = 0


class TrajectoryRecorder:
    """Record one trajectory with OBS and live poses.

    New bridges receive the complete trajectory once and interpolate it inside
    the injected process. The closed-loop mover remains only for pre-positioning
    and as a compatibility path for old test doubles/bridges.
    """

    def __init__(
        self,
        *,
        bridge: PoseReader,
        mover: PoseMover,
        obs: RecordingOBS,
        output_dir: str | Path,
        obs_restart_factory: Callable[[Path], RecordingOBS] | None = None,
        obs_restart_interval_seconds: float = 0.0,
        pose_hz: float = 30.0,
        pre_record_settle_seconds: float = 0.35,
        playback_hz: float = 60.0,
    ) -> None:
        self.bridge = bridge
        self.mover = mover
        self.obs = obs
        self.output_dir = Path(output_dir).resolve()
        self.obs_restart_factory = obs_restart_factory
        self.obs_restart_interval_seconds = max(0.0, float(obs_restart_interval_seconds))
        self.pose_hz = max(1.0, float(pose_hz))
        self.pre_record_settle_seconds = max(0.0, float(pre_record_settle_seconds))
        self.playback_hz = min(240.0, max(30.0, float(playback_hz)))
        self.stop_event = threading.Event()
        self.active = False

    def request_stop(self) -> None:
        self.stop_event.set()

    def capture(
        self,
        trajectory: ImportedTrajectory,
        *,
        source_path: str | Path,
        progress_callback: Callable[[int, int, str], None] | None = None,
        log_callback: Callable[[str], None] | None = None,
    ) -> TrajectoryCaptureResult:
        if self.active:
            raise RuntimeError("轨迹采集已经在运行 / Trajectory capture is already running")
        if not trajectory.points:
            raise ValueError("轨迹没有关键帧 / Trajectory has no keyframes")
        source = Path(source_path).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"轨迹源文件不存在 / Source file not found: {source}")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        raw_dir = self.output_dir / "raw"
        raw_dir.mkdir(exist_ok=True)
        manifest_path = self.output_dir / "recording_manifest.json"
        source_csv = self.output_dir / "source_keyframes.csv"
        playback_csv = self.output_dir / "playback_plan.csv"
        pose_csv = self.output_dir / "observed_pose.csv"
        timing_csv = self.output_dir / "trajectory_timing.csv"

        point_rows = [
            {
                "sequence": sequence,
                "index": point.index,
                "label": point.label,
                "time_sec": point.time_sec,
                **_pose_row(point.pose),
            }
            for sequence, point in enumerate(trajectory.points, start=1)
        ]
        point_fields = list(point_rows[0].keys())
        _write_csv(source_csv, point_rows, point_fields)
        _write_csv(playback_csv, point_rows, point_fields)

        observed: list[dict[str, Any]] = []
        timing: list[dict[str, Any]] = []
        pose_lock = threading.Lock()
        pose_stop = threading.Event()
        started_monotonic = time.monotonic()
        obs: RecordingOBS | None = self.obs
        recording_started = False
        audio_muted = False
        muted_count = 0
        muted_counts: list[int] = []
        completed = 0
        stopped = False
        error: str | None = None
        video_path: Path | None = None
        video_paths: list[Path] = []
        video_segments: list[dict[str, Any]] = []
        obs_restart_events: list[dict[str, Any]] = []
        segment_index = 0
        segment_started_monotonic: float | None = None
        last_camera_elapsed: float | None = None
        next_obs_restart_monotonic: float | None = None
        final_obs_stop_error: str | None = None
        start_pose: CameraPose | None = None
        restore_attempted = False
        restore_succeeded: bool | None = None
        restored_pose: CameraPose | None = None
        restore_error: str | None = None
        pre_record_started: float | None = None
        pre_record_finished: float | None = None
        pre_record_pose: CameraPose | None = None
        pre_record_stable = False
        stabilization_attempts = 0
        smooth_playback = False
        smooth_start_attempted = False
        smooth_playback_started: float | None = None
        smooth_playback_finished: float | None = None
        smooth_last_segment = -1

        def append_timing(sequence: int, point: CapturePoint, actual: CameraPose) -> None:
            source_time = point.time_sec - trajectory.points[0].time_sec
            timing.append(
                {
                    "sequence": sequence,
                    "point_index": point.index,
                    "label": point.label,
                    "source_time_sec": point.time_sec,
                    "move_started_sec": source_time,
                    "move_finished_sec": source_time,
                    "move_duration_sec": 0.0,
                    "actual_x": actual.x,
                    "actual_y": actual.y,
                    "actual_z": actual.z,
                    "actual_yaw_degrees": actual.yaw_degrees,
                    "actual_pitch_degrees": actual.pitch_degrees,
                    "actual_roll_degrees": actual.roll_degrees,
                    "actual_fov_degrees": actual.fov_degrees,
                }
            )

        def recording_elapsed() -> float:
            return max(0.0, time.monotonic() - started_monotonic)

        def start_obs_segment(camera_elapsed: float | None = None) -> None:
            nonlocal audio_muted
            nonlocal muted_count, recording_started, segment_index
            nonlocal segment_started_monotonic, started_monotonic
            nonlocal next_obs_restart_monotonic
            if obs is None:
                raise RuntimeError("OBS 未连接，无法开始录像 / OBS is disconnected; recording cannot start")
            if segment_index == 0:
                # The recording clock starts only after the first keyframe has
                # converged.  It is never reset by an OBS restart.
                started_monotonic = time.monotonic()
            segment_index += 1
            segment_dir = raw_dir / f"segment_{segment_index:04d}"
            segment_dir.mkdir(parents=True, exist_ok=True)
            obs.set_record_directory(segment_dir)
            muted_count = int(obs.mute_all_audio_inputs())
            muted_counts.append(muted_count)
            audio_muted = True
            obs.start_recording()
            recording_started = True
            segment_started_monotonic = time.monotonic()
            video_segments.append(
                {
                    "segment_index": segment_index,
                    "directory": str(segment_dir),
                    "status": "recording",
                    "recording_start_elapsed_sec": recording_elapsed(),
                    "recording_end_elapsed_sec": None,
                    "recording_duration_sec": None,
                    "trajectory_start_elapsed_sec": camera_elapsed,
                    "trajectory_end_elapsed_sec": None,
                    "video_path": None,
                    "stop_reason": "pending",
                    "error": "",
                }
            )
            if self.obs_restart_interval_seconds > 0.0:
                next_obs_restart_monotonic = (
                    segment_started_monotonic + self.obs_restart_interval_seconds
                )

        def finish_obs_segment(
            reason: str,
            camera_elapsed: float | None = None,
        ) -> Path | None:
            nonlocal audio_muted, final_obs_stop_error, video_path
            if not video_segments or obs is None:
                return None
            segment = video_segments[-1]
            returned: str | None = None
            try:
                returned = obs.stop_recording()
            except Exception as exc:
                segment["status"] = "stop_failed"
                segment["stop_reason"] = reason
                segment["error"] = str(exc)
                final_obs_stop_error = str(exc)
                raise
            if audio_muted:
                obs.restore_audio_inputs()
                audio_muted = False
            segment_path = Path(returned).resolve() if returned else _find_video(Path(segment["directory"]))
            if segment_path is not None and segment_path.is_file():
                segment_path = segment_path.resolve()
                video_path = segment_path
                if segment_path not in video_paths:
                    video_paths.append(segment_path)
                segment["video_path"] = str(segment_path)
                segment["status"] = "completed"
            else:
                segment["status"] = "no_video"
            finished_elapsed = recording_elapsed()
            segment["recording_end_elapsed_sec"] = finished_elapsed
            segment["recording_duration_sec"] = max(
                0.0,
                finished_elapsed - float(segment["recording_start_elapsed_sec"]),
            )
            segment["trajectory_end_elapsed_sec"] = camera_elapsed
            segment["stop_reason"] = reason
            return segment_path

        def restart_obs_segment(camera_elapsed: float | None) -> None:
            nonlocal obs
            if self.obs_restart_factory is None:
                raise RuntimeError(
                    "已启用 OBS 定时重启，但未配置 obs_restart_factory。 / "
                    "Timed OBS restart is enabled but obs_restart_factory is missing."
                )
            event: dict[str, Any] = {
                "event_index": len(obs_restart_events) + 1,
                "reason": "interval",
                "previous_segment": segment_index,
                "next_segment": segment_index + 1,
                "requested_elapsed_sec": recording_elapsed(),
                "camera_elapsed_sec": camera_elapsed,
                "status": "restarting",
            }
            restart_started = time.monotonic()
            previous_obs = obs
            try:
                finish_obs_segment("obs_restart", camera_elapsed)
                obs = None
                previous_obs.close()
                obs = self.obs_restart_factory(self.output_dir)
                start_obs_segment(camera_elapsed)
                event.update(
                    {
                        "status": "completed",
                        "completed_elapsed_sec": recording_elapsed(),
                        "restart_duration_sec": time.monotonic() - restart_started,
                        "gap_after_previous_segment_sec": max(
                            0.0,
                            float(video_segments[-1]["recording_start_elapsed_sec"])
                            - float(video_segments[-2]["recording_end_elapsed_sec"]),
                        )
                        if len(video_segments) >= 2
                        else None,
                    }
                )
            except Exception as exc:
                event.update(
                    {
                        "status": "failed",
                        "completed_elapsed_sec": recording_elapsed(),
                        "restart_duration_sec": time.monotonic() - restart_started,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                if obs is not None and obs is not previous_obs:
                    try:
                        obs.close()
                    except Exception:
                        pass
                raise
            finally:
                obs_restart_events.append(event)

        def maybe_restart_obs(camera_elapsed: float | None, remaining_seconds: float | None) -> None:
            if self.obs_restart_interval_seconds <= 0.0:
                return
            if self.obs_restart_factory is None:
                raise RuntimeError(
                    "OBS 定时重启已启用，但没有重启回调。 / OBS restart callback is missing."
                )
            if next_obs_restart_monotonic is None:
                return
            if time.monotonic() < next_obs_restart_monotonic:
                return
            # Do not start a fresh OBS segment when the native path is about
            # to finish.  This mirrors RE9's boundary-safe restart policy and
            # avoids a tiny tail segment containing only the final frame.
            if remaining_seconds is not None and remaining_seconds <= 2.0:
                return
            restart_obs_segment(camera_elapsed)

        def pose_logger() -> None:
            interval = 1.0 / self.pose_hz
            frame = 0
            deadline = time.monotonic()
            while not pose_stop.is_set():
                try:
                    current = self.bridge.read_pose()
                    row = {
                        "frame": frame,
                        "elapsed_sec": time.monotonic() - started_monotonic,
                        "captured_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                        **_pose_row(current),
                    }
                    with pose_lock:
                        observed.append(row)
                    frame += 1
                except Exception as exc:
                    if log_callback is not None:
                        log_callback(f"Pose 采样跳过 / Sample skipped: {exc}")
                deadline += interval
                pose_stop.wait(max(0.0, deadline - time.monotonic()))

        logger = threading.Thread(target=pose_logger, name="bmw-pose-log", daemon=True)
        self.stop_event.clear()
        self.active = True
        try:
            start_pose = self.bridge.read_pose()

            # Reaching the first keyframe can take several seconds because this
            # adapter only has feedback-controlled relative movement, not an
            # atomic absolute setPose. Keep that positioning move out of the
            # video and start OBS only after the camera has converged.
            first_point = trajectory.points[0]
            total = len(trajectory.points)
            if progress_callback is not None:
                progress_callback(0, total, f"录像前定位 / Pre-positioning {first_point.label}")
            pre_record_started = time.monotonic()
            position_tolerance = max(
                0.01, float(getattr(self.mover, "position_tolerance", 4.0))
            )
            angle_tolerance = max(
                0.01, float(getattr(self.mover, "angle_tolerance", 1.5))
            )
            fov_tolerance = max(
                0.01, float(getattr(self.mover, "fov_tolerance", 0.5))
            )
            for stabilization_attempts in range(1, 4):
                pre_record_pose = self.mover.move_to(
                    first_point.pose,
                    stop_requested=self.stop_event.is_set,
                    on_update=log_callback,
                )
                pre_record_pose = _wait_for_stable_pose(
                    self.bridge,
                    self.stop_event,
                    stable_seconds=self.pre_record_settle_seconds,
                    timeout_seconds=max(2.0, self.pre_record_settle_seconds + 1.5),
                )
                if _pose_within(
                    pre_record_pose,
                    first_point.pose,
                    position_tolerance=position_tolerance,
                    angle_tolerance=angle_tolerance,
                    fov_tolerance=fov_tolerance,
                ):
                    pre_record_stable = True
                    break
                if log_callback is not None:
                    log_callback(
                        "首点仍有残余运动，重新定位 / Residual motion detected; repositioning "
                        f"({stabilization_attempts}/3)"
                    )
            if not pre_record_stable:
                raise RuntimeError("首点稳定定位失败，OBS 未开始 / First point did not settle; OBS was not started")
            pre_record_finished = time.monotonic()

            if self.obs_restart_interval_seconds > 0.0 and self.obs_restart_factory is None:
                raise RuntimeError(
                    "轨迹采集未配置 OBS 重启回调。 / OBS restart callback is not configured."
                )
            start_obs_segment(0.0)
            logger.start()
            completed = 1
            if progress_callback is not None:
                progress_callback(1, total, f"已就位并开始录像 / Positioned; recording {first_point.label}")

            smooth_controller = self.bridge
            smooth_start = getattr(smooth_controller, "start_native_trajectory", None)
            smooth_read_status = getattr(smooth_controller, "read_trajectory_status", None)
            smooth_stop = getattr(smooth_controller, "stop_native_trajectory", None)
            can_smooth = all(callable(value) for value in (smooth_start, smooth_read_status, smooth_stop))

            if can_smooth and len(trajectory.points) >= 2:
                smooth_playback = True
                smooth_start_attempted = True
                smooth_status = smooth_start(
                    trajectory.points,
                    playback_hz=self.playback_hz,
                )
                smooth_playback_started = time.monotonic()
                append_timing(1, first_point, self.bridge.read_pose())
                smooth_last_segment = 0
                while True:
                    if self.stop_event.is_set():
                        stopped = True
                        smooth_stop()
                        smooth_start_attempted = False
                        break
                    smooth_status = smooth_read_status()
                    if smooth_status is None:
                        raise RuntimeError("平滑轨迹状态不可读 / Smooth trajectory status is unavailable")
                    if getattr(smooth_status, "failed", False):
                        raise RuntimeError(
                            f"平滑轨迹播放失败 / Smooth playback failed: {smooth_status.error_message}"
                        )
                    try:
                        last_camera_elapsed = float(
                            getattr(smooth_status, "elapsed_seconds", 0.0)
                        )
                    except (TypeError, ValueError):
                        last_camera_elapsed = None
                    segment = max(0, int(getattr(smooth_status, "current_segment", 0)))
                    while segment > smooth_last_segment and smooth_last_segment + 1 < total:
                        sequence = smooth_last_segment + 2
                        append_timing(sequence, trajectory.points[sequence - 1], self.bridge.read_pose())
                        completed = sequence
                        smooth_last_segment += 1
                        if progress_callback is not None:
                            progress_callback(
                                completed,
                                total,
                                f"连续播放 / Continuous · {trajectory.points[completed - 1].label}",
                            )
                    if getattr(smooth_status, "completed", False):
                        completed = total
                        smooth_playback_finished = time.monotonic()
                        while smooth_last_segment + 1 < total:
                            smooth_last_segment += 1
                            sequence = smooth_last_segment + 1
                            append_timing(sequence, trajectory.points[sequence - 1], self.bridge.read_pose())
                        smooth_start_attempted = False
                        break
                    if getattr(smooth_status, "stopped", False):
                        stopped = True
                        smooth_start_attempted = False
                        break
                    remaining_seconds = max(
                        0.0,
                        trajectory.points[-1].time_sec
                        - trajectory.points[0].time_sec
                        - (last_camera_elapsed or 0.0),
                    )
                    maybe_restart_obs(last_camera_elapsed, remaining_seconds)
                    time.sleep(0.005)
            else:
                # Compatibility path for an old bridge. The first keyframe is
                # already reached before recording, so it is deliberately not
                # issued a second time.
                for sequence, point in enumerate(trajectory.points[1:], start=2):
                    if self.stop_event.is_set():
                        stopped = True
                        break
                    if progress_callback is not None:
                        progress_callback(sequence - 1, total, f"前往 / Moving to {point.label}")
                    move_started = time.monotonic()
                    actual = self.mover.move_to(
                        point.pose,
                        stop_requested=self.stop_event.is_set,
                        on_update=log_callback,
                    )
                    move_finished = time.monotonic()
                    completed = sequence
                    if completed < total:
                        maybe_restart_obs(None, None)
                    timing.append(
                        {
                            "sequence": sequence,
                            "point_index": point.index,
                            "label": point.label,
                            "source_time_sec": point.time_sec,
                            "move_started_sec": move_started - started_monotonic,
                            "move_finished_sec": move_finished - started_monotonic,
                            "move_duration_sec": move_finished - move_started,
                            "actual_x": actual.x,
                            "actual_y": actual.y,
                            "actual_z": actual.z,
                            "actual_yaw_degrees": actual.yaw_degrees,
                            "actual_pitch_degrees": actual.pitch_degrees,
                            "actual_roll_degrees": actual.roll_degrees,
                            "actual_fov_degrees": actual.fov_degrees,
                        }
                    )
                    if progress_callback is not None:
                        progress_callback(sequence, total, f"已到达 / Reached {point.label}")
        except InterruptedError:
            stopped = True
            if not recording_started:
                error = "PreRecordStoppedError: 录像前首点定位被停止 / Pre-positioning stopped before OBS recording"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            pose_stop.set()
            if logger.is_alive():
                logger.join(timeout=2.0)
            if recording_started:
                if smooth_start_attempted and callable(smooth_stop):
                    try:
                        smooth_stop()
                    except Exception as exc:
                        if error is None:
                            error = f"TrajectoryStopError: {exc}"
                    smooth_start_attempted = False
                if obs is not None:
                    try:
                        finish_obs_segment("trajectory_finished", last_camera_elapsed)
                    except Exception as exc:
                        if error is None:
                            error = f"OBSStopError: {exc}"
            if audio_muted and obs is not None:
                restore_allowed = final_obs_stop_error is None
                if not restore_allowed:
                    try:
                        restore_allowed = not bool(obs.recording_status()["active"])
                    except Exception:
                        restore_allowed = False
                if restore_allowed:
                    try:
                        obs.restore_audio_inputs()
                        audio_muted = False
                    except Exception as exc:
                        if error is None:
                            error = f"AudioRestoreError: {exc}"
                elif error is None:
                    error = "AudioRestoreDeferred: OBS 仍在录像，音频保持静音 / OBS is still recording; audio remains muted"
            close_obs = getattr(obs, "close", None) if obs is not None else None
            if callable(close_obs):
                try:
                    close_obs()
                except Exception as exc:
                    if log_callback is not None:
                        log_callback(f"OBS WebSocket 关闭提示 / Close warning: {exc}")

            # Restore only if positioning failed before OBS began. Once a
            # trajectory has started, keep its terminal pose so a batch can
            # immediately position for the next trajectory.
            if (
                not recording_started
                and (error is not None or stopped)
                and start_pose is not None
            ):
                restore_attempted = True
                try:
                    restored_pose = self.mover.move_to(
                        start_pose,
                        stop_requested=lambda: False,
                        on_update=(
                            (lambda message: log_callback(f"回位 / Restore：{message}"))
                            if log_callback is not None
                            else None
                        ),
                    )
                    restore_succeeded = True
                except Exception as exc:
                    restore_succeeded = False
                    restore_error = f"{type(exc).__name__}: {exc}"
                    if log_callback is not None:
                        log_callback(f"相机自动回位失败 / Camera restore failed: {restore_error}")
            self.active = False

            with pose_lock:
                pose_rows = list(observed)
            if recording_started and not pose_rows and error is None:
                error = "PoseMissingError: 录像期间没有有效 Pose / No valid pose samples during recording"
            pose_fields = list(pose_rows[0].keys()) if pose_rows else ["frame", "elapsed_sec"]
            _write_csv(pose_csv, pose_rows, pose_fields)
            timing_fields = list(timing[0].keys()) if timing else ["sequence", "point_index", "label"]
            _write_csv(timing_csv, timing, timing_fields)
            if video_path is None or not video_path.is_file():
                video_path = _find_video(self.output_dir)
            if video_path is not None and video_path.is_file() and video_path not in video_paths:
                video_paths.append(video_path.resolve())
            if recording_started and video_path is None and error is None:
                error = "VideoMissingError: OBS 未返回视频 / OBS returned no video and no output file was found"
            status = "failed" if error else ("stopped" if stopped else "completed")
            manifest = {
                "format": "bmw-standalone-trajectory-capture-v4",
                "status": status,
                "error": error,
                "control_method": (
                    "standalone_in_process_absolute_hermite"
                    if smooth_playback
                    else "standalone_absolute_set_pose_feedback"
                ),
                "absolute_target_pose": True,
                "atomic_absolute_set_pose": True,
                "absolute_set_pose": True,
                "smooth_playback": smooth_playback,
                "native_controller_revision": (
                    "v9_standalone_input_hud_absolute_pose"
                    if smooth_playback
                    else None
                ),
                "bridge_metadata_version": METADATA_VERSION if smooth_playback else None,
                "standalone_smoothing_reset_before_playback": False,
                "terminal_pose_held": smooth_playback,
                "playback_hz": self.playback_hz if smooth_playback else None,
                "playback_duration_sec": (
                    smooth_playback_finished - smooth_playback_started
                    if smooth_playback_started is not None and smooth_playback_finished is not None
                    else None
                ),
                "trajectory_id": trajectory.trajectory_id,
                "trajectory_index": trajectory.index + 1,
                "source_path": str(source),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "output_dir": str(self.output_dir),
                "video_path": str(video_path) if video_path else None,
                "video_paths": [str(path) for path in video_paths],
                "video_segments": video_segments,
                "obs_restart_interval_seconds": self.obs_restart_interval_seconds,
                "obs_restart_enabled": self.obs_restart_interval_seconds > 0.0,
                "obs_restart_events": obs_restart_events,
                "source_keyframes_csv": str(source_csv),
                "playback_plan_csv": str(playback_csv),
                "observed_pose_csv": str(pose_csv),
                "trajectory_timing_csv": str(timing_csv),
                "requested_points": len(trajectory.points),
                "completed_points": completed,
                "pose_samples": len(pose_rows),
                "audio_capture": (
                    "disabled_pending_restore"
                    if audio_muted and recording_started
                    else "disabled"
                    if recording_started
                    else "not_started"
                ),
                "audio_muted_input_count": max(muted_counts, default=muted_count),
                "obs_stop_error": final_obs_stop_error,
                "start_pose": _pose_row(start_pose) if start_pose is not None else None,
                "pre_record_positioning": {
                    "performed": pre_record_started is not None,
                    "target_label": trajectory.points[0].label,
                    "duration_sec": (
                        pre_record_finished - pre_record_started
                        if pre_record_started is not None and pre_record_finished is not None
                        else None
                    ),
                    "settle_seconds": self.pre_record_settle_seconds,
                    "stable_pose_verified": pre_record_stable,
                    "stabilization_attempts": stabilization_attempts,
                    "actual_pose": (
                        _pose_row(pre_record_pose) if pre_record_pose is not None else None
                    ),
                    "included_in_video": False,
                },
                "restore_attempted": restore_attempted,
                "restore_policy": "pre_record_failures_only_keep_terminal_pose_after_recording",
                "restore_succeeded": restore_succeeded,
                "restored_pose": (
                    _pose_row(restored_pose) if restored_pose is not None else None
                ),
                "restore_error": restore_error,
                "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        if error:
            raise RuntimeError(error)
        assert video_path is not None
        return TrajectoryCaptureResult(
            output_dir=self.output_dir,
            manifest_path=manifest_path,
            video_path=video_path,
            completed_points=completed,
            requested_points=len(trajectory.points),
            stopped=stopped,
            video_paths=tuple(video_paths),
            obs_restart_count=len(obs_restart_events),
        )


class BatchTrajectoryRecorder:
    def __init__(
        self,
        *,
        bridge: PoseReader,
        mover_factory: Callable[[], PoseMover],
        obs_factory: Callable[[], RecordingOBS],
        scene_id: str,
        obs_restart_factory: Callable[[Path], RecordingOBS] | None = None,
        obs_restart_interval_seconds: float = 0.0,
        pose_hz: float = 30.0,
        playback_hz: float = 60.0,
    ) -> None:
        self.bridge = bridge
        self.mover_factory = mover_factory
        self.obs_factory = obs_factory
        self.scene_id = safe_id(scene_id)
        self.obs_restart_factory = obs_restart_factory
        self.obs_restart_interval_seconds = max(0.0, float(obs_restart_interval_seconds))
        self.pose_hz = pose_hz
        self.playback_hz = min(240.0, max(30.0, float(playback_hz)))
        self.stop_event = threading.Event()
        self.active = False
        self.current: TrajectoryRecorder | None = None
        self.batch_dir: Path | None = None

    def request_stop(self) -> None:
        self.stop_event.set()
        if self.current is not None:
            self.current.request_stop()

    def capture(
        self,
        trajectories: Sequence[ImportedTrajectory],
        *,
        source_path: str | Path,
        start_index: int = 0,
        batch_dir: str | Path | None = None,
        trajectory_indices: Sequence[int] | None = None,
        trajectory_callback: Callable[[int, int, ImportedTrajectory, str], None] | None = None,
        frame_callback: Callable[[int, int, ImportedTrajectory, int, int, str], None] | None = None,
        log_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        if self.active:
            raise RuntimeError("批量采集已经在运行 / Batch capture is already running")
        if not trajectories:
            raise ValueError("轨迹集为空 / Trajectory set is empty")
        source = Path(source_path).resolve()
        total = len(trajectories)
        indices = (
            sorted({int(value) for value in trajectory_indices})
            if trajectory_indices is not None
            else list(range(start_index, total))
        )
        if not indices or any(index < 0 or index >= total for index in indices):
            raise ValueError("批量索引超出范围 / Batch index is outside the trajectory set")

        resumed = batch_dir is not None
        if resumed:
            target = Path(batch_dir).resolve()
            manifest_path = target / "run_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            source_value = str(manifest.get("source_copy") or "")
            source_copy = Path(source_value) if source_value else target / f"trajectory_set_source{source.suffix.lower()}"
        else:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            target = (
                TRAJECTORY_CAPTURES_DIR
                / self.scene_id
                / f"run_{stamp}_batch_{indices[0] + 1:04d}_to_{indices[-1] + 1:04d}"
            ).resolve()
            target.mkdir(parents=True, exist_ok=False)
            source_copy = target / f"trajectory_set_source{source.suffix.lower()}"
            shutil.copy2(source, source_copy)
            manifest_path = target / "run_manifest.json"
            manifest = {
                "format": "bmw-uuu-trajectory-batch-v2",
                "scene_id": self.scene_id,
                "source_path": str(source),
                "source_copy": str(source_copy),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "output_dir": str(target),
                "total_trajectories": total,
                "planned_indices": [index + 1 for index in indices],
                "requested_trajectories": len(indices),
                "items": [],
            }
        self.batch_dir = target
        items = {
            int(item["index"]): dict(item)
            for item in manifest.get("items", [])
            if isinstance(item, dict) and str(item.get("index", "")).isdigit()
        }
        index_csv = target / "trajectory_index.csv"

        def write_state(status: str) -> None:
            ordered = sorted(items.values(), key=lambda item: int(item["index"]))
            manifest.update(
                {
                    "status": status,
                    "obs_restart_interval_seconds": self.obs_restart_interval_seconds,
                    "obs_restart_enabled": self.obs_restart_interval_seconds > 0.0,
                    "obs_restart_events": batch_obs_restart_events,
                    "items": ordered,
                    "completed_trajectories": sum(item.get("status") == "completed" for item in ordered),
                    "failed_trajectories": sum(item.get("status") == "failed" for item in ordered),
                    "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
            )
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            fields = ["index", "trajectory_id", "status", "output_dir", "video_path", "manifest_path", "error"]
            _write_csv(index_csv, ordered, fields)

        self.stop_event.clear()
        self.active = True
        completed_run = 0
        failed_run = 0
        batch_next_obs_restart_monotonic: float | None = None
        batch_obs_restart_events: list[dict[str, Any]] = []
        write_state("resuming" if resumed else "running")
        try:
            for index in indices:
                if self.stop_event.is_set():
                    break
                trajectory = trajectories[index]
                output_dir = target / f"traj_{index + 1:04d}"
                if trajectory_callback:
                    trajectory_callback(index, total, trajectory, "starting")
                if (
                    batch_next_obs_restart_monotonic is None
                    and self.obs_restart_interval_seconds > 0.0
                ):
                    batch_next_obs_restart_monotonic = (
                        time.monotonic() + self.obs_restart_interval_seconds
                    )
                obs_for_trajectory: RecordingOBS
                if (
                    index != indices[0]
                    and batch_next_obs_restart_monotonic is not None
                    and time.monotonic() >= batch_next_obs_restart_monotonic
                    and self.obs_restart_factory is not None
                    and not self.stop_event.is_set()
                ):
                    boundary_event: dict[str, Any] = {
                        "event_index": len(batch_obs_restart_events) + 1,
                        "scope": "batch_boundary",
                        "after_trajectory_index": index,
                        "requested_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "status": "restarting",
                    }
                    boundary_started = time.monotonic()
                    try:
                        if log_callback is not None:
                            log_callback("达到 OBS 重启间隔，将在下一条轨迹前重启 / Restarting OBS before the next trajectory")
                        obs_for_trajectory = self.obs_restart_factory(target)
                        batch_next_obs_restart_monotonic = (
                            time.monotonic() + self.obs_restart_interval_seconds
                        )
                        boundary_event.update(
                            {
                                "status": "completed",
                                "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                                "restart_duration_sec": time.monotonic() - boundary_started,
                            }
                        )
                    except Exception as exc:
                        boundary_event.update(
                            {
                                "status": "failed",
                                "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                                "restart_duration_sec": time.monotonic() - boundary_started,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                        batch_obs_restart_events.append(boundary_event)
                        failed_run += 1
                        items[index + 1] = {
                            "index": index + 1,
                            "trajectory_id": trajectory.trajectory_id,
                            "status": "failed",
                            "output_dir": str(output_dir),
                            "error": f"OBS batch-boundary restart failed: {exc}",
                        }
                        if trajectory_callback:
                            trajectory_callback(index, total, trajectory, "failed")
                        self.stop_event.set()
                        raise
                    batch_obs_restart_events.append(boundary_event)
                else:
                    obs_for_trajectory = self.obs_factory()
                recorder = TrajectoryRecorder(
                    bridge=self.bridge,
                    mover=self.mover_factory(),
                    obs=obs_for_trajectory,
                    output_dir=output_dir,
                    obs_restart_factory=self.obs_restart_factory,
                    obs_restart_interval_seconds=self.obs_restart_interval_seconds,
                    pose_hz=self.pose_hz,
                    playback_hz=self.playback_hz,
                )
                self.current = recorder
                try:
                    result = recorder.capture(
                        trajectory,
                        source_path=source,
                        progress_callback=(
                            lambda done, frame_total, message, index=index, trajectory=trajectory: frame_callback(
                                index, total, trajectory, done, frame_total, message
                            ) if frame_callback else None
                        ),
                        log_callback=log_callback,
                    )
                except Exception as exc:
                    failed_run += 1
                    items[index + 1] = {
                        "index": index + 1,
                        "trajectory_id": trajectory.trajectory_id,
                        "status": "failed",
                        "output_dir": str(output_dir),
                        "error": str(exc),
                    }
                    if trajectory_callback:
                        trajectory_callback(index, total, trajectory, "failed")
                else:
                    status = "stopped" if result.stopped else "completed"
                    completed_run += status == "completed"
                    items[index + 1] = {
                        "index": index + 1,
                        "trajectory_id": trajectory.trajectory_id,
                        "status": status,
                        "output_dir": str(output_dir),
                        "video_path": str(result.video_path),
                        "video_paths": [str(path) for path in result.video_paths],
                        "obs_restart_count": result.obs_restart_count,
                        "manifest_path": str(result.manifest_path),
                        "error": "",
                    }
                    if trajectory_callback:
                        trajectory_callback(index, total, trajectory, status)
                    if (
                        result.obs_restart_count > 0
                        and self.obs_restart_interval_seconds > 0.0
                    ):
                        batch_next_obs_restart_monotonic = (
                            time.monotonic() + self.obs_restart_interval_seconds
                        )
                finally:
                    self.current = None
                    write_state("running")
        finally:
            self.active = False
            write_state("stopped" if self.stop_event.is_set() else "completed")

        return {
            "output_dir": str(target),
            "batch_manifest_path": str(manifest_path),
            "trajectory_index_csv": str(index_csv),
            "requested_trajectories": len(indices),
            "completed_trajectories": completed_run,
            "failed_trajectories": failed_run,
            "stopped": self.stop_event.is_set(),
            "resumed": resumed,
        }
