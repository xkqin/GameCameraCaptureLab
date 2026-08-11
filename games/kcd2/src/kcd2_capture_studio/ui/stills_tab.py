from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from threading import Event

from ..backend import CameraBackend
from ..capture import StillCaptureSession
from ..obs_bridge import OBSBridge, obs_available
from ..paths import PLANS_DIR, RUNS_DIR, STILLS_DIR
from ..pose_control import ClosedLoopPoseController, PoseTolerance
from ..recording import VideoPoseSession
from ..scan_capture import AutomatedStillScan, load_scan_samples
from .common import AppHost, labeled_entry, open_in_explorer


class StillsTab(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        app: AppHost,
        backend: CameraBackend,
        scene_var: tk.StringVar,
        settings: dict,
    ) -> None:
        super().__init__(parent, padding=12)
        self.app = app
        self.backend = backend
        self.scene_var = scene_var
        obs = settings.get("obs", {})
        self.host_var = tk.StringVar(value=str(obs.get("host", "127.0.0.1")))
        self.port_var = tk.IntVar(value=int(obs.get("port", 4455)))
        self.password_var = tk.StringVar()
        self.source_var = tk.StringVar(value=str(obs.get("source", "")))
        self.format_var = tk.StringVar(value=str(obs.get("image_format", "jpg")))
        self.width_var = tk.IntVar(value=int(obs.get("width", 1920)))
        self.height_var = tk.IntVar(value=int(obs.get("height", 1080)))
        self.quality_var = tk.IntVar(value=int(obs.get("quality", 100)))
        self.label_var = tk.StringVar(value="current")
        self.pose_hz_var = tk.DoubleVar(
            value=float(settings.get("pose_logger_hz", 30.0))
        )
        self.status_var = tk.StringVar(
            value=(
                "obsws-python 可用"
                if obs_available()
                else "当前 Python 缺少 obsws-python，请用项目启动器"
            )
        )
        self.capture_session: StillCaptureSession | None = None
        self.video_session: VideoPoseSession | None = None
        self.recording = False
        self.scan_plan_var = tk.StringVar(
            value=str(settings.get("last_scan_plan", ""))
        )
        self.scan_start_var = tk.IntVar(value=1)
        self.scan_end_var = tk.StringVar()
        self.scan_settle_var = tk.DoubleVar(
            value=float(settings.get("scan_settle_seconds", 0.05))
        )
        self.scan_strict_var = tk.BooleanVar(value=True)
        self.scan_progress_var = tk.StringVar(value="尚未载入扫描计划")
        self.scan_progress_value = tk.DoubleVar(value=0.0)
        self.auto_scan: AutomatedStillScan | None = None
        self.auto_scan_running = False

        obs_box = ttk.LabelFrame(self, text="OBS WebSocket", padding=10)
        obs_box.pack(fill="x")
        labeled_entry(obs_box, 0, "Host", self.host_var)
        labeled_entry(obs_box, 0, "Port", self.port_var, column=2, width=8)
        labeled_entry(
            obs_box, 0, "Password", self.password_var, column=4, show="•"
        )
        labeled_entry(obs_box, 1, "Source（空=当前场景）", self.source_var, width=24)
        ttk.Button(obs_box, text="测试 OBS", command=self.test_obs).grid(
            row=1, column=2, columnspan=2, padx=6, pady=4, sticky="w"
        )
        ttk.Label(
            obs_box, textvariable=self.status_var, foreground="#1d4ed8"
        ).grid(row=2, column=0, columnspan=6, sticky="w", pady=(6, 0))
        for column in (1, 5):
            obs_box.columnconfigure(column, weight=1)

        still = ttk.LabelFrame(self, text="当前机位静态截图 + Pose Metadata", padding=10)
        still.pack(fill="x", pady=10)
        labeled_entry(still, 0, "标签", self.label_var, width=20)
        ttk.Label(still, text="格式").grid(row=0, column=2, sticky="w")
        ttk.Combobox(
            still,
            textvariable=self.format_var,
            values=("jpg", "png"),
            state="readonly",
            width=7,
        ).grid(row=0, column=3, padx=(6, 12))
        labeled_entry(still, 0, "宽", self.width_var, column=4, width=8)
        labeled_entry(still, 0, "高", self.height_var, column=6, width=8)
        labeled_entry(still, 0, "质量", self.quality_var, column=8, width=8)
        ttk.Button(still, text="截图并记录 Pose", command=self.capture_current).grid(
            row=0, column=10, padx=6
        )
        ttk.Button(
            still,
            text="打开截图目录",
            command=lambda: open_in_explorer(STILLS_DIR),
        ).grid(row=0, column=11, padx=6)

        record = ttk.LabelFrame(self, text="OBS 录像 + 连续 Pose 对齐", padding=10)
        record.pack(fill="x")
        labeled_entry(record, 0, "Pose Hz", self.pose_hz_var, width=8)
        self.record_button = ttk.Button(
            record, text="开始录像 + Pose", command=self.toggle_recording
        )
        self.record_button.grid(row=0, column=2, padx=8)
        ttk.Button(
            record,
            text="打开运行目录",
            command=lambda: open_in_explorer(RUNS_DIR),
        ).grid(row=0, column=3, padx=8)
        ttk.Label(
            record,
            text=(
                "清单会保存录像请求时间、pose CSV、帧计数和 OBS 输出路径；"
                "密码只保留在本次界面内，不写配置文件。"
            ),
            wraplength=900,
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 0))

        auto = ttk.LabelFrame(self, text="空间计划 × 22 方向自动采集", padding=10)
        auto.pack(fill="x", pady=10)
        labeled_entry(auto, 0, "计划 JSON", self.scan_plan_var, width=48)
        ttk.Button(auto, text="浏览", command=self.browse_scan_plan).grid(
            row=0, column=2, padx=5
        )
        ttk.Button(auto, text="检查计划", command=self.inspect_scan_plan).grid(
            row=0, column=3, padx=5
        )
        labeled_entry(auto, 1, "起始 Sample", self.scan_start_var, width=8)
        labeled_entry(auto, 1, "结束（空=全部）", self.scan_end_var, column=2, width=10)
        labeled_entry(auto, 1, "稳定等待(s)", self.scan_settle_var, column=4, width=8)
        ttk.Checkbutton(
            auto,
            text="位姿误差超限即停止",
            variable=self.scan_strict_var,
        ).grid(row=1, column=6, padx=8, sticky="w")
        self.scan_button = ttk.Button(
            auto,
            text="开始自动采集",
            command=self.toggle_auto_scan,
        )
        self.scan_button.grid(row=1, column=7, padx=6)
        ttk.Progressbar(
            auto,
            variable=self.scan_progress_value,
            maximum=100.0,
        ).grid(row=2, column=0, columnspan=8, sticky="ew", pady=(8, 4))
        ttk.Label(
            auto,
            textvariable=self.scan_progress_var,
            foreground="#9a3412",
            wraplength=940,
        ).grid(row=3, column=0, columnspan=8, sticky="w")
        auto.columnconfigure(1, weight=1)

    def _obs(self) -> OBSBridge:
        return OBSBridge(
            self.host_var.get(),
            int(self.port_var.get()),
            self.password_var.get(),
        )

    def create_obs_bridge(self) -> OBSBridge:
        return self._obs()

    def test_obs(self) -> None:
        def task():
            bridge = self._obs()
            return bridge.test()

        self.app.run_async("连接 OBS", task, self._show_obs_status)

    def capture_current(self) -> None:
        def task():
            bridge = self._obs()
            if (
                self.capture_session is None
                or self.capture_session.scene_id != self.scene_var.get()
            ):
                self.capture_session = StillCaptureSession(
                    self.backend,
                    bridge,
                    scene_id=self.scene_var.get(),
                )
            else:
                self.capture_session.obs = bridge
            return self.capture_session.capture_current(
                label=self.label_var.get(),
                source_name=self.source_var.get(),
                image_format=self.format_var.get(),
                width=int(self.width_var.get()),
                height=int(self.height_var.get()),
                quality=int(self.quality_var.get()),
            )

        self.app.run_async("OBS 截图并写入 pose metadata", task, self._after_capture)

    def toggle_recording(self) -> None:
        if self.recording:
            self.app.run_async(
                "停止 OBS 录像和 pose logger",
                self._stop_recording,
                self._after_recording_stopped,
            )
        else:
            self.app.run_async(
                "启动 OBS 录像和 pose logger",
                self._start_recording,
                self._after_recording_started,
            )
        self.record_button.configure(state="disabled")

    def close(self) -> None:
        if self.auto_scan is not None:
            self.auto_scan.stop()
        if self.video_session and self.video_session.pose_recorder.running:
            self.video_session.pose_recorder.stop()

    def settings_payload(self) -> dict:
        return {
            "pose_logger_hz": float(self.pose_hz_var.get()),
            "scan_settle_seconds": float(self.scan_settle_var.get()),
            "last_scan_plan": self.scan_plan_var.get(),
            "obs": {
                "host": self.host_var.get(),
                "port": int(self.port_var.get()),
                "source": self.source_var.get(),
                "image_format": self.format_var.get(),
                "width": int(self.width_var.get()),
                "height": int(self.height_var.get()),
                "quality": int(self.quality_var.get()),
            },
        }

    def browse_scan_plan(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="选择空间扫描计划",
            initialdir=str(PLANS_DIR),
            filetypes=(("Scan plan JSON", "*_plan.json"), ("JSON", "*.json")),
        )
        if path:
            self.scan_plan_var.set(path)
            self.inspect_scan_plan()

    def inspect_scan_plan(self) -> None:
        try:
            manifest, samples = load_scan_samples(self.scan_plan_var.get())
        except Exception as exc:
            messagebox.showerror("计划无效", str(exc), parent=self)
            return
        points = len({sample.point_index for sample in samples})
        self.scan_progress_var.set(
            f"计划有效：{points} 个空间点，{len(samples)} 张；"
            f"Scene={manifest.get('scene_id', '-')}"
        )
        self.scan_end_var.set(str(len(samples)))

    def toggle_auto_scan(self) -> None:
        if self.auto_scan_running:
            if self.auto_scan is not None:
                self.auto_scan.stop()
            self.scan_button.configure(state="disabled", text="正在停止…")
            self.scan_progress_var.set("已请求停止；完成当前样本后恢复起点")
            return
        if self.recording:
            messagebox.showerror(
                "无法开始",
                "请先停止当前 OBS 录像，再运行自动静态采集。",
                parent=self,
            )
            return
        self.auto_scan_running = True
        self.scan_progress_value.set(0.0)
        self.scan_button.configure(text="停止并恢复起点")
        self.app.run_async(
            "执行空间计划 × 22 方向采集",
            self._run_auto_scan,
            self._after_auto_scan,
            self._auto_scan_failed,
        )

    def _start_recording(self):
        self.video_session = VideoPoseSession(
            self.backend,
            self._obs(),
            scene_id=self.scene_var.get(),
            pose_hz=float(self.pose_hz_var.get()),
        )
        return self.video_session.start()

    def _stop_recording(self):
        if self.video_session is None:
            raise RuntimeError("没有活动录像")
        return self.video_session.stop()

    def _run_auto_scan(self):
        end_text = self.scan_end_var.get().strip()
        controller = ClosedLoopPoseController(
            self.backend,
            tolerance=PoseTolerance(),
        )
        self.auto_scan = AutomatedStillScan(
            controller,
            self._obs(),
            stop_event=Event(),
        )
        return self.auto_scan.run(
            self.scan_plan_var.get(),
            scene_id=self.scene_var.get(),
            source_name=self.source_var.get(),
            image_format=self.format_var.get(),
            width=int(self.width_var.get()),
            height=int(self.height_var.get()),
            quality=int(self.quality_var.get()),
            settle_seconds=float(self.scan_settle_var.get()),
            start_sample=int(self.scan_start_var.get()),
            end_sample=int(end_text) if end_text else None,
            strict_pose=bool(self.scan_strict_var.get()),
            progress_callback=self._scan_progress,
        )

    def _after_recording_started(self, manifest: dict) -> None:
        self.recording = True
        self.record_button.configure(text="停止录像 + Pose", state="normal")
        self.status_var.set(f"正在录像：{manifest['session_id']}")
        self.app.log(f"录像与 pose logger 已启动：{manifest['pose_csv']}")

    def _after_recording_stopped(self, manifest: dict) -> None:
        self.recording = False
        self.record_button.configure(text="开始录像 + Pose", state="normal")
        self.status_var.set(
            f"录像已停止；pose {manifest.get('pose_frames', 0)} 帧"
        )
        self.app.log(
            f"录像清单：{self.video_session.manifest_path if self.video_session else '-'}"
        )

    def _show_obs_status(self, result: dict) -> None:
        self.status_var.set(
            f"OBS {result.get('obs_version')} / WebSocket "
            f"{result.get('websocket_version')} 连接成功"
        )

    def _after_capture(self, row: dict) -> None:
        self.status_var.set(f"截图完成：{row['image_path']}")
        self.app.log(
            f"静态样本 #{row['sample_index']} 已保存：{row['image_path']}"
        )

    def _scan_progress(
        self,
        sample,
        completed: int,
        total: int,
        image_path,
        report: dict,
    ) -> None:
        position_error = report.get("error", {}).get("position", 0.0)
        self.app.root.after(
            0,
            lambda: self._show_scan_progress(
                sample.sample_index,
                completed,
                total,
                str(image_path),
                float(position_error),
            ),
        )

    def _show_scan_progress(
        self,
        sample_index: int,
        completed: int,
        total: int,
        image_path: str,
        position_error: float,
    ) -> None:
        self.scan_progress_value.set(100.0 * completed / max(1, total))
        self.scan_progress_var.set(
            f"{completed}/{total}，Sample {sample_index}，"
            f"位置误差 {position_error:.4f}：{image_path}"
        )

    def _after_auto_scan(self, result: dict) -> None:
        self.auto_scan_running = False
        self.scan_button.configure(text="开始自动采集", state="normal")
        status = result.get("status")
        self.scan_progress_var.set(
            f"采集{status}：{result.get('completed_count')}/"
            f"{result.get('selected_count')}；起点已恢复；"
            f"清单 {result.get('run_manifest')}"
        )
        self.app.log(f"自动静态采集结束：{result.get('run_manifest')}")

    def _auto_scan_failed(self, exc: Exception) -> None:
        self.auto_scan_running = False
        self.scan_button.configure(text="开始自动采集", state="normal")
        self.scan_progress_var.set(f"自动采集失败并已尝试恢复起点：{exc}")

    def task_failed(self) -> None:
        self.record_button.configure(
            text="停止录像 + Pose" if self.recording else "开始录像 + Pose",
            state="normal",
        )
        if self.auto_scan_running:
            self.auto_scan_running = False
            self.scan_button.configure(text="开始自动采集", state="normal")
