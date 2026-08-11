from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from ..backend import CameraBackend, LivePoseRecorder
from ..igcs_path import IGCSCameraPathBuilder
from ..obs_bridge import OBSBridge
from ..path_recording import PathPlaybackRecorder
from ..pose_control import ClosedLoopPoseController
from ..storage import safe_id
from ..trajectory import TrajectoryService
from ..trajectory_recording import ImportedTrajectoryRecorder
from .common import (
    AppHost,
    configure_tree_columns,
    labeled_entry,
    set_tree_rows,
)


class TrajectoryTab(ttk.Frame):
    COLUMNS = ("step", "time", "x", "y", "z", "yaw", "pitch", "roll", "fov")

    def __init__(
        self,
        parent: tk.Misc,
        app: AppHost,
        backend: CameraBackend,
        settings: dict,
        scene_var: tk.StringVar,
        obs_factory: Callable[[], OBSBridge],
    ) -> None:
        super().__init__(parent, padding=12)
        self.app = app
        self.backend = backend
        self.scene_var = scene_var
        self.obs_factory = obs_factory
        trajectory = settings.get("trajectory", {})
        self.trajectory_id_var = tk.StringVar(value="trajectory_01")
        self.logger_session_var = tk.StringVar(value="manual_walk")
        self.logger_hz_var = tk.DoubleVar(
            value=float(settings.get("pose_logger_hz", 30.0))
        )
        self.duration_var = tk.DoubleVar(
            value=float(trajectory.get("duration", 8.0))
        )
        self.random_hz_var = tk.DoubleVar(value=float(trajectory.get("hz", 20.0)))
        self.xy_scale_var = tk.DoubleVar(
            value=float(trajectory.get("xy_scale", 12.0))
        )
        self.seed_var = tk.StringVar()
        self.status_var = tk.StringVar(value="等待采集或运行轨迹")
        self.service = TrajectoryService(backend, self.trajectory_id_var.get())
        self.pose_recorder = LivePoseRecorder(backend)
        self.imported_frames = []
        self.imported_source_path: Path | None = None
        self.direct_recording: ImportedTrajectoryRecorder | None = None
        self.path_builder: IGCSCameraPathBuilder | None = None
        self.path_build_running = False
        self.path_recording: PathPlaybackRecorder | None = None

        logger = ttk.LabelFrame(self, text="连续 Pose Logger", padding=10)
        logger.pack(fill="x")
        labeled_entry(logger, 0, "Session", self.logger_session_var, width=20)
        labeled_entry(logger, 0, "Hz", self.logger_hz_var, column=2, width=8)
        self.logger_button = ttk.Button(
            logger, text="开始连续记录", command=self.toggle_logger
        )
        self.logger_button.grid(row=0, column=4, padx=6)
        ttk.Label(logger, textvariable=self.status_var, foreground="#1d4ed8").grid(
            row=1, column=0, columnspan=6, sticky="w", pady=(6, 0)
        )

        keyframes = ttk.LabelFrame(self, text="轨迹关键帧", padding=10)
        keyframes.pack(fill="both", expand=True, pady=10)
        controls = ttk.Frame(keyframes)
        controls.pack(fill="x", pady=(0, 8))
        labeled_entry(controls, 0, "Trajectory ID", self.trajectory_id_var, width=22)
        ttk.Button(
            controls, text="载入", command=self.load_trajectory
        ).grid(row=0, column=2, padx=5)
        ttk.Button(
            controls, text="采集当前关键帧", command=self.capture_keyframe
        ).grid(row=0, column=3, padx=5)
        ttk.Button(
            controls, text="清空关键帧", command=self.clear_keyframes
        ).grid(row=0, column=4, padx=5)
        ttk.Button(
            controls, text="导入 JSON", command=self.import_json
        ).grid(row=0, column=5, padx=5)
        self.path_build_button = ttk.Button(
            controls,
            text="构建 IGCS Camera Path",
            command=self.toggle_path_build,
        )
        self.path_build_button.grid(row=0, column=6, padx=5)

        table_frame = ttk.Frame(keyframes)
        table_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            table_frame,
            columns=self.COLUMNS,
            show="headings",
            height=8,
        )
        configure_tree_columns(
            self.tree,
            self.COLUMNS,
            ("Step", "Time", "X", "Y", "Z", "Yaw", "Pitch", "Roll", "FOV"),
            (55, 75, 100, 100, 100, 80, 80, 80, 70),
        )
        scrollbar = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        direct_box = ttk.LabelFrame(
            self, text="导入轨迹直接采集（绝对位姿）", padding=10
        )
        direct_box.pack(fill="x", pady=(0, 10))
        self.direct_record_button = ttk.Button(
            direct_box,
            text="采集这一条轨迹（OBS + 60Hz Pose）",
            command=self.capture_imported_trajectory,
        )
        self.direct_record_button.pack(side="left", padx=(0, 12))
        ttk.Label(
            direct_box,
            text=(
                "按 JSON time_sec 逐帧写入完整 XYZ/旋转/FOV；"
                "适合 931 帧稠密轨迹，不经过 IGCS Camera Path 节点。"
            ),
            foreground="#166534",
            wraplength=760,
        ).pack(side="left")

        random_box = ttk.LabelFrame(
            self, text="已验证：随机平滑运镜（官方 DLL 导出）", padding=10
        )
        random_box.pack(fill="x")
        labeled_entry(random_box, 0, "时长(s)", self.duration_var, width=8)
        labeled_entry(random_box, 0, "Hz", self.random_hz_var, column=2, width=8)
        labeled_entry(random_box, 0, "XY Scale", self.xy_scale_var, column=4, width=8)
        labeled_entry(random_box, 0, "Seed（空=随机）", self.seed_var, column=6, width=12)
        ttk.Button(
            random_box, text="在游戏中运行", command=self.run_random
        ).grid(row=0, column=8, padx=6)
        ttk.Button(
            random_box, text="恢复轨迹起点", command=self.restore_start
        ).grid(row=0, column=9, padx=6)
        ttk.Label(
            random_box,
            text=(
                "随机运镜结束后截图 Session 保持活动；点击“恢复轨迹起点”"
                "会调用 EndScreenshotSession 精确返回起点。"
            ),
            wraplength=930,
        ).grid(row=1, column=0, columnspan=10, sticky="w", pady=(8, 0))

        path_box = ttk.LabelFrame(
            self, text="IGCS Camera Path 回放", padding=10
        )
        path_box.pack(fill="x", pady=(10, 0))
        ttk.Button(
            path_box,
            text="播放 / 暂停 (F7)",
            command=lambda: self.app.run_async(
                "播放或暂停 IGCS Camera Path",
                self._path_play_pause,
            ),
        ).pack(side="left", padx=(0, 8))
        ttk.Button(
            path_box,
            text="停止回放 (F8)",
            command=lambda: self.app.run_async(
                "停止 IGCS Camera Path",
                self._path_stop,
            ),
        ).pack(side="left", padx=8)
        self.path_record_button = ttk.Button(
            path_box,
            text="播放 + OBS + Pose",
            command=self.toggle_path_recording,
        )
        self.path_record_button.pack(side="left", padx=8)
        ttk.Label(
            path_box,
            text=(
                "构建时逐个闭环到 JSON/手动关键帧并按 F10 写入节点；"
                "位置、方向、FOV 来自关键帧，播放时长和插值由 IGCS Path 设置控制。"
            ),
            foreground="#9a3412",
            wraplength=760,
        ).pack(side="left", padx=12)

        self.refresh_keyframes()
        autoload_path = (
            os.environ.get("KCD2_TRAJECTORY_JSON", "").strip()
            or str(trajectory.get("autoload_json", "")).strip()
        )
        if autoload_path:
            self.app.root.after(
                150,
                lambda path=Path(autoload_path): self._load_json_path(path),
            )

    def toggle_logger(self) -> None:
        if self.pose_recorder.running:
            path = self.pose_recorder.stop()
            self.logger_button.configure(text="开始连续记录")
            self.status_var.set(
                f"连续日志已停止：{path}；{self.pose_recorder.frame_count} 帧"
            )
            return
        try:
            path = self.pose_recorder.start(
                session_id=self.logger_session_var.get(),
                hz=float(self.logger_hz_var.get()),
            )
        except Exception as exc:
            messagebox.showerror("启动失败", str(exc), parent=self)
            return
        self.logger_button.configure(text="停止连续记录")
        self.status_var.set(f"正在记录：{path}")

    def load_trajectory(self) -> None:
        self.service.set_trajectory_id(self.trajectory_id_var.get())
        self.trajectory_id_var.set(self.service.store.trajectory_id)
        self.refresh_keyframes()

    def capture_keyframe(self) -> None:
        self.load_trajectory()
        self.app.run_async(
            "采集轨迹关键帧",
            self.service.capture_keyframe,
            lambda frame: self._after_keyframe(frame.step),
        )

    def clear_keyframes(self) -> None:
        self.load_trajectory()
        if not messagebox.askyesno(
            "清空关键帧",
            f"清空 {self.service.store.trajectory_id} 的所有关键帧？",
            parent=self,
        ):
            return
        self.service.clear_keyframes()
        self.refresh_keyframes()
        self.app.log(f"关键帧已清空：{self.service.store.trajectory_id}")

    def import_json(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="导入轨迹 JSON",
            filetypes=(("JSON", "*.json"), ("All files", "*.*")),
        )
        if not path:
            return
        self._load_json_path(Path(path))

    def _load_json_path(self, path: Path) -> None:
        try:
            self.imported_frames = self.service.load_external_json(path)
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc), parent=self)
            return
        self.imported_source_path = path.resolve()
        if self.service.last_import_trajectory_id:
            self.trajectory_id_var.set(
                safe_id(self.service.last_import_trajectory_id)
            )
        self._show_frames(self.imported_frames)
        self.status_var.set(
            f"已导入 1 条轨迹、{len(self.imported_frames)} 帧；可直接采集"
        )
        self.app.log(f"已解析外部轨迹：{path}")

    def capture_imported_trajectory(self) -> None:
        if self.direct_recording is not None and self.direct_recording.active:
            self.direct_recording.request_stop()
            self.direct_record_button.configure(state="disabled", text="正在停止…")
            self.status_var.set("正在安全停止轨迹、OBS 和 Pose…")
            return
        if not self.imported_frames or self.imported_source_path is None:
            messagebox.showerror(
                "尚未导入轨迹",
                "请先点“导入 JSON”选择要采集的那一条轨迹。",
                parent=self,
            )
            return
        self.direct_record_button.configure(
            state="disabled", text="正在采集这条轨迹…"
        )
        self.app.run_async(
            "直接回放导入轨迹并采集 OBS + Pose",
            self._run_imported_trajectory_capture,
            self._after_imported_trajectory_capture,
            self._imported_trajectory_capture_failed,
        )

    def run_random(self) -> None:
        seed_text = self.seed_var.get().strip()
        seed = int(seed_text) if seed_text else None
        self.app.run_async(
            "在游戏中运行随机运镜",
            lambda: self.service.run_random(
                duration=float(self.duration_var.get()),
                hz=float(self.random_hz_var.get()),
                seed=seed,
                xy_scale=float(self.xy_scale_var.get()),
            ),
            self._after_random,
        )

    def toggle_path_build(self) -> None:
        if self.path_build_running:
            if self.path_builder is not None:
                self.path_builder.stop()
            self.path_build_button.configure(state="disabled", text="正在停止…")
            return
        frames = self.imported_frames or self.service.load_keyframes()
        if len(frames) < 2:
            messagebox.showerror(
                "关键帧不足",
                "请先采集至少两个关键帧，或导入轨迹 JSON。",
                parent=self,
            )
            return
        self.path_build_running = True
        self.path_build_button.configure(text="停止构建")
        self.path_builder = IGCSCameraPathBuilder(
            self.backend,
            ClosedLoopPoseController(self.backend),
        )
        self.app.run_async(
            "构建 IGCS Camera Path",
            lambda: self.path_builder.build(
                frames,
                trajectory_id=self.trajectory_id_var.get(),
                progress_callback=self._path_progress,
            ),
            self._after_path_build,
            self._path_build_failed,
        )

    def toggle_path_recording(self) -> None:
        if self.path_recording is not None and self.path_recording.active:
            self.path_record_button.configure(state="disabled")
            self.app.run_async(
                "停止 IGCS Path、OBS 和 Pose",
                self.path_recording.stop,
                self._after_path_recording_stopped,
                self._path_recording_failed,
            )
            return
        self.path_record_button.configure(state="disabled")
        self.app.run_async(
            "播放 IGCS Path 并启动 OBS + Pose",
            self._start_path_recording,
            self._after_path_recording_started,
            self._path_recording_failed,
        )

    def restore_start(self) -> None:
        self.app.run_async(
            "恢复随机轨迹起点",
            self.service.restore_start,
            lambda result: self._after_restore(result),
        )

    def refresh_keyframes(self) -> None:
        self._show_frames(self.service.load_keyframes())

    def close(self) -> None:
        if self.path_builder is not None:
            self.path_builder.stop()
        if self.pose_recorder.running:
            self.pose_recorder.stop()
        if self.path_recording is not None and self.path_recording.active:
            try:
                self.path_recording.stop()
            except Exception:
                pass
        if self.direct_recording is not None and self.direct_recording.active:
            self.direct_recording.request_stop()

    def settings_payload(self) -> dict:
        return {
            "pose_logger_hz": float(self.logger_hz_var.get()),
            "trajectory": {
                "duration": float(self.duration_var.get()),
                "hz": float(self.random_hz_var.get()),
                "xy_scale": float(self.xy_scale_var.get()),
                "autoload_json": (
                    str(self.imported_source_path)
                    if self.imported_source_path is not None
                    else ""
                ),
            },
        }

    def _show_frames(self, frames) -> None:
        rows = [
            (
                frame.step,
                f"{frame.time_sec:.3f}",
                f"{frame.x:.4f}",
                f"{frame.y:.4f}",
                f"{frame.z:.4f}",
                f"{frame.yaw_degrees:.2f}",
                f"{frame.pitch_degrees:.2f}",
                f"{frame.roll_degrees:.2f}",
                f"{frame.fov_degrees:.2f}",
            )
            for frame in frames
        ]
        set_tree_rows(self.tree, rows)

    def _after_keyframe(self, step: int) -> None:
        self.refresh_keyframes()
        self.status_var.set(f"已采集关键帧 step={step}")

    def _after_random(self, result: dict) -> None:
        self.seed_var.set(str(result.get("seed", "")))
        self.status_var.set(
            f"随机运镜完成：{result.get('frames')} 帧；"
            f"CSV={result.get('csv_path')}"
        )
        self.app.log(f"随机轨迹清单：{result.get('manifest_path')}")

    def _run_imported_trajectory_capture(self) -> dict:
        assert self.imported_source_path is not None
        self.direct_recording = ImportedTrajectoryRecorder(
            self.backend,
            self.obs_factory(),
            scene_id=self.scene_var.get(),
            trajectory_id=self.trajectory_id_var.get(),
            pose_hz=60.0,
        )
        return self.direct_recording.capture(
            self.imported_frames,
            source_path=self.imported_source_path,
            progress_callback=self._direct_trajectory_progress,
        )

    def _direct_trajectory_progress(self, completed: int, total: int) -> None:
        self.app.root.after(
            0,
            lambda: self.status_var.set(
                f"正在采集导入轨迹：{completed}/{total} 帧"
            ),
        )

    def _after_imported_trajectory_capture(self, result: dict) -> None:
        self.direct_record_button.configure(
            state="normal", text="采集这一条轨迹（OBS + 60Hz Pose）"
        )
        playback = result.get("trajectory_playback", {})
        self.status_var.set(
            f"轨迹采集完成：{playback.get('completed_frames')}/"
            f"{playback.get('requested_frames')} 帧；video={result.get('video_path')}"
        )
        if self.direct_recording is not None:
            self.app.log(
                f"导入轨迹采集清单：{self.direct_recording.session.manifest_path}"
            )

    def _imported_trajectory_capture_failed(self, exc: Exception) -> None:
        self.direct_record_button.configure(
            state="normal", text="采集这一条轨迹（OBS + 60Hz Pose）"
        )
        self.status_var.set(f"导入轨迹采集失败：{exc}")

    def _after_restore(self, result: dict) -> None:
        after = result.get("after", {})
        self.status_var.set(
            "已结束 Screenshot Session 并恢复起点："
            f"({after.get('x')}, {after.get('y')}, {after.get('z')})"
        )

    def _path_progress(self, frame, completed: int, total: int, report: dict) -> None:
        self.app.root.after(
            0,
            lambda: self.status_var.set(
                f"正在构建 IGCS Path：{completed}/{total}，"
                f"step={frame.step}，误差={report['error']['position']:.4f}"
            ),
        )

    def _after_path_build(self, result: dict) -> None:
        self.path_build_running = False
        self.path_build_button.configure(
            text="构建 IGCS Camera Path", state="normal"
        )
        self.status_var.set(
            f"IGCS Path {result.get('status')}："
            f"{result.get('completed_nodes')}/{result.get('requested_nodes')} 节点；"
            f"起点已恢复"
        )
        self.app.log(f"IGCS Path 构建清单：{result.get('report_path')}")

    def _path_build_failed(self, exc: Exception) -> None:
        self.path_build_running = False
        self.path_build_button.configure(
            text="构建 IGCS Camera Path", state="normal"
        )
        self.status_var.set(f"IGCS Path 构建失败并已尝试恢复起点：{exc}")

    def _path_play_pause(self) -> None:
        builder = self.path_builder or IGCSCameraPathBuilder(
            self.backend,
            ClosedLoopPoseController(self.backend),
        )
        builder.play_pause()

    def _path_stop(self) -> None:
        builder = self.path_builder or IGCSCameraPathBuilder(
            self.backend,
            ClosedLoopPoseController(self.backend),
        )
        builder.stop_playback()

    def _start_path_recording(self) -> dict:
        self.path_recording = PathPlaybackRecorder(
            self.backend,
            self.obs_factory(),
            scene_id=self.scene_var.get(),
            trajectory_id=self.trajectory_id_var.get(),
            pose_hz=float(self.logger_hz_var.get()),
        )
        return self.path_recording.start()

    def _after_path_recording_started(self, result: dict) -> None:
        self.path_record_button.configure(
            text="停止 Path + OBS + Pose", state="normal"
        )
        self.status_var.set(
            f"正在录制 IGCS Path：{result.get('session_id')}；"
            f"Pose={result.get('pose_csv')}"
        )

    def _after_path_recording_stopped(self, result: dict) -> None:
        self.path_record_button.configure(
            text="播放 + OBS + Pose", state="normal"
        )
        self.status_var.set(
            f"Path 录像完成：pose {result.get('pose_frames')} 帧；"
            f"video={result.get('video_path')}"
        )
        if self.path_recording is not None:
            self.app.log(
                f"Path 录像清单：{self.path_recording.session.manifest_path}"
            )

    def _path_recording_failed(self, exc: Exception) -> None:
        active = bool(self.path_recording and self.path_recording.active)
        self.path_record_button.configure(
            text="停止 Path + OBS + Pose" if active else "播放 + OBS + Pose",
            state="normal",
        )
        self.status_var.set(f"Path 录像操作失败：{exc}")
