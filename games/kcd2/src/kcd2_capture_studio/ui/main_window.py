from __future__ import annotations

import datetime as dt
import threading
import tkinter as tk
from tkinter import messagebox, ttk
import traceback
from typing import Any, Callable

from ..backend import CameraBackend
from ..paths import DATA_ROOT, PROJECT_ROOT, ensure_data_dirs
from ..settings import load_settings, save_settings
from .analysis_tab import AnalysisTab
from .points_tab import PointsTab
from .stills_tab import StillsTab
from .system_tab import SystemTab
from .trajectory_tab import TrajectoryTab


class CaptureStudioApp:
    def __init__(self, root: tk.Tk | None = None) -> None:
        ensure_data_dirs()
        self.root = root or tk.Tk()
        self.root.title("KCD2 Camera Capture Studio")
        self.root.geometry("1120x820")
        self.root.minsize(980, 700)
        self.backend = CameraBackend()
        self.settings = load_settings()
        self.scene_var = tk.StringVar(
            value=str(self.settings.get("scene_id", "new_scene"))
        )
        self.status_var = tk.StringVar(
            value=f"项目：{PROJECT_ROOT}    |    就绪"
        )

        self._configure_style()
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        title = ttk.Frame(outer)
        title.pack(fill="x", pady=(0, 8))
        ttk.Label(
            title,
            text="KCD2 Camera Capture Studio",
            style="Title.TLabel",
        ).pack(side="left")
        ttk.Label(
            title,
            text="独立项目 · Pose / 点位 / OBS / 轨迹",
            foreground="#475569",
        ).pack(side="right", pady=(8, 0))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        self.system_tab = SystemTab(notebook, self, self.backend)
        self.points_tab = PointsTab(
            notebook,
            self,
            self.backend,
            self.scene_var,
            self.settings,
        )
        self.stills_tab = StillsTab(
            notebook,
            self,
            self.backend,
            self.scene_var,
            self.settings,
        )
        self.trajectory_tab = TrajectoryTab(
            notebook,
            self,
            self.backend,
            self.settings,
            self.scene_var,
            self.stills_tab.create_obs_bridge,
        )
        self.analysis_tab = AnalysisTab(notebook, self, self.settings)
        notebook.add(self.system_tab, text="系统与实时 Pose")
        notebook.add(self.points_tab, text="场景点与空间规划")
        notebook.add(self.stills_tab, text="OBS 静态/录像采集")
        notebook.add(self.trajectory_tab, text="轨迹采集与运镜")
        notebook.add(self.analysis_tab, text="评分、对齐与报告")

        log_box = ttk.LabelFrame(outer, text="运行日志", padding=6)
        log_box.pack(fill="x", pady=(8, 0))
        self.log_text = tk.Text(
            log_box,
            height=6,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
        )
        self.log_text.pack(fill="x", expand=True)
        ttk.Label(
            outer,
            textvariable=self.status_var,
            relief="sunken",
            anchor="w",
        ).pack(fill="x", pady=(5, 0))

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.log(f"应用已启动；项目数据目录：{DATA_ROOT}")

    def run(self) -> None:
        self.root.mainloop()

    def run_async(
        self,
        label: str,
        function: Callable[[], Any],
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self.status_var.set(f"正在执行：{label}")
        self.log(f"开始：{label}")

        def worker() -> None:
            try:
                result = function()
            except Exception as exc:
                details = traceback.format_exc()
                error = exc
                self.root.after(
                    0,
                    lambda caught=error: self._task_failed(
                        label, caught, details, on_error
                    ),
                )
            else:
                self.root.after(
                    0,
                    lambda: self._task_succeeded(label, result, on_success),
                )

        threading.Thread(target=worker, daemon=True).start()

    def log(self, message: str) -> None:
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def close(self) -> None:
        path_recording = (
            self.trajectory_tab.path_recording
            if hasattr(self, "trajectory_tab")
            else None
        )
        if self.stills_tab.recording or (
            path_recording is not None and path_recording.active
        ):
            if not messagebox.askyesno(
                "录像仍在进行",
                "OBS 录像仍在进行。确定退出？Pose logger 会停止，但请在 OBS 中确认录像状态。",
                parent=self.root,
            ):
                return
        self._save_settings()
        self.system_tab.close()
        self.stills_tab.close()
        self.trajectory_tab.close()
        self.analysis_tab.close()
        self.root.destroy()

    def _save_settings(self) -> None:
        merged = load_settings()
        merged["scene_id"] = self.scene_var.get()
        merged.update(self.points_tab.settings_payload())
        merged.update(self.stills_tab.settings_payload())
        trajectory_settings = self.trajectory_tab.settings_payload()
        merged["trajectory"] = trajectory_settings["trajectory"]
        merged.update(self.analysis_tab.settings_payload())
        save_settings(merged)

    def _task_succeeded(
        self,
        label: str,
        result: Any,
        callback: Callable[[Any], None] | None,
    ) -> None:
        self.status_var.set(f"完成：{label}")
        if callback is not None:
            callback(result)
        self.log(f"完成：{label}")

    def _task_failed(
        self,
        label: str,
        exc: Exception,
        details: str,
        callback: Callable[[Exception], None] | None,
    ) -> None:
        self.status_var.set(f"失败：{label} — {exc}")
        self.log(f"失败：{label} — {exc}")
        if callback is not None:
            callback(exc)
        if hasattr(self, "stills_tab"):
            self.stills_tab.task_failed()
        messagebox.showerror(label, str(exc), parent=self.root)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("TNotebook.Tab", padding=(14, 7))
