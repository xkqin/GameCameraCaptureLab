from __future__ import annotations

import json
from pathlib import Path
from threading import Event
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..aesthetic import LAIONAestheticScorer
from ..paths import ANALYSIS_DIR
from ..reports import build_score_ascent_trajectory, generate_capture_report
from ..video_analysis import align_frames_with_pose, extract_video_frames
from .common import AppHost, labeled_entry, open_in_explorer


class AnalysisTab(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        app: AppHost,
        settings: dict,
    ) -> None:
        super().__init__(parent, padding=12)
        self.app = app
        analysis = settings.get("analysis", {})
        self.manifest_var = tk.StringVar(
            value=str(analysis.get("recording_manifest", ""))
        )
        self.video_var = tk.StringVar(value=str(analysis.get("video_path", "")))
        self.pose_var = tk.StringVar(value=str(analysis.get("pose_csv", "")))
        self.output_var = tk.StringVar(
            value=str(analysis.get("output_dir", ANALYSIS_DIR / "latest"))
        )
        self.fps_var = tk.DoubleVar(value=float(analysis.get("extract_fps", 2.0)))
        self.batch_var = tk.IntVar(value=int(analysis.get("batch_size", 16)))
        self.device_var = tk.StringVar(value=str(analysis.get("device", "auto")))
        self.status_var = tk.StringVar(value="载入录像清单后按 1 → 5 执行")
        self.progress_var = tk.DoubleVar(value=0.0)
        self.stop_event = Event()

        source = ttk.LabelFrame(self, text="录像与 Pose 输入", padding=10)
        source.pack(fill="x")
        labeled_entry(source, 0, "Recording Manifest", self.manifest_var, width=58)
        ttk.Button(source, text="浏览", command=self.browse_manifest).grid(
            row=0, column=2, padx=5
        )
        ttk.Button(source, text="载入清单", command=self.load_manifest).grid(
            row=0, column=3, padx=5
        )
        labeled_entry(source, 1, "Video", self.video_var, width=58)
        ttk.Button(
            source,
            text="浏览",
            command=lambda: self._browse_file(
                self.video_var,
                (("Video", "*.mkv *.mp4 *.mov *.avi"), ("All", "*.*")),
            ),
        ).grid(row=1, column=2, padx=5)
        labeled_entry(source, 2, "Pose CSV", self.pose_var, width=58)
        ttk.Button(
            source,
            text="浏览",
            command=lambda: self._browse_file(
                self.pose_var,
                (("CSV", "*.csv"), ("All", "*.*")),
            ),
        ).grid(row=2, column=2, padx=5)
        labeled_entry(source, 3, "分析输出目录", self.output_var, width=58)
        ttk.Button(source, text="选择目录", command=self.browse_output).grid(
            row=3, column=2, padx=5
        )
        ttk.Button(
            source,
            text="打开输出",
            command=lambda: open_in_explorer(self.output_var.get()),
        ).grid(row=3, column=3, padx=5)
        source.columnconfigure(1, weight=1)

        parameters = ttk.LabelFrame(self, text="分析参数", padding=10)
        parameters.pack(fill="x", pady=10)
        labeled_entry(parameters, 0, "抽帧 FPS", self.fps_var, width=8)
        labeled_entry(parameters, 0, "评分 Batch", self.batch_var, column=2, width=8)
        ttk.Label(parameters, text="设备").grid(row=0, column=4, sticky="w")
        ttk.Combobox(
            parameters,
            textvariable=self.device_var,
            values=("auto", "cuda", "cpu"),
            state="readonly",
            width=8,
        ).grid(row=0, column=5, padx=6)
        ttk.Button(
            parameters, text="停止当前步骤", command=self.stop_current
        ).grid(row=0, column=6, padx=12)

        workflow = ttk.LabelFrame(
            self, text="RE9 同款：抽帧 → LAION → Pose 对齐 → 报告 → 轨迹", padding=10
        )
        workflow.pack(fill="x")
        actions = (
            ("1. 抽取视频帧", self.extract_frames),
            ("2. LAION 美学评分", self.score_frames),
            ("3. 帧与 Pose 对齐", self.align_pose),
            ("4. 生成 HTML 报告", self.generate_report),
            ("5. 生成升分轨迹 JSON", self.build_trajectory),
        )
        for column, (label, command) in enumerate(actions):
            ttk.Button(workflow, text=label, command=command).grid(
                row=0, column=column, sticky="ew", padx=4
            )
            workflow.columnconfigure(column, weight=1)
        ttk.Progressbar(
            workflow, variable=self.progress_var, maximum=100.0
        ).grid(row=1, column=0, columnspan=5, sticky="ew", pady=(10, 4))
        ttk.Label(
            workflow,
            textvariable=self.status_var,
            foreground="#1d4ed8",
            wraplength=950,
        ).grid(row=2, column=0, columnspan=5, sticky="w")

        ttk.Label(
            self,
            text=(
                "时间对齐使用录像清单中的 pose_time_at_obs_start_sec："
                "视频 t=0 对应 OBS StartRecord 请求时刻。LAION 首次运行可能下载"
                " OpenCLIP 与美学头权重。"
            ),
            foreground="#9a3412",
            wraplength=960,
        ).pack(anchor="w", pady=(12, 0))

    def browse_manifest(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="选择 recording_manifest.json",
            filetypes=(("Recording manifest", "recording_manifest.json"), ("JSON", "*.json")),
        )
        if path:
            self.manifest_var.set(path)
            self.load_manifest()

    def load_manifest(self) -> None:
        try:
            path = Path(self.manifest_var.get()).resolve()
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            messagebox.showerror("清单读取失败", str(exc), parent=self)
            return
        self.video_var.set(str(manifest.get("video_path") or ""))
        self.pose_var.set(str(manifest.get("pose_csv") or ""))
        session = str(manifest.get("session_id") or path.parent.name)
        self.output_var.set(str(ANALYSIS_DIR / session))
        self.status_var.set(
            f"已载入 {session}；pose offset="
            f"{manifest.get('pose_time_at_obs_start_sec', 0)} s"
        )

    def browse_output(self) -> None:
        path = filedialog.askdirectory(
            parent=self,
            title="选择分析输出目录",
            initialdir=str(ANALYSIS_DIR),
        )
        if path:
            self.output_var.set(path)

    def extract_frames(self) -> None:
        self._start_step(
            "抽取视频帧",
            lambda: extract_video_frames(
                self.video_var.get(),
                self.output_var.get(),
                target_fps=float(self.fps_var.get()),
                stop_event=self.stop_event,
                progress_callback=self._extract_progress,
            ),
            lambda result: self._finish(
                f"抽帧完成：{result['frame_count']} 帧；{result['metadata_csv']}"
            ),
        )

    def score_frames(self) -> None:
        metadata = Path(self.output_var.get()) / "frames" / "frame_metadata.csv"
        output = Path(self.output_var.get()) / "scores.csv"
        self._start_step(
            "LAION 美学评分",
            lambda: LAIONAestheticScorer(
                device=self.device_var.get()
            ).score_metadata(
                metadata,
                output,
                batch_size=int(self.batch_var.get()),
                progress_callback=self._score_progress,
            ),
            lambda result: self._finish(
                f"评分完成：{result['scored_count']} 帧，device={result['device']}；"
                f"{result['output_csv']}"
            ),
        )

    def align_pose(self) -> None:
        output_dir = Path(self.output_var.get())
        scores = output_dir / "scores.csv"
        source = scores if scores.exists() else output_dir / "frames" / "frame_metadata.csv"
        output = output_dir / "scores_with_pose.csv"
        self._start_step(
            "帧与 Pose 对齐",
            lambda: align_frames_with_pose(
                source,
                self.pose_var.get(),
                output,
                recording_manifest=self.manifest_var.get() or None,
            ),
            lambda result: self._finish(
                f"对齐完成：{result['aligned_count']}/{result['frame_count']}；"
                f"{result['output_csv']}"
            ),
        )

    def generate_report(self) -> None:
        output = Path(self.output_var.get())
        self._start_step(
            "生成采集报告",
            lambda: generate_capture_report(
                output / "scores_with_pose.csv",
                output / "report",
            ),
            lambda result: self._finish(f"报告完成：{result['report']}"),
        )

    def build_trajectory(self) -> None:
        output = Path(self.output_var.get())
        self._start_step(
            "生成升分轨迹",
            lambda: build_score_ascent_trajectory(
                output / "scores_with_pose.csv",
                output / "score_ascent_trajectory.json",
            ),
            lambda result: self._finish(
                f"轨迹完成：{result['keyframe_count']} 帧，"
                f"{result['start_score']:.3f} → {result['end_score']:.3f}；"
                f"{result['output_json']}"
            ),
        )

    def stop_current(self) -> None:
        self.stop_event.set()
        self.status_var.set("已请求停止当前步骤")

    def close(self) -> None:
        self.stop_event.set()

    def settings_payload(self) -> dict:
        return {
            "analysis": {
                "recording_manifest": self.manifest_var.get(),
                "video_path": self.video_var.get(),
                "pose_csv": self.pose_var.get(),
                "output_dir": self.output_var.get(),
                "extract_fps": float(self.fps_var.get()),
                "batch_size": int(self.batch_var.get()),
                "device": self.device_var.get(),
            }
        }

    def _start_step(self, label, function, success) -> None:
        self.stop_event.clear()
        self.progress_var.set(0.0)
        self.app.run_async(label, function, success)

    def _finish(self, message: str) -> None:
        self.progress_var.set(100.0)
        self.status_var.set(message)
        self.app.log(message)

    def _extract_progress(self, completed: int, total: int, path: Path) -> None:
        self.app.root.after(
            0,
            lambda: self._show_progress(
                completed, total, f"抽帧 {completed}/{total}：{path.name}"
            ),
        )

    def _score_progress(self, completed: int, total: int) -> None:
        self.app.root.after(
            0,
            lambda: self._show_progress(
                completed, total, f"评分 {completed}/{total}"
            ),
        )

    def _show_progress(self, completed: int, total: int, text: str) -> None:
        self.progress_var.set(100.0 * completed / max(1, total))
        self.status_var.set(text)

    def _browse_file(self, variable: tk.StringVar, filetypes) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            filetypes=filetypes,
        )
        if path:
            variable.set(path)
