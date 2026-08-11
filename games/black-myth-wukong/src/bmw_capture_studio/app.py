from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import os
from pathlib import Path
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from .bridge import PoseUnavailableError, UuuPoseBridge
from .capture_runner import CaptureRunResult, CaptureRunner
from .connection import ConnectionReport, probe_connection
from .files import load_points, save_points
from .input_control import ClosedLoopMover
from .models import CameraPose, CapturePoint
from .paths import (
    CAPTURES_DIR,
    POINT_FILES_DIR,
    TRAJECTORY_FILES_DIR,
    ensure_directories,
)
from .screen_capture import enable_dpi_awareness, focus_game_window
from .settings import load_settings, save_settings
from .uuu import (
    UuuIntegrationError,
    find_game_pid,
    inject_bridge,
    integration_status,
    launch_uuu_client,
)


BG = "#10151c"
CARD = "#18202a"
CARD_ALT = "#202a36"
TEXT = "#ecf2f8"
MUTED = "#94a4b8"
ACCENT = "#d69b3c"
ACCENT_ACTIVE = "#ebb55b"
GOOD = "#52c68a"
BAD = "#ef6b73"


class UserActionRequired(RuntimeError):
    """Expected workflow state that should guide the user, not show an error."""


class CaptureStudioApp:
    def __init__(self, root: tk.Tk) -> None:
        ensure_directories()
        self.root = root
        self.root.title("黑神话：悟空 · UUU 相机采集")
        self.root.geometry("1100x980")
        self.root.minsize(980, 850)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.settings = load_settings()
        self.bridge = UuuPoseBridge()
        self.current_pose: CameraPose | None = None
        self.points: list[CapturePoint] = []
        self.trajectory_points: list[CapturePoint] = []
        self.trajectory_path: Path | None = None
        self.stop_event = threading.Event()
        self.capture_thread: threading.Thread | None = None
        self.capture_busy = False
        self.connection_report: ConnectionReport | None = None
        self.last_connection_code: str | None = None
        self.status_refresh_inflight = False
        self.closing = False

        self.status_var = tk.StringVar(value="等待连接游戏")
        self.status_detail_var = tk.StringVar(
            value="先启动游戏，再按顺序完成 1 → 2 → UUU Inject → Insert"
        )
        self.uuu_dir_var = tk.StringVar(value=str(self.settings["uuu_dir"]))
        self.pose_var = tk.StringVar(value="X --    Y --    Z --")
        self.angle_var = tk.StringVar(value="Yaw --°    Pitch --°    Roll --°    FOV --°")
        self.camera_state_var = tk.StringVar(value="Pose 未连接")
        self.points_count_var = tk.StringVar(value="0 个点位")
        self.trajectory_var = tk.StringVar(value="尚未 Load 轨迹文件")
        self.progress_text_var = tk.StringVar(value="空闲")

        self._configure_style()
        self._build_ui()
        self._poll_pose()
        self.refresh_status()

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("AltCard.TFrame", background=CARD_ALT)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Card.TLabel", background=CARD, foreground=TEXT)
        style.configure("Muted.Card.TLabel", background=CARD, foreground=MUTED)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Microsoft YaHei UI", 19, "bold"))
        style.configure("Subtitle.TLabel", background=BG, foreground=MUTED)
        style.configure("Section.Card.TLabel", background=CARD, foreground=TEXT, font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Pose.Card.TLabel", background=CARD, foreground=TEXT, font=("Consolas", 12, "bold"))
        style.configure("State.Card.TLabel", background=CARD, foreground=ACCENT)
        style.configure("TButton", background=CARD_ALT, foreground=TEXT, borderwidth=0, padding=(11, 8))
        style.map("TButton", background=[("active", "#2a3745"), ("disabled", "#151b22")], foreground=[("disabled", "#637083")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#15100a", font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", ACCENT_ACTIVE), ("disabled", "#6e5733")])
        style.configure("Danger.TButton", background="#803c45", foreground=TEXT)
        style.map("Danger.TButton", background=[("active", "#9d4b56")])
        style.configure("TEntry", fieldbackground="#111821", foreground=TEXT, insertcolor=TEXT, bordercolor="#344151", padding=7)
        style.configure("Treeview", background="#111821", fieldbackground="#111821", foreground=TEXT, rowheight=27, borderwidth=0)
        style.configure("Treeview.Heading", background="#253140", foreground=TEXT, relief="flat", padding=(6, 7))
        style.map("Treeview", background=[("selected", "#69512d")], foreground=[("selected", "#ffffff")])
        style.configure("Horizontal.TProgressbar", troughcolor="#111821", background=ACCENT, bordercolor="#111821", lightcolor=ACCENT, darkcolor=ACCENT)

    def _build_ui(self) -> None:
        shell = ttk.Frame(self.root, padding=(22, 18, 22, 16))
        shell.pack(fill="both", expand=True)

        header = ttk.Frame(shell)
        header.pack(fill="x")
        ttk.Label(header, text="黑神话相机采集", style="Title.TLabel").pack(side="left")
        ttk.Label(
            header,
            text="UUU Pose · Points · Frames",
            style="Subtitle.TLabel",
        ).pack(side="left", padx=(14, 0), pady=(8, 0))

        setup = ttk.Frame(shell, style="Card.TFrame", padding=14)
        setup.pack(fill="x", pady=(15, 10))
        setup.columnconfigure(1, weight=1)
        ttk.Label(setup, text="UUU 文件夹", style="Muted.Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.uuu_entry = ttk.Entry(setup, textvariable=self.uuu_dir_var)
        self.uuu_entry.grid(row=0, column=1, sticky="ew")
        ttk.Button(setup, text="浏览", command=self.browse_uuu).grid(row=0, column=2, padx=(8, 12))
        self.prepare_button = ttk.Button(setup, text="1  准备位姿桥", style="Accent.TButton", command=self.prepare_bridge)
        self.prepare_button.grid(row=0, column=3, padx=(0, 8))
        self.open_uuu_button = ttk.Button(setup, text="2  打开 UUU", command=self.open_uuu)
        self.open_uuu_button.grid(row=0, column=4, padx=(0, 8))
        ttk.Button(setup, text="刷新", command=self.refresh_status).grid(row=0, column=5)

        status_row = ttk.Frame(setup, style="Card.TFrame")
        status_row.grid(row=1, column=0, columnspan=6, sticky="ew", pady=(12, 0))
        self.status_dot = tk.Canvas(status_row, width=12, height=12, bg=CARD, highlightthickness=0)
        self.status_dot.pack(side="left", padx=(2, 8))
        self.status_dot_id = self.status_dot.create_oval(2, 2, 10, 10, fill=ACCENT, outline="")
        ttk.Label(status_row, textvariable=self.status_var, style="Section.Card.TLabel").pack(side="left")
        ttk.Label(status_row, textvariable=self.status_detail_var, style="Muted.Card.TLabel").pack(side="left", padx=(14, 0))

        pose_card = ttk.Frame(shell, style="Card.TFrame", padding=(15, 11))
        pose_card.pack(fill="x", pady=(0, 10))
        ttk.Label(pose_card, text="实时位姿", style="Section.Card.TLabel").pack(side="left", padx=(0, 18))
        ttk.Label(pose_card, textvariable=self.pose_var, style="Pose.Card.TLabel").pack(side="left")
        ttk.Label(pose_card, textvariable=self.angle_var, style="Pose.Card.TLabel").pack(side="left", padx=(26, 0))
        ttk.Label(pose_card, textvariable=self.camera_state_var, style="State.Card.TLabel").pack(side="right")

        body = ttk.Frame(shell)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        points_card = ttk.Frame(body, style="Card.TFrame", padding=13)
        points_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        points_card.columnconfigure(0, weight=1)
        points_card.rowconfigure(2, weight=1)
        point_header = ttk.Frame(points_card, style="Card.TFrame")
        point_header.grid(row=0, column=0, sticky="ew")
        ttk.Label(point_header, text="点位列表", style="Section.Card.TLabel").pack(side="left")
        ttk.Label(point_header, textvariable=self.points_count_var, style="Muted.Card.TLabel").pack(side="right")

        point_buttons = ttk.Frame(points_card, style="Card.TFrame")
        point_buttons.grid(row=1, column=0, sticky="ew", pady=(10, 10))
        self.record_point_button = ttk.Button(point_buttons, text="记录当前点位", style="Accent.TButton", command=self.record_point, state="disabled")
        self.record_point_button.pack(side="left")
        ttk.Button(point_buttons, text="Load 点位", command=self.load_point_file).pack(side="left", padx=(8, 0))
        ttk.Button(point_buttons, text="导出", command=self.export_points).pack(side="left", padx=(8, 0))
        ttk.Button(point_buttons, text="删除", command=self.delete_selected_points).pack(side="right")

        columns = ("index", "label", "x", "y", "z", "yaw", "pitch", "fov")
        self.point_tree = ttk.Treeview(points_card, columns=columns, show="headings", selectmode="extended")
        headings = {
            "index": "#",
            "label": "名称",
            "x": "X",
            "y": "Y",
            "z": "Z",
            "yaw": "Yaw",
            "pitch": "Pitch",
            "fov": "FOV",
        }
        widths = {"index": 42, "label": 105, "x": 78, "y": 78, "z": 78, "yaw": 66, "pitch": 66, "fov": 58}
        for column in columns:
            self.point_tree.heading(column, text=headings[column])
            self.point_tree.column(column, width=widths[column], minwidth=42, anchor="center", stretch=column == "label")
        self.point_tree.grid(row=2, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(points_card, orient="vertical", command=self.point_tree.yview)
        scrollbar.grid(row=2, column=1, sticky="ns")
        self.point_tree.configure(yscrollcommand=scrollbar.set)

        actions = ttk.Frame(body, style="Card.TFrame", padding=14)
        actions.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        actions.columnconfigure(0, weight=1)
        ttk.Label(actions, text="采集", style="Section.Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(actions, text="按点位文件逐点移动并保存原始游戏客户区画面。", style="Muted.Card.TLabel", wraplength=340).grid(row=1, column=0, sticky="w", pady=(5, 11))
        self.capture_points_button = ttk.Button(actions, text="按点位采集照片", style="Accent.TButton", command=lambda: self.start_capture("points"), state="disabled")
        self.capture_points_button.grid(row=2, column=0, sticky="ew")

        ttk.Separator(actions, orient="horizontal").grid(row=3, column=0, sticky="ew", pady=15)
        ttk.Label(actions, text="轨迹文件", style="Section.Card.TLabel").grid(row=4, column=0, sticky="w")
        ttk.Label(actions, textvariable=self.trajectory_var, style="Muted.Card.TLabel", wraplength=340).grid(row=5, column=0, sticky="w", pady=(5, 9))
        ttk.Button(actions, text="Load 轨迹", command=self.load_trajectory_file).grid(row=6, column=0, sticky="ew")
        self.capture_trajectory_button = ttk.Button(actions, text="按轨迹样本采集", style="Accent.TButton", command=lambda: self.start_capture("trajectory"), state="disabled")
        self.capture_trajectory_button.grid(row=7, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(actions, text="说明：当前为低频闭环逐样本采集；电影级实时插值仍需原生 setPose 桥。", style="Muted.Card.TLabel", wraplength=340).grid(row=8, column=0, sticky="w", pady=(9, 0))

        progress_frame = ttk.Frame(actions, style="Card.TFrame")
        progress_frame.grid(row=9, column=0, sticky="ew", pady=(16, 0))
        progress_frame.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(progress_frame, mode="determinate", maximum=1)
        self.progress.grid(row=0, column=0, sticky="ew")
        ttk.Label(progress_frame, textvariable=self.progress_text_var, style="Muted.Card.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.stop_button = ttk.Button(progress_frame, text="停止采集", style="Danger.TButton", command=self.stop_capture, state="disabled")
        self.stop_button.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(progress_frame, text="打开采集目录", command=lambda: os.startfile(CAPTURES_DIR)).grid(row=3, column=0, sticky="ew", pady=(8, 0))

        log_card = ttk.Frame(shell, style="Card.TFrame", padding=(12, 8))
        log_card.pack(fill="x", pady=(10, 0))
        self.log_text = tk.Text(log_card, height=4, bg="#0c1117", fg=MUTED, insertbackground=TEXT, relief="flat", borderwidth=0, font=("Microsoft YaHei UI", 9), padx=8, pady=7, state="disabled")
        self.log_text.pack(fill="x")
        self.log("工具已启动。建议使用无边框窗口模式，避免独占全屏截图为黑屏。")

    def browse_uuu(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.uuu_dir_var.get(), title="选择 UUU_v5.8.21 文件夹")
        if selected:
            self.uuu_dir_var.set(selected)
            self._persist_settings()

    def _persist_settings(self) -> None:
        self.settings["uuu_dir"] = self.uuu_dir_var.get().strip()
        save_settings(self.settings)

    def prepare_bridge(self) -> None:
        def work() -> dict[str, object]:
            before = integration_status()
            if not before.get("game_running"):
                raise UserActionRequired("请先启动《黑神话：悟空》并进入游戏画面。")
            if not before.get("module_scan_ok", True):
                raise UuuIntegrationError(str(before.get("message", "无法检查游戏模块")))
            if before.get("uuu_loaded") and not before.get("bridge_loaded"):
                raise UserActionRequired(
                    "UUU 已经先注入。为避免假连接，程序已阻止补注入；请彻底退出游戏后重试。"
                )
            pid = find_game_pid()
            return inject_bridge(pid)

        def success(result: dict[str, object]) -> None:
            self.log(f"位姿桥已准备：PID {result['pid']}")
            self.refresh_status()

        self._background_action("正在准备位姿桥…", work, success)

    def open_uuu(self) -> None:
        try:
            report = probe_connection(self.bridge)
            if report.code != "uuu_needed":
                raise UserActionRequired(report.detail)
            self._persist_settings()
            result = launch_uuu_client(self.uuu_dir_var.get())
            if result.get("already_running"):
                self.log("UUU Client 已经在运行，请切换到它并选择当前黑神话进程 Inject。")
            else:
                self.log("UUU Client 已打开：选择黑神话进程并点击 Inject。")
            self.status_detail_var.set("在 UUU 中选择游戏进程并 Inject，然后按 Insert")
        except UserActionRequired as exc:
            self._show_guidance(str(exc))
        except Exception as exc:
            self._show_error("打开 UUU 失败", exc)

    def refresh_status(self) -> None:
        if self.status_refresh_inflight or self.closing:
            return
        self.status_refresh_inflight = True

        def work() -> ConnectionReport:
            return probe_connection(self.bridge)

        def success(report: ConnectionReport) -> None:
            self.status_refresh_inflight = False
            self._apply_connection_report(report)

        def finished_with_error(exc: Exception) -> None:
            self.status_refresh_inflight = False
            self.log(f"状态检查失败：{exc}")
            if not self.closing:
                self.root.after(1000, self.refresh_status)

        self._background_action(
            None,
            work,
            success,
            quiet=True,
            on_error=finished_with_error,
        )

    def _apply_connection_report(self, report: ConnectionReport) -> None:
        self.connection_report = report
        colors = {
            "success": GOOD,
            "warning": ACCENT,
            "error": BAD,
        }
        self._set_status(report.title, report.detail, colors.get(report.level, ACCENT))
        if report.code != self.last_connection_code:
            self.log(f"连接状态：{report.title}；{report.detail}")
            self.last_connection_code = report.code

        self.prepare_button.configure(
            state="normal" if report.code == "bridge_needed" else "disabled"
        )
        self.open_uuu_button.configure(
            state="normal" if report.code == "uuu_needed" else "disabled"
        )
        pose_available = report.pose is not None
        self.record_point_button.configure(
            state="normal" if pose_available and not self.capture_busy else "disabled"
        )
        if not self.capture_busy:
            capture_state = "normal" if report.ready else "disabled"
            self.capture_points_button.configure(state=capture_state)
            self.capture_trajectory_button.configure(state=capture_state)
        if not pose_available:
            self.current_pose = None
            self.pose_var.set("X --    Y --    Z --")
            self.angle_var.set("Yaw --°    Pitch --°    Roll --°    FOV --°")
            self.camera_state_var.set("Pose 未连接")

        if not self.closing:
            self.root.after(1000, self.refresh_status)

    def _poll_pose(self) -> None:
        if self.closing:
            return
        live_pose_codes = {"camera_off", "camera_locked", "ready"}
        if (
            self.connection_report is None
            or self.connection_report.code not in live_pose_codes
        ):
            self.root.after(250, self._poll_pose)
            return
        try:
            pose = self.bridge.read_pose()
            self.current_pose = pose
            self.pose_var.set(f"X {pose.x: .3f}    Y {pose.y: .3f}    Z {pose.z: .3f}")
            self.angle_var.set(
                f"Yaw {pose.yaw_degrees: .2f}°    Pitch {pose.pitch_degrees: .2f}°    "
                f"Roll {pose.roll_degrees: .2f}°    FOV {pose.fov_degrees: .2f}°"
            )
            if pose.camera_enabled:
                state = "Camera ON"
                if pose.movement_locked:
                    state += " · Locked"
                self.camera_state_var.set(state)
            else:
                self.camera_state_var.set("Camera OFF · 按 Insert")
        except (PoseUnavailableError, OSError, ValueError):
            self.current_pose = None
            if self.connection_report is None or self.connection_report.pose is None:
                self.camera_state_var.set("Pose 未连接")
        self.root.after(250, self._poll_pose)

    def record_point(self) -> None:
        if self.connection_report is None or self.connection_report.pose is None:
            self._show_guidance("Pose 尚未连接，按顶部状态提示完成连接后再记录点位。")
            return
        try:
            pose = self.bridge.read_pose()
        except PoseUnavailableError as exc:
            self._show_guidance(str(exc))
            return
        index = len(self.points) + 1
        self.points.append(
            CapturePoint(index=index, label=f"point_{index:04d}", pose=pose)
        )
        self._refresh_point_tree()
        self.log(f"已记录 point_{index:04d}")

    def load_point_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Load 点位文件",
            initialdir=POINT_FILES_DIR,
            filetypes=[("点位文件", "*.json *.csv"), ("JSON", "*.json"), ("CSV", "*.csv")],
        )
        if not selected:
            return
        try:
            self.points = load_points(selected)
            self._refresh_point_tree()
            self.log(f"已 Load {len(self.points)} 个点位：{selected}")
        except Exception as exc:
            self._show_error("Load 点位失败", exc)

    def export_points(self) -> None:
        if not self.points:
            messagebox.showinfo("没有点位", "请先记录或 Load 点位。")
            return
        default_name = f"bmw_points_{datetime.now():%Y%m%d_%H%M%S}.json"
        selected = filedialog.asksaveasfilename(
            title="导出点位",
            initialdir=POINT_FILES_DIR,
            initialfile=default_name,
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("CSV", "*.csv")],
        )
        if not selected:
            return
        try:
            target = save_points(selected, self.points, kind="points")
            self.log(f"点位已导出：{target}")
        except Exception as exc:
            self._show_error("导出失败", exc)

    def delete_selected_points(self) -> None:
        selected = set(self.point_tree.selection())
        if not selected:
            return
        keep = [point for item, point in zip(self.point_tree.get_children(), self.points) if item not in selected]
        self.points = [replace(point, index=index) for index, point in enumerate(keep, 1)]
        self._refresh_point_tree()

    def _refresh_point_tree(self) -> None:
        self.point_tree.delete(*self.point_tree.get_children())
        for point in self.points:
            pose = point.pose
            self.point_tree.insert(
                "",
                "end",
                values=(
                    point.index,
                    point.label,
                    f"{pose.x:.2f}",
                    f"{pose.y:.2f}",
                    f"{pose.z:.2f}",
                    f"{pose.yaw_degrees:.1f}",
                    f"{pose.pitch_degrees:.1f}",
                    f"{pose.fov_degrees:.1f}",
                ),
            )
        self.points_count_var.set(f"{len(self.points)} 个点位")

    def load_trajectory_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Load 轨迹文件",
            initialdir=TRAJECTORY_FILES_DIR,
            filetypes=[("轨迹文件", "*.json *.csv"), ("JSON", "*.json"), ("CSV", "*.csv")],
        )
        if not selected:
            return
        try:
            self.trajectory_points = load_points(selected)
            self.trajectory_path = Path(selected)
            self.trajectory_var.set(f"{self.trajectory_path.name} · {len(self.trajectory_points)} 个样本")
            self.log(f"轨迹已 Load：{selected}")
        except Exception as exc:
            self._show_error("Load 轨迹失败", exc)

    def start_capture(self, mode: str) -> None:
        if self.capture_thread is not None and self.capture_thread.is_alive():
            messagebox.showinfo("正在采集", "请先等待当前任务结束或点击停止采集。")
            return
        points = self.points if mode == "points" else self.trajectory_points
        if not points:
            messagebox.showinfo("没有数据", "请先记录/Load 点位，或 Load 轨迹文件。")
            return
        if self.connection_report is None or not self.connection_report.ready:
            detail = (
                self.connection_report.detail
                if self.connection_report is not None
                else "正在检查连接状态，请稍候。"
            )
            self._show_guidance(detail)
            return
        try:
            pid = find_game_pid()
            pose = self.bridge.read_pose()
            if not pose.camera_enabled:
                raise RuntimeError("UUU 相机未启用，请回到游戏按 Insert")
        except (PoseUnavailableError, UuuIntegrationError, RuntimeError) as exc:
            self._show_guidance(str(exc))
            return

        self.stop_event.clear()
        self._set_capture_busy(True)
        self.progress.configure(maximum=len(points), value=0)
        self.progress_text_var.set(f"准备采集 0 / {len(points)}")
        mover = ClosedLoopMover(
            self.bridge,
            position_tolerance=float(self.settings["position_tolerance"]),
            angle_tolerance=float(self.settings["angle_tolerance_degrees"]),
            fov_tolerance=float(self.settings["fov_tolerance_degrees"]),
            move_pulse_sec=float(self.settings["move_pulse_sec"]),
            rotate_pulse_sec=float(self.settings["rotate_pulse_sec"]),
            max_seconds=float(self.settings["max_move_seconds"]),
            focus_game=lambda: focus_game_window(pid),
        )
        runner = CaptureRunner(
            bridge=self.bridge,
            mover=mover,
            pid=pid,
            settle_seconds=float(self.settings["capture_interval_sec"]),
            image_format=str(self.settings["screenshot_format"]),
        )

        def progress(done: int, total: int, message: str) -> None:
            self.root.after(0, lambda: self._update_progress(done, total, message))

        def worker() -> None:
            try:
                result = runner.run(
                    points,
                    CAPTURES_DIR,
                    mode=mode,
                    stop_requested=self.stop_event.is_set,
                    on_progress=progress,
                    on_log=self.log,
                    respect_timestamps=False,
                )
            except Exception as exc:
                self.root.after(0, lambda error=exc: self._capture_failed(error))
            else:
                self.root.after(0, lambda value=result: self._capture_finished(value))

        self.capture_thread = threading.Thread(target=worker, name="bmw-capture", daemon=True)
        self.capture_thread.start()
        self.log(f"开始{('点位' if mode == 'points' else '轨迹')}采集，共 {len(points)} 帧。")

    def stop_capture(self) -> None:
        self.stop_event.set()
        self.progress_text_var.set("正在停止…")
        self.log("已请求停止，将在当前按键脉冲结束后退出。")

    def _update_progress(self, done: int, total: int, message: str) -> None:
        self.progress.configure(maximum=max(total, 1), value=done)
        self.progress_text_var.set(f"{done} / {total} · {message}")

    def _capture_finished(self, result: CaptureRunResult) -> None:
        self._set_capture_busy(False)
        status = "已停止" if result.stopped else "采集完成"
        self.progress_text_var.set(f"{status} · {result.captured_count} / {result.requested_count}")
        self.log(f"{status}：{result.session_dir}")
        if not result.stopped:
            messagebox.showinfo("采集完成", f"已保存 {result.captured_count} 张图片。\n\n{result.session_dir}")

    def _capture_failed(self, exc: Exception) -> None:
        self._set_capture_busy(False)
        self.progress_text_var.set("采集失败")
        self._show_error("采集失败", exc)

    def _set_capture_busy(self, busy: bool) -> None:
        self.capture_busy = busy
        ready = self.connection_report is not None and self.connection_report.ready
        state = "disabled" if busy or not ready else "normal"
        self.capture_points_button.configure(state=state)
        self.capture_trajectory_button.configure(state=state)
        pose_available = self.connection_report is not None and self.connection_report.pose is not None
        self.record_point_button.configure(
            state="disabled" if busy or not pose_available else "normal"
        )
        self.stop_button.configure(state="normal" if busy else "disabled")

    def _background_action(
        self,
        label: str | None,
        work: Callable[[], object],
        success: Callable[[object], None],
        *,
        quiet: bool = False,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        if label:
            self.status_detail_var.set(label)

        def runner() -> None:
            try:
                value = work()
            except Exception as exc:
                if on_error is not None:
                    self.root.after(0, lambda error=exc: on_error(error))
                elif isinstance(exc, UserActionRequired):
                    self.root.after(0, lambda error=exc: self._show_guidance(str(error)))
                elif not quiet:
                    self.root.after(0, lambda error=exc: self._show_error("操作失败", error))
            else:
                self.root.after(0, lambda: success(value))

        threading.Thread(target=runner, daemon=True).start()

    def _set_status(self, title: str, detail: str, color: str) -> None:
        self.status_var.set(title)
        self.status_detail_var.set(detail)
        self.status_dot.itemconfigure(self.status_dot_id, fill=color)

    def log(self, message: str) -> None:
        if threading.current_thread() is not threading.main_thread():
            self.root.after(0, lambda: self.log(message))
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] {message}\n")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 160:
            self.log_text.delete("1.0", "40.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _show_error(self, title: str, exc: Exception) -> None:
        self.log(f"{title}：{exc}")
        messagebox.showerror(title, str(exc))

    def _show_guidance(self, message: str) -> None:
        self.log(f"操作提示：{message}")
        self.status_detail_var.set(message)

    def _on_close(self) -> None:
        if self.capture_thread is not None and self.capture_thread.is_alive():
            if not messagebox.askyesno("采集进行中", "采集仍在进行，确定停止并退出吗？"):
                return
            self.stop_event.set()
        self.closing = True
        self.bridge.close()
        self.root.destroy()


def run_app() -> None:
    enable_dpi_awareness()
    root = tk.Tk()
    CaptureStudioApp(root)
    root.mainloop()
