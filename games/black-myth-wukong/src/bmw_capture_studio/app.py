from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
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
from .files import load_points, load_trajectories, save_points
from .global_hotkey import F8_VK, GlobalHotkey
from .input_control import ClosedLoopMover
from .models import CameraPose, CapturePoint, ImportedTrajectory
from .obs_bridge import OBSBridge
from .paths import (
    ACTIVE_POINT_MAP_PATH,
    POINT_FILES_DIR,
    PROJECT_ROOT,
    STATIC_CAPTURES_DIR,
    TRAJECTORY_CAPTURES_DIR,
    TRAJECTORY_FILES_DIR,
    ensure_directories,
)
from .platform_support import open_path
from .screen_capture import enable_dpi_awareness, focus_game_window, foreground_process_id
from .settings import load_settings, save_settings
from .still_scan import (
    build_22_view_plan,
    find_latest_resumable_static_run,
    view_pattern_manifest,
)
from .trajectory_capture import BatchTrajectoryRecorder, find_latest_resumable_batch
from .trajectory_catalog import build_trajectory_choice_map, discover_trajectory_files
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
    def __init__(
        self,
        root: tk.Tk,
        *,
        trajectory_file: str | Path | None = None,
    ) -> None:
        ensure_directories()
        self.root = root
        self.root.title("黑神话：悟空 · UUU 相机采集")
        self.root.geometry("1180x840")
        self.root.minsize(1000, 760)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.settings = load_settings()
        self.always_on_top_var = tk.BooleanVar(
            value=bool(self.settings.get("always_on_top", True))
        )
        self.pin_text_var = tk.StringVar()
        self.bridge = UuuPoseBridge()
        self.current_pose: CameraPose | None = None
        self.points: list[CapturePoint] = []
        self.point_map_path: Path | None = None
        self.point_map_dirty = False
        self.trajectories: list[ImportedTrajectory] = []
        self.trajectory_path: Path | None = None
        self.trajectory_choice_paths: dict[str, Path] = {}
        requested = trajectory_file or os.environ.get("BMW_TRAJECTORY_FILE")
        self.requested_trajectory_path = Path(requested).expanduser() if requested else None
        self.batch_recorder: BatchTrajectoryRecorder | None = None
        self.latest_capture_output_dir: Path = TRAJECTORY_CAPTURES_DIR.resolve()
        self.latest_static_output_dir: Path = STATIC_CAPTURES_DIR.resolve()
        self.static_resume_info: dict[str, object] | None = None
        self.static_progress_total = 0
        self.static_progress_offset = 0
        self.stop_event = threading.Event()
        self.capture_thread: threading.Thread | None = None
        self.capture_busy = False
        self.active_capture_kind: str | None = None
        self.connection_report: ConnectionReport | None = None
        self.last_connection_code: str | None = None
        self.status_refresh_inflight = False
        self.closing = False
        self.record_point_hotkey = GlobalHotkey(F8_VK)

        self.status_var = tk.StringVar(value="等待连接游戏")
        self.status_detail_var = tk.StringVar(
            value="先启动游戏，再按顺序完成 1 → 2 → UUU Inject → Insert"
        )
        self.uuu_dir_var = tk.StringVar(value=str(self.settings["uuu_dir"]))
        self.pose_var = tk.StringVar(value="X --    Y --    Z --")
        self.angle_var = tk.StringVar(value="Yaw --°    Pitch --°    Roll --°    FOV --°")
        self.camera_state_var = tk.StringVar(value="Pose 未连接")
        self.points_count_var = tk.StringVar(value="0 个空间点 · 预计 0 张")
        self.record_hotkey_status_var = tk.StringVar(value="游戏内 F8：正在注册…")
        self.point_map_var = tk.StringVar(value="尚未记录或 Load 点位图")
        self.static_start_point_var = tk.IntVar(value=1)
        self.static_progress_var = tk.DoubleVar(value=0)
        self.static_progress_text_var = tk.StringVar(value="静态采集：空闲")
        self.static_output_var = tk.StringVar(value="输出根目录：still_captures")
        self.static_resume_var = tk.StringVar(value="没有可继续的静态采集任务")
        self.trajectory_var = tk.StringVar(value="尚未 Load 轨迹文件")
        self.trajectory_choice_var = tk.StringVar(value="")
        self.trajectory_index_var = tk.IntVar(value=1)
        self.scene_id_var = tk.StringVar(value=str(self.settings["scene_id"]))
        self.obs_host_var = tk.StringVar(value=str(self.settings["obs_host"]))
        self.obs_port_var = tk.StringVar(value=str(self.settings["obs_port"]))
        self.obs_password_var = tk.StringVar(value=os.environ.get("BMW_OBS_PASSWORD", ""))
        self.task_progress_var = tk.DoubleVar(value=0)
        self.frame_progress_var = tk.DoubleVar(value=0)
        self.task_progress_text_var = tk.StringVar(value="任务进度：空闲")
        self.frame_progress_text_var = tk.StringVar(value="当前轨迹：空闲")
        self.output_var = tk.StringVar(value="输出根目录：trajectory_captures")

        self._configure_style()
        self._build_ui()
        self._refresh_latest_output_dirs()
        self._apply_always_on_top()
        self._load_active_point_map()
        self._refresh_static_resume()
        self._start_record_point_hotkey()
        self._refresh_trajectory_choices(preferred=self.requested_trajectory_path)
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
        style.configure("AltCard.TLabel", background=CARD_ALT, foreground=TEXT)
        style.configure("Muted.AltCard.TLabel", background=CARD_ALT, foreground=MUTED)
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
        style.configure("Compact.TButton", background=CARD_ALT, foreground=TEXT, borderwidth=0, padding=(7, 4))
        style.map("Compact.TButton", background=[("active", "#2a3745"), ("disabled", "#151b22")], foreground=[("disabled", "#637083")])
        style.configure("CompactAccent.TButton", background=ACCENT, foreground="#15100a", borderwidth=0, padding=(7, 4), font=("Microsoft YaHei UI", 9, "bold"))
        style.map("CompactAccent.TButton", background=[("active", ACCENT_ACTIVE), ("disabled", "#6e5733")])
        style.configure("TEntry", fieldbackground="#111821", foreground=TEXT, insertcolor=TEXT, bordercolor="#344151", padding=7)
        style.configure(
            "TCombobox",
            fieldbackground="#111821",
            background=CARD_ALT,
            foreground=TEXT,
            arrowcolor=TEXT,
            bordercolor="#344151",
            padding=6,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", "#111821"), ("disabled", "#151b22")],
            foreground=[("readonly", TEXT), ("disabled", "#637083")],
        )
        self.root.option_add("*TCombobox*Listbox.background", "#111821")
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#69512d")
        style.configure("Treeview", background="#111821", fieldbackground="#111821", foreground=TEXT, rowheight=27, borderwidth=0)
        style.configure("Treeview.Heading", background="#253140", foreground=TEXT, relief="flat", padding=(6, 7))
        style.map("Treeview", background=[("selected", "#69512d")], foreground=[("selected", "#ffffff")])
        style.configure("Horizontal.TProgressbar", troughcolor="#111821", background=ACCENT, bordercolor="#111821", lightcolor=ACCENT, darkcolor=ACCENT)
        style.configure(
            "Capture.Horizontal.TProgressbar",
            troughcolor="#0b1016",
            background=ACCENT_ACTIVE,
            bordercolor="#344151",
            lightcolor=ACCENT_ACTIVE,
            darkcolor=ACCENT,
            thickness=12,
        )

    def _build_ui(self) -> None:
        # The complete studio is taller than a normal laptop viewport. Keep
        # the rightmost scrollbar attached to the whole page so the lower
        # trajectory/progress controls remain reachable; the action panel
        # below keeps its own scrollbar for its dense controls.
        viewport = ttk.Frame(self.root)
        viewport.pack(fill="both", expand=True)
        self.main_canvas = tk.Canvas(
            viewport,
            bg=BG,
            borderwidth=0,
            highlightthickness=0,
        )
        self.main_canvas.grid(row=0, column=0, sticky="nsew")
        self.main_scrollbar = ttk.Scrollbar(
            viewport,
            orient="vertical",
            command=self.main_canvas.yview,
        )
        self.main_scrollbar.grid(row=0, column=1, sticky="ns")
        viewport.columnconfigure(0, weight=1)
        viewport.rowconfigure(0, weight=1)
        self.main_canvas.configure(yscrollcommand=self.main_scrollbar.set)
        shell = ttk.Frame(self.main_canvas, padding=(18, 12, 18, 10))
        self.main_canvas_window = self.main_canvas.create_window(
            (0, 0),
            window=shell,
            anchor="nw",
        )
        shell.bind(
            "<Configure>",
            lambda _event: self.main_canvas.configure(
                scrollregion=self.main_canvas.bbox("all")
            ),
        )
        self.main_canvas.bind(
            "<Configure>",
            lambda event: self.main_canvas.itemconfigure(
                self.main_canvas_window,
                width=event.width,
            ),
        )

        header = ttk.Frame(shell)
        header.pack(fill="x")
        ttk.Label(header, text="黑神话相机采集", style="Title.TLabel").pack(side="left")
        ttk.Label(
            header,
            text="轨迹文件 → 自动定位 → 连续录像 → Pose 数据",
            style="Subtitle.TLabel",
        ).pack(side="left", padx=(14, 0), pady=(8, 0))
        self.pin_button = ttk.Button(
            header,
            textvariable=self.pin_text_var,
            command=self.toggle_always_on_top,
            style="Compact.TButton",
        )
        self.pin_button.pack(side="right", pady=(3, 0))

        setup = ttk.Frame(shell, style="Card.TFrame", padding=14)
        setup.pack(fill="x", pady=(10, 7))
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
        # Keep the recorded-point rows readable even when the lower capture
        # controls request more vertical space.
        points_card.rowconfigure(3, weight=1, minsize=190)
        point_header = ttk.Frame(points_card, style="Card.TFrame")
        point_header.grid(row=0, column=0, sticky="ew")
        ttk.Label(point_header, text="静态 22 方向采集", style="Section.Card.TLabel").pack(side="left")
        ttk.Label(point_header, textvariable=self.points_count_var, style="Muted.Card.TLabel").pack(side="right")
        ttk.Label(
            point_header,
            textvariable=self.record_hotkey_status_var,
            style="Muted.Card.TLabel",
        ).pack(side="left", padx=(14, 0))
        ttk.Label(
            points_card,
            text="记录或 Load 空间点位图；每点自动采集水平 8 + 上视 6 + 下视 6 + 顶/底 2 张。",
            style="Muted.Card.TLabel",
            wraplength=520,
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))

        point_buttons = ttk.Frame(points_card, style="Card.TFrame")
        point_buttons.grid(row=2, column=0, sticky="ew", pady=(8, 8))
        for column in range(3):
            point_buttons.columnconfigure(column, weight=1)
        self.record_point_button = ttk.Button(point_buttons, text="记录当前点位", style="Accent.TButton", command=self.record_point, state="disabled")
        self.record_point_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(point_buttons, text="Load 点位图", command=self.load_point_file).grid(row=0, column=1, sticky="ew", padx=3)
        ttk.Button(
            point_buttons,
            text="打开点位文件",
            command=self.open_active_point_map,
        ).grid(row=0, column=2, sticky="ew", padx=(3, 0))
        ttk.Button(point_buttons, text="删除所选点位", command=self.delete_selected_points).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(5, 0))

        columns = ("index", "label", "x", "y", "z", "yaw", "pitch", "fov")
        self.point_tree = ttk.Treeview(
            points_card,
            columns=columns,
            show="headings",
            selectmode="extended",
            height=6,
        )
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
        widths = {
            "index": 36,
            "label": 92,
            "x": 70,
            "y": 70,
            "z": 70,
            "yaw": 58,
            "pitch": 58,
            "fov": 48,
        }
        for column in columns:
            self.point_tree.heading(column, text=headings[column])
            self.point_tree.column(column, width=widths[column], minwidth=42, anchor="center", stretch=column == "label")
        self.point_tree.grid(row=3, column=0, sticky="nsew")
        point_scrollbar = ttk.Scrollbar(points_card, orient="vertical", command=self.point_tree.yview)
        point_scrollbar.grid(row=3, column=1, sticky="ns")
        self.point_tree.configure(yscrollcommand=point_scrollbar.set)

        static_section = ttk.Frame(
            points_card,
            style="AltCard.TFrame",
            padding=(10, 8),
        )
        static_section.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        static_section.columnconfigure((0, 1), weight=1)
        ttk.Label(
            static_section,
            text="点位图 → 每点 22 方向原图",
            style="AltCard.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            static_section,
            textvariable=self.point_map_var,
            style="Muted.AltCard.TLabel",
            wraplength=280,
        ).grid(row=0, column=1, sticky="e")
        static_start = ttk.Frame(static_section, style="AltCard.TFrame")
        static_start.grid(row=1, column=0, sticky="w", pady=(7, 0))
        ttk.Label(static_start, text="从第", style="Muted.AltCard.TLabel").pack(side="left")
        self.static_start_point_spin = ttk.Spinbox(
            static_start,
            from_=1,
            to=1,
            textvariable=self.static_start_point_var,
            width=7,
        )
        self.static_start_point_spin.pack(side="left", padx=(6, 8))
        ttk.Label(
            static_start,
            text="个空间点开始 · 每点固定 22 张",
            style="Muted.AltCard.TLabel",
        ).pack(side="left")
        self.capture_points_button = ttk.Button(
            static_section,
            text="开始自动静态 22 方向采集",
            command=self.start_static_22_capture,
            style="CompactAccent.TButton",
            state="disabled",
        )
        self.capture_points_button.grid(
            row=1,
            column=1,
            sticky="ew",
            padx=(8, 0),
            pady=(7, 0),
        )
        self.static_progress = ttk.Progressbar(
            static_section,
            variable=self.static_progress_var,
            mode="determinate",
            maximum=1,
            style="Capture.Horizontal.TProgressbar",
        )
        self.static_progress.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        ttk.Label(
            static_section,
            textvariable=self.static_progress_text_var,
            style="Muted.AltCard.TLabel",
            wraplength=250,
        ).grid(row=3, column=0, sticky="w", pady=(3, 0))
        ttk.Label(
            static_section,
            textvariable=self.static_output_var,
            style="Muted.AltCard.TLabel",
            wraplength=250,
        ).grid(row=3, column=1, sticky="e", pady=(3, 0))
        ttk.Button(
            static_section,
            text="打开静态图片目录",
            command=self.open_static_output,
            style="Compact.TButton",
        ).grid(row=4, column=0, sticky="ew", padx=(0, 3), pady=(5, 0))
        self.static_stop_button = ttk.Button(
            static_section,
            text="停止静态采集",
            command=self.stop_capture,
            style="Compact.TButton",
            state="disabled",
        )
        self.static_stop_button.grid(row=4, column=1, sticky="ew", padx=(3, 0), pady=(5, 0))
        ttk.Label(
            static_section,
            textvariable=self.static_resume_var,
            style="Muted.AltCard.TLabel",
            wraplength=520,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(5, 0))
        self.static_resume_button = ttk.Button(
            static_section,
            text="继续上次静态采集",
            command=self.resume_static_22_capture,
            style="Compact.TButton",
            state="disabled",
        )
        self.static_resume_button.grid(
            row=6,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(4, 0),
        )

        actions_host = ttk.Frame(body, style="Card.TFrame")
        actions_host.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        actions_host.columnconfigure(0, weight=1)
        actions_host.rowconfigure(0, weight=1)

        self.actions_canvas = tk.Canvas(
            actions_host,
            bg=CARD,
            borderwidth=0,
            highlightthickness=0,
        )
        self.actions_canvas.grid(row=0, column=0, sticky="nsew")
        actions_scrollbar = ttk.Scrollbar(
            actions_host,
            orient="vertical",
            command=self.actions_canvas.yview,
        )
        actions_scrollbar.grid(row=0, column=1, sticky="ns")
        self.actions_canvas.configure(yscrollcommand=actions_scrollbar.set)

        actions = ttk.Frame(self.actions_canvas, style="Card.TFrame", padding=14)
        self.actions_canvas_window = self.actions_canvas.create_window(
            (0, 0),
            window=actions,
            anchor="nw",
        )
        actions.bind(
            "<Configure>",
            lambda _event: self.actions_canvas.configure(
                scrollregion=self.actions_canvas.bbox("all")
            ),
        )
        self.actions_canvas.bind(
            "<Configure>",
            lambda event: self.actions_canvas.itemconfigure(
                self.actions_canvas_window,
                width=event.width,
            ),
        )
        self.root.bind_all(
            "<MouseWheel>",
            self._scroll_actions_with_mousewheel,
            add="+",
        )
        actions.columnconfigure(0, weight=1)
        ttk.Label(actions, text="连续轨迹采集", style="Section.Card.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            actions,
            text="选择文件即自动加载。点击一次后自动定位首点、录像、保存 Pose，并连续进入下一条轨迹。",
            style="Muted.Card.TLabel",
            wraplength=350,
        ).grid(row=1, column=0, sticky="w", pady=(3, 8))

        trajectory_section = ttk.Frame(
            actions,
            style="AltCard.TFrame",
            padding=(10, 8),
        )
        trajectory_section.grid(row=2, column=0, sticky="ew")
        trajectory_section.columnconfigure((0, 1), weight=1)
        ttk.Label(
            trajectory_section,
            text="轨迹文件",
            style="AltCard.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 5))
        self.trajectory_combo = ttk.Combobox(
            trajectory_section,
            textvariable=self.trajectory_choice_var,
            values=(),
            state="readonly",
        )
        self.trajectory_combo.grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
        )
        self.trajectory_combo.bind("<<ComboboxSelected>>", self._on_trajectory_choice)
        ttk.Button(
            trajectory_section,
            text="浏览…",
            command=self.load_trajectory_file,
            style="Compact.TButton",
        ).grid(row=2, column=0, sticky="ew", padx=(0, 3), pady=(5, 0))
        ttk.Button(
            trajectory_section,
            text="刷新",
            command=self._refresh_trajectory_choices,
            style="Compact.TButton",
        ).grid(row=2, column=1, sticky="ew", padx=(3, 0), pady=(5, 0))
        ttk.Label(
            trajectory_section,
            textvariable=self.trajectory_var,
            style="Muted.AltCard.TLabel",
            wraplength=350,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(5, 0))

        capture_config = ttk.Frame(actions, style="Card.TFrame")
        capture_config.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        capture_config.columnconfigure(1, weight=1)
        ttk.Label(capture_config, text="从第", style="Muted.Card.TLabel").grid(row=0, column=0, sticky="w")
        self.trajectory_index_spin = ttk.Spinbox(capture_config, from_=1, to=1, textvariable=self.trajectory_index_var, width=7)
        self.trajectory_index_spin.grid(row=0, column=1, sticky="w", padx=(6, 8))
        ttk.Label(capture_config, text="条开始并连续到末尾", style="Muted.Card.TLabel").grid(row=0, column=2, columnspan=3, sticky="w")
        ttk.Label(capture_config, text="场景", style="Muted.Card.TLabel").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(capture_config, textvariable=self.scene_id_var, width=12).grid(row=1, column=1, columnspan=4, sticky="ew", padx=(6, 0), pady=(6, 0))
        ttk.Label(capture_config, text="OBS", style="Muted.Card.TLabel").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(capture_config, textvariable=self.obs_host_var, width=12).grid(row=2, column=1, columnspan=2, sticky="ew", padx=(6, 4), pady=(6, 0))
        ttk.Entry(capture_config, textvariable=self.obs_port_var, width=6).grid(row=2, column=3, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Label(capture_config, text="密码", style="Muted.Card.TLabel").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(capture_config, textvariable=self.obs_password_var, width=12, show="•").grid(row=3, column=1, columnspan=4, sticky="ew", pady=(6, 0))

        self.continuous_capture_button = ttk.Button(
            actions,
            text="开始连续采集（自动定位 + OBS + Pose）",
            style="CompactAccent.TButton",
            command=self.start_continuous_trajectory_capture,
            state="disabled",
        )
        self.continuous_capture_button.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        task_buttons = ttk.Frame(actions, style="Card.TFrame")
        task_buttons.grid(row=5, column=0, sticky="ew", pady=(5, 0))
        task_buttons.columnconfigure(0, weight=1)
        self.resume_capture_button = ttk.Button(task_buttons, text="继续最近未完成批次", command=self.resume_trajectory_capture, style="Compact.TButton")
        self.resume_capture_button.grid(row=0, column=0, sticky="ew")

        progress_frame = ttk.Frame(actions, style="Card.TFrame")
        progress_frame.grid(row=6, column=0, sticky="ew", pady=(7, 0))
        progress_frame.columnconfigure((0, 1), weight=1)
        ttk.Label(progress_frame, textvariable=self.output_var, style="Muted.Card.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        self.task_progress = ttk.Progressbar(progress_frame, variable=self.task_progress_var, mode="determinate", maximum=1, style="Capture.Horizontal.TProgressbar")
        self.task_progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Label(progress_frame, textvariable=self.task_progress_text_var, style="Muted.Card.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.frame_progress = ttk.Progressbar(progress_frame, variable=self.frame_progress_var, mode="determinate", maximum=1, style="Capture.Horizontal.TProgressbar")
        self.frame_progress.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Label(progress_frame, textvariable=self.frame_progress_text_var, style="Muted.Card.TLabel").grid(row=4, column=0, columnspan=2, sticky="w", pady=(2, 0))
        ttk.Button(progress_frame, text="打开输出目录", command=self.open_capture_output, style="Compact.TButton").grid(row=5, column=0, sticky="ew", padx=(0, 3), pady=(4, 0))
        self.stop_button = ttk.Button(progress_frame, text="停止采集", style="Compact.TButton", command=self.stop_capture, state="disabled")
        self.stop_button.grid(row=5, column=1, sticky="ew", padx=(3, 0), pady=(4, 0))

        log_card = ttk.Frame(shell, style="Card.TFrame", padding=(12, 8))
        log_card.pack(fill="x", pady=(10, 0))
        self.log_text = tk.Text(log_card, height=2, bg="#0c1117", fg=MUTED, insertbackground=TEXT, relief="flat", borderwidth=0, font=("Microsoft YaHei UI", 9), padx=8, pady=5, state="disabled")
        self.log_text.pack(fill="x")
        self.log("工具已启动。建议使用无边框窗口模式，避免独占全屏截图为黑屏。")

    def _scroll_actions_with_mousewheel(self, event: tk.Event) -> str | None:
        """Scroll the nested action panel or the complete page."""

        canvas = self.actions_canvas
        left = canvas.winfo_rootx()
        top = canvas.winfo_rooty()
        right = left + canvas.winfo_width()
        bottom = top + canvas.winfo_height()
        if not (left <= event.x_root < right and top <= event.y_root < bottom):
            canvas = self.main_canvas
            left = canvas.winfo_rootx()
            top = canvas.winfo_rooty()
            right = left + canvas.winfo_width()
            bottom = top + canvas.winfo_height()
            if not (left <= event.x_root < right and top <= event.y_root < bottom):
                return None
        delta = int(event.delta)
        if delta == 0:
            return None
        units = max(1, abs(delta) // 120)
        canvas.yview_scroll(-units if delta > 0 else units, "units")
        return "break"

    def browse_uuu(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.uuu_dir_var.get(), title="选择 UUU_v5.8.21 文件夹")
        if selected:
            self.uuu_dir_var.set(selected)
            self._persist_settings()

    def _apply_always_on_top(self) -> None:
        enabled = bool(self.always_on_top_var.get())
        self.root.attributes("-topmost", enabled)
        self.pin_text_var.set("窗口置顶：开" if enabled else "窗口置顶：关")

    def toggle_always_on_top(self) -> None:
        self.always_on_top_var.set(not self.always_on_top_var.get())
        self._apply_always_on_top()
        self._persist_settings()
        state = "开启" if self.always_on_top_var.get() else "关闭"
        self.log(f"窗口置顶已{state}。")

    def _persist_settings(self) -> None:
        self.settings["uuu_dir"] = self.uuu_dir_var.get().strip()
        self.settings["obs_host"] = self.obs_host_var.get().strip()
        self.settings["obs_port"] = int(self.obs_port_var.get().strip() or "4455")
        self.settings["scene_id"] = self.scene_id_var.get().strip() or "scene_1"
        self.settings["autoload_trajectory"] = (
            str(self.trajectory_path) if self.trajectory_path is not None else ""
        )
        self.settings["always_on_top"] = bool(self.always_on_top_var.get())
        save_settings(self.settings)

    def _refresh_trajectory_choices(
        self,
        preferred: str | Path | None = None,
    ) -> None:
        if self.capture_busy:
            self.log("采集进行中，轨迹文件列表暂不刷新。")
            return
        configured = str(self.settings.get("autoload_trajectory") or "").strip()
        extras = [
            value
            for value in (
                preferred,
                self.requested_trajectory_path,
                self.trajectory_path,
                configured,
            )
            if value is not None and str(value).strip()
        ]
        paths = discover_trajectory_files(
            (
                PROJECT_ROOT / "examples" / "trajectory_files",
                TRAJECTORY_FILES_DIR,
            ),
            extra_paths=extras,
        )
        self.trajectory_choice_paths = build_trajectory_choice_map(
            paths,
            project_root=PROJECT_ROOT,
        )
        self.trajectory_combo.configure(values=list(self.trajectory_choice_paths))

        candidates = [preferred, self.trajectory_path, self.requested_trajectory_path, configured]
        target = next(
            (
                Path(value).expanduser().resolve()
                for value in candidates
                if value is not None
                and str(value).strip()
                and Path(value).expanduser().is_file()
            ),
            paths[0] if paths else None,
        )
        if target is None:
            self.trajectory_choice_var.set("")
            self.trajectory_var.set("未发现轨迹文件；点击“浏览…”添加 JSON/CSV")
            return
        try:
            self._load_trajectory_path(target)
            self.log(f"轨迹文件已自动加载：{target}")
        except Exception as exc:
            self.log(f"自动加载轨迹失败：{exc}")

    def _set_trajectory_choice_path(self, path: Path) -> None:
        resolved = path.resolve()
        for label, candidate in self.trajectory_choice_paths.items():
            if candidate == resolved:
                self.trajectory_choice_var.set(label)
                return
        self.trajectory_choice_paths = build_trajectory_choice_map(
            [*self.trajectory_choice_paths.values(), resolved],
            project_root=PROJECT_ROOT,
        )
        self.trajectory_combo.configure(values=list(self.trajectory_choice_paths))
        for label, candidate in self.trajectory_choice_paths.items():
            if candidate == resolved:
                self.trajectory_choice_var.set(label)
                return

    def _on_trajectory_choice(self, _event: object | None = None) -> None:
        if self.capture_busy:
            if self.trajectory_path is not None:
                self._set_trajectory_choice_path(self.trajectory_path)
            return
        selected = self.trajectory_choice_paths.get(self.trajectory_choice_var.get())
        if selected is None:
            return
        try:
            self._load_trajectory_path(selected)
            self.log(f"已切换轨迹文件：{selected}")
        except Exception as exc:
            if self.trajectory_path is not None:
                self._set_trajectory_choice_path(self.trajectory_path)
            self._show_error("加载轨迹失败", exc)

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
            self.capture_points_button.configure(
                state=capture_state if self.points else "disabled"
            )
            trajectory_state = capture_state if self.trajectories else "disabled"
            self.continuous_capture_button.configure(state=trajectory_state)
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
        if self.capture_busy:
            self.log("采集任务运行中，已忽略记录点位操作。")
            return
        if self.connection_report is None or self.connection_report.pose is None:
            self._show_guidance("Pose 尚未连接，按顶部状态提示完成连接后再记录点位。")
            return
        try:
            pose = self.bridge.read_pose()
        except PoseUnavailableError as exc:
            self._show_guidance(str(exc))
            return
        index = len(self.points) + 1
        updated_points = list(self.points)
        updated_points.append(
            CapturePoint(index=index, label=f"point_{index:04d}", pose=pose)
        )
        try:
            self._write_active_point_map(updated_points)
        except Exception as exc:
            self._show_error("点位写入失败", exc)
            return
        self.points = updated_points
        self._refresh_point_tree()
        self.log(f"已记录并写入 {ACTIVE_POINT_MAP_PATH.name}：point_{index:04d}")

    def _start_record_point_hotkey(self) -> None:
        if not self.record_point_hotkey.supported:
            self.record_hotkey_status_var.set("Linux：全局 F8 未启用，请使用界面按钮")
            self.log("Linux 不注册 Windows 全局 F8；请使用界面中的“记录当前 Pose”按钮。")
            self.root.after(50, self._poll_record_point_hotkey)
            return
        try:
            self.record_point_hotkey.start()
        except RuntimeError as exc:
            self.record_hotkey_status_var.set("游戏内 F8：注册失败")
            self.log(str(exc))
        else:
            self.record_hotkey_status_var.set("游戏内 F8：记录点位")
        self.root.after(50, self._poll_record_point_hotkey)

    def _poll_record_point_hotkey(self) -> None:
        if self.closing:
            return
        if self.record_point_hotkey.consume():
            self._record_point_from_game_hotkey()
        self.root.after(50, self._poll_record_point_hotkey)

    def _record_point_from_game_hotkey(self) -> None:
        if not self.record_point_hotkey.supported:
            self.log("Linux 不支持游戏进程前台检测；请使用界面中的“记录当前 Pose”按钮。")
            return
        try:
            game_pid = find_game_pid()
        except RuntimeError as exc:
            self.log(f"F8 未记录：{exc}")
            return
        if foreground_process_id() != game_pid:
            self.log("F8 未记录：当前前台窗口不是《黑神话：悟空》。")
            return
        self.record_point()

    def load_point_file(self) -> None:
        if self.capture_busy:
            self.log("采集进行中，不能切换点位图。")
            return
        selected = filedialog.askopenfilename(
            title="Load 点位文件",
            initialdir=POINT_FILES_DIR,
            filetypes=[("点位文件", "*.json *.csv"), ("JSON", "*.json"), ("CSV", "*.csv")],
        )
        if not selected:
            return
        try:
            imported_points = load_points(selected)
            self._write_active_point_map(imported_points)
            self.points = imported_points
            self._refresh_point_tree()
            self.log(
                f"已导入 {len(self.points)} 个点位并同步到 "
                f"{ACTIVE_POINT_MAP_PATH.name}：{selected}"
            )
        except Exception as exc:
            self._show_error("Load 点位失败", exc)

    def _load_active_point_map(self) -> None:
        try:
            if ACTIVE_POINT_MAP_PATH.is_file():
                self.points = load_points(
                    ACTIVE_POINT_MAP_PATH,
                    allow_empty=True,
                )
                self.point_map_path = ACTIVE_POINT_MAP_PATH.resolve()
                self.point_map_dirty = False
            else:
                self.points = []
                self._write_active_point_map(self.points)
            self._refresh_point_tree()
            self.log(
                f"实时点位文件已就绪：{ACTIVE_POINT_MAP_PATH} "
                f"({len(self.points)} 个点位)"
            )
        except Exception as exc:
            self.point_map_path = ACTIVE_POINT_MAP_PATH.resolve()
            self.point_map_dirty = True
            self._refresh_point_tree()
            self.log(f"实时点位文件读取失败：{exc}")

    def _write_active_point_map(self, points: list[CapturePoint]) -> None:
        target = save_points(
            ACTIVE_POINT_MAP_PATH,
            points,
            kind="points",
        )
        self.point_map_path = target.resolve()
        self.point_map_dirty = False

    def open_active_point_map(self) -> None:
        try:
            self._write_active_point_map(self.points)
            open_path(ACTIVE_POINT_MAP_PATH)
        except Exception as exc:
            self._show_error("打开点位文件失败", exc)

    def delete_selected_points(self) -> None:
        if self.capture_busy:
            self.log("采集进行中，不能修改点位图。")
            return
        selected = set(self.point_tree.selection())
        if not selected:
            return
        keep = [point for item, point in zip(self.point_tree.get_children(), self.points) if item not in selected]
        updated_points = [
            replace(point, index=index, label=f"point_{index:04d}")
            for index, point in enumerate(keep, 1)
        ]
        try:
            self._write_active_point_map(updated_points)
        except Exception as exc:
            self._show_error("删除点位失败", exc)
            return
        self.points = updated_points
        self._refresh_point_tree()
        self.log(
            f"已删除选中点位并同步 {ACTIVE_POINT_MAP_PATH.name}；"
            f"剩余 {len(self.points)} 个点位。"
        )

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
        point_count = len(self.points)
        image_count = point_count * 22
        self.points_count_var.set(
            f"{point_count} 个空间点 · 预计 {image_count} 张"
        )
        self.static_start_point_spin.configure(to=max(1, point_count))
        if self.static_start_point_var.get() > max(1, point_count):
            self.static_start_point_var.set(max(1, point_count))
        if point_count == 0:
            self.point_map_var.set("尚未记录或 Load 点位图")
        else:
            source = self.point_map_path.name if self.point_map_path is not None else "内存点位图"
            dirty = " · 写入异常" if self.point_map_dirty else " · 自动保存"
            self.point_map_var.set(
                f"{source}{dirty} · {point_count} 个空间点 × 22 = {image_count} 张"
            )
        ready = self.connection_report is not None and self.connection_report.ready
        self.capture_points_button.configure(
            state=(
                "normal"
                if point_count and ready and not self.capture_busy
                else "disabled"
            )
        )
        self.static_start_point_spin.configure(
            state="normal" if point_count and not self.capture_busy else "disabled"
        )
        self._refresh_static_resume()

    def _refresh_static_resume(self) -> None:
        """Find the newest interrupted static run for the current point map."""

        scene_id = self.scene_id_var.get().strip() or "scene_1"
        self.static_resume_info = find_latest_resumable_static_run(
            STATIC_CAPTURES_DIR,
            scene_id=scene_id,
            point_map_source=self.point_map_path,
        )
        if self.static_resume_info is not None:
            self.latest_static_output_dir = Path(
                str(self.static_resume_info["manifest_path"])
            ).parent.resolve()

        if self.static_resume_info is None:
            self.static_resume_var.set("没有可继续的静态采集任务")
        else:
            info = self.static_resume_info
            self.static_resume_var.set(
                "可继续上次静态采集："
                f"已完成至第 {info['last_sample']}/{info['requested_count']} 张，"
                f"下次从第 {info['next_sample']} 张开始"
            )
        if hasattr(self, "static_resume_button"):
            ready = self.connection_report is not None and self.connection_report.ready
            self.static_resume_button.configure(
                state=(
                    "normal"
                    if self.static_resume_info is not None
                    and ready
                    and not self.capture_busy
                    else "disabled"
                )
            )

    def resume_static_22_capture(self) -> None:
        if self.capture_thread is not None and self.capture_thread.is_alive():
            messagebox.showinfo("正在采集", "请先等待当前采集任务结束。")
            return
        self._refresh_static_resume()
        if self.static_resume_info is None:
            messagebox.showinfo("没有可继续任务", "没有找到当前点位图对应的失败或停止任务。")
            return
        selected_start = int(self.static_resume_info.get("selected_start_ordinal") or 1)
        self.static_start_point_var.set(selected_start)
        self.start_static_22_capture(resume_info=self.static_resume_info)

    def load_trajectory_file(self) -> None:
        if self.capture_busy:
            self.log("采集进行中，不能切换轨迹文件。")
            return
        selected = filedialog.askopenfilename(
            title="选择轨迹文件",
            initialdir=(
                self.trajectory_path.parent
                if self.trajectory_path is not None
                else PROJECT_ROOT / "examples" / "trajectory_files"
            ),
            filetypes=[("轨迹文件", "*.json *.csv"), ("JSON", "*.json"), ("CSV", "*.csv")],
        )
        if not selected:
            return
        try:
            self._load_trajectory_path(Path(selected))
            self.log(f"轨迹文件已加载：{selected}")
        except Exception as exc:
            self._show_error("加载轨迹失败", exc)

    def _load_trajectory_path(self, path: Path) -> None:
        self.trajectories = load_trajectories(path)
        self.trajectory_path = path.resolve()
        self._set_trajectory_choice_path(self.trajectory_path)
        point_total = sum(len(trajectory.points) for trajectory in self.trajectories)
        self.trajectory_var.set(
            f"已加载：{path.name} · {len(self.trajectories)} 条轨迹 · {point_total} 个关键帧"
        )
        self.trajectory_index_spin.configure(to=max(1, len(self.trajectories)))
        self.trajectory_index_var.set(1)
        self._persist_settings()
        ready = self.connection_report is not None and self.connection_report.ready
        state = "normal" if ready and not self.capture_busy else "disabled"
        self.continuous_capture_button.configure(state=state)

    def _validate_trajectory_capture(self) -> int:
        if not self.trajectories or self.trajectory_path is None:
            raise UserActionRequired("请先从下拉菜单选择轨迹 JSON/CSV。")
        if self.connection_report is None or not self.connection_report.ready:
            raise UserActionRequired(
                self.connection_report.detail
                if self.connection_report is not None
                else "正在检查 UUU 连接状态，请稍候。"
            )
        pid = find_game_pid()
        pose = self.bridge.read_pose()
        if not pose.camera_enabled:
            raise UserActionRequired("UUU 相机未启用，请回到游戏按 Insert。")
        self._persist_settings()
        obs = self._make_obs()
        try:
            obs.test()
        finally:
            obs.close()
        return pid

    def _make_mover(self, pid: int) -> ClosedLoopMover:
        return ClosedLoopMover(
            self.bridge,
            position_tolerance=float(self.settings["position_tolerance"]),
            angle_tolerance=float(self.settings["angle_tolerance_degrees"]),
            fov_tolerance=float(self.settings["fov_tolerance_degrees"]),
            move_pulse_sec=float(self.settings["move_pulse_sec"]),
            rotate_pulse_sec=float(self.settings["rotate_pulse_sec"]),
            max_seconds=float(self.settings["max_move_seconds"]),
            feedback_timeout_sec=float(
                self.settings.get("native_feedback_timeout_sec", 0.5)
            ),
            focus_game=lambda: focus_game_window(pid),
            prefer_native=True,
            allow_hotkey_fallback=False,
        )

    def _make_obs(self) -> OBSBridge:
        return OBSBridge(
            self.obs_host_var.get().strip() or "127.0.0.1",
            int(self.obs_port_var.get().strip() or "4455"),
            self.obs_password_var.get(),
        )

    def start_continuous_trajectory_capture(self) -> None:
        self._start_trajectory_capture()

    def _start_trajectory_capture(
        self,
        *,
        resume: dict[str, object] | None = None,
    ) -> None:
        if self.capture_thread is not None and self.capture_thread.is_alive():
            messagebox.showinfo("正在采集", "请先停止或等待当前采集任务。")
            return
        try:
            pid = self._validate_trajectory_capture()
        except UserActionRequired as exc:
            self._show_guidance(str(exc))
            return
        except Exception as exc:
            self._show_error("轨迹采集准备失败", exc)
            return

        selected = min(
            max(0, self.trajectory_index_var.get() - 1),
            len(self.trajectories) - 1,
        )
        planned = (
            list(resume["pending_indices"])
            if resume
            else list(range(selected, len(self.trajectories)))
        )
        if not planned:
            messagebox.showinfo("没有待采轨迹", "当前批次没有需要继续采集的轨迹。")
            return

        scene_id = self.scene_id_var.get().strip() or "scene_1"
        self.stop_event.clear()
        self.active_capture_kind = "trajectory"
        self._set_capture_busy(True)
        self.task_progress_var.set(0)
        self.frame_progress_var.set(0)
        count = len(planned)
        self.task_progress.configure(maximum=max(1, count))
        self.frame_progress.configure(maximum=1)
        self.task_progress_text_var.set(f"任务进度：0/{count} 条")
        self.frame_progress_text_var.set("当前轨迹：准备 OBS、静音和 Pose")
        recorder = BatchTrajectoryRecorder(
            bridge=self.bridge,
            mover_factory=lambda: self._make_mover(pid),
            obs_factory=self._make_obs,
            scene_id=scene_id,
            pose_hz=float(self.settings["pose_log_hz"]),
            playback_hz=float(self.settings.get("trajectory_playback_hz", 60.0)),
        )
        self.batch_recorder = recorder
        if resume:
            self._set_output_path(Path(resume["batch_dir"]))

        def trajectory_progress(index: int, total: int, trajectory: ImportedTrajectory, phase: str) -> None:
            def update() -> None:
                try:
                    position = planned.index(index) + (0 if phase == "starting" else 1)
                except ValueError:
                    position = 0
                self.task_progress_var.set(position)
                self.task_progress_text_var.set(
                    f"任务进度：{position}/{len(planned)} 条；全局第 {index + 1}/{total} 条 {phase}"
                )
                if recorder.batch_dir is not None:
                    self._set_output_path(recorder.batch_dir)
            self.root.after(0, update)

        def frame_progress(index: int, total: int, trajectory: ImportedTrajectory, done: int, frame_total: int, message: str) -> None:
            self.root.after(0, lambda: self._update_trajectory_frame(done, frame_total, message))

        def worker() -> None:
            try:
                result = recorder.capture(
                    self.trajectories,
                    source_path=self.trajectory_path,
                    trajectory_indices=planned,
                    batch_dir=resume["batch_dir"] if resume else None,
                    trajectory_callback=trajectory_progress,
                    frame_callback=frame_progress,
                    log_callback=self.log,
                )
            except Exception as exc:
                self.root.after(0, lambda error=exc: self._trajectory_capture_failed(error))
            else:
                self.root.after(0, lambda value=result: self._trajectory_capture_finished(value))

        self.capture_thread = threading.Thread(target=worker, name="bmw-trajectory-batch", daemon=True)
        self.capture_thread.start()
        self.log(
            f"开始连续轨迹采集：从第 {planned[0] + 1} 条开始，共 {len(planned)} 条；"
            "OBS 录制前强制静音。"
        )

    def resume_trajectory_capture(self) -> None:
        scene_id = self.scene_id_var.get().strip() or "scene_1"
        resume = find_latest_resumable_batch(scene_id)
        if resume is None:
            messagebox.showinfo("没有待续任务", f"场景 {scene_id} 没有发现未完成批次。")
            return
        source = Path(resume["source_path"])
        try:
            self._load_trajectory_path(source)
        except Exception as exc:
            self._show_error("读取续采源文件失败", exc)
            return
        if int(resume["total"]) != len(self.trajectories):
            self._show_error("续采轨迹集不匹配", RuntimeError("源文件轨迹数量与批次清单不一致"))
            return
        self._start_trajectory_capture(resume=resume)

    def _update_trajectory_frame(self, done: int, total: int, message: str) -> None:
        self.frame_progress.configure(maximum=max(1, total))
        self.frame_progress_var.set(done)
        self.frame_progress_text_var.set(f"当前轨迹：{done}/{total} 样本 · {message}")

    def _trajectory_capture_finished(self, result: dict[str, object]) -> None:
        self.active_capture_kind = None
        self._set_capture_busy(False)
        self.batch_recorder = None
        requested = int(result.get("requested_trajectories") or 0)
        completed = int(result.get("completed_trajectories") or 0)
        failed = int(result.get("failed_trajectories") or 0)
        self.task_progress_var.set(completed + failed)
        status = "已停止" if result.get("stopped") else "已完成"
        self.task_progress_text_var.set(f"任务进度：{completed + failed}/{requested} 条（{status}）")
        self.frame_progress_text_var.set("当前轨迹：空闲")
        output = result.get("output_dir")
        if output:
            self._set_output_path(Path(str(output)))
        self.log(f"轨迹批次{status}：完成 {completed}，失败 {failed}；{output}")

    def _trajectory_capture_failed(self, exc: Exception) -> None:
        batch_dir = (
            self.batch_recorder.batch_dir
            if self.batch_recorder is not None
            else None
        )
        self.active_capture_kind = None
        self._set_capture_busy(False)
        self.batch_recorder = None
        if batch_dir is not None:
            self._set_output_path(batch_dir)
        self.task_progress_text_var.set(f"任务进度：失败 — {exc}")
        self.frame_progress_text_var.set("当前轨迹：已停止")
        self._show_error("轨迹采集失败", exc)

    def _set_output_path(self, path: Path) -> None:
        self.latest_capture_output_dir = path.resolve()
        self.output_var.set(f"输出批次：{self.latest_capture_output_dir.name}")
        self.log(f"输出目录：{self.latest_capture_output_dir}")

    def _set_static_output_path(self, path: Path) -> None:
        self.latest_static_output_dir = path.resolve()
        self.static_output_var.set(f"输出：{self.latest_static_output_dir.name}")
        self.log(f"静态图片目录：{self.latest_static_output_dir}")

    def _refresh_latest_output_dirs(self) -> None:
        """Point both directory buttons at the newest real output folders."""

        static_dirs = [
            path
            for path in STATIC_CAPTURES_DIR.iterdir()
            if path.is_dir()
        ]
        if static_dirs:
            self._set_static_output_path(
                max(static_dirs, key=lambda path: path.stat().st_mtime)
            )

        trajectory_dirs = [
            path
            for path in TRAJECTORY_CAPTURES_DIR.glob("**/run_*")
            if path.is_dir()
        ]
        if trajectory_dirs:
            self._set_output_path(
                max(trajectory_dirs, key=lambda path: path.stat().st_mtime)
            )

    def open_capture_output(self) -> None:
        self._refresh_latest_output_dirs()
        path = self.latest_capture_output_dir
        path.mkdir(parents=True, exist_ok=True)
        open_path(path)

    def open_static_output(self) -> None:
        self._refresh_latest_output_dirs()
        session_dir = self.latest_static_output_dir
        image_dir = session_dir / "images"
        path = image_dir if image_dir.is_dir() else session_dir
        path.mkdir(parents=True, exist_ok=True)
        open_path(path)

    def start_static_22_capture(
        self,
        *,
        resume_info: dict[str, object] | None = None,
    ) -> None:
        if self.capture_thread is not None and self.capture_thread.is_alive():
            messagebox.showinfo("正在采集", "请先等待当前任务结束或点击停止采集。")
            return
        if not self.points:
            messagebox.showinfo("没有点位图", "请先记录或 Load 空间点位图。")
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

        try:
            requested_start = (
                resume_info.get("selected_start_ordinal")
                if resume_info is not None
                else self.static_start_point_var.get()
            )
            start_ordinal = min(max(1, int(requested_start)), len(self.points))
        except (TypeError, ValueError, tk.TclError):
            messagebox.showerror("起始点无效", "起始空间点必须是有效整数。")
            return
        spatial_points = list(self.points[start_ordinal - 1 :])
        all_samples = build_22_view_plan(spatial_points)
        sample_offset = 0
        if resume_info is not None:
            expected = int(
                resume_info.get("expected_image_count")
                or resume_info.get("requested_count")
                or 0
            )
            if expected and expected != len(all_samples):
                messagebox.showerror(
                    "续采计划不匹配",
                    "当前点位图与失败任务的 22 方向计划数量不同，已停止续采。",
                )
                return
            sample_offset = max(0, int(resume_info.get("next_sample", 1)) - 1)
            if sample_offset >= len(all_samples):
                self._refresh_static_resume()
                messagebox.showinfo("没有待采样本", "上次静态任务已经没有剩余样本。")
                return
        samples = all_samples[sample_offset:]
        if not samples:
            messagebox.showinfo("没有待采样本", "当前起始点之后没有可采集空间点。")
            return

        scene_id = self.scene_id_var.get().strip() or "scene_1"
        if resume_info is None:
            self.static_resume_info = None
        self.stop_event.clear()
        self.active_capture_kind = "static22"
        self._set_capture_busy(True)
        self.static_progress_total = len(all_samples)
        self.static_progress_offset = sample_offset
        self.static_progress.configure(maximum=max(1, len(all_samples)))
        self.static_progress_var.set(sample_offset)
        self.static_progress_text_var.set(
            f"静态采集：{sample_offset}/{len(all_samples)} 张 · "
            f"{len(spatial_points)} 个空间点"
        )
        mover = self._make_mover(pid)
        # Still datasets are always Full HD JPG, even when an older settings
        # file still contains the previous PNG/2K values.
        image_format = "jpg"
        self.settings["screenshot_format"] = image_format

        def progress(done: int, total: int, message: str) -> None:
            self.root.after(
                0,
                lambda: self._update_static_progress(
                    sample_offset + done,
                    len(all_samples),
                    len(spatial_points),
                    message,
                ),
            )

        def worker() -> None:
            obs: OBSBridge | None = None
            runner: CaptureRunner | None = None
            try:
                # Create and use the OBS client inside the worker thread. The
                # Program source is resolved once per run, avoiding one extra
                # WebSocket request for every one of the 22 views.
                obs = self._make_obs()
                obs.test()
                obs_source = obs.current_scene()
                obs_width, obs_height = obs.capture_size()
                obs_canvas_width, obs_canvas_height = obs.video_canvas_size()

                def obs_screenshotter(_pid: int, target: str | Path) -> Path:
                    obs.save_screenshot(
                        target,
                        source_name=obs_source,
                        image_format=image_format,
                        width=obs_width,
                        height=obs_height,
                    )
                    return Path(target)

                runner = CaptureRunner(
                    bridge=self.bridge,
                    mover=mover,
                    pid=pid,
                    settle_seconds=float(self.settings["capture_interval_sec"]),
                    image_format=image_format,
                    screenshotter=obs_screenshotter,
                )
                result = runner.run(
                    samples,
                    STATIC_CAPTURES_DIR,
                    mode=f"{scene_id}_static22",
                    stop_requested=self.stop_event.is_set,
                    on_progress=progress,
                    on_log=self.log,
                    respect_timestamps=False,
                    run_metadata={
                        "format": "bmw-static-22-view-plan-v1",
                        "scene_id": scene_id,
                        "point_map_source": (
                            str(self.point_map_path)
                            if self.point_map_path is not None
                            else None
                        ),
                        "point_map_dirty_at_start": self.point_map_dirty,
                        "selected_start_ordinal": start_ordinal,
                        "spatial_point_count": len(spatial_points),
                        "views_per_point": 22,
                        "image_format": image_format,
                        "expected_image_count": len(all_samples),
                        "selected_start_sample": sample_offset + 1,
                        "selected_end_sample": sample_offset + len(samples),
                        "resume_source_manifest": (
                            str(resume_info.get("manifest_path"))
                            if resume_info is not None
                            else None
                        ),
                        "orientation_mode": "absolute_yaw_pitch_degrees",
                        "screenshot_source": "obs_websocket_source",
                        "obs_source_name": obs_source,
                        "image_width": obs_width,
                        "image_height": obs_height,
                        "obs_canvas_width": obs_canvas_width,
                        "obs_canvas_height": obs_canvas_height,
                        "view_patterns": view_pattern_manifest(),
                        "spatial_points": [
                            point.flat_dict() for point in spatial_points
                        ],
                    },
                )
            except Exception as exc:
                output_dir = runner.last_session_dir if runner is not None else None
                self.root.after(
                    0,
                    lambda error=exc, output=output_dir: self._static_capture_failed(
                        error, output
                    ),
                )
            else:
                self.root.after(
                    0,
                    lambda value=result: self._static_capture_finished(value),
                )
            finally:
                if obs is not None:
                    obs.close()

        self.capture_thread = threading.Thread(
            target=worker,
            name="bmw-static-22-capture",
            daemon=True,
        )
        self.capture_thread.start()
        self.log(
            f"开始自动静态 22 方向采集：从空间点 {start_ordinal} 开始，"
            f"{len(spatial_points)} 个空间点，共 {len(samples)} 张原图。"
        )

    def stop_capture(self) -> None:
        self.stop_event.set()
        if self.batch_recorder is not None and self.batch_recorder.active:
            self.batch_recorder.request_stop()
        if self.active_capture_kind == "static22":
            self.static_progress_text_var.set(
                "静态采集：正在停止；当前控制结束后恢复任务起点…"
            )
        else:
            self.frame_progress_text_var.set("当前轨迹：正在安全停止 OBS 与 Pose…")
        self.log("已请求停止，将在当前控制脉冲结束后退出。")

    def _update_static_progress(
        self,
        done: int,
        total: int,
        spatial_point_count: int,
        message: str,
    ) -> None:
        self.static_progress.configure(maximum=max(total, 1))
        self.static_progress_var.set(done)
        completed_points = min(spatial_point_count, (done + 21) // 22)
        self.static_progress_text_var.set(
            f"静态采集：{done}/{total} 张 · 空间点 "
            f"{completed_points}/{spatial_point_count} · {message}"
        )

    def _static_capture_finished(self, result: CaptureRunResult) -> None:
        self.active_capture_kind = None
        self._set_capture_busy(False)
        status = "已停止" if result.stopped else "采集完成"
        self.static_progress_var.set(result.captured_count)
        self.static_progress_text_var.set(
            f"静态采集：{status} · "
            f"{result.captured_count}/{result.requested_count} 张"
        )
        total = max(
            self.static_progress_total,
            self.static_progress_offset + result.requested_count,
        )
        completed_total = min(
            total,
            self.static_progress_offset + result.captured_count,
        )
        self.static_progress.configure(maximum=max(1, total))
        self.static_progress_var.set(completed_total)
        self.static_progress_text_var.set(
            f"静态采集：{status} · {completed_total}/{total} 张"
        )
        self._refresh_static_resume()
        self.latest_static_output_dir = result.session_dir.resolve()
        self._set_static_output_path(result.session_dir)
        self.log(f"{status}：{result.session_dir}")
        if not result.stopped:
            messagebox.showinfo(
                "静态 22 方向采集完成",
                f"已保存 {result.captured_count} 张图片。\n\n{result.session_dir}",
            )

    def _static_capture_failed(
        self,
        exc: Exception,
        output_dir: Path | None = None,
    ) -> None:
        self.active_capture_kind = None
        self._set_capture_busy(False)
        if output_dir is not None:
            self._set_static_output_path(output_dir)
        self._refresh_static_resume()
        self.static_progress_text_var.set(f"静态采集失败：{exc}")
        self._show_error("静态 22 方向采集失败", exc)

    def _set_capture_busy(self, busy: bool) -> None:
        self.capture_busy = busy
        ready = self.connection_report is not None and self.connection_report.ready
        state = "disabled" if busy or not ready else "normal"
        self.capture_points_button.configure(
            state=state if self.points else "disabled"
        )
        self.static_resume_button.configure(
            state=(
                "normal"
                if self.static_resume_info is not None and ready and not busy
                else "disabled"
            )
        )
        self.static_start_point_spin.configure(
            state="disabled" if busy or not self.points else "normal"
        )
        trajectory_state = state if self.trajectories else "disabled"
        self.continuous_capture_button.configure(state=trajectory_state)
        self.resume_capture_button.configure(state="disabled" if busy else "normal")
        self.trajectory_combo.configure(state="disabled" if busy else "readonly")
        self.trajectory_index_spin.configure(
            state="disabled" if busy or not self.trajectories else "normal"
        )
        pose_available = self.connection_report is not None and self.connection_report.pose is not None
        self.record_point_button.configure(
            state="disabled" if busy or not pose_available else "normal"
        )
        self.static_stop_button.configure(
            state=(
                "normal"
                if busy and self.active_capture_kind == "static22"
                else "disabled"
            )
        )
        self.stop_button.configure(
            state=(
                "normal"
                if busy and self.active_capture_kind == "trajectory"
                else "disabled"
            )
        )

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
        if self.batch_recorder is not None and self.batch_recorder.active:
            self.batch_recorder.request_stop()
        self.closing = True
        self.record_point_hotkey.stop()
        self.bridge.close()
        self.root.destroy()


def run_app(*, trajectory_file: str | Path | None = None) -> None:
    enable_dpi_awareness()
    root = tk.Tk()
    CaptureStudioApp(root, trajectory_file=trajectory_file)
    root.mainloop()
