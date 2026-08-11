from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable, Iterable

from .igcs_client import IGCSClientManager
from .models import Pose, TrajectoryKeyframe
from .paths import (
    CAMERA_TOOLS_DIR,
    POSE_CONFIG_PATH,
    POSE_LOGS_DIR,
    PROJECT_ROOT,
    ensure_data_dirs,
)


if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import kcd2_pose_control as engine


class CameraBackend:
    """Thin application-facing adapter over the verified low-level bridge."""

    def __init__(
        self,
        client_manager: IGCSClientManager | None = None,
    ) -> None:
        self.client_manager = client_manager or IGCSClientManager()

    def status(self) -> dict[str, Any]:
        result = engine.status()
        result["igcs_client"] = self.client_manager.status()
        result["igcs_pipe_error"] = self._latest_log_has_pipe_error()
        return result

    def inject(
        self,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        report = progress or (lambda _: None)
        report("正在检查 KCD2 游戏进程…")
        try:
            game_pid = engine.find_process_id()
        except RuntimeError as exc:
            raise RuntimeError(
                "没有检测到 KCD2。请先启动游戏并进入主菜单或存档，"
                "再点击一键准备并注入。"
            ) from exc

        try:
            loaded_module = engine.find_module(game_pid)
        except RuntimeError:
            loaded_module = None

        if loaded_module is not None:
            state = self.client_manager.status()
            if self._latest_log_has_pipe_error():
                raise RuntimeError(
                    "当前游戏进程里的相机 DLL 已在 IGCS Client 启动前加载，"
                    "而且不会自动重连。请退出并重新启动 KCD2；重启后只需"
                    "点击一次“一键准备并注入”。"
                )
            if not (
                state["dll_to_client_pipe"]
                and state["client_to_dll_pipe"]
            ):
                raise RuntimeError(
                    "相机 DLL 已加载，但没有检测到完整的 IGCS 双向管道。"
                    "为避免错误控制，请重启 KCD2 后重新执行一键注入。"
                )
            report("相机 DLL 与 IGCS 双向管道已经就绪")
            return {
                "pid": game_pid,
                "already_loaded": True,
                "module": loaded_module,
                "igcs_client": state,
                "pipe_verified": True,
            }

        report("正在准备 IGCS Client（必须先于 DLL）…")
        client_state = self.client_manager.ensure_server_ready(
            progress=report,
        )
        report("Client 管道已就绪，正在注入已校验的相机 DLL…")
        result = engine.inject_camera_dll()
        pipe_state = self.client_manager.wait_for_bidirectional_pipes(
            progress=report,
        )
        if self._latest_log_has_pipe_error():
            raise RuntimeError(
                "DLL 日志仍报告无法连接 IGCS Client。请退出 KCD2 后重试；"
                "采集程序不会在这个错误会话中继续控制相机。"
            )
        report("注入成功，IGCS 双向管道验证通过")
        result["igcs_client"] = client_state
        result["igcs_pipes"] = pipe_state
        result["pipe_verified"] = True
        return result

    def pose(self) -> Pose:
        return Pose.from_mapping(engine.read_pose(POSE_CONFIG_PATH))

    def send_action(self, action: str, duration_ms: int = 120) -> None:
        engine.perform_action(action, duration_ms)

    def run_random_trajectory(
        self,
        *,
        duration: float,
        hz: float,
        seed: int | None,
        xy_scale: float,
    ) -> dict[str, Any]:
        return engine.random_trajectory(
            POSE_CONFIG_PATH,
            duration=duration,
            hz=hz,
            seed=seed,
            xy_scale=xy_scale,
        )

    def run_imported_trajectory(
        self,
        frames: Iterable[TrajectoryKeyframe],
        *,
        timing_csv_path: Path,
        stop_requested: Callable[[], bool] | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        return engine.play_absolute_trajectory(
            POSE_CONFIG_PATH,
            (frame.as_dict() for frame in frames),
            timing_csv_path=timing_csv_path,
            stop_requested=stop_requested,
            progress_callback=progress_callback,
        )

    def restore_export_session(self) -> dict[str, Any]:
        return engine.end_export_session(POSE_CONFIG_PATH)

    def visible_move_test(
        self,
        *,
        right: float = 0.0,
        up: float = 0.0,
        panorama_degrees: float = 0.0,
        hold_seconds: float = 2.0,
    ) -> dict[str, Any]:
        return engine.exported_camera_test(
            POSE_CONFIG_PATH,
            step_left_right=right,
            step_up_down=up,
            fov_degrees=0.0,
            panorama_degrees=panorama_degrees,
            hold_seconds=hold_seconds,
        )

    def start_export_pose(
        self,
        *,
        x: float,
        y: float,
        z: float,
        yaw_degrees: float,
        pitch_degrees: float = 0.0,
        roll_degrees: float = 0.0,
        fov_degrees: float,
    ) -> dict[str, Any]:
        if engine.latest_camera_enabled_state() is not True:
            raise RuntimeError(
                "KCD2 free camera is disabled. Enable CameraTools with Insert "
                "before starting an automatic scan."
            )
        return engine.exported_camera_goto(
            POSE_CONFIG_PATH,
            target_x=x,
            target_y=y,
            target_z=z,
            target_yaw_degrees=yaw_degrees,
            target_pitch_degrees=pitch_degrees,
            target_roll_degrees=roll_degrees,
            target_fov_degrees=fov_degrees,
            write_report=False,
        )

    def adjust_active_export_pose(
        self,
        *,
        x: float,
        y: float,
        z: float,
        yaw_degrees: float,
        pitch_degrees: float = 0.0,
        roll_degrees: float = 0.0,
        fov_degrees: float,
    ) -> dict[str, Any]:
        return engine.adjust_active_export_to(
            POSE_CONFIG_PATH,
            target_x=x,
            target_y=y,
            target_z=z,
            target_yaw_degrees=yaw_degrees,
            target_pitch_degrees=pitch_degrees,
            target_roll_degrees=roll_degrees,
            target_fov_degrees=fov_degrees,
        )

    @staticmethod
    def _latest_log_has_pipe_error() -> bool:
        log_path = CAMERA_TOOLS_DIR / "KCD2CameraTools.dll.log"
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return "Couldn't connect to named pipe DLL -> Client" in text


class LivePoseRecorder:
    """Continuous pose logger analogous to the RE9 Lua logger."""

    FIELDNAMES = [
        "session_id",
        "frame_id",
        "timestamp_sec",
        "wall_time",
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

    def __init__(self, backend: CameraBackend) -> None:
        self.backend = backend
        self.stop_event = threading.Event()
        self.ready_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.output_path: Path | None = None
        self.last_error: Exception | None = None
        self.frame_count = 0
        self.started_perf_counter_ns: int | None = None
        self.started_wall_time: str | None = None

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(
        self,
        *,
        session_id: str,
        hz: float = 30.0,
        on_sample: Callable[[Pose, int], None] | None = None,
    ) -> Path:
        if self.running:
            raise RuntimeError("Pose recorder is already running")
        if not 1.0 <= hz <= 120.0:
            raise ValueError("Pose logger Hz must be between 1 and 120")
        ensure_data_dirs()
        safe_session = "".join(
            char if char.isalnum() or char in "._-" else "_"
            for char in session_id
        ).strip("._") or "session"
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.output_path = POSE_LOGS_DIR / f"{stamp}_{safe_session}_pose.csv"
        self.stop_event.clear()
        self.ready_event.clear()
        self.last_error = None
        self.frame_count = 0
        self.started_perf_counter_ns = None
        self.started_wall_time = None
        self.thread = threading.Thread(
            target=self._run,
            args=(safe_session, hz, on_sample),
            daemon=True,
        )
        self.thread.start()
        return self.output_path

    def wait_until_ready(self, timeout: float = 2.0) -> None:
        if not self.ready_event.wait(timeout):
            raise RuntimeError("Pose recorder did not initialize before timeout")
        if self.last_error is not None:
            raise RuntimeError(f"Pose recorder failed to initialize: {self.last_error}")

    def stop(self, timeout: float = 3.0) -> Path | None:
        self.stop_event.set()
        thread = self.thread
        if thread is not None:
            thread.join(timeout)
        return self.output_path

    def _run(
        self,
        session_id: str,
        hz: float,
        on_sample: Callable[[Pose, int], None] | None,
    ) -> None:
        assert self.output_path is not None
        interval = 1.0 / hz
        started = time.perf_counter()
        self.started_perf_counter_ns = time.perf_counter_ns()
        self.started_wall_time = dt.datetime.now().astimezone().isoformat()
        next_tick = started
        try:
            with self.output_path.open(
                "w", newline="", encoding="utf-8-sig"
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=self.FIELDNAMES)
                writer.writeheader()
                handle.flush()
                self.ready_event.set()
                while not self.stop_event.is_set():
                    now = time.perf_counter()
                    if now < next_tick:
                        self.stop_event.wait(min(next_tick - now, 0.01))
                        continue
                    pose = self.backend.pose()
                    writer.writerow(
                        {
                            "session_id": session_id,
                            "frame_id": self.frame_count,
                            "timestamp_sec": f"{now - started:.9f}",
                            "wall_time": pose.captured_at,
                            **{
                                key: pose.as_dict()[key]
                                for key in self.FIELDNAMES
                                if key in pose.as_dict()
                            },
                        }
                    )
                    if self.frame_count % max(1, round(hz)) == 0:
                        handle.flush()
                    if on_sample is not None:
                        on_sample(pose, self.frame_count)
                    self.frame_count += 1
                    next_tick += interval
        except Exception as exc:
            self.last_error = exc
            self.stop_event.set()
        finally:
            self.ready_event.set()
