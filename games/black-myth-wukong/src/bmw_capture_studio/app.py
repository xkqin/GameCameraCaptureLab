from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from .bridge import PoseUnavailableError, create_pose_bridge
from .capture_runner import CaptureRunResult, CaptureRunner
from .depth_bridge import DepthBridge
from .config import load_shared_config
from .connection import ConnectionReport, probe_connection
from .discord_notify import DiscordNotifier
from .feishu_notify import FeishuNotifier
from .files import load_points, load_trajectories, save_points
from .game_context import GAME_ID, GAME_NAME, PRODUCT_TITLE
from .input_control import ClosedLoopMover
from .models import CameraPose, CapturePoint, ImportedTrajectory
from .obs_bridge import OBSBridge
from .obs_restart import OBSProcessRestarter
from .paths import (
    ACTIVE_POINT_MAP_PATH,
    POINT_FILES_DIR,
    PROJECT_ROOT,
    REPOSITORY_ROOT,
    STATIC_CAPTURES_DIR,
    TRAJECTORY_CAPTURES_DIR,
    TRAJECTORY_FILES_DIR,
    ensure_directories,
)
from .platform_support import open_path
from .repair import CodexRecoveryTrigger
from .screen_capture import enable_dpi_awareness, focus_game_window, foreground_process_id
from .settings import load_settings, save_settings
from .still_scan import (
    build_22_view_plan,
    find_latest_resumable_static_run,
    view_pattern_manifest,
)
from .trajectory_capture import BatchTrajectoryRecorder, find_latest_resumable_batch
from .trajectory_catalog import build_trajectory_choice_map, discover_trajectory_files
from .injection import (
    CameraIntegrationError,
    find_game_pid,
)
from .integration_repair import repair_and_inject


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
        self.settings = load_settings()
        self.language = str(self.settings.get("language", "zh")).strip().casefold()
        if self.language not in {"zh", "en"}:
            self.language = "zh"
        self.language_var = tk.StringVar(
            value="English" if self.language == "en" else "中文"
        )
        self._localized_widgets: dict[tk.Misc, str] = {}
        self._localized_vars: dict[str, tk.StringVar] = {}
        self._localized_raw_values: dict[str, str] = {}
        self._localization_guard = False
        self.root.title(PRODUCT_TITLE)
        # The studio contains two dense capture workflows.  Start wide enough
        # for both columns and maximize on Windows so the trajectory controls
        # are not hidden behind the viewport edge on a restored window.
        self.root.geometry("1320x900")
        self.root.minsize(1120, 760)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if os.name == "nt":
            self.root.after_idle(lambda: self.root.state("zoomed"))

        self.shared_config = load_shared_config()
        self.discord_notifier = DiscordNotifier.from_config(self.shared_config)
        self.feishu_notifier = FeishuNotifier.from_config(self.shared_config)
        self.codex_recovery = CodexRecoveryTrigger.from_config(self.shared_config)
        self.always_on_top_var = tk.BooleanVar(
            value=bool(self.settings.get("always_on_top", True))
        )
        self.pin_text_var = tk.StringVar()
        # Windows reads the native named mapping directly.  Linux/Proton uses
        # the optional loopback relay exposed by the injected bridge DLL.
        self.bridge = create_pose_bridge(
            str(self.settings.get("bridge_endpoint") or "").strip() or None
        )
        self.current_pose: CameraPose | None = None
        self.points: list[CapturePoint] = []
        self.point_map_path: Path | None = None
        self.point_map_dirty = False
        self.trajectories: list[ImportedTrajectory] = []
        self.trajectory_path: Path | None = None
        self.trajectory_choice_paths: dict[str, Path] = {}
        requested = (
            trajectory_file
            or os.environ.get("UNIFIED_TRAJECTORY_FILE")
            or os.environ.get("BMW_TRAJECTORY_FILE")
        )
        self.depth_supported = os.name == "nt"
        self.depth_enabled_var = tk.BooleanVar(
            value=(
                self.depth_supported
                and bool(self.settings.get("depth_enabled", False))
            )
        )
        self.depth_status_var = tk.StringVar()
        self.depth_bridge = DepthBridge()
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

        self.status_var = tk.StringVar(value="等待连接游戏 / Waiting for game")
        self.status_detail_var = tk.StringVar(
            value="启动游戏后点击检查并注入 / Start the game, then Check & Inject"
        )
        self.pose_var = tk.StringVar(value="X --    Y --    Z --")
        self.angle_var = tk.StringVar(value="Yaw --°    Pitch --°    Roll --°    FOV --°")
        self.camera_state_var = tk.StringVar(value="Pose 未连接 / Disconnected")
        self.hud_status_var = tk.StringVar(value="HUD：等待 / Waiting")
        self.points_count_var = tk.StringVar(value="0 个空间点 / points · 0 张 / images")
        self.record_hotkey_status_var = tk.StringVar(value="游戏内 E / In-game E：等待 Runtime…")
        self.point_map_var = tk.StringVar(value="尚未记录或加载点位图 / No point map")
        self.static_start_point_var = tk.IntVar(value=1)
        self.static_progress_var = tk.DoubleVar(value=0)
        self.static_progress_text_var = tk.StringVar(value="静态采集 / Still capture：空闲 / Idle")
        self.static_output_var = tk.StringVar(value="输出 / Output：still_captures")
        self.static_resume_var = tk.StringVar(value="没有可继续任务 / No resumable still run")
        self._update_depth_status()
        self.trajectory_var = tk.StringVar(value="尚未加载轨迹 / No trajectory loaded")
        self.trajectory_choice_var = tk.StringVar(value="")
        self.trajectory_index_var = tk.IntVar(value=1)
        self.scene_id_var = tk.StringVar(value=str(self.settings["scene_id"]))
        self.obs_host_var = tk.StringVar(value=str(self.settings["obs_host"]))
        self.obs_port_var = tk.StringVar(value=str(self.settings["obs_port"]))
        self.obs_password_var = tk.StringVar(
            value=(
                os.environ.get("UNIFIED_OBS_PASSWORD")
                or os.environ.get("BMW_OBS_PASSWORD", "")
            )
        )
        self.task_progress_var = tk.DoubleVar(value=0)
        self.frame_progress_var = tk.DoubleVar(value=0)
        self.task_progress_text_var = tk.StringVar(value="任务进度 / Task：空闲 / Idle")
        self.frame_progress_text_var = tk.StringVar(value="当前轨迹 / Trajectory：空闲 / Idle")
        self.output_var = tk.StringVar(value="输出 / Output：trajectory_captures")
        self.feishu_status_var = tk.StringVar(
            value=self.feishu_notifier.status_text
        )
        self.discord_status_var = tk.StringVar(
            value=self.discord_notifier.status_text
        )
        self.repair_status_var = tk.StringVar(value=self.codex_recovery.status_text)
        self.alert_config_var = tk.StringVar(
            value=f"共享配置 / Config：{self.shared_config.source_text}"
        )
        for display_var in (
            self.pin_text_var,
            self.status_var,
            self.status_detail_var,
            self.camera_state_var,
            self.hud_status_var,
            self.points_count_var,
            self.record_hotkey_status_var,
            self.point_map_var,
            self.static_progress_text_var,
            self.static_output_var,
            self.static_resume_var,
            self.depth_status_var,
            self.trajectory_var,
            self.task_progress_text_var,
            self.frame_progress_text_var,
            self.output_var,
            self.feishu_status_var,
            self.discord_status_var,
            self.repair_status_var,
            self.alert_config_var,
        ):
            self._register_localized_var(display_var)

        self._configure_style()
        self._build_ui()
        self._capture_localized_widgets(self.root)
        self._apply_localization()
        self._refresh_latest_output_dirs()
        self._apply_always_on_top()
        self._load_active_point_map()
        self._refresh_static_resume()
        self._start_record_point_hotkey()
        self._refresh_trajectory_choices(preferred=self.requested_trajectory_path)
        self._poll_pose()
        self.refresh_status()

    @staticmethod
    def _contains_cjk(value: str) -> bool:
        return any("\u3400" <= char <= "\u9fff" for char in value)

    def _localize_text(self, value: object) -> str:
        """Turn a bilingual source string into the selected language.

        Existing capture messages use ``Chinese / English`` pairs.  Keeping
        the source pair in one place lets the language selector update both
        static widgets and background status messages without duplicating
        every capture callback.
        """

        text = str(value)
        if not self._contains_cjk(text):
            return text
        if "\n" in text:
            lines = text.splitlines()
            if len(lines) == 2 and not self._contains_cjk(lines[1]):
                return lines[1] if self.language == "en" else lines[0]
        if text.count(" / ") == 1:
            zh_text, en_text = text.split(" / ", 1)
            return en_text.strip() if self.language == "en" else zh_text.strip()
        if self.language == "zh":
            if " / " in text:
                return text.split(" / ", 1)[0].strip()
            return text
        # English mode keeps technical tokens, numbers and paths while
        # removing the Chinese part of mixed status strings.
        english = re.sub(r"[\u3400-\u9fff]+", "", text)
        english = english.replace(" / ", " ")
        english = re.sub(r"\s+", " ", english).strip()
        return english or text

    def _register_localized_var(self, variable: tk.StringVar) -> None:
        name = str(variable._name)
        self._localized_vars[name] = variable
        self._localized_raw_values[name] = variable.get()
        variable.trace_add(
            "write",
            lambda *_args, variable_name=name: self._on_localized_var_write(
                variable_name
            ),
        )
        self._set_localized_var(name)

    def _on_localized_var_write(self, name: str) -> None:
        if self._localization_guard or name not in self._localized_vars:
            return
        variable = self._localized_vars[name]
        self._localized_raw_values[name] = variable.get()
        self._set_localized_var(name)

    def _set_localized_var(self, name: str) -> None:
        variable = self._localized_vars[name]
        localized = self._localize_text(self._localized_raw_values[name])
        if variable.get() == localized:
            return
        self._localization_guard = True
        try:
            variable.set(localized)
        finally:
            self._localization_guard = False

    def _capture_localized_widgets(self, parent: tk.Misc) -> None:
        for child in parent.winfo_children():
            try:
                raw_text = str(child.cget("text"))
            except (tk.TclError, TypeError):
                raw_text = ""
            if raw_text and self._contains_cjk(raw_text) and (
                " / " in raw_text or "\n" in raw_text
            ):
                self._localized_widgets[child] = raw_text
                child.configure(text=self._localize_text(raw_text))
            self._capture_localized_widgets(child)

    def _set_localized_widget_text(self, widget: tk.Misc, raw_text: str) -> None:
        self._localized_widgets[widget] = raw_text
        widget.configure(text=self._localize_text(raw_text))

    def _apply_localization(self) -> None:
        self.root.title(self._localize_text(PRODUCT_TITLE))
        self.language_var.set("English" if self.language == "en" else "中文")
        for widget, raw_text in list(self._localized_widgets.items()):
            try:
                if widget.winfo_exists():
                    widget.configure(text=self._localize_text(raw_text))
            except tk.TclError:
                self._localized_widgets.pop(widget, None)
        for name in self._localized_vars:
            self._set_localized_var(name)

    def _on_language_changed(self, _event: object | None = None) -> None:
        self.language = "en" if self.language_var.get() == "English" else "zh"
        self.settings["language"] = self.language
        save_settings(self.settings)
        self._apply_localization()
        self.log("语言已切换 / Language switched")

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
        ttk.Label(
            header,
            text=PRODUCT_TITLE,
            style="Title.TLabel",
        ).pack(side="left")
        ttk.Label(
            header,
            text=(
                f"当前适配器：{GAME_NAME.split(' / ', 1)[0]} ({GAME_ID}) / "
                f"Current adapter: {GAME_NAME.split(' / ', 1)[-1]} ({GAME_ID})"
            ),
            style="Subtitle.TLabel",
            wraplength=520,
            justify="left",
        ).pack(side="left", padx=(14, 0), pady=(8, 0), fill="x", expand=True)

        setup = ttk.Frame(shell, style="Card.TFrame", padding=14)
        setup.pack(fill="x", pady=(10, 7))
        setup.columnconfigure(1, weight=1)
        ttk.Label(
            setup,
            text=(
                "统一相机运行时 / Unified Camera Runtime：WASD · Space/Q · Mouse · "
                "Shift 5× · Insert On/Off · Home Lock"
            ),
            style="Muted.Card.TLabel",
            wraplength=520,
            justify="left",
        ).grid(row=0, column=0, sticky="ew", padx=(0, 12))
        setup.columnconfigure(0, weight=1)
        self.prepare_button = ttk.Button(
            setup,
            text="检查并注入 / Check & Inject",
            style="Accent.TButton",
            command=self.prepare_bridge,
        )
        self.prepare_button.grid(row=0, column=1, sticky="e", padx=(0, 8))
        self.integration_repair_button = ttk.Button(
            setup,
            text="自动修复并注入 / Repair & Inject",
            command=self.repair_bridge,
        )
        self.integration_repair_button.grid(row=0, column=2, sticky="e", padx=(0, 8))
        ttk.Button(setup, text="刷新 / Refresh", command=self.refresh_status).grid(row=0, column=3)

        status_row = ttk.Frame(setup, style="Card.TFrame")
        status_row.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        self.status_dot = tk.Canvas(status_row, width=12, height=12, bg=CARD, highlightthickness=0)
        self.status_dot.pack(side="left", padx=(2, 8))
        self.status_dot_id = self.status_dot.create_oval(2, 2, 10, 10, fill=ACCENT, outline="")
        ttk.Label(status_row, textvariable=self.status_var, style="Section.Card.TLabel").pack(side="left")
        ttk.Label(status_row, textvariable=self.status_detail_var, style="Muted.Card.TLabel").pack(side="left", padx=(14, 0))

        camera_controls = ttk.Frame(setup, style="Card.TFrame")
        camera_controls.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        ttk.Label(
            camera_controls,
            text="Delete 切换 HUD / Toggle HUD",
            style="Muted.Card.TLabel",
        ).pack(side="left")
        ttk.Label(
            camera_controls,
            textvariable=self.hud_status_var,
            style="State.Card.TLabel",
        ).pack(side="left", padx=(16, 0))
        self.hud_button = ttk.Button(
            camera_controls,
            text="隐藏 HUD / Hide HUD",
            command=self.toggle_hud,
            style="Compact.TButton",
            state="disabled",
        )
        self.hud_button.pack(side="right")

        utility_controls = ttk.Frame(setup, style="Card.TFrame")
        utility_controls.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        self.pin_button = ttk.Button(
            utility_controls,
            textvariable=self.pin_text_var,
            command=self.toggle_always_on_top,
            style="Compact.TButton",
        )
        self.pin_button.pack(side="right", padx=(14, 0))
        ttk.Label(
            utility_controls,
            text="语言 / Language",
            style="Muted.Card.TLabel",
        ).pack(side="right", padx=(14, 6))
        self.language_combo = ttk.Combobox(
            utility_controls,
            textvariable=self.language_var,
            values=("中文", "English"),
            state="readonly",
            width=8,
        )
        self.language_combo.pack(side="right")
        self.language_combo.bind("<<ComboboxSelected>>", self._on_language_changed)

        pose_card = ttk.Frame(shell, style="Card.TFrame", padding=(15, 11))
        pose_card.pack(fill="x", pady=(0, 10))
        ttk.Label(pose_card, text="实时位姿 / Live Pose", style="Section.Card.TLabel").pack(side="left", padx=(0, 18))
        ttk.Label(pose_card, textvariable=self.pose_var, style="Pose.Card.TLabel").pack(side="left")
        ttk.Label(pose_card, textvariable=self.angle_var, style="Pose.Card.TLabel").pack(side="left", padx=(26, 0))
        ttk.Label(pose_card, textvariable=self.camera_state_var, style="State.Card.TLabel").pack(side="right")

        alert_card = ttk.Frame(shell, style="Card.TFrame", padding=(15, 9))
        alert_card.pack(fill="x", pady=(0, 10))
        alert_card.columnconfigure(1, weight=1)
        ttk.Label(
            alert_card,
            text="通知与恢复\nAlerts & Recovery",
            style="Section.Card.TLabel",
        ).grid(row=0, column=0, rowspan=4, sticky="nw", padx=(0, 16))
        ttk.Label(
            alert_card,
            textvariable=self.feishu_status_var,
            style="Muted.Card.TLabel",
            wraplength=700,
            justify="left",
        ).grid(row=0, column=1, sticky="ew")
        ttk.Label(
            alert_card,
            textvariable=self.discord_status_var,
            style="Muted.Card.TLabel",
            wraplength=700,
            justify="left",
        ).grid(row=1, column=1, sticky="ew", pady=(3, 0))
        ttk.Label(
            alert_card,
            textvariable=self.repair_status_var,
            style="Muted.Card.TLabel",
            wraplength=700,
            justify="left",
        ).grid(row=2, column=1, sticky="ew", pady=(3, 0))
        ttk.Label(
            alert_card,
            textvariable=self.alert_config_var,
            style="Muted.Card.TLabel",
            wraplength=700,
            justify="left",
        ).grid(row=3, column=1, sticky="ew", pady=(3, 0))
        self.feishu_test_button = ttk.Button(
            alert_card,
            text="测试飞书 / Test Feishu",
            command=self._send_feishu_test,
            style="Compact.TButton",
            state="normal" if self.feishu_notifier.enabled else "disabled",
        )
        self.feishu_test_button.grid(row=0, column=2, sticky="e", padx=(12, 0))
        self.discord_test_button = ttk.Button(
            alert_card,
            text="测试 Discord / Test Discord",
            command=self._send_discord_test,
            style="Compact.TButton",
            state="normal" if self.discord_notifier.enabled else "disabled",
        )
        self.discord_test_button.grid(row=1, column=2, sticky="e", padx=(12, 0), pady=(3, 0))
        ttk.Button(
            alert_card,
            text="设置指南 / Setup Guide",
            command=self._show_notification_setup_guide,
            style="Compact.TButton",
        ).grid(row=2, column=2, rowspan=2, sticky="e", padx=(12, 0), pady=(3, 0))

        body = ttk.Frame(shell)
        body.pack(fill="both", expand=True)
        # Keep enough horizontal room for the trajectory form.  A 3:2 split
        # made the right panel collapse on a restored 1000-1200px window.
        body.columnconfigure(0, weight=1, minsize=560)
        body.columnconfigure(1, weight=1, minsize=400)
        body.rowconfigure(0, weight=1)

        points_card = ttk.Frame(body, style="Card.TFrame", padding=13)
        points_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        points_card.columnconfigure(0, weight=1)
        # Keep the recorded-point rows readable even when the lower capture
        # controls request more vertical space.
        points_card.rowconfigure(3, weight=1, minsize=190)
        point_header = ttk.Frame(points_card, style="Card.TFrame")
        point_header.grid(row=0, column=0, sticky="ew")
        ttk.Label(point_header, text="静态 22 方向采集 / 22-View Still Capture", style="Section.Card.TLabel").pack(side="left")
        ttk.Label(point_header, textvariable=self.points_count_var, style="Muted.Card.TLabel").pack(side="right")
        ttk.Label(
            point_header,
            textvariable=self.record_hotkey_status_var,
            style="Muted.Card.TLabel",
        ).pack(side="left", padx=(14, 0))
        ttk.Label(
            points_card,
            text="记录或加载空间点位图；每点自动采集 22 个方向。 / Load points; capture 22 views per point.",
            style="Muted.Card.TLabel",
            wraplength=520,
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))

        point_buttons = ttk.Frame(points_card, style="Card.TFrame")
        point_buttons.grid(row=2, column=0, sticky="ew", pady=(8, 8))
        for column in range(3):
            point_buttons.columnconfigure(column, weight=1)
        self.record_point_button = ttk.Button(point_buttons, text="记录点位 / Record Point", style="Accent.TButton", command=self.record_point, state="disabled")
        self.record_point_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ttk.Button(point_buttons, text="加载点位图 / Load Map", command=self.load_point_file).grid(row=0, column=1, sticky="ew", padx=3)
        ttk.Button(
            point_buttons,
            text="打开点位文件 / Open",
            command=self.open_active_point_map,
        ).grid(row=0, column=2, sticky="ew", padx=(3, 0))
        ttk.Button(point_buttons, text="删除所选点位 / Delete Selected", command=self.delete_selected_points).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(5, 0))

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
            "label": "名称 / Name",
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
            text="点位图 → 每点 22 方向 / Point Map → 22 Views",
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
        ttk.Label(static_start, text="从第 / From", style="Muted.AltCard.TLabel").pack(side="left")
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
            text="个点开始 / point · 22 images each",
            style="Muted.AltCard.TLabel",
        ).pack(side="left")
        self.capture_points_button = ttk.Button(
            static_section,
            text="开始静态采集 / Start Still Capture",
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
        depth_controls = ttk.Frame(static_section, style="AltCard.TFrame")
        depth_controls.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        self.depth_checkbox = ttk.Checkbutton(
            depth_controls,
            text="同时保存深度 / Save Depth",
            variable=self.depth_enabled_var,
            command=self._on_depth_toggled,
            state="normal" if self.depth_supported else "disabled",
        )
        self.depth_checkbox.pack(side="left")
        ttk.Label(
            depth_controls,
            textvariable=self.depth_status_var,
            style="Muted.AltCard.TLabel",
        ).pack(side="left", padx=(12, 0))
        self.static_progress = ttk.Progressbar(
            static_section,
            variable=self.static_progress_var,
            mode="determinate",
            maximum=1,
            style="Capture.Horizontal.TProgressbar",
        )
        self.static_progress.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(7, 0))
        ttk.Label(
            static_section,
            textvariable=self.static_progress_text_var,
            style="Muted.AltCard.TLabel",
            wraplength=250,
        ).grid(row=4, column=0, sticky="w", pady=(3, 0))
        ttk.Label(
            static_section,
            textvariable=self.static_output_var,
            style="Muted.AltCard.TLabel",
            wraplength=250,
        ).grid(row=4, column=1, sticky="e", pady=(3, 0))
        ttk.Button(
            static_section,
            text="打开图片目录 / Open Images",
            command=self.open_static_output,
            style="Compact.TButton",
        ).grid(row=5, column=0, sticky="ew", padx=(0, 3), pady=(5, 0))
        self.static_stop_button = ttk.Button(
            static_section,
            text="停止静态采集 / Stop Still Capture",
            command=self.stop_capture,
            style="Compact.TButton",
            state="disabled",
        )
        self.static_stop_button.grid(row=5, column=1, sticky="ew", padx=(3, 0), pady=(5, 0))
        ttk.Label(
            static_section,
            textvariable=self.static_resume_var,
            style="Muted.AltCard.TLabel",
            wraplength=520,
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(5, 0))
        self.static_resume_button = ttk.Button(
            static_section,
            text="继续上次静态采集 / Resume Still Capture",
            command=self.resume_static_22_capture,
            style="Compact.TButton",
            state="disabled",
        )
        self.static_resume_button.grid(
            row=7,
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
        ttk.Label(
            actions,
            text="连续轨迹采集 / Continuous Trajectory Capture",
            style="Section.Card.TLabel",
            wraplength=420,
            justify="left",
        ).grid(row=0, column=0, sticky="ew")
        ttk.Label(
            actions,
            text="选择文件后自动定位、录像并保存 Pose。 / Auto-position, record and save Pose from the selected file.",
            style="Muted.Card.TLabel",
            wraplength=420,
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
            text="轨迹文件 / Trajectory File",
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
            text="浏览 / Browse…",
            command=self.load_trajectory_file,
            style="Compact.TButton",
        ).grid(row=2, column=0, sticky="ew", padx=(0, 3), pady=(5, 0))
        ttk.Button(
            trajectory_section,
            text="刷新 / Refresh",
            command=self._refresh_trajectory_choices,
            style="Compact.TButton",
        ).grid(row=2, column=1, sticky="ew", padx=(3, 0), pady=(5, 0))
        ttk.Label(
            trajectory_section,
            textvariable=self.trajectory_var,
            style="Muted.AltCard.TLabel",
            wraplength=420,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(5, 0))

        capture_config = ttk.Frame(actions, style="Card.TFrame")
        capture_config.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        capture_config.columnconfigure(1, weight=1)
        ttk.Label(capture_config, text="从第 / From", style="Muted.Card.TLabel").grid(row=0, column=0, sticky="w")
        self.trajectory_index_spin = ttk.Spinbox(capture_config, from_=1, to=1, textvariable=self.trajectory_index_var, width=7)
        self.trajectory_index_spin.grid(row=0, column=1, sticky="w", padx=(6, 8))
        ttk.Label(
            capture_config,
            text="条开始到末尾 / trajectory to end",
            style="Muted.Card.TLabel",
            wraplength=220,
            justify="left",
        ).grid(row=0, column=2, columnspan=3, sticky="w")
        ttk.Label(capture_config, text="场景 / Scene", style="Muted.Card.TLabel").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(capture_config, textvariable=self.scene_id_var, width=12).grid(row=1, column=1, columnspan=4, sticky="ew", padx=(6, 0), pady=(6, 0))
        ttk.Label(capture_config, text="OBS", style="Muted.Card.TLabel").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(capture_config, textvariable=self.obs_host_var, width=12).grid(row=2, column=1, columnspan=2, sticky="ew", padx=(6, 4), pady=(6, 0))
        ttk.Entry(capture_config, textvariable=self.obs_port_var, width=6).grid(row=2, column=3, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Label(capture_config, text="密码 / Password", style="Muted.Card.TLabel").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(capture_config, textvariable=self.obs_password_var, width=12, show="•").grid(row=3, column=1, columnspan=4, sticky="ew", pady=(6, 0))
        ttk.Label(
            capture_config,
            text=(
                f"OBS 自动重启 / Auto-restart：每 / every "
                f"{float(self.settings.get('trajectory_obs_restart_interval_sec', 30.0)):.0f}s "
                "分段 / segmented"
            ),
            style="Muted.Card.TLabel",
            wraplength=420,
            justify="left",
        ).grid(row=4, column=0, columnspan=5, sticky="w", pady=(6, 0))

        self.continuous_capture_button = ttk.Button(
            actions,
            text="开始连续采集 / Start Continuous Capture",
            style="CompactAccent.TButton",
            command=self.start_continuous_trajectory_capture,
            state="disabled",
        )
        self.continuous_capture_button.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        task_buttons = ttk.Frame(actions, style="Card.TFrame")
        task_buttons.grid(row=6, column=0, sticky="ew", pady=(5, 0))
        task_buttons.columnconfigure(0, weight=1)
        self.resume_capture_button = ttk.Button(task_buttons, text="继续未完成批次 / Resume", command=self.resume_trajectory_capture, style="Compact.TButton")
        self.resume_capture_button.grid(row=0, column=0, sticky="ew")

        progress_frame = ttk.Frame(actions, style="Card.TFrame")
        progress_frame.grid(row=7, column=0, sticky="ew", pady=(7, 0))
        progress_frame.columnconfigure((0, 1), weight=1)
        ttk.Label(progress_frame, textvariable=self.output_var, style="Muted.Card.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        self.task_progress = ttk.Progressbar(progress_frame, variable=self.task_progress_var, mode="determinate", maximum=1, style="Capture.Horizontal.TProgressbar")
        self.task_progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Label(progress_frame, textvariable=self.task_progress_text_var, style="Muted.Card.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.frame_progress = ttk.Progressbar(progress_frame, variable=self.frame_progress_var, mode="determinate", maximum=1, style="Capture.Horizontal.TProgressbar")
        self.frame_progress.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Label(progress_frame, textvariable=self.frame_progress_text_var, style="Muted.Card.TLabel").grid(row=4, column=0, columnspan=2, sticky="w", pady=(2, 0))
        ttk.Button(progress_frame, text="打开输出 / Open Output", command=self.open_capture_output, style="Compact.TButton").grid(row=5, column=0, sticky="ew", padx=(0, 3), pady=(4, 0))
        self.stop_button = ttk.Button(progress_frame, text="停止采集 / Stop", style="Compact.TButton", command=self.stop_capture, state="disabled")
        self.stop_button.grid(row=5, column=1, sticky="ew", padx=(3, 0), pady=(4, 0))

        log_card = ttk.Frame(shell, style="Card.TFrame", padding=(12, 8))
        log_card.pack(fill="x", pady=(10, 0))
        self.log_text = tk.Text(log_card, height=2, bg="#0c1117", fg=MUTED, insertbackground=TEXT, relief="flat", borderwidth=0, font=("Microsoft YaHei UI", 9), padx=8, pady=5, state="disabled")
        self.log_text.pack(fill="x")
        self.log("统一采集器已启动 / Unified studio started. 建议使用无边框窗口模式 / Borderless mode recommended.")

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

    def _apply_always_on_top(self) -> None:
        enabled = bool(self.always_on_top_var.get())
        self.root.attributes("-topmost", enabled)
        self.pin_text_var.set(
            "置顶采集窗口：开 / Keep Capture Studio on Top: On"
            if enabled
            else "置顶采集窗口：关 / Keep Capture Studio on Top: Off"
        )

    def toggle_always_on_top(self) -> None:
        self.always_on_top_var.set(not self.always_on_top_var.get())
        self._apply_always_on_top()
        self._persist_settings()
        state = "开启 / On" if self.always_on_top_var.get() else "关闭 / Off"
        self.log(f"窗口置顶 / Topmost：{state}")

    def _update_depth_status(self) -> None:
        if not self.depth_supported:
            self.depth_status_var.set(
                "当前深度桥仅支持 Windows / Depth bridge is Windows-only"
            )
        elif self.depth_enabled_var.get():
            depth_status = self.depth_bridge.status()
            latest = (
                depth_status.get("runtime")
                or depth_status.get("latest_response")
                or depth_status.get("last_capture")
            )
            bridge_state = (
                str(latest.get("state") or latest.get("status") or "unknown")
                if isinstance(latest, dict)
                else "waiting"
            )
            self.depth_status_var.set(
                "自研 D3D12 原始深度 · "
                f"{bridge_state} / Native D3D12 raw depth · {bridge_state}"
            )
        else:
            self.depth_status_var.set("深度已关闭 / Depth disabled")

    def _on_depth_toggled(self) -> None:
        self._update_depth_status()
        self._persist_settings()

    def _persist_settings(self) -> None:
        self.settings["language"] = self.language
        self.settings["obs_host"] = self.obs_host_var.get().strip()
        self.settings["obs_port"] = int(self.obs_port_var.get().strip() or "4455")
        self.settings["scene_id"] = self.scene_id_var.get().strip() or "scene_1"
        self.settings["autoload_trajectory"] = (
            str(self.trajectory_path) if self.trajectory_path is not None else ""
        )
        self.settings["always_on_top"] = bool(self.always_on_top_var.get())
        self.settings["depth_enabled"] = bool(self.depth_enabled_var.get())
        save_settings(self.settings)

    def _refresh_trajectory_choices(
        self,
        preferred: str | Path | None = None,
    ) -> None:
        if self.capture_busy:
            self.log("采集进行中，暂不刷新轨迹列表。 / Capture active; trajectory list refresh skipped.")
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
            self.trajectory_var.set("未发现轨迹；点击“浏览…”添加 JSON/CSV / No trajectory found; use Browse")
            return
        try:
            self._load_trajectory_path(target)
            self.log(f"已自动加载轨迹 / Auto-loaded trajectory: {target}")
        except Exception as exc:
            self.log(f"自动加载轨迹失败 / Auto-load failed: {exc}")

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
            self.log(f"已切换轨迹 / Trajectory selected: {selected}")
        except Exception as exc:
            if self.trajectory_path is not None:
                self._set_trajectory_choice_path(self.trajectory_path)
            self._show_error("加载轨迹失败 / Load Trajectory Failed", exc)

    def prepare_bridge(self) -> None:
        def work() -> dict[str, object]:
            return repair_and_inject(auto_repair=False)

        def success(result: dict[str, object]) -> None:
            self.log(
                f"预检通过并已注入 / Preflight passed and injected：PID {result['pid']} · "
                f"profile={result.get('profile')} · "
                f"Camera matches={result.get('camera_match_count')}"
            )
            self.refresh_status()

        self._background_action("正在扫描并注入… / Scanning, validating, and injecting…", work, success)

    def repair_bridge(self) -> None:
        self.integration_repair_button.configure(state="disabled")

        def work() -> dict[str, object]:
            return repair_and_inject(auto_repair=True)

        def success(result: dict[str, object]) -> None:
            rebuilt = "；已重新构建 / rebuilt" if result.get("native_rebuilt") else ""
            self.log(
                f"自动修复并注入完成 / Repair and injection complete：PID {result['pid']} · "
                f"profile={result.get('profile')}{rebuilt}"
            )
            self.integration_repair_button.configure(state="normal")
            self.refresh_status()

        def failed(exc: Exception) -> None:
            self.integration_repair_button.configure(state="normal")
            self._show_error("自动修复未完成 / Repair Incomplete", exc)

        self._background_action(
            "正在自动修复并验证… / Repairing and validating…",
            work,
            success,
            on_error=failed,
        )

    def refresh_status(self) -> None:
        if self.status_refresh_inflight or self.closing:
            return
        self.status_refresh_inflight = True

        def work() -> ConnectionReport:
            return probe_connection(self.bridge)

        def success(report: ConnectionReport) -> None:
            self.status_refresh_inflight = False
            self._apply_connection_report(report)
            self._update_depth_status()

        def finished_with_error(exc: Exception) -> None:
            self.status_refresh_inflight = False
            self.log(f"状态检查失败 / Status check failed: {exc}")
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
            self.log(f"连接状态 / Connection：{report.title}；{report.detail}")
            self.last_connection_code = report.code

        self.prepare_button.configure(
            state=(
                "normal"
                if report.code in {
                    "bridge_needed",
                    "linux_bridge_waiting",
                    "platform_unsupported",
                }
                else "disabled"
            )
        )
        self.integration_repair_button.configure(
            state=(
                "normal"
                if report.code in {
                    "integration_repair_needed",
                    "bridge_needed",
                    "hook_unavailable",
                    "hud_control_unavailable",
                }
                else "disabled"
            )
        )
        pose_available = report.pose is not None
        hud_ready = bool(report.metadata and report.metadata.hud_control_ready)
        self.hud_button.configure(state="normal" if hud_ready else "disabled")
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
            self.camera_state_var.set("Pose 未连接 / Disconnected")
            self.hud_status_var.set("HUD：等待 Runtime / Waiting")

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
                if pose.input_captured:
                    state += " · 输入独占 / Input Captured"
                self.camera_state_var.set(state)
            else:
                self.camera_state_var.set("Camera OFF · 按 Insert / Press Insert")
            self.hud_status_var.set("HUD：已隐藏 / Hidden" if pose.hud_hidden else "HUD：显示中 / Visible")
            self._set_localized_widget_text(
                self.hud_button,
                "显示 HUD / Show HUD" if pose.hud_hidden else "隐藏 HUD / Hide HUD",
            )
        except (PoseUnavailableError, OSError, ValueError):
            self.current_pose = None
            if self.connection_report is None or self.connection_report.pose is None:
                self.camera_state_var.set("Pose 未连接 / Disconnected")
        self.root.after(250, self._poll_pose)

    def toggle_hud(self) -> None:
        if self.current_pose is None:
            self._show_guidance("HUD 控制尚未连接，请先注入 Runtime。 / HUD control is not connected; inject the runtime first.")
            return
        target_hidden = not self.current_pose.hud_hidden
        self.hud_button.configure(state="disabled")

        def work() -> object:
            return self.bridge.set_hud_hidden(target_hidden)

        def success(_result: object) -> None:
            self.hud_status_var.set("HUD：已隐藏 / Hidden" if target_hidden else "HUD：显示中 / Visible")
            self._set_localized_widget_text(
                self.hud_button,
                "显示 HUD / Show HUD" if target_hidden else "隐藏 HUD / Hide HUD",
            )
            self.hud_button.configure(state="normal")
            self.log("已隐藏 HUD / HUD hidden" if target_hidden else "已恢复 HUD / HUD restored")

        def failed(exc: Exception) -> None:
            self.hud_button.configure(state="normal")
            self._show_error("HUD 切换失败 / HUD Toggle Failed", exc)

        self._background_action(
            "正在切换 HUD… / Toggling HUD…",
            work,
            success,
            on_error=failed,
        )

    def record_point(self) -> None:
        if self.capture_busy:
            self.log("采集运行中，已忽略记录点位。 / Capture active; record-point request ignored.")
            return
        if self.connection_report is None or self.connection_report.pose is None:
            self._show_guidance("Pose 尚未连接，请先完成连接。 / Pose is disconnected; complete connection first.")
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
            self._show_error("点位写入失败 / Point Write Failed", exc)
            return
        self.points = updated_points
        self._refresh_point_tree()
        self.log(f"已记录点位 / Point recorded to {ACTIVE_POINT_MAP_PATH.name}: point_{index:04d}")

    def _start_record_point_hotkey(self) -> None:
        self.record_hotkey_status_var.set("游戏内 E：等待 Runtime / Waiting for Runtime")
        self.root.after(50, self._poll_record_point_hotkey)

    def _poll_record_point_hotkey(self) -> None:
        if self.closing:
            return
        supported, triggered = self.bridge.poll_record_point_hotkey()
        if supported:
            self.record_hotkey_status_var.set("游戏内 E：记录点位 / Record point")
        else:
            self.record_hotkey_status_var.set("游戏内 E：等待新版 Runtime / Update Runtime")
        if triggered:
            self._record_point_from_game_hotkey()
        self.root.after(50, self._poll_record_point_hotkey)

    def _record_point_from_game_hotkey(self) -> None:
        try:
            game_pid = find_game_pid()
        except RuntimeError as exc:
            self.log(f"E 未记录 / E record failed: {exc}")
            return
        if (
            not getattr(self.bridge, "is_linux_relay", False)
            and foreground_process_id() != game_pid
        ):
            self.log("E 未记录：前台不是当前游戏。 / E ignored because the selected game is not foreground.")
            return
        self.record_point()

    def load_point_file(self) -> None:
        if self.capture_busy:
            self.log("采集进行中，不能切换点位图。 / Capture active; point map cannot be changed.")
            return
        selected = filedialog.askopenfilename(
            title="加载点位文件 / Load Point File",
            initialdir=POINT_FILES_DIR,
            filetypes=[("点位文件 / Point Files", "*.json *.csv"), ("JSON", "*.json"), ("CSV", "*.csv")],
        )
        if not selected:
            return
        try:
            imported_points = load_points(selected)
            self._write_active_point_map(imported_points)
            self.points = imported_points
            self._refresh_point_tree()
            self.log(
                f"已导入 / Imported {len(self.points)} points；同步到 / synced to "
                f"{ACTIVE_POINT_MAP_PATH.name}：{selected}"
            )
        except Exception as exc:
            self._show_error("加载点位失败 / Load Points Failed", exc)

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
                f"实时点位文件已就绪 / Live point file ready: {ACTIVE_POINT_MAP_PATH} "
                f"({len(self.points)} points)"
            )
        except Exception as exc:
            self.point_map_path = ACTIVE_POINT_MAP_PATH.resolve()
            self.point_map_dirty = True
            self._refresh_point_tree()
            self.log(f"读取实时点位失败 / Live point-file read failed: {exc}")

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
            self._show_error("打开点位文件失败 / Open Point File Failed", exc)

    def delete_selected_points(self) -> None:
        if self.capture_busy:
            self.log("采集进行中，不能修改点位图。 / Capture active; point map cannot be edited.")
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
            self._show_error("删除点位失败 / Delete Point Failed", exc)
            return
        self.points = updated_points
        self._refresh_point_tree()
        self.log(
            f"已删除并同步 / Deleted and synced {ACTIVE_POINT_MAP_PATH.name}；"
            f"剩余 / Remaining: {len(self.points)} points."
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
            f"{point_count} 个空间点 · 预计 {image_count} 张图片 / "
            f"{point_count} points · estimated {image_count} images"
        )
        self.static_start_point_spin.configure(to=max(1, point_count))
        if self.static_start_point_var.get() > max(1, point_count):
            self.static_start_point_var.set(max(1, point_count))
        if point_count == 0:
            self.point_map_var.set("尚未记录或加载点位图 / No point map")
        else:
            source = self.point_map_path.name if self.point_map_path is not None else "内存点位图"
            source_en = self.point_map_path.name if self.point_map_path is not None else "In-memory map"
            dirty_zh = "写入异常" if self.point_map_dirty else "自动保存"
            dirty_en = "Write error" if self.point_map_dirty else "Autosaved"
            self.point_map_var.set(
                f"{source} · {dirty_zh} · {point_count} 个点 × 22 = {image_count} 张图片 / "
                f"{source_en} · {dirty_en} · {point_count} points × 22 = {image_count} images"
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
            self.static_resume_var.set("没有可继续的静态任务 / No resumable still run")
        else:
            info = self.static_resume_info
            self.static_resume_var.set(
                f"可继续上次静态采集：已完成 {info['last_sample']}/{info['requested_count']}，"
                f"下次 {info['next_sample']} / "
                f"Resumable still run: completed {info['last_sample']}/{info['requested_count']}, "
                f"next {info['next_sample']}"
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
            self._show_info("正在采集 / Capture Active", "请等待当前任务结束。 / Wait for the current task to finish.")
            return
        self._refresh_static_resume()
        if self.static_resume_info is None:
            self._show_info("没有可继续任务 / Nothing to Resume", "未找到匹配的失败或停止任务。 / No matching failed or stopped run was found.")
            return
        selected_start = int(self.static_resume_info.get("selected_start_ordinal") or 1)
        self.static_start_point_var.set(selected_start)
        self.start_static_22_capture(resume_info=self.static_resume_info)

    def load_trajectory_file(self) -> None:
        if self.capture_busy:
            self.log("采集进行中，不能切换轨迹。 / Capture active; trajectory cannot be changed.")
            return
        selected = filedialog.askopenfilename(
            title="选择轨迹文件 / Select Trajectory File",
            initialdir=(
                self.trajectory_path.parent
                if self.trajectory_path is not None
                else PROJECT_ROOT / "examples" / "trajectory_files"
            ),
            filetypes=[("轨迹文件 / Trajectory Files", "*.json *.csv"), ("JSON", "*.json"), ("CSV", "*.csv")],
        )
        if not selected:
            return
        try:
            self._load_trajectory_path(Path(selected))
            self.log(f"轨迹已加载 / Trajectory loaded: {selected}")
        except Exception as exc:
            self._show_error("加载轨迹失败 / Load Trajectory Failed", exc)

    def _load_trajectory_path(self, path: Path) -> None:
        self.trajectories = load_trajectories(path)
        self.trajectory_path = path.resolve()
        self._set_trajectory_choice_path(self.trajectory_path)
        point_total = sum(len(trajectory.points) for trajectory in self.trajectories)
        self.trajectory_var.set(
            f"已加载：{path.name} · {len(self.trajectories)} 条轨迹 · {point_total} 个关键帧 / "
            f"Loaded: {path.name} · {len(self.trajectories)} trajectories · {point_total} keyframes"
        )
        self.trajectory_index_spin.configure(to=max(1, len(self.trajectories)))
        self.trajectory_index_var.set(1)
        self._persist_settings()
        ready = self.connection_report is not None and self.connection_report.ready
        state = "normal" if ready and not self.capture_busy else "disabled"
        self.continuous_capture_button.configure(state=state)

    def _validate_trajectory_capture(self) -> int:
        if not self.trajectories or self.trajectory_path is None:
            raise UserActionRequired("请先选择轨迹 JSON/CSV。 / Select a trajectory JSON/CSV first.")
        if self.connection_report is None or not self.connection_report.ready:
            raise UserActionRequired(
                self.connection_report.detail
                if self.connection_report is not None
                else "正在检查 Runtime 连接，请稍候。 / Checking runtime connection…"
            )
        pid = self.connection_report.pid or find_game_pid()
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
            self._show_info("正在采集 / Capture Active", "请停止或等待当前任务。 / Stop or wait for the current task.")
            return
        try:
            pid = self._validate_trajectory_capture()
        except UserActionRequired as exc:
            self._show_guidance(str(exc))
            return
        except Exception as exc:
            self._show_error("轨迹采集准备失败 / Trajectory Setup Failed", exc)
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
            self._show_info("没有待采轨迹 / No Pending Trajectory", "当前批次已完成。 / This batch is complete.")
            return

        scene_id = self.scene_id_var.get().strip() or "scene_1"
        try:
            obs_restart_interval = max(
                0.0,
                float(self.settings.get("trajectory_obs_restart_interval_sec", 30.0)),
            )
            obs_restart_manager = (
                OBSProcessRestarter(
                    obs_factory=self._make_obs,
                    host=self.obs_host_var.get().strip() or "127.0.0.1",
                    command=str(self.settings.get("obs_restart_command") or ""),
                    wait_seconds=float(self.settings.get("obs_restart_wait_sec", 20.0)),
                )
                if obs_restart_interval > 0.0
                else None
            )
            if obs_restart_manager is not None:
                obs_restart_manager.validate()
        except Exception as exc:
            self._show_error("OBS 重启配置无效 / Invalid OBS Restart Settings", exc)
            return

        def restart_obs_for_trajectory(output_dir: Path) -> OBSBridge:
            if obs_restart_manager is None:
                raise RuntimeError("OBS 定时重启未初始化 / OBS restart is not initialized")
            self.log(
                f"录像达到 {obs_restart_interval:.0f}s，重启 OBS。 / Restarting OBS after the segment interval."
            )
            candidate = obs_restart_manager.restart(
                log_path=output_dir / "obs_restart.log"
            )
            try:
                # RE9 re-activates the game window after OBS relaunch.  The
                # native bridge does not need focus for pose control, but
                # refocusing keeps the game's render/input state predictable.
                focus_game_window(pid)
            except Exception as exc:
                self.log(f"OBS 重启后恢复窗口提示 / Refocus warning after OBS restart: {exc}")
            if not isinstance(candidate, OBSBridge):
                # Keep the callback contract honest for future adapters while
                # allowing test doubles to be used in the recorder tests.
                return candidate  # type: ignore[return-value]
            return candidate

        self.stop_event.clear()
        self.active_capture_kind = "trajectory"
        self._set_capture_busy(True)
        self.task_progress_var.set(0)
        self.frame_progress_var.set(0)
        count = len(planned)
        self.task_progress.configure(maximum=max(1, count))
        self.frame_progress.configure(maximum=1)
        self.task_progress_text_var.set(
            f"任务进度：0/{count} / Task: 0/{count}"
        )
        self.frame_progress_text_var.set(
            "当前轨迹：准备 OBS、静音和 Pose / "
            "Trajectory: preparing OBS, mute, and Pose"
        )
        recorder = BatchTrajectoryRecorder(
            bridge=self.bridge,
            mover_factory=lambda: self._make_mover(pid),
            obs_factory=self._make_obs,
            scene_id=scene_id,
            obs_restart_factory=(
                restart_obs_for_trajectory if obs_restart_manager is not None else None
            ),
            obs_restart_interval_seconds=obs_restart_interval,
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
                    f"任务进度：{position}/{len(planned)}；全局 {index + 1}/{total} · {phase} / "
                    f"Task: {position}/{len(planned)}; global {index + 1}/{total} · {phase}"
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
            f"开始连续采集 / Continuous capture started：from {planned[0] + 1}, total {len(planned)}；"
            f"OBS muted before recording; restart every {obs_restart_interval:.0f}s."
        )

    def resume_trajectory_capture(self) -> None:
        scene_id = self.scene_id_var.get().strip() or "scene_1"
        resume = find_latest_resumable_batch(scene_id)
        if resume is None:
            self._show_info("没有待续任务 / Nothing to Resume", f"场景 / Scene {scene_id} 没有未完成批次 / has no incomplete batch.")
            return
        source = Path(resume["source_path"])
        try:
            self._load_trajectory_path(source)
        except Exception as exc:
            self._show_error("读取续采源文件失败 / Resume Source Read Failed", exc)
            return
        if int(resume["total"]) != len(self.trajectories):
            self._show_error(
                "续采轨迹集不匹配 / Resume Set Mismatch",
                RuntimeError("源文件轨迹数量与清单不一致 / Source trajectory count differs from the manifest"),
            )
            return
        self._start_trajectory_capture(resume=resume)

    def _update_trajectory_frame(self, done: int, total: int, message: str) -> None:
        self.frame_progress.configure(maximum=max(1, total))
        self.frame_progress_var.set(done)
        self.frame_progress_text_var.set(
            f"当前轨迹：{done}/{total} 个采样 · {message} / "
            f"Trajectory: {done}/{total} samples · {message}"
        )

    def _trajectory_capture_finished(self, result: dict[str, object]) -> None:
        self.active_capture_kind = None
        self._set_capture_busy(False)
        self.batch_recorder = None
        requested = int(result.get("requested_trajectories") or 0)
        completed = int(result.get("completed_trajectories") or 0)
        failed = int(result.get("failed_trajectories") or 0)
        self.task_progress_var.set(completed + failed)
        status_zh, status_en = (
            ("已停止", "Stopped")
            if result.get("stopped")
            else ("已完成", "Completed")
        )
        self.task_progress_text_var.set(
            f"任务进度：{completed + failed}/{requested}（{status_zh}） / "
            f"Task: {completed + failed}/{requested} ({status_en})"
        )
        self.frame_progress_text_var.set("当前轨迹：空闲 / Trajectory: Idle")
        output = result.get("output_dir")
        if output:
            self._set_output_path(Path(str(output)))
        self.log(
            f"轨迹批次：{status_zh} · 完成 {completed}，失败 {failed}；{output} / "
            f"Trajectory batch: {status_en} · completed {completed}, failed {failed}; {output}"
        )

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
        self.task_progress_text_var.set(
            f"任务进度：失败 — {exc} / Task: Failed — {exc}"
        )
        self.frame_progress_text_var.set("当前轨迹：已停止 / Trajectory: Stopped")
        self._show_error("轨迹采集失败 / Trajectory Capture Failed", exc)

    def _set_output_path(self, path: Path) -> None:
        self.latest_capture_output_dir = path.resolve()
        self.output_var.set(f"输出批次 / Output：{self.latest_capture_output_dir.name}")
        self.log(f"输出目录 / Output directory：{self.latest_capture_output_dir}")

    def _set_static_output_path(self, path: Path) -> None:
        self.latest_static_output_dir = path.resolve()
        self.static_output_var.set(f"输出 / Output：{self.latest_static_output_dir.name}")
        self.log(f"静态图片目录 / Still image directory：{self.latest_static_output_dir}")

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
            self._show_info("正在采集 / Capture Active", "请等待或停止当前任务。 / Wait for or stop the current task.")
            return
        if not self.points:
            self._show_info("没有点位图 / No Point Map", "请先记录或加载点位。 / Record or load points first.")
            return
        if self.connection_report is None or not self.connection_report.ready:
            detail = (
                self.connection_report.detail
                if self.connection_report is not None
                else "正在检查连接，请稍候。 / Checking connection…"
            )
            self._show_guidance(detail)
            return
        try:
            pid = self.connection_report.pid or find_game_pid()
            self.bridge.read_pose()
        except (PoseUnavailableError, CameraIntegrationError, RuntimeError) as exc:
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
            self._show_error_message("起始点无效 / Invalid Start Point", "起始点必须是有效整数。 / Start point must be an integer.")
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
                self._show_error_message(
                    "续采计划不匹配 / Resume Plan Mismatch",
                    "点位图与原任务的 22 方向计划不同。 / Point map differs from the original 22-view plan.",
                )
                return
            sample_offset = max(0, int(resume_info.get("next_sample", 1)) - 1)
            if sample_offset >= len(all_samples):
                self._refresh_static_resume()
                self._show_info("没有待采样本 / No Pending Samples", "上次静态任务已完成。 / The previous still run is complete.")
                return
        samples = all_samples[sample_offset:]
        if not samples:
            self._show_info("没有待采样本 / No Pending Samples", "起始点之后没有可采点位。 / No points remain after the selected start.")
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
            f"静态采集 / Still：{sample_offset}/{len(all_samples)} images · "
            f"{len(spatial_points)} points"
        )
        mover = self._make_mover(pid)
        # Still datasets are always Full HD JPG, even when an older settings
        # file still contains the previous PNG/2K values.
        image_format = "jpg"
        self.settings["screenshot_format"] = image_format
        depth_enabled = bool(self.depth_enabled_var.get())
        depth_timeout = float(self.settings.get("depth_timeout_seconds", 8.0))
        self._persist_settings()

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
                    depth_bridge=self.depth_bridge,
                    depth_enabled=depth_enabled,
                    depth_timeout=depth_timeout,
                    screenshot_source=obs_source,
                    screenshot_width=obs_width,
                    screenshot_height=obs_height,
                    screenshot_quality=100,
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
                        "depth_enabled": depth_enabled,
                        "depth_timeout_seconds": depth_timeout,
                        "depth_space": (
                            "raw_device_depth" if depth_enabled else None
                        ),
                        "metric_depth": False,
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
            f"开始静态 22 方向采集 / Starting 22-view capture：from point {start_ordinal}，"
            f"{len(spatial_points)} points，{len(samples)} images."
        )

    def stop_capture(self) -> None:
        self.stop_event.set()
        if self.batch_recorder is not None and self.batch_recorder.active:
            self.batch_recorder.request_stop()
        if self.active_capture_kind == "static22":
            self.static_progress_text_var.set(
                "静态采集：正在停止… / Still capture: stopping…"
            )
        else:
            self.frame_progress_text_var.set("当前轨迹：正在停止… / Trajectory: stopping safely…")
        self.log("已请求停止。 / Stop requested.")

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
            f"静态采集：{done}/{total} 张图片 · 点位 {completed_points}/{spatial_point_count} · {message} / "
            f"Still capture: {done}/{total} images · points {completed_points}/{spatial_point_count} · {message}"
        )

    def _static_capture_finished(self, result: CaptureRunResult) -> None:
        self.active_capture_kind = None
        self._set_capture_busy(False)
        status_zh, status_en = (
            ("已停止", "Stopped") if result.stopped else ("采集完成", "Completed")
        )
        self.static_progress_var.set(result.captured_count)
        self.static_progress_text_var.set(
            f"静态采集：{status_zh} · {result.captured_count}/{result.requested_count} 张图片 / "
            f"Still capture: {status_en} · {result.captured_count}/{result.requested_count} images"
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
            f"静态采集：{status_zh} · {completed_total}/{total} 张图片 / "
            f"Still capture: {status_en} · {completed_total}/{total} images"
        )
        self._refresh_static_resume()
        self.latest_static_output_dir = result.session_dir.resolve()
        self._set_static_output_path(result.session_dir)
        self.log(
            f"{status_zh}：{result.session_dir} / "
            f"{status_en}: {result.session_dir}"
        )
        if not result.stopped:
            self._show_info(
                "静态 22 方向采集完成 / 22-View Capture Complete",
                f"已保存 / Saved {result.captured_count} images.\n\n{result.session_dir}",
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
        self.static_progress_text_var.set(f"静态采集失败 / Still capture failed：{exc}")
        self._show_error("静态 22 方向采集失败 / 22-View Capture Failed", exc)

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
                    self.root.after(0, lambda error=exc: self._show_error("操作失败 / Operation Failed", error))
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
        self.log_text.insert("end", f"[{stamp}] {self._localize_text(message)}\n")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 160:
            self.log_text.delete("1.0", "40.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _show_info(self, title: str, message: str) -> None:
        messagebox.showinfo(
            self._localize_text(title),
            self._localize_text(message),
        )

    def _ask_yes_no(self, title: str, message: str) -> bool:
        return bool(
            messagebox.askyesno(
                self._localize_text(title),
                self._localize_text(message),
            )
        )

    def _show_error_message(self, title: str, message: str) -> None:
        messagebox.showerror(
            self._localize_text(title),
            self._localize_text(message),
        )

    def _show_error(self, title: str, exc: Exception) -> None:
        localized_title = self._localize_text(title)
        localized_error = self._localize_text(exc)
        self.log(f"{title}：{exc}")
        self._notify_failure(title, exc)
        messagebox.showerror(localized_title, localized_error)

    def _notify_failure(self, title: str, exc: Exception) -> None:
        fields = {
            "Adapter": GAME_ID,
            "Scene": self.scene_id_var.get(),
            "Capture kind": self.active_capture_kind or "idle",
            "Config": (
                Path(self.shared_config.source_text).name
                if self.shared_config.path is not None
                else "defaults"
            ),
        }
        try:
            if self.discord_notifier.notify_error(title, str(exc), fields=fields):
                self.log("已排队发送 Discord 错误报警 / Discord alert queued.")
        except Exception as notify_error:
            self.log(f"Discord 报警排队失败 / queue failed: {type(notify_error).__name__}")
        try:
            if self.feishu_notifier.notify_error(title, str(exc), fields=fields):
                self.log("已排队发送飞书错误报警 / Feishu alert queued.")
        except Exception as notify_error:
            self.log(f"飞书报警排队失败 / Feishu queue failed: {type(notify_error).__name__}")
        try:
            if self.codex_recovery.trigger(title, str(exc), fields=fields):
                self.log("已启动后台自动修复 / Recovery started; see capture_data/logs.")
        except Exception as repair_error:
            self.log(f"自动修复启动失败 / Recovery start failed: {type(repair_error).__name__}")

    def _send_feishu_test(self) -> None:
        if not self.feishu_notifier.enabled:
            self._show_guidance(
                "飞书报警未启用；点击“设置指南 / Setup Guide”配置 webhook，"
                "然后重启界面。 / Configure the webhook, then restart the studio."
            )
            return
        self.feishu_test_button.configure(state="disabled")
        self.feishu_status_var.set("飞书报警：正在发送 / Feishu: sending test…")

        def worker() -> None:
            sent = self.feishu_notifier.send_error(
                "Unified Camera Capture Studio test alert",
                f"统一游戏相机采集器飞书测试成功。Adapter: {GAME_ID}",
                fields={
                    "Adapter": GAME_ID,
                    "Config": (
                        self.shared_config.path.name
                        if self.shared_config.path is not None
                        else "defaults"
                    ),
                },
            )
            self.root.after(0, lambda: self._set_feishu_test_result(sent))

        threading.Thread(target=worker, name="unified-feishu-test", daemon=True).start()

    def _set_feishu_test_result(self, sent: bool) -> None:
        self.feishu_test_button.configure(state="normal")
        if sent:
            self.feishu_status_var.set(f"{self.feishu_notifier.status_text}；测试已送达 / delivered")
            self._show_info(
                "飞书测试成功 / Feishu Test Passed",
                "测试报警已发送到飞书机器人。\nTest alert was delivered to Feishu.",
            )
            return
        self.feishu_status_var.set(f"{self.feishu_notifier.status_text}；测试失败 / failed")
        self._show_error_message(
            "飞书测试失败 / Feishu Test Failed",
            f"测试消息发送失败，请查看日志。\nDelivery failed; see {self.feishu_notifier.log_path}.",
        )

    def _send_discord_test(self) -> None:
        if not self.discord_notifier.enabled:
            self._show_guidance(
                "Discord 未启用；点击“设置指南 / Setup Guide”配置 webhook，"
                "然后重启界面。 / Configure the webhook, then restart the studio."
            )
            return
        self.discord_test_button.configure(state="disabled")
        self.discord_status_var.set("Discord：正在发送 / Sending test…")

        def worker() -> None:
            sent = self.discord_notifier.send_error(
                "Unified Camera Capture Studio test alert",
                f"Discord notification test passed. Adapter: {GAME_ID}",
                fields={
                    "Adapter": GAME_ID,
                    "Config": (
                        self.shared_config.path.name
                        if self.shared_config.path is not None
                        else "defaults"
                    ),
                },
            )
            self.root.after(0, lambda: self._set_discord_test_result(sent))

        threading.Thread(
            target=worker,
            name="unified-discord-test",
            daemon=True,
        ).start()

    def _set_discord_test_result(self, sent: bool) -> None:
        self.discord_test_button.configure(state="normal")
        if sent:
            self.discord_status_var.set(
                f"{self.discord_notifier.status_text}；测试已送达 / delivered"
            )
            self._show_info(
                "Discord 测试成功 / Test Passed",
                "测试报警已发送到 Discord。\nTest alert was delivered to Discord.",
            )
            return
        self.discord_status_var.set(
            f"{self.discord_notifier.status_text}；测试失败 / failed"
        )
        self._show_error_message(
            "Discord 测试失败 / Test Failed",
            f"测试消息发送失败，请查看日志。\nDelivery failed; see {self.discord_notifier.log_path}.",
        )

    def _show_notification_setup_guide(self) -> None:
        window = tk.Toplevel(self.root)
        window.title(self._localize_text("通知设置指南 / Notification Setup Guide"))
        window.geometry("860x650")
        window.minsize(720, 520)
        window.configure(bg=BG)
        window.transient(self.root)

        guide_frame = ttk.Frame(window, padding=10)
        guide_frame.pack(fill="both", expand=True, padx=14, pady=(14, 8))
        config_path = (
            self.shared_config.path
            if self.shared_config.path is not None
            else REPOSITORY_ROOT / "configs" / "windows.local.yaml"
        )
        zh = f"""统一游戏相机采集器通知设置

推荐配置文件：{config_path}
请把真实 Webhook 只放在未提交的 windows.local.yaml，禁止提交到 GitHub。

Discord
1. Discord 服务器 → 编辑频道 → Integrations → Webhooks → New Webhook。
2. 复制 Webhook URL。
3. 写入 notifications.discord.webhook_url，或设置 UNIFIED_DISCORD_WEBHOOK_URL。

飞书
1. 飞书群 → 设置 → 群机器人 → 添加机器人 → 自定义机器人。
2. 复制 Webhook；如启用签名校验，同时复制 Secret。
3. 写入 notifications.feishu，或设置 UNIFIED_FEISHU_WEBHOOK_URL / UNIFIED_FEISHU_SECRET。

YAML 示例（不要把真实地址提交到仓库）：
notifications:
  discord:
    webhook_url: "https://discord.com/api/webhooks/..."
    mention: ""
    username: "Unified Camera Capture"
    timeout_sec: 5
  feishu:
    webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/..."
    secret: ""
    mention_open_id: ""
    timeout_sec: 5

配置完成后重启采集器，再分别点击“测试 Discord”和“测试飞书”。
也可以用 UNIFIED_CAMERA_CONFIG 指向独立 YAML。
旧 BMW_CONFIG、RE9_CONFIG 与 RE9_* 环境变量仍然兼容；Unified 变量优先。
"""
        en = f"""Unified Game Camera Capture Studio — Notification Setup

Recommended config file: {config_path}
Keep real webhooks only in the untracked windows.local.yaml. Never commit them.

Discord
1. Discord server → Edit Channel → Integrations → Webhooks → New Webhook.
2. Copy the webhook URL.
3. Set notifications.discord.webhook_url or UNIFIED_DISCORD_WEBHOOK_URL.

Feishu
1. Target group → Settings → Bots → Add Bot → Custom Bot.
2. Copy the webhook and, if signature verification is enabled, its secret.
3. Set notifications.feishu or UNIFIED_FEISHU_WEBHOOK_URL / UNIFIED_FEISHU_SECRET.

YAML template:
notifications:
  discord:
    webhook_url: "https://discord.com/api/webhooks/..."
    mention: ""
    username: "Unified Camera Capture"
    timeout_sec: 5
  feishu:
    webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/..."
    secret: ""
    mention_open_id: ""
    timeout_sec: 5

You can also point UNIFIED_CAMERA_CONFIG to a dedicated YAML file.
Restart the studio, then use both Test buttons. Legacy BMW_CONFIG, RE9_CONFIG,
and RE9_* variables remain supported; UNIFIED_* variables take precedence.
"""
        text_widget = tk.Text(
            guide_frame,
            wrap="word",
            bg=CARD,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            padx=12,
            pady=10,
            font=("Microsoft YaHei UI", 10),
        )
        scrollbar = ttk.Scrollbar(guide_frame, orient="vertical", command=text_widget.yview)
        text_widget.configure(yscrollcommand=scrollbar.set)
        text_widget.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        text_widget.insert("1.0", zh if self.language == "zh" else en)
        text_widget.configure(state="disabled")

        controls = ttk.Frame(window, padding=(14, 0, 14, 12))
        controls.pack(fill="x")
        ttk.Button(
            controls,
            text=self._localize_text("打开配置目录 / Open Config Folder"),
            command=lambda: open_path(config_path.parent),
        ).pack(side="left")
        ttk.Button(
            controls,
            text=self._localize_text("关闭 / Close"),
            command=window.destroy,
        ).pack(side="right")

    def _show_guidance(self, message: str) -> None:
        self.log(f"操作提示 / Guidance：{message}")
        self.status_detail_var.set(message)

    def _on_close(self) -> None:
        if self.capture_thread is not None and self.capture_thread.is_alive():
            if not self._ask_yes_no(
                "采集进行中 / Capture Active",
                "采集仍在进行，确定停止并退出吗？ / Stop capture and exit?",
            ):
                return
        self.stop_event.set()
        if self.batch_recorder is not None and self.batch_recorder.active:
            self.batch_recorder.request_stop()
        self.closing = True
        self.bridge.close()
        self.root.destroy()


def run_app(*, trajectory_file: str | Path | None = None) -> None:
    enable_dpi_awareness()
    root = tk.Tk()
    CaptureStudioApp(root, trajectory_file=trajectory_file)
    root.mainloop()
