from __future__ import annotations

import csv
import json
import math
import shutil
import time
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import AppConfig
from .lua_control import LuaControl, make_session_id
from .obs_control import OBSController, find_latest_video_file
from .paths import ensure_dir


@dataclass(frozen=True)
class ReplayKeyframe:
    step: int
    time_sec: float
    x: float
    y: float
    z: float
    yaw: float
    pitch: float
    fov: float | None
    score: float | None
    image_path: str


@dataclass(frozen=True)
class ReplayTrajectory:
    trajectory_id: str
    source_json: Path
    scene_id: str
    angle_unit: str
    keyframes: list[ReplayKeyframe]

    @property
    def duration_sec(self) -> float:
        if not self.keyframes:
            return 0.0
        return max(frame.time_sec for frame in self.keyframes)


def _wait_for_obs_record_active(controller: OBSController, timeout_sec: float = 8.0) -> bool:
    """Wait until OBS has actually entered recording state.

    ``StartRecord`` can return before the encoder/output is ready.  Starting
    the next trajectory immediately after a previous stop can otherwise leave
    us with no output file and a misleading ``StopRecord 501`` on teardown.
    """
    deadline = time.monotonic() + max(0.0, timeout_sec)
    while time.monotonic() < deadline:
        try:
            status = controller.get_record_status()
            if bool(getattr(status, "output_active", False)):
                return True
        except Exception:
            pass
        time.sleep(0.1)
    return False


def _wait_for_obs_record_idle(controller: OBSController, timeout_sec: float = 30.0) -> bool:
    """Wait until OBS finishes finalizing the current recording file."""
    deadline = time.monotonic() + max(0.0, timeout_sec)
    while time.monotonic() < deadline:
        try:
            status = controller.get_record_status()
            if not bool(getattr(status, "output_active", False)):
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def _stop_obs_recording_and_wait(controller: OBSController) -> str | None:
    """Stop OBS recording and wait for file finalization.

    OBS returns error 501 when a previous stop already completed while the
    WebSocket request was in flight.  In that case the output is still usable;
    the caller will locate the finalized file in the recording directory.
    """
    try:
        status = controller.get_record_status()
        if not bool(getattr(status, "output_active", False)):
            return None
    except Exception:
        # Preserve the original stop request as a best-effort fallback when
        # the status query itself is temporarily unavailable.
        pass

    output: str | None = None
    try:
        output = controller.stop_recording()
    except Exception:
        try:
            status = controller.get_record_status()
            if bool(getattr(status, "output_active", False)):
                time.sleep(0.5)
                output = controller.stop_recording()
            else:
                # StopRecord 501 means OBS is already idle.  Continue and let
                # find_latest_video_file locate a file if one was finalized.
                output = None
        except Exception:
            raise

    if not _wait_for_obs_record_idle(controller):
        raise RuntimeError("OBS did not finish finalizing the trajectory recording within 30s.")
    return output


def load_replay_trajectory(
    json_path: str | Path,
    trajectory_id: str | None = None,
    trajectory_index: int = 1,
    angle_unit: str = "auto",
    keyframe_interval_sec: float = 0.2,
    unwrap_yaw: bool = True,
    reverse: bool = False,
) -> ReplayTrajectory:
    """Load one trajectory from a trajectory JSON file."""
    source = Path(json_path)
    with source.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    trajectories = payload.get("trajectories")
    if isinstance(trajectories, list):
        if trajectory_id:
            selected = next((item for item in trajectories if str(item.get("trajectory_id")) == trajectory_id), None)
            if selected is None:
                raise ValueError(f"Trajectory id not found: {trajectory_id}")
        else:
            if trajectory_index <= 0 or trajectory_index > len(trajectories):
                raise ValueError(f"trajectory_index must be between 1 and {len(trajectories)}.")
            selected = trajectories[trajectory_index - 1]
    elif isinstance(payload.get("keyframes"), list):
        selected = payload
    else:
        raise ValueError("Trajectory JSON must contain either trajectories[] or keyframes[].")

    detected_unit = _detect_angle_unit(payload, selected, angle_unit)
    keyframes = _parse_keyframes(selected.get("keyframes") or [], keyframe_interval_sec)
    if reverse:
        keyframes = _reversed_keyframes(keyframes)
    if unwrap_yaw:
        keyframes = _with_unwrapped_yaw(keyframes, detected_unit)
    if detected_unit == "degrees":
        keyframes = _with_radian_angles(keyframes)

    return ReplayTrajectory(
        trajectory_id=str(selected.get("trajectory_id") or trajectory_id or f"trajectory_{trajectory_index:03d}"),
        source_json=source,
        scene_id=str(payload.get("scene_id") or selected.get("scene_id") or ""),
        angle_unit="radians",
        keyframes=keyframes,
    )


def replay_trajectory_to_obs(
    config: AppConfig,
    trajectory: ReplayTrajectory,
    obs_password: str = "",
    output_dir: str | Path | None = None,
    session_id: str | None = None,
    countdown_sec: float = 3.0,
    settle_sec: float = 1.0,
    post_roll_sec: float = 1.0,
    speed: float = 1.0,
    duration_sec: float | None = None,
    record: bool = True,
    write_pose_log: bool = True,
    smooth_playback: bool = True,
    playback_hz: float = 60.0,
) -> dict[str, Path | str]:
    """Replay a loaded trajectory through Lua set_pose controls and optionally record OBS."""
    if len(trajectory.keyframes) < 2:
        raise ValueError("A replay trajectory needs at least two keyframes.")
    if speed <= 0:
        raise ValueError("speed must be positive.")
    if countdown_sec < 0 or settle_sec < 0 or post_roll_sec < 0:
        raise ValueError("countdown/settle/post-roll values must be non-negative.")
    if playback_hz <= 0:
        raise ValueError("playback_hz must be positive.")

    session_base = session_id or f"replay_{make_session_id()}_{_safe_name(trajectory.trajectory_id)}"
    # Every replay invocation, including GUI retries, gets a fresh Lua
    # session.  The Lua logger historically ignored start when a stopped
    # session reused the same name, which made all later retries fail.
    session = f"{session_base}_attempt_{time.time_ns()}"
    base_output = ensure_dir(output_dir or (Path("data/videos/trajectories") / session))
    pose_log = config.pose_log_file.with_name(f"{config.pose_log_file.stem}_{session}.csv")
    metadata_csv = base_output / "replay_keyframes.csv"
    metadata_json = base_output / "replay_result.json"

    control = LuaControl(config)
    controller: OBSController | None = None
    recording_started = False
    recording_stopped = False
    record_dir = base_output / "raw"
    video_path: Path | None = None
    started_at = time.time()

    scaled = _scaled_keyframes(trajectory.keyframes, speed=speed, duration_sec=duration_sec)
    playback_frames = _smooth_resample_keyframes(scaled, sample_rate_hz=playback_hz) if smooth_playback else scaled
    _write_keyframe_csv(metadata_csv, trajectory, scaled)

    try:
        _prepare_lua_replay(
            control,
            session,
            trajectory.trajectory_id,
            scaled[0],
            pose_log,
            float(config.raw["lua_logger"]["default_interval_sec"]),
            settle_sec,
            write_pose_log,
        )

        if countdown_sec > 0:
            for remaining in range(int(math.ceil(countdown_sec)), 0, -1):
                print(f"Starting replay in {remaining}...")
                time.sleep(1.0)

        if record:
            obs_cfg = config.raw["obs"]
            controller = OBSController(obs_cfg["host"], int(obs_cfg["port"]), obs_password or obs_cfg.get("password", ""))
            try:
                controller.set_record_directory(record_dir)
            except Exception as exc:
                raise RuntimeError(f"OBS could not set recording directory {record_dir}: {exc}") from exc
            started_at = time.time()
            controller.start_recording()
            recording_started = True
            if not _wait_for_obs_record_active(controller):
                raise RuntimeError("OBS did not enter recording state after StartRecord.")
            time.sleep(0.25)

        # A unique acknowledgement id prevents a retry from accepting stale
        # trajectory status left by an earlier attempt of the same index.
        playback_id = f"{trajectory.trajectory_id}_play_{time.time_ns()}"
        _run_lua_trajectory(control, session, playback_id, playback_frames)

        if post_roll_sec > 0:
            time.sleep(post_roll_sec)

        if controller is not None:
            output = _stop_obs_recording_and_wait(controller)
            recording_started = False
            recording_stopped = True
            if output:
                video_path = Path(output)
            if video_path is None or not video_path.exists():
                video_path = find_latest_video_file(record_dir, before_time=started_at, supported_extensions=config.supported_video_extensions)
    finally:
        try:
            # If Lua or OBS fails after StartRecord, close the recording before
            # the caller retries. Otherwise OBS stays active and rejects the
            # next SetRecordDirectory/StartRecord request with code 500.
            if controller is not None and recording_started and not recording_stopped:
                try:
                    _stop_obs_recording_and_wait(controller)
                except Exception:
                    # Keep the original replay exception; the GUI retry path
                    # can restart OBS if its state is still unhealthy.
                    pass
            if not write_pose_log:
                control.write_clear_pose_control(session)
        finally:
            if write_pose_log:
                # Stop whichever logger session is actually active.  If a new
                # start command was missed, Lua can still be logging the
                # previous trajectory, so stopping only ``session`` leaks a
                # growing pose log forever.  Leave the final stop command in
                # place when acknowledgement fails instead of overwriting it
                # immediately with clear_pose.
                _stop_active_lua_logging(control, fallback_session=session)

    pose_copy: Path | None = None
    if pose_log.exists():
        pose_copy = base_output / "pose_log.csv"
        shutil.copy2(pose_log, pose_copy)

    result: dict[str, Path | str] = {
        "session_id": session,
        "output_dir": base_output,
        "metadata_csv": metadata_csv,
        "metadata_json": metadata_json,
        "source_json": trajectory.source_json,
    }
    if video_path is not None:
        result["video_path"] = video_path
    if pose_copy is not None:
        result["pose_log"] = pose_copy

    metadata_json.write_text(
        json.dumps(
            {
                "session_id": session,
                "trajectory_id": trajectory.trajectory_id,
                "scene_id": trajectory.scene_id,
                "source_json": str(trajectory.source_json),
                "video_path": str(video_path) if video_path else "",
                "pose_log": str(pose_copy) if pose_copy else "",
                "metadata_csv": str(metadata_csv),
                "duration_sec": scaled[-1].time_sec if scaled else 0.0,
                "replay_mode": "lua_play_trajectory",
                "source_keyframe_count": len(scaled),
                "playback_frame_count": len(playback_frames),
                "smooth_playback": smooth_playback,
                "playback_hz": playback_hz,
                "recorded": bool(record),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return result


def _parse_keyframes(raw_keyframes: list[dict[str, Any]], keyframe_interval_sec: float) -> list[ReplayKeyframe]:
    if not raw_keyframes:
        raise ValueError("Trajectory has no keyframes.")
    frames: list[ReplayKeyframe] = []
    for index, item in enumerate(raw_keyframes):
        time_sec = _optional_float(item.get("time_sec"))
        if time_sec is None:
            time_sec = index * keyframe_interval_sec
        frames.append(
            ReplayKeyframe(
                step=int(item.get("step", index)),
                time_sec=float(time_sec),
                x=_required_float(item, "x"),
                y=_required_float(item, "y"),
                z=_required_float(item, "z"),
                yaw=_required_float(item, "yaw"),
                pitch=_required_float(item, "pitch"),
                fov=_optional_float(item.get("fov")),
                score=_optional_float(item.get("score")),
                image_path=str(item.get("image_path") or ""),
            )
        )
    frames.sort(key=lambda frame: (frame.time_sec, frame.step))
    return _ensure_strictly_increasing_time(frames, keyframe_interval_sec)


def _run_keyframe_segments(control: LuaControl, session: str, trajectory_id: str, frames: list[ReplayKeyframe]) -> None:
    for index in range(len(frames) - 1):
        start = frames[index]
        end = frames[index + 1]
        duration = max(0.001, end.time_sec - start.time_sec)
        segment_id = f"{trajectory_id}_k{index:04d}_{index + 1:04d}"
        control.write_set_pose_control(
            session,
            start.x,
            start.y,
            start.z,
            start.yaw,
            start.pitch,
            fov=start.fov,
            segment_id=segment_id,
            x_end=end.x,
            y_end=end.y,
            z_end=end.z,
            yaw_end=end.yaw,
            pitch_end=end.pitch,
            fov_end=end.fov,
            duration_sec=duration,
        )
        time.sleep(duration)
    _send_static_pose(control, session, frames[-1], segment_id=f"{trajectory_id}_final")


def _run_lua_trajectory(
    control: LuaControl,
    session: str,
    trajectory_id: str,
    frames: list[ReplayKeyframe],
    attempts: int = 3,
) -> None:
    """Start playback with an exact acknowledgement and retry lost file commands."""
    last_error: RuntimeError | None = None
    keyframes = _lua_keyframes(frames)
    for attempt in range(1, max(1, int(attempts)) + 1):
        attempt_id = f"{trajectory_id}_attempt_{attempt}_{time.time_ns()}"
        control.write_play_trajectory_control(session, keyframes, trajectory_id=attempt_id)
        command_id = control.last_written_command_id
        try:
            _wait_for_lua_trajectory(control, session, attempt_id, command_id=command_id)
        except RuntimeError as exc:
            last_error = exc
            continue
        _raise_if_lua_rejected_pose(control)
        time.sleep((frames[-1].time_sec if frames else 0.0) + 0.1)
        return
    raise RuntimeError(
        f"Lua did not acknowledge play_trajectory after {max(1, int(attempts))} attempts. "
        f"Last error: {last_error}"
    )


def _prepare_lua_replay(
    control: LuaControl,
    session: str,
    trajectory_id: str,
    first_frame: ReplayKeyframe,
    pose_log: Path,
    interval_sec: float,
    settle_sec: float,
    write_pose_log: bool,
) -> None:
    """Require current Lua acknowledgements before OBS starts recording."""
    if write_pose_log:
        _start_lua_logging(control, session, pose_log, interval_sec)

    prepare_segment_id = f"{trajectory_id}_prepare_{time.time_ns()}"
    prepare_command_id = _send_static_pose(control, session, first_frame, segment_id=prepare_segment_id)
    if not control.wait_until_scan_pose(
        prepare_segment_id,
        timeout_sec=max(3.0, float(settle_sec) + 2.0),
        poll_interval_sec=0.05,
        stable_polls=2,
        command_id=prepare_command_id,
    ):
        _raise_if_lua_rejected_pose(control)
        status = control.read_status() or {}
        raise RuntimeError(
            f"Lua did not acknowledge the prepare pose for {trajectory_id}; OBS was not started. "
            f"Last status: {status}"
        )
    _raise_if_lua_rejected_pose(control)
    if settle_sec > 0:
        time.sleep(settle_sec)


def _start_lua_logging(
    control: LuaControl,
    session: str,
    pose_log: Path,
    interval_sec: float,
    attempts: int = 3,
    timeout_sec: float = 8.0,
) -> None:
    """Stop stale logging, then retry start without outrunning a stalled game.

    REFramework normally consumes the file command quickly, but the game can
    pause its Lua update loop for several seconds during an OBS restart or a
    loading hitch. Rewriting the control file every two or three seconds can
    overwrite every ``start`` before Lua sees one. Leave each command in place
    long enough for the game to recover, and explicitly stop an older session
    before starting this attempt.
    """
    status = control.read_status() or {}
    active_session = str(status.get("session_id") or "")
    if (
        status.get("logging") is True
        and active_session
        and active_session != session
        and not _stop_active_lua_logging(
            control,
            fallback_session=active_session,
            attempts=1,
            timeout_sec=timeout_sec,
        )
    ):
        raise RuntimeError(
            f"Lua did not stop the previous pose logging session {active_session}; "
            "OBS was not started."
        )

    for _ in range(max(1, int(attempts))):
        control.write_start_control(session, pose_log, interval_sec)
        command_id = control.last_written_command_id
        if control.wait_until_lua_logging_started(
            session,
            timeout_sec=timeout_sec,
            command_id=command_id,
        ):
            return
    status = control.read_status() or {}
    raise RuntimeError(
        f"Lua did not acknowledge pose logging start for {session}; OBS was not started. "
        f"Last status: {status}"
    )


def _stop_active_lua_logging(
    control: LuaControl,
    fallback_session: str,
    attempts: int = 2,
    timeout_sec: float = 8.0,
) -> bool:
    """Stop the active Lua session without rapidly overwriting a pending stop."""
    status = control.read_status() or {}
    if status and status.get("logging") is False:
        try:
            control.write_clear_pose_control(fallback_session)
        except OSError:
            pass
        return True

    active_session = str(status.get("session_id") or fallback_session)
    for _ in range(max(1, int(attempts))):
        try:
            control.write_stop_control(active_session)
        except OSError:
            continue
        command_id = control.last_written_command_id
        if control.wait_until_lua_logging_stopped(
            active_session,
            timeout_sec=timeout_sec,
            command_id=command_id,
        ):
            return True
        latest = control.read_status() or {}
        if latest.get("logging") is False:
            return True
        active_session = str(latest.get("session_id") or active_session)
    return False


def _send_static_pose(control: LuaControl, session: str, frame: ReplayKeyframe, segment_id: str) -> str:
    control.write_set_pose_control(
        session,
        frame.x,
        frame.y,
        frame.z,
        frame.yaw,
        frame.pitch,
        fov=frame.fov,
        segment_id=segment_id,
        x_end=frame.x,
        y_end=frame.y,
        z_end=frame.z,
        yaw_end=frame.yaw,
        pitch_end=frame.pitch,
        fov_end=frame.fov,
        duration_sec=0.0,
    )
    return control.last_written_command_id


def _lua_keyframes(frames: list[ReplayKeyframe]) -> list[dict[str, float | int | None]]:
    return [
        {
            "step": frame.step,
            "time_sec": frame.time_sec,
            "x": frame.x,
            "y": frame.y,
            "z": frame.z,
            "yaw": frame.yaw,
            "pitch": frame.pitch,
            "fov": frame.fov,
        }
        for frame in frames
    ]


def _wait_for_lua_trajectory(
    control: LuaControl,
    session: str,
    trajectory_id: str,
    timeout_sec: float = 2.0,
    command_id: str = "",
) -> None:
    deadline = time.monotonic() + timeout_sec
    last_status: dict[str, Any] = {}
    while time.monotonic() < deadline:
        status = control.read_status() or {}
        last_status = status
        if (
            str(status.get("session_id") or "") == session
            and str(status.get("trajectory_id") or "") == trajectory_id
            and int(status.get("trajectory_frame_count") or 0) > 1
            and control._status_matches_command(status, command_id)
        ):
            return
        error = str(status.get("last_error") or "")
        if "Enable FreeCam" in error or "play_trajectory requires" in error:
            return
        time.sleep(0.1)
    raise RuntimeError(
        "Lua did not acknowledge play_trajectory. Reload scripts in REFramework or restart the game so the smooth trajectory patch is active. "
        f"Last status: {last_status}"
    )


def _raise_if_lua_rejected_pose(control: LuaControl) -> None:
    status = control.read_status() or {}
    error = str(status.get("last_error") or "")
    if "Enable FreeCam" in error:
        raise RuntimeError("Lua rejected set_pose. Enable FreeCam in-game first, then run replay again.")
    if "play_trajectory requires" in error:
        raise RuntimeError("Lua rejected play_trajectory. Re-run patch-lua-logger so RE9FreeCam.lua has the smooth trajectory patch.")


def _detect_angle_unit(payload: dict[str, Any], selected: dict[str, Any], requested: str) -> str:
    if requested not in {"auto", "degrees", "radians"}:
        raise ValueError("angle_unit must be auto, degrees, or radians.")
    if requested != "auto":
        return requested
    coordinate_system = payload.get("coordinate_system") if isinstance(payload.get("coordinate_system"), dict) else {}
    yaw_unit = str(coordinate_system.get("yaw_unit") or coordinate_system.get("angle_unit") or "").lower()
    pitch_unit = str(coordinate_system.get("pitch_unit") or "").lower()
    if "degree" in yaw_unit or "degree" in pitch_unit:
        return "degrees"
    if "radian" in yaw_unit or "radian" in pitch_unit:
        return "radians"
    keyframes = selected.get("keyframes") or []
    values = [abs(_optional_float(item.get("yaw")) or 0.0) for item in keyframes]
    values += [abs(_optional_float(item.get("pitch")) or 0.0) for item in keyframes]
    return "degrees" if values and max(values) > (2.0 * math.pi + 0.5) else "radians"


def _with_unwrapped_yaw(frames: list[ReplayKeyframe], angle_unit: str) -> list[ReplayKeyframe]:
    if len(frames) < 2:
        return frames
    period = 360.0 if angle_unit == "degrees" else 2.0 * math.pi
    half = period / 2.0
    unwrapped = [frames[0].yaw]
    for frame in frames[1:]:
        previous = unwrapped[-1]
        delta = ((frame.yaw - previous + half) % period) - half
        unwrapped.append(previous + delta)
    return [
        ReplayKeyframe(frame.step, frame.time_sec, frame.x, frame.y, frame.z, yaw, frame.pitch, frame.fov, frame.score, frame.image_path)
        for frame, yaw in zip(frames, unwrapped)
    ]


def _with_radian_angles(frames: list[ReplayKeyframe]) -> list[ReplayKeyframe]:
    return [
        ReplayKeyframe(
            frame.step,
            frame.time_sec,
            frame.x,
            frame.y,
            frame.z,
            math.radians(frame.yaw),
            math.radians(frame.pitch),
            frame.fov,
            frame.score,
            frame.image_path,
        )
        for frame in frames
    ]


def _scaled_keyframes(frames: list[ReplayKeyframe], speed: float, duration_sec: float | None) -> list[ReplayKeyframe]:
    original_duration = max(0.001, frames[-1].time_sec - frames[0].time_sec)
    if duration_sec is not None:
        scale = duration_sec / original_duration
    else:
        scale = 1.0 / speed
    start_time = frames[0].time_sec
    return [
        ReplayKeyframe(
            frame.step,
            (frame.time_sec - start_time) * scale,
            frame.x,
            frame.y,
            frame.z,
            frame.yaw,
            frame.pitch,
            frame.fov,
            frame.score,
            frame.image_path,
        )
        for frame in frames
    ]


def _smooth_resample_keyframes(
    frames: list[ReplayKeyframe],
    sample_rate_hz: float = 60.0,
    position_tangent_scale: float = 0.45,
) -> list[ReplayKeyframe]:
    """Create dense, C1-continuous playback poses while preserving every source pose."""
    if len(frames) < 2:
        return frames
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive.")

    times = [frame.time_sec for frame in frames]
    duration = times[-1]
    if duration <= 0:
        return frames

    position_values = {
        "x": [frame.x for frame in frames],
        "y": [frame.y for frame in frames],
        "z": [frame.z for frame in frames],
    }
    position_tangents = {
        key: _path_tangents(values, times, scale=position_tangent_scale)
        for key, values in position_values.items()
    }
    yaw_values = [frame.yaw for frame in frames]
    pitch_values = [frame.pitch for frame in frames]
    yaw_tangents = _monotone_tangents(yaw_values, times)
    pitch_tangents = _monotone_tangents(pitch_values, times)

    interval = 1.0 / sample_rate_hz
    sample_count = max(1, int(math.ceil(duration * sample_rate_hz)))
    sample_times = [min(duration, index * interval) for index in range(sample_count + 1)]
    for source_time in times:
        sample_times = [
            value for value in sample_times
            if value == source_time or abs(value - source_time) >= interval * 0.4
        ]
        sample_times.append(source_time)
    ordered_times = sorted(set(sample_times))
    source_by_time = {frame.time_sec: frame for frame in frames}

    dense: list[ReplayKeyframe] = []
    for step, sample_time in enumerate(ordered_times):
        source = source_by_time.get(sample_time)
        segment = min(len(frames) - 2, max(0, bisect_right(times, sample_time) - 1))
        start_time = times[segment]
        end_time = times[segment + 1]
        span = max(1e-9, end_time - start_time)
        u = min(1.0, max(0.0, (sample_time - start_time) / span))

        x = _hermite_value(position_values["x"], position_tangents["x"], segment, span, u)
        y = _hermite_value(position_values["y"], position_tangents["y"], segment, span, u)
        z = _hermite_value(position_values["z"], position_tangents["z"], segment, span, u)
        yaw = _hermite_value(yaw_values, yaw_tangents, segment, span, u)
        pitch = _hermite_value(pitch_values, pitch_tangents, segment, span, u)
        fov = _interpolate_optional(frames[segment].fov, frames[segment + 1].fov, u)

        if source is not None:
            x, y, z = source.x, source.y, source.z
            yaw, pitch, fov = source.yaw, source.pitch, source.fov

        dense.append(
            ReplayKeyframe(
                step=step,
                time_sec=sample_time,
                x=x,
                y=y,
                z=z,
                yaw=yaw,
                pitch=pitch,
                fov=fov,
                score=source.score if source is not None else None,
                image_path=source.image_path if source is not None else "",
            )
        )
    return dense


def _path_tangents(values: list[float], times: list[float], scale: float) -> list[float]:
    """Estimate damped path derivatives; endpoints stop gently before recording roll-off."""
    if len(values) != len(times):
        raise ValueError("values and times must have equal length.")
    if len(values) < 2:
        return [0.0] * len(values)
    tangents = [0.0] * len(values)
    for index in range(1, len(values) - 1):
        span = max(1e-9, times[index + 1] - times[index - 1])
        tangents[index] = scale * (values[index + 1] - values[index - 1]) / span
    return tangents


def _monotone_tangents(values: list[float], times: list[float]) -> list[float]:
    """Compute shape-preserving angle derivatives without overshoot or direction wobble."""
    if len(values) != len(times):
        raise ValueError("values and times must have equal length.")
    if len(values) < 2:
        return [0.0] * len(values)

    slopes = [
        (values[index + 1] - values[index]) / max(1e-9, times[index + 1] - times[index])
        for index in range(len(values) - 1)
    ]
    tangents = [0.0] * len(values)
    for index in range(1, len(values) - 1):
        left = slopes[index - 1]
        right = slopes[index]
        if left == 0.0 or right == 0.0 or left * right <= 0.0:
            tangents[index] = 0.0
            continue
        left_span = times[index] - times[index - 1]
        right_span = times[index + 1] - times[index]
        weight_left = 2.0 * right_span + left_span
        weight_right = right_span + 2.0 * left_span
        tangents[index] = (weight_left + weight_right) / (
            weight_left / left + weight_right / right
        )
    return tangents


def _hermite_value(
    values: list[float],
    tangents: list[float],
    segment: int,
    span: float,
    u: float,
) -> float:
    u2 = u * u
    u3 = u2 * u
    return (
        (2.0 * u3 - 3.0 * u2 + 1.0) * values[segment]
        + (u3 - 2.0 * u2 + u) * span * tangents[segment]
        + (-2.0 * u3 + 3.0 * u2) * values[segment + 1]
        + (u3 - u2) * span * tangents[segment + 1]
    )


def _interpolate_optional(start: float | None, end: float | None, u: float) -> float | None:
    if start is None and end is None:
        return None
    if start is None:
        return end
    if end is None:
        return start
    eased = u * u * (3.0 - 2.0 * u)
    return start + (end - start) * eased


def _ensure_strictly_increasing_time(frames: list[ReplayKeyframe], interval: float) -> list[ReplayKeyframe]:
    fixed: list[ReplayKeyframe] = []
    previous = -float("inf")
    for index, frame in enumerate(frames):
        time_sec = frame.time_sec
        if time_sec <= previous:
            time_sec = previous + max(0.001, interval)
        fixed.append(
            ReplayKeyframe(frame.step, time_sec, frame.x, frame.y, frame.z, frame.yaw, frame.pitch, frame.fov, frame.score, frame.image_path)
        )
        previous = time_sec
    return fixed


def _reversed_keyframes(frames: list[ReplayKeyframe]) -> list[ReplayKeyframe]:
    if len(frames) < 2:
        return frames
    ordered = list(reversed(frames))
    original_times = [frame.time_sec for frame in frames]
    original_duration = max(original_times) - min(original_times)
    reversed_frames: list[ReplayKeyframe] = []
    for index, frame in enumerate(ordered):
        # Preserve original segment durations while resetting the reversed path to t=0.
        time_sec = original_duration - (frame.time_sec - min(original_times))
        reversed_frames.append(
            ReplayKeyframe(
                step=index,
                time_sec=time_sec,
                x=frame.x,
                y=frame.y,
                z=frame.z,
                yaw=frame.yaw,
                pitch=frame.pitch,
                fov=frame.fov,
                score=frame.score,
                image_path=frame.image_path,
            )
        )
    reversed_frames.sort(key=lambda frame: frame.time_sec)
    return [
        ReplayKeyframe(index, frame.time_sec, frame.x, frame.y, frame.z, frame.yaw, frame.pitch, frame.fov, frame.score, frame.image_path)
        for index, frame in enumerate(reversed_frames)
    ]


def _write_keyframe_csv(path: Path, trajectory: ReplayTrajectory, frames: list[ReplayKeyframe]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "trajectory_id",
                "scene_id",
                "step",
                "time_sec",
                "x",
                "y",
                "z",
                "yaw_rad",
                "pitch_rad",
                "fov",
                "score",
                "image_path",
            ],
        )
        writer.writeheader()
        for frame in frames:
            writer.writerow(
                {
                    "trajectory_id": trajectory.trajectory_id,
                    "scene_id": trajectory.scene_id,
                    "step": frame.step,
                    "time_sec": frame.time_sec,
                    "x": frame.x,
                    "y": frame.y,
                    "z": frame.z,
                    "yaw_rad": frame.yaw,
                    "pitch_rad": frame.pitch,
                    "fov": "" if frame.fov is None else frame.fov,
                    "score": "" if frame.score is None else frame.score,
                    "image_path": frame.image_path,
                }
            )


def _required_float(item: dict[str, Any], key: str) -> float:
    value = _optional_float(item.get(key))
    if value is None:
        raise ValueError(f"Keyframe is missing numeric field: {key}")
    return value


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_name(value: str) -> str:
    cleaned = []
    for char in value:
        cleaned.append(char if char.isalnum() or char in "._-" else "_")
    return "".join(cleaned).strip("._") or "trajectory"

