from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading
import time
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .codex_recovery import CodexRecoveryTrigger
from .config import AppConfig
from .discord_notify import DiscordNotifier
from .feishu_notify import FeishuNotifier
from .paths import PROJECT_ROOT, ensure_dir
from .still_scan import (
    StillSample,
    build_layered_still_scan_plan,
    build_still_scan_plan,
    load_still_layers,
    load_still_pose_plan,
    parse_float_list,
    run_layered_still_scan,
    run_still_pose_plan,
    run_still_scan,
    slice_still_scan_plan_from_layer,
)
from .trajectory_replay import load_replay_trajectory, replay_trajectory_to_obs
from .utils import timestamp_id


DEFAULT_TRAJECTORY_JSON = PROJECT_ROOT / "data" / "trajectories" / "scene_1.1" / "scene_1_1_trajectories.json"
DEFAULT_TRAJECTORY_OUTPUT_DIR = PROJECT_ROOT / "data" / "videos" / "trajectories" / "scene_1.1_low_to_high"
TOPSTART20_TRAJECTORY_JSON = (
    PROJECT_ROOT
    / "data"
    / "trajectories"
    / "scene_1.1_topstart20_gain1p3_primitives_sample"
    / "scene_1_1_topstart_20_gain1p3_primitives_sample_trajectories.json"
)
TOPSTART20_TRAJECTORY_OUTPUT_DIR = (
    PROJECT_ROOT / "data" / "videos" / "trajectories" / "scene_1.1_topstart20_gain1p3_primitives_sample"
)
TRUE_GAIN2_TRAJECTORY_JSON = (
    PROJECT_ROOT
    / "data"
    / "trajectory_exports"
    / "scene_1_1_true_keyframes_gain2_smoke10"
    / "scene_1_1_true_gain2_optimal_10_trajectories.json"
)
TRUE_GAIN2_TRAJECTORY_OUTPUT_DIR = (
    PROJECT_ROOT / "data" / "videos" / "trajectories" / "scene_1_1_true_keyframes_gain2_smoke10"
)
COVERAGE_SMOKE10_TRAJECTORY_JSON = (
    PROJECT_ROOT
    / "data"
    / "trajectory_exports"
    / "scene_1_1_true_keyframes_gain2_no_backtrack_coverage_smoke10_low_to_high"
    / "scene_1_1_coverage_smoke10_low_to_high_trajectories.json"
)
COVERAGE_SMOKE10_TRAJECTORY_OUTPUT_DIR = (
    PROJECT_ROOT / "data" / "videos" / "trajectories" / "scene_1_1_coverage_smoke10_low_to_high"
)
SCENE_2_SMOKE15_TRAJECTORY_JSON = (
    PROJECT_ROOT
    / "data"
    / "trajectory_exports"
    / "scene_2_true_keyframes_gain2p3_distance4_step4_singleanchor_smoke15_cluster3"
    / "scene_2_true_gain2p3_distance4_step4_singleanchor_smoke15_cluster3_low_to_high_ui.json"
)
SCENE_2_SMOKE15_OUTPUT_SUBDIR = "scene_2_true_keyframes_gain2p3_distance4_step4_singleanchor_smoke15_cluster3"
SCENE_2_AGAIN_13000_TRAJECTORY_JSON = (
    PROJECT_ROOT
    / "data"
    / "trajectory_exports"
    / "scene_2_again"
    / "trajectories_true_keyframes_gain2_distance4_step4_singleanchor_balanced_fast64_13000"
    / "scene_2_again_true_gain2_distance4_step4_singleanchor_balanced_fast64_13000_low_to_high_ui.json"
)
SCENE_2_AGAIN_13000_OUTPUT_SUBDIR = (
    "scene_2_again_true_gain2_distance4_step4_singleanchor_balanced_fast64_13000"
)
SCENE_3_1_15000_TRAJECTORY_JSON = (
    PROJECT_ROOT
    / "data"
    / "trajectory_exports"
    / "scene_3.1"
    / "trajectories_true_keyframes_gain2p5_distance10_step8_singlemax_globaloverlap90_fast64_15000"
    / "scene_3_1_true_gain2p5_distance10_step8_singlemax_globaloverlap90_fast64_15000_low_to_high_ui.json"
)
SCENE_3_1_15000_OUTPUT_SUBDIR = (
    "scene_3_1_true_gain2p5_distance10_step8_singlemax_globaloverlap90_fast64_15000"
)
SCENE_3_2_13000_TRAJECTORY_JSON = (
    PROJECT_ROOT
    / "data"
    / "trajectory_exports"
    / "scene_3.2"
    / "trajectories_true_keyframes_gain2p5_distance10_step8_singlemax_globaloverlap90_fast64_13000"
    / "scene_3_2_true_gain2p5_distance10_step8_singlemax_globaloverlap90_fast64_13000_low_to_high_ui.json"
)
SCENE_3_2_13000_OUTPUT_SUBDIR = (
    "scene_3_2_true_gain2p5_distance10_step8_singlemax_globaloverlap90_fast64_13000"
)
DEFAULT_TRAJECTORY_SET_ID = "scene_1_1_coverage_smoke10_low_to_high"
MIN_VALID_TRAJECTORY_VIDEO_BYTES = 64_000
TRAJECTORY_VIDEO_SETTLE_TIMEOUT_SEC = 20.0
TRAJECTORY_VIDEO_STABLE_CHECKS = 3
TRAJECTORY_SETS = {
    "scene_3_1_true_gain2p5_distance10_step8_singlemax_globaloverlap90_fast64_15000": {
        "label": "scene 3.1 gain2p5 distance10 step8 single-max global-overlap90 fast64 (15,000)",
        "json": SCENE_3_1_15000_TRAJECTORY_JSON,
        "output_dir": PROJECT_ROOT / "data" / "videos" / "trajectories" / SCENE_3_1_15000_OUTPUT_SUBDIR,
        "output_subdir": SCENE_3_1_15000_OUTPUT_SUBDIR,
        "capture_group": "scene_3",
        "session_prefix": "scene_3_1_traj",
        "trust_run_state": True,
    },
    "scene_3_2_true_gain2p5_distance10_step8_singlemax_globaloverlap90_fast64_13000": {
        "label": "scene 3.2 gain2p5 distance10 step8 single-max global-overlap90 fast64 (13,000)",
        "json": SCENE_3_2_13000_TRAJECTORY_JSON,
        "output_dir": PROJECT_ROOT / "data" / "videos" / "trajectories" / SCENE_3_2_13000_OUTPUT_SUBDIR,
        "output_subdir": SCENE_3_2_13000_OUTPUT_SUBDIR,
        "capture_group": "scene_3",
        "session_prefix": "scene_3_2_traj",
        "trust_run_state": True,
    },
    "scene_2_again_true_gain2_distance4_step4_singleanchor_balanced_fast64_13000": {
        "label": "scene 2 again gain2 distance4 step4 single-anchor balanced fast64 (13,000)",
        "json": SCENE_2_AGAIN_13000_TRAJECTORY_JSON,
        "output_dir": PROJECT_ROOT / "data" / "videos" / "trajectories" / SCENE_2_AGAIN_13000_OUTPUT_SUBDIR,
        "output_subdir": SCENE_2_AGAIN_13000_OUTPUT_SUBDIR,
        "session_prefix": "scene_2_again_traj",
        "trust_run_state": True,
    },
    "scene_2_true_gain2p3_distance4_step4_singleanchor_smoke15_cluster3": {
        "label": "scene 2 true keyframes gain2p3 distance4 step4 single-anchor smoke15 cluster3",
        "json": SCENE_2_SMOKE15_TRAJECTORY_JSON,
        "output_dir": PROJECT_ROOT / "data" / "videos" / "trajectories" / SCENE_2_SMOKE15_OUTPUT_SUBDIR,
        "output_subdir": SCENE_2_SMOKE15_OUTPUT_SUBDIR,
        "session_prefix": "scene_2_gain2p3_distance4_step4_singleanchor_smoke15_cluster3_traj",
    },
    "scene_1_1_coverage_smoke10_low_to_high": {
        "label": "scene_1.1 coverage smoke10 low-to-high",
        "json": COVERAGE_SMOKE10_TRAJECTORY_JSON,
        "output_dir": COVERAGE_SMOKE10_TRAJECTORY_OUTPUT_DIR,
        "session_prefix": "scene_1_1_coverage_smoke10_traj",
    },
    "scene_1_1_true_gain2_smoke10": {
        "label": "scene_1.1 true keyframes gain2 smoke10",
        "json": TRUE_GAIN2_TRAJECTORY_JSON,
        "output_dir": TRUE_GAIN2_TRAJECTORY_OUTPUT_DIR,
        "session_prefix": "scene_1_1_true_gain2_smoke10_traj",
    },
    "scene_1_1_topstart20": {
        "label": "scene_1.1 topstart20 gain1p3 primitives",
        "json": TOPSTART20_TRAJECTORY_JSON,
        "output_dir": TOPSTART20_TRAJECTORY_OUTPUT_DIR,
        "session_prefix": "scene_1_1_topstart20_traj",
    },
    "scene_1_1_low_to_high": {
        "label": "scene_1.1 original low -> high",
        "json": DEFAULT_TRAJECTORY_JSON,
        "output_dir": DEFAULT_TRAJECTORY_OUTPUT_DIR,
        "session_prefix": "scene_1_1_low_to_high_traj",
    },
}


def configured_trajectory_sets(config: AppConfig) -> dict[str, dict[str, object]]:
    """Resolve optional machine-local capture storage for registered profiles."""
    profiles = {set_id: dict(item) for set_id, item in TRAJECTORY_SETS.items()}
    trajectory_config = config.raw.get("trajectory", {})
    if not isinstance(trajectory_config, dict):
        return profiles
    default_capture_root = _resolve_capture_root(trajectory_config.get("capture_root"))
    capture_roots = trajectory_config.get("capture_roots", {})
    if not isinstance(capture_roots, dict):
        capture_roots = {}
    for item in profiles.values():
        output_subdir = item.get("output_subdir")
        capture_group = str(item.get("capture_group") or "")
        grouped_capture_root = _resolve_capture_root(capture_roots.get(capture_group))
        capture_root = grouped_capture_root or default_capture_root
        if output_subdir and capture_root is not None:
            item["output_dir"] = capture_root / str(output_subdir)
    return profiles


def _resolve_capture_root(value: object) -> Path | None:
    if not value:
        return None
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


class StillScanApp:
    """Small progress window for OBS still-image grid scans."""

    def __init__(
        self,
        config: AppConfig,
        obs_password: str,
        x_min: float,
        x_max: float,
        z_min: float,
        z_max: float,
        y_values: str,
        points_x: int,
        points_z: int,
        settle_seconds: float,
        source_name: str | None,
        image_format: str,
        image_width: int,
        image_height: int,
        image_quality: int,
        max_samples: int | None,
        session_id: str | None,
        layers_config: Path | None = None,
        resume_from_layer: str | None = None,
        pose_plan_config: Path | None = None,
        trajectory_set_id: str = DEFAULT_TRAJECTORY_SET_ID,
        trajectory_json: Path | None = None,
        trajectory_output_dir: Path | None = None,
        trajectory_label: str | None = None,
        trajectory_session_prefix: str | None = None,
        topmost: bool = True,
    ) -> None:
        self.config = config
        self.obs_password = obs_password
        self.x_min = x_min
        self.x_max = x_max
        self.z_min = z_min
        self.z_max = z_max
        self.y_values = y_values
        self.points_x = points_x
        self.points_z = points_z
        self.settle_seconds = settle_seconds
        self.source_name = source_name
        self.image_format = image_format
        self.image_width = image_width
        self.image_height = image_height
        self.image_quality = image_quality
        self.max_samples = max_samples
        self.session_id = session_id
        self.layers_config = layers_config
        self.resume_from_layer = resume_from_layer
        self.pose_plan_config = pose_plan_config
        self.stop_event = threading.Event()
        self.trajectory_stop_event = threading.Event()
        self.topmost = topmost
        self.obs_restart_every = self._read_int_env("RE9_OBS_RESTART_EVERY_N", 0)
        self.obs_restart_command = os.environ.get("RE9_OBS_RESTART_COMMAND", "")
        self.obs_restart_wait_sec = self._read_float_env("RE9_OBS_RESTART_WAIT_SEC", 20.0)
        self.discord_notifier = DiscordNotifier.from_config(config)
        self.feishu_notifier = FeishuNotifier.from_config(config)
        self.codex_recovery = CodexRecoveryTrigger.from_config(config)
        self.running = False
        self.qa_running = False
        self.trajectory_running = False
        self.output_dir: Path | None = None
        self.captured_count = 0
        self.trajectory_sets = configured_trajectory_sets(config)
        if trajectory_json is not None:
            custom_output = trajectory_output_dir or (
                PROJECT_ROOT / "data" / "videos" / "trajectories" / (trajectory_json.stem or "custom_trajectory")
            )
            self.trajectory_sets["custom"] = {
                "label": trajectory_label or f"custom: {trajectory_json.stem}",
                "json": trajectory_json,
                "output_dir": custom_output,
                "session_prefix": trajectory_session_prefix or "custom_traj",
            }
            trajectory_set_id = "custom"
        if trajectory_set_id not in self.trajectory_sets:
            trajectory_set_id = DEFAULT_TRAJECTORY_SET_ID
        self.trajectory_set_id = trajectory_set_id
        self.trajectory_json = Path(self.trajectory_sets[self.trajectory_set_id]["json"])
        self.trajectory_output_dir = Path(self.trajectory_sets[self.trajectory_set_id]["output_dir"])
        self.trajectory_session_prefix = str(self.trajectory_sets[self.trajectory_set_id]["session_prefix"])
        self.current_trajectory_run_dir: Path | None = None
        self.trajectory_state_path: Path | None = None
        self.planned_trajectory_indices: list[int] = []
        self.completed_trajectory_indices: set[int] = set()
        self.trajectory_count = self._load_trajectory_count()

        if pose_plan_config is not None:
            planned = load_still_pose_plan(pose_plan_config, group_id=session_id or "scene_1_extra")
            layer_count = len({sample.layer_id for sample in planned})
            zone_count = len({sample.zone_id for sample in planned})
            plan_text = f"Plan: {layer_count} subscenes, {zone_count} sample kinds, {len(planned)} explicit images"
        elif layers_config is not None:
            layers = load_still_layers(layers_config)
            planned = build_layered_still_scan_plan(layers, points_x, points_z)
            layer_count = len({layer.layer_id for layer in layers})
            zone_count = len(layers)
            plan_text = f"Plan: {layer_count} layers, {zone_count} zones, 22 views per point, {len(planned)} images"
            if resume_from_layer:
                remaining = slice_still_scan_plan_from_layer(planned, resume_from_layer)
                self.captured_count = remaining[0].sample_index - 1
                plan_text = (
                    f"Resume from {resume_from_layer}: {len(remaining)} images remaining; "
                    f"preserving {self.captured_count} earlier images, total {len(planned)}"
                )
        else:
            heights = parse_float_list(y_values)
            planned = build_still_scan_plan(x_min, x_max, z_min, z_max, heights, points_x, points_z)
            layer_count = len(heights)
            zone_count = layer_count
            plan_text = f"Plan: {layer_count} layers, {zone_count} zones, 22 views per point, {len(planned)} images"
        self.total = len(planned) if max_samples is None else min(len(planned), max_samples)

        self.root = tk.Tk()
        self.root.title("RE9 Still Scan Progress")
        self.root.geometry("860x640")
        self.root.minsize(760, 500)
        self.root.resizable(True, True)
        self.root.attributes("-topmost", self.topmost)
        if self.topmost:
            self.root.after(250, self._keep_window_on_top)

        self.status_var = tk.StringVar(value="Ready")
        if max_samples is not None and self.total != len(planned):
            plan_text = f"{plan_text} (limited to {self.total})"
        self.plan_var = tk.StringVar(value=plan_text)
        self.pose_var = tk.StringVar(value="Current pose: -")
        self.file_var = tk.StringVar(value="Last image: -")
        self.output_var = tk.StringVar(value="Output: -")
        self.progress_var = tk.IntVar(value=self.captured_count)
        self.trajectory_index_var = tk.IntVar(value=1)
        self.trajectory_set_var = tk.StringVar(value=self._trajectory_set_label(self.trajectory_set_id))
        self.trajectory_var = tk.StringVar(value=self._trajectory_status_text())
        self.trajectory_output_var = tk.StringVar(value=f"Output: {self.trajectory_output_dir}")
        self.trajectory_resume_var = tk.StringVar(value=self._trajectory_resume_status_text())
        self.discord_status_var = tk.StringVar(value=self.discord_notifier.status_text)
        self.feishu_status_var = tk.StringVar(value=self.feishu_notifier.status_text)
        self.codex_recovery_status_var = tk.StringVar(
            value=self.codex_recovery.status_text
        )

        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        frame = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=frame, anchor="nw")

        def update_scroll_region(_event: object | None = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def fit_frame_width(event: tk.Event) -> None:
            canvas.itemconfigure(window_id, width=event.width)

        def on_mousewheel(event: tk.Event) -> None:
            if getattr(event, "num", None) == 4:
                canvas.yview_scroll(-3, "units")
            elif getattr(event, "num", None) == 5:
                canvas.yview_scroll(3, "units")
            elif getattr(event, "delta", 0):
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        frame.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", fit_frame_width)
        self.root.bind_all("<MouseWheel>", on_mousewheel)
        self.root.bind_all("<Button-4>", on_mousewheel)
        self.root.bind_all("<Button-5>", on_mousewheel)

        content = ttk.Frame(frame)
        content.pack(fill="both", expand=True, padx=16, pady=14)

        capture_frame = ttk.LabelFrame(content, text="Still image capture")
        capture_frame.pack(fill="x", pady=(0, 8))
        self.start_button = ttk.Button(capture_frame, text="Start Still Scan", command=self.start)
        self.start_button.pack(fill="x", padx=8, pady=4)
        self.stop_button = ttk.Button(capture_frame, text="Stop After Current Shot", command=self.stop, state="disabled")
        self.stop_button.pack(fill="x", padx=8, pady=4)
        self.qa_button = ttk.Button(
            capture_frame,
            text="Delete Broken Capture Images",
            command=self.run_bad_image_cleanup,
            state="disabled",
        )
        self.qa_button.pack(fill="x", padx=8, pady=4)

        trajectory_frame = ttk.LabelFrame(content, text="Trajectory video capture")
        trajectory_frame.pack(fill="x", pady=(0, 8))
        trajectory_picker = ttk.Frame(trajectory_frame)
        trajectory_picker.pack(fill="x", padx=8, pady=(5, 2))
        ttk.Label(trajectory_picker, text="Set").pack(side="left")
        self.trajectory_set_combo = ttk.Combobox(
            trajectory_picker,
            textvariable=self.trajectory_set_var,
            values=self._trajectory_set_choices(),
            state="readonly",
            width=42,
        )
        self.trajectory_set_combo.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.trajectory_set_combo.bind("<<ComboboxSelected>>", self._on_trajectory_set_change)
        ttk.Label(trajectory_frame, textvariable=self.trajectory_var, wraplength=760).pack(anchor="w", padx=8, pady=(5, 2))
        ttk.Label(trajectory_frame, textvariable=self.trajectory_output_var, wraplength=760).pack(anchor="w", padx=8, pady=(0, 5))
        trajectory_controls = ttk.Frame(trajectory_frame)
        trajectory_controls.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(trajectory_controls, text="Index").pack(side="left")
        self.trajectory_spinbox = ttk.Spinbox(
            trajectory_controls,
            from_=1,
            to=max(1, self.trajectory_count),
            textvariable=self.trajectory_index_var,
            width=6,
        )
        self.trajectory_spinbox.pack(side="left", padx=(6, 12))
        self.record_one_trajectory_button = ttk.Button(
            trajectory_controls,
            text="Record Selected Low -> High",
            command=self.record_selected_trajectory,
        )
        self.record_one_trajectory_button.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.record_all_trajectories_button = ttk.Button(
            trajectory_controls,
            text="Record All Low -> High",
            command=self.record_all_trajectories,
        )
        self.record_all_trajectories_button.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.resume_trajectory_button = ttk.Button(
            trajectory_frame,
            text="Resume Latest Run",
            command=self.resume_latest_trajectory_run,
        )
        self.resume_trajectory_button.pack(fill="x", padx=8, pady=(0, 4))
        self.stop_trajectory_button = ttk.Button(
            trajectory_frame,
            text="Stop After Current Trajectory",
            command=self.stop_after_current_trajectory,
            state="disabled",
        )
        self.stop_trajectory_button.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(trajectory_frame, textvariable=self.trajectory_resume_var, wraplength=760).pack(anchor="w", padx=8, pady=(0, 8))

        discord_frame = ttk.LabelFrame(content, text="Discord error alerts")
        discord_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(discord_frame, textvariable=self.discord_status_var, wraplength=620).pack(
            side="left",
            fill="x",
            expand=True,
            padx=8,
            pady=6,
        )
        self.discord_test_button = ttk.Button(
            discord_frame,
            text="Send Test Alert",
            command=self._send_discord_test,
            state="normal" if self.discord_notifier.enabled else "disabled",
        )
        self.discord_test_button.pack(side="right", padx=8, pady=6)

        feishu_frame = ttk.LabelFrame(content, text="Feishu error alerts")
        feishu_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(feishu_frame, textvariable=self.feishu_status_var, wraplength=620).pack(
            side="left",
            fill="x",
            expand=True,
            padx=8,
            pady=6,
        )
        self.feishu_test_button = ttk.Button(
            feishu_frame,
            text="Send Test Alert",
            command=self._send_feishu_test,
            state="normal" if self.feishu_notifier.enabled else "disabled",
        )
        self.feishu_test_button.pack(side="right", padx=8, pady=6)

        codex_recovery_frame = ttk.LabelFrame(
            content,
            text="Codex automatic recovery",
        )
        codex_recovery_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(
            codex_recovery_frame,
            textvariable=self.codex_recovery_status_var,
            wraplength=760,
        ).pack(anchor="w", fill="x", padx=8, pady=6)

        ttk.Label(content, textvariable=self.status_var, font=("Segoe UI", 11, "bold"), wraplength=720).pack(anchor="w", pady=5)
        ttk.Label(content, textvariable=self.plan_var, wraplength=720).pack(anchor="w", pady=5)
        self.progress = ttk.Progressbar(content, maximum=max(1, self.total), variable=self.progress_var)
        self.progress.pack(fill="x", pady=8)
        ttk.Label(content, textvariable=self.pose_var, wraplength=720).pack(anchor="w", pady=5)
        ttk.Label(content, textvariable=self.file_var, wraplength=720).pack(anchor="w", pady=5)
        ttk.Label(content, textvariable=self.output_var, wraplength=720).pack(anchor="w", pady=5)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        if os.environ.get("RE9_TRAJECTORY_AUTO_RESUME", "").lower() in {"1", "true", "yes"}:
            # Useful for unattended recovery after an OBS/UI restart.  The
            # normal button remains confirmation-based.
            self.root.after(1200, lambda: self.resume_latest_trajectory_run(confirm=False))

    def _keep_window_on_top(self) -> None:
        if not self.topmost:
            return
        try:
            self.root.attributes("-topmost", True)
            self.root.lift()
        except tk.TclError:
            return
        self.root.after(2000, self._keep_window_on_top)

    def run(self) -> None:
        self.root.mainloop()

    def start(self) -> None:
        if self.running or self.trajectory_running:
            return
        self.running = True
        self.stop_event.clear()
        self.start_button.configure(state="disabled")
        self._set_trajectory_buttons("disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("Starting in 5 seconds. Press Insert to close REFramework/FreeCam UI now.")
        self._countdown_start(5)

    def _countdown_start(self, seconds_left: int) -> None:
        if self.stop_event.is_set():
            self.running = False
            self.status_var.set("Start cancelled.")
            self.start_button.configure(state="normal")
            self._set_trajectory_buttons("normal")
            self.stop_button.configure(state="disabled")
            return
        if seconds_left <= 0:
            self.status_var.set("Connecting to OBS and starting still scan...")
            threading.Thread(target=self._worker, daemon=True).start()
            return
        self.status_var.set(
            f"Starting in {seconds_left}s. Close REFramework/FreeCam UI before capture starts."
        )
        self.root.after(1000, lambda: self._countdown_start(seconds_left - 1))

    def stop(self) -> None:
        self.stop_event.set()
        self.status_var.set("Stopping after the current shot finishes...")
        self.stop_button.configure(state="disabled")

    def run_bad_image_cleanup(self) -> None:
        if self.running or self.qa_running or self.trajectory_running:
            return
        if self.output_dir is None:
            messagebox.showerror("QA unavailable", "No scan output folder is available yet.")
            return
        samples_csv = self.output_dir / "samples.csv"
        if not samples_csv.exists():
            messagebox.showerror("QA unavailable", f"samples.csv was not found:\n{samples_csv}")
            return
        self.qa_running = True
        self.start_button.configure(state="disabled")
        self.qa_button.configure(state="disabled")
        self.status_var.set("Checking for broken capture images and deleting only obvious failures...")
        threading.Thread(target=self._qa_worker, args=(samples_csv,), daemon=True).start()

    def record_selected_trajectory(self) -> None:
        if self.running or self.trajectory_running:
            return
        if self.trajectory_count <= 0:
            messagebox.showerror("Trajectory unavailable", f"No trajectory JSON found:\n{self.trajectory_json}")
            return
        index = max(1, min(int(self.trajectory_index_var.get()), self.trajectory_count))
        if not self._confirm_trajectory_ready(f"Record trajectory {index:02d} low -> high?"):
            return
        self._start_trajectory_worker([index])

    def record_all_trajectories(self) -> None:
        if self.running or self.trajectory_running:
            return
        if self.trajectory_count <= 0:
            messagebox.showerror("Trajectory unavailable", f"No trajectory JSON found:\n{self.trajectory_json}")
            return
        if not self._confirm_trajectory_ready(
            f"Record all {self.trajectory_count} {self._trajectory_set_label(self.trajectory_set_id)} trajectories low -> high?"
        ):
            return
        self._start_trajectory_worker(list(range(1, self.trajectory_count + 1)))

    def resume_latest_trajectory_run(self, confirm: bool = True) -> None:
        if self.running or self.trajectory_running:
            return
        run_dir = self._latest_trajectory_run_dir()
        if run_dir is None:
            messagebox.showerror("Resume unavailable", f"No run folder was found under:\n{self.trajectory_output_dir}")
            return
        planned = self._load_planned_indices_for_run(run_dir)
        completed = self._detect_completed_trajectory_indices(run_dir, planned)
        remaining = [index for index in planned if index not in completed]
        if not remaining:
            messagebox.showinfo("Resume unavailable", f"No missing trajectories were found in:\n{run_dir}")
            return
        if confirm and not self._confirm_trajectory_ready(
            f"Resume {run_dir.name} from trajectory {remaining[0]:02d}? "
            f"Completed {len(completed)}/{len(planned)}, remaining {len(remaining)}."
        ):
            return
        self._start_trajectory_worker(
            remaining,
            run_dir=run_dir,
            planned_indices=planned,
            completed_indices=completed,
        )

    def stop_after_current_trajectory(self) -> None:
        self.trajectory_stop_event.set()
        self.status_var.set("Stopping after the current trajectory finishes...")
        self.stop_trajectory_button.configure(state="disabled")

    def on_close(self) -> None:
        if self.trajectory_running:
            should_stop = messagebox.askyesno("Trajectory active", "Stop after the current trajectory and close?")
            if should_stop:
                self.stop_after_current_trajectory()
            return
        if self.running:
            should_stop = messagebox.askyesno("Scan active", "Stop after the current shot and close?")
            if should_stop:
                self.stop()
            return
        self.root.destroy()

    def _worker(self) -> None:
        try:
            if self.pose_plan_config is not None:
                outputs = run_still_pose_plan(
                    self.config,
                    obs_password=self.obs_password,
                    pose_plan=self.pose_plan_config,
                    settle_seconds=self.settle_seconds,
                    source_name=self.source_name,
                    image_format=self.image_format,
                    image_width=self.image_width,
                    image_height=self.image_height,
                    image_quality=self.image_quality,
                    session_id=self.session_id,
                    max_samples=self.max_samples,
                    progress_callback=self._progress,
                    stop_event=self.stop_event,
                )
            elif self.layers_config is not None:
                outputs = run_layered_still_scan(
                    self.config,
                    obs_password=self.obs_password,
                    layers=load_still_layers(self.layers_config),
                    points_x=self.points_x,
                    points_z=self.points_z,
                    settle_seconds=self.settle_seconds,
                    source_name=self.source_name,
                    image_format=self.image_format,
                    image_width=self.image_width,
                    image_height=self.image_height,
                    image_quality=self.image_quality,
                    session_id=self.session_id,
                    max_samples=self.max_samples,
                    progress_callback=self._progress,
                    stop_event=self.stop_event,
                    resume_from_layer=self.resume_from_layer,
                )
            else:
                heights = parse_float_list(self.y_values)
                outputs = run_still_scan(
                    self.config,
                    obs_password=self.obs_password,
                    x_min=self.x_min,
                    x_max=self.x_max,
                    z_min=self.z_min,
                    z_max=self.z_max,
                    y_values=heights,
                    points_x=self.points_x,
                    points_z=self.points_z,
                    settle_seconds=self.settle_seconds,
                    source_name=self.source_name,
                    image_format=self.image_format,
                    image_width=self.image_width,
                    image_height=self.image_height,
                    image_quality=self.image_quality,
                    session_id=self.session_id,
                    max_samples=self.max_samples,
                    progress_callback=self._progress,
                    stop_event=self.stop_event,
                )
            self.output_dir = outputs["output_dir"]
            done = self.captured_count
            status = "Stopped." if self.stop_event.is_set() and done < self.total else "Done."
            self._set_done(status, outputs["output_dir"])
        except Exception as exc:
            message = str(exc)
            error_log = ensure_dir(self.config.output_dir) / "still_scan_errors.log"
            with error_log.open("a", encoding="utf-8") as handle:
                handle.write(traceback.format_exc())
                handle.write("\n")
            self.root.after(0, lambda message=message: self._set_error(message, error_log))

    def _start_trajectory_worker(
        self,
        indices: list[int],
        run_dir: Path | None = None,
        planned_indices: list[int] | None = None,
        completed_indices: set[int] | None = None,
    ) -> None:
        self.trajectory_running = True
        self.trajectory_stop_event.clear()
        label = "all" if len(indices) > 1 else f"traj_{indices[0]:02d}"
        self.current_trajectory_run_dir = ensure_dir(run_dir or (self.trajectory_output_dir / f"run_{timestamp_id()}_{label}_low_to_high"))
        self.trajectory_state_path = self.current_trajectory_run_dir / "trajectory_run_state.json"
        self.planned_trajectory_indices = list(planned_indices or indices)
        self.completed_trajectory_indices = set(completed_indices or set())
        completed_start = len(self.completed_trajectory_indices)
        planned_total = len(self.planned_trajectory_indices)
        self.progress_var.set(completed_start)
        self.progress.configure(maximum=max(1, planned_total))
        self.start_button.configure(state="disabled")
        self.qa_button.configure(state="disabled")
        self._set_trajectory_buttons("disabled")
        self.stop_trajectory_button.configure(state="normal")
        self.status_var.set("Starting trajectory recording. Hide REFramework UI before countdown finishes.")
        self.output_var.set(f"Output: {self.current_trajectory_run_dir}")
        self._write_trajectory_run_state(status="starting")
        threading.Thread(target=self._trajectory_worker, args=(indices, completed_start, planned_total), daemon=True).start()

    def _trajectory_worker(self, indices: list[int], completed_start: int, planned_total: int) -> None:
        completed = completed_start
        try:
            run_dir = self.current_trajectory_run_dir or ensure_dir(self.trajectory_output_dir / f"run_{timestamp_id()}_low_to_high")
            self._write_trajectory_run_state(status="recording")
            for position, trajectory_index in enumerate(indices, start=completed_start + 1):
                if self.trajectory_stop_event.is_set():
                    break
                trajectory = load_replay_trajectory(
                    self.trajectory_json,
                    trajectory_index=trajectory_index,
                    reverse=False,
                )
                session = f"{self.trajectory_session_prefix}_{trajectory_index:02d}"
                output_dir = run_dir / f"traj_{trajectory_index:02d}"
                self.root.after(
                    0,
                    lambda pos=position, total=len(indices), idx=trajectory_index, traj=trajectory: self._set_trajectory_progress_text(
                        pos, total, idx, traj.trajectory_id
                    ),
                )
                last_error: Exception | None = None
                for attempt in range(1, 4):
                    try:
                        result = replay_trajectory_to_obs(
                            self.config,
                            trajectory,
                            obs_password=self.obs_password,
                            output_dir=output_dir,
                            session_id=session,
                            countdown_sec=3.0,
                            settle_sec=1.0,
                            post_roll_sec=1.0,
                            speed=1.0,
                            duration_sec=None,
                            record=True,
                            write_pose_log=True,
                        )
                        video_path = self._validate_trajectory_result(result, trajectory_index)
                        break
                    except Exception as exc:
                        last_error = exc
                        if attempt >= 3 or self.trajectory_stop_event.is_set():
                            raise
                        delay = 2.0 * attempt
                        self.root.after(
                            0,
                            lambda idx=trajectory_index, n=attempt, wait=delay, err=str(exc): self.status_var.set(
                                f"Trajectory {idx:02d} failed ({err}); retry {n + 1}/3 in {wait:.0f}s..."
                            ),
                        )
                        # A failed replay can leave OBS in a partially active
                        # output state. Restart it before retrying so the next
                        # SetRecordDirectory/StartRecord starts cleanly.
                        if self.obs_restart_command:
                            try:
                                self._restart_obs_between_batches(completed, planned_total)
                            except Exception as restart_exc:
                                last_error = RuntimeError(
                                    f"{exc}; OBS restart before retry failed: {restart_exc}"
                                )
                        time.sleep(delay)
                else:
                    raise last_error or RuntimeError(f"Trajectory {trajectory_index:02d} failed.")
                self.completed_trajectory_indices.add(trajectory_index)
                completed = len(self.completed_trajectory_indices)
                self._write_trajectory_run_state(status="recording", current_index=trajectory_index, last_video=video_path)
                self.root.after(0, lambda value=completed: self.progress_var.set(value))
                if (
                    self.obs_restart_every > 0
                    and completed < planned_total
                    and completed % self.obs_restart_every == 0
                    and not self.trajectory_stop_event.is_set()
                ):
                    self._write_trajectory_run_state(status="restarting_obs", current_index=trajectory_index)
                    self._restart_obs_between_batches(completed, planned_total)
                    self._write_trajectory_run_state(status="recording")

            status = f"Trajectory recording done. Recorded {completed}/{planned_total} low -> high videos."
            if self.trajectory_stop_event.is_set():
                status = f"Trajectory recording stopped. Recorded {completed}/{planned_total} videos."
            self._write_trajectory_run_state(status="stopped" if self.trajectory_stop_event.is_set() else "done")
            self.root.after(0, lambda: self._set_trajectory_done(status))
        except Exception as exc:
            message = str(exc)
            error_log = ensure_dir(self.config.output_dir) / "trajectory_replay_gui_errors.log"
            with error_log.open("a", encoding="utf-8") as handle:
                handle.write(traceback.format_exc())
                handle.write("\n")
            self._write_trajectory_run_state(status="failed", error=message)
            self.root.after(0, lambda message=message: self._set_trajectory_error(message, error_log))

    def _qa_worker(self, samples_csv: Path) -> None:
        try:
            from .bad_still_detector import detect_inaccessible_points

            outputs = detect_inaccessible_points(
                samples_csv,
                output_dir=samples_csv.parent / "qa",
                delete_invalid_images=True,
            )
            self.root.after(0, lambda: self._set_qa_done(outputs))
        except Exception as exc:
            message = str(exc)
            error_log = ensure_dir(self.config.output_dir) / "still_scan_errors.log"
            with error_log.open("a", encoding="utf-8") as handle:
                handle.write(traceback.format_exc())
                handle.write("\n")
            self.root.after(0, lambda message=message: self._set_qa_error(message, error_log))

    def _progress(self, sample: StillSample, total: int, image_path: Path) -> None:
        self.captured_count = sample.sample_index

        def apply() -> None:
            self.progress.configure(maximum=max(1, total))
            self.progress_var.set(sample.sample_index)
            self.status_var.set(f"Captured {sample.sample_index}/{total}")
            self.pose_var.set(
                f"Current pose: {sample.layer_id}/{sample.zone_id}, x={sample.x:.2f}, y={sample.y:.2f}, z={sample.z:.2f}, "
                f"yaw={sample.yaw_deg:.1f}, pitch={sample.pitch_deg:.1f}, {sample.pattern}"
            )
            self.file_var.set(f"Last image: {image_path}")
            self.output_var.set(f"Output: {image_path.parent.parent}")

        self.root.after(0, apply)

    def _set_trajectory_progress_text(self, position: int, total: int, index: int, trajectory_id: str) -> None:
        self.status_var.set(f"Recording trajectory {position}/{total}: {trajectory_id} low -> high")
        self.pose_var.set(f"Current trajectory index: {index:02d}")
        self.file_var.set("Current mode: OBS video recording from trajectory JSON")
        base = self.current_trajectory_run_dir or self.trajectory_output_dir
        self.output_var.set(f"Output: {base / f'traj_{index:02d}'}")

    def _set_done(self, status: str, output_dir: Path) -> None:
        def apply() -> None:
            self.running = False
            self.status_var.set(status)
            self.output_var.set(f"Output: {output_dir}")
            self.start_button.configure(state="normal")
            self.stop_button.configure(state="disabled")
            self._set_trajectory_buttons("normal")
            self._refresh_qa_button()

        self.root.after(0, apply)

    def _notification_error_fields(self, mode: str, error_log: Path) -> dict[str, object]:
        fields: dict[str, object] = {
            "Mode": mode,
            "Session": self.session_id or "-",
            "Error log": error_log,
        }
        if mode == "trajectory recording":
            completed = len(self.completed_trajectory_indices)
            planned = len(self.planned_trajectory_indices) or self.trajectory_count
            remaining = sorted(
                set(self.planned_trajectory_indices) - self.completed_trajectory_indices
            )
            fields.update(
                {
                    "Trajectory set": self._trajectory_set_label(self.trajectory_set_id),
                    "Progress": f"{completed}/{planned}",
                    "Next trajectory": remaining[0] if remaining else "-",
                    "Run directory": self.current_trajectory_run_dir or self.trajectory_output_dir,
                }
            )
        elif mode == "still scan":
            fields["Progress"] = f"{self.captured_count}/{self.total}"
        elif self.output_dir is not None:
            fields["Output directory"] = self.output_dir
        return fields

    def _notify_error_alerts(self, title: str, message: str, mode: str, error_log: Path) -> None:
        fields = self._notification_error_fields(mode, error_log)
        recovery_queued = self.codex_recovery.trigger(
            title,
            message,
            fields=fields,
        )
        if recovery_queued:
            fields["Codex recovery"] = "queued"
            self.codex_recovery_status_var.set(
                f"{self.codex_recovery.status_text}; recovery queued"
            )
        self.discord_notifier.notify_error(
            title,
            message,
            fields=fields,
        )
        self.feishu_notifier.notify_error(
            title,
            message,
            fields=fields,
        )

    def _send_discord_test(self) -> None:
        if not self.discord_notifier.enabled:
            messagebox.showerror(
                "Discord alerts disabled",
                "Set RE9_DISCORD_WEBHOOK_URL or notifications.discord.webhook_url, then restart the UI.",
            )
            return

        self.discord_test_button.configure(state="disabled")
        self.discord_status_var.set("Discord error alerts: sending test alert...")

        def worker() -> None:
            sent = self.discord_notifier.send_error(
                "RE9 Discord test alert",
                "Discord error notifications are configured correctly.",
                fields={
                    "Mode": "test",
                    "Session": self.session_id or "-",
                    "Trajectory set": self._trajectory_set_label(self.trajectory_set_id),
                },
            )
            self.root.after(0, lambda: self._set_discord_test_result(sent))

        threading.Thread(target=worker, name="re9-discord-test", daemon=True).start()

    def _set_discord_test_result(self, sent: bool) -> None:
        self.discord_test_button.configure(state="normal")
        if sent:
            self.discord_status_var.set(f"{self.discord_notifier.status_text}; test delivered")
            messagebox.showinfo("Discord alert sent", "The Discord test alert was delivered.")
            return
        self.discord_status_var.set(f"{self.discord_notifier.status_text}; last test failed")
        messagebox.showerror(
            "Discord alert failed",
            f"The test alert could not be delivered. Check {self.discord_notifier.log_path}.",
        )

    def _send_feishu_test(self) -> None:
        if not self.feishu_notifier.enabled:
            messagebox.showerror(
                "Feishu alerts disabled",
                "Set RE9_FEISHU_WEBHOOK_URL or notifications.feishu.webhook_url, then restart the UI.",
            )
            return

        self.feishu_test_button.configure(state="disabled")
        self.feishu_status_var.set("Feishu error alerts: sending test alert...")

        def worker() -> None:
            sent = self.feishu_notifier.send_error(
                "RE9 Feishu test alert",
                "Feishu error notifications are configured correctly.",
                fields={
                    "Mode": "test",
                    "Session": self.session_id or "-",
                    "Trajectory set": self._trajectory_set_label(self.trajectory_set_id),
                },
            )
            self.root.after(0, lambda: self._set_feishu_test_result(sent))

        threading.Thread(target=worker, name="re9-feishu-test", daemon=True).start()

    def _set_feishu_test_result(self, sent: bool) -> None:
        self.feishu_test_button.configure(state="normal")
        if sent:
            self.feishu_status_var.set(f"{self.feishu_notifier.status_text}; test delivered")
            messagebox.showinfo("Feishu alert sent", "The Feishu test alert was delivered.")
            return
        self.feishu_status_var.set(f"{self.feishu_notifier.status_text}; last test failed")
        messagebox.showerror(
            "Feishu alert failed",
            f"The test alert could not be delivered. Check {self.feishu_notifier.log_path}.",
        )

    def _set_error(self, message: str, error_log: Path) -> None:
        self._notify_error_alerts("RE9 still scan failed", message, "still scan", error_log)
        self.running = False
        self.status_var.set(f"Failed: {message}")
        self.output_var.set(f"Error log: {error_log}")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self._set_trajectory_buttons("normal")
        self._refresh_qa_button()
        messagebox.showerror("Still scan failed", message)

    def _set_trajectory_done(self, status: str) -> None:
        self.trajectory_running = False
        self.status_var.set(status)
        self.output_var.set(f"Output: {self.current_trajectory_run_dir or self.trajectory_output_dir}")
        self.trajectory_resume_var.set(self._trajectory_resume_status_text())
        self.start_button.configure(state="normal")
        self._set_trajectory_buttons("normal")
        self.stop_trajectory_button.configure(state="disabled")
        self._refresh_qa_button()

    def _set_trajectory_error(self, message: str, error_log: Path) -> None:
        self._notify_error_alerts(
            "RE9 trajectory recording failed",
            message,
            "trajectory recording",
            error_log,
        )
        self.trajectory_running = False
        self.status_var.set(f"Trajectory failed: {message}")
        self.output_var.set(f"Error log: {error_log}")
        self.trajectory_resume_var.set(self._trajectory_resume_status_text())
        self.start_button.configure(state="normal")
        self._set_trajectory_buttons("normal")
        self.stop_trajectory_button.configure(state="disabled")
        self._refresh_qa_button()
        messagebox.showerror("Trajectory replay failed", message)

    def _latest_trajectory_run_dir(self) -> Path | None:
        if not self.trajectory_output_dir.exists():
            return None
        candidates = [
            path
            for path in self.trajectory_output_dir.iterdir()
            if path.is_dir() and path.name.startswith("run_") and (path / "trajectory_run_state.json").exists()
        ]
        if not candidates:
            candidates = [path for path in self.trajectory_output_dir.iterdir() if path.is_dir() and path.name.startswith("run_")]
        if not candidates:
            return None
        candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return candidates[0]

    def _load_planned_indices_for_run(self, run_dir: Path) -> list[int]:
        state_path = run_dir / "trajectory_run_state.json"
        if state_path.exists():
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                planned = payload.get("planned_indices")
                if isinstance(planned, list):
                    indices = sorted({int(item) for item in planned if int(item) > 0})
                    if indices:
                        return indices
            except Exception:
                pass
        return list(range(1, self.trajectory_count + 1))

    def _detect_completed_trajectory_indices(self, run_dir: Path, planned: list[int]) -> set[int]:
        profile_trusts_state = bool(
            self.trajectory_sets.get(self.trajectory_set_id, {}).get("trust_run_state", False)
        )
        env_trusts_state = os.environ.get("RE9_TRAJECTORY_TRUST_RUN_STATE", "").lower() in {"1", "true", "yes"}
        if profile_trusts_state or env_trusts_state:
            state_path = run_dir / "trajectory_run_state.json"
            if state_path.exists():
                try:
                    payload = json.loads(state_path.read_text(encoding="utf-8"))
                    completed = payload.get("completed_indices")
                    if isinstance(completed, list):
                        planned_set = set(planned)
                        return {
                            index
                            for item in completed
                            if (index := int(item)) > 0 and index in planned_set
                        }
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    pass
        return {index for index in planned if self._valid_video_for_trajectory(run_dir, index) is not None}

    def _wait_for_stable_video_file(self, video_path: Path, timeout_sec: float = TRAJECTORY_VIDEO_SETTLE_TIMEOUT_SEC) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_sec)
        stable_size = -1
        stable_seen = 0
        while time.monotonic() < deadline:
            if not video_path.exists():
                time.sleep(0.5)
                continue
            size = video_path.stat().st_size
            if size >= MIN_VALID_TRAJECTORY_VIDEO_BYTES and size == stable_size:
                stable_seen += 1
                if stable_seen >= TRAJECTORY_VIDEO_STABLE_CHECKS:
                    return True
            else:
                stable_size = size
                stable_seen = 0
            time.sleep(1.0)
        return video_path.exists() and video_path.stat().st_size >= MIN_VALID_TRAJECTORY_VIDEO_BYTES

    def _video_has_frames(self, video_path: Path) -> bool:
        if not video_path.exists() or video_path.stat().st_size < MIN_VALID_TRAJECTORY_VIDEO_BYTES:
            return False
        try:
            import cv2  # type: ignore

            capture = cv2.VideoCapture(str(video_path))
            try:
                frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                if frames > 0:
                    return True
                ok, _frame = capture.read()
                return bool(ok)
            finally:
                capture.release()
        except Exception:
            return True

    def _is_valid_trajectory_video(self, video_path: Path, wait: bool = False) -> bool:
        if wait and not self._wait_for_stable_video_file(video_path):
            return False
        return self._video_has_frames(video_path)

    def _valid_video_for_trajectory(self, run_dir: Path, trajectory_index: int) -> Path | None:
        trajectory_dir = run_dir / f"traj_{trajectory_index:02d}"
        result_json = trajectory_dir / "replay_result.json"
        if result_json.exists():
            try:
                payload = json.loads(result_json.read_text(encoding="utf-8"))
                video_text = str(payload.get("video_path") or "")
                if video_text:
                    video_path = Path(video_text)
                    if self._is_valid_trajectory_video(video_path):
                        return video_path
            except Exception:
                pass
        raw_dir = trajectory_dir / "raw"
        if raw_dir.exists():
            candidates = [
                path
                for path in raw_dir.iterdir()
                if path.is_file()
                and path.suffix.lower() in {".mp4", ".mkv", ".mov", ".flv", ".avi"}
                and self._is_valid_trajectory_video(path)
            ]
            if candidates:
                candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
                return candidates[0]
        return None

    def _validate_trajectory_result(self, result: dict[str, Path | str], trajectory_index: int) -> Path:
        video_value = result.get("video_path")
        video_path = Path(video_value) if video_value else None
        if video_path is None or not self._is_valid_trajectory_video(video_path, wait=True):
            run_dir = self.current_trajectory_run_dir or self.trajectory_output_dir
            fallback = self._valid_video_for_trajectory(run_dir, trajectory_index)
            if fallback is not None:
                return fallback
            raise RuntimeError(f"Trajectory {trajectory_index:02d} did not produce a valid video file.")
        return video_path

    def _write_trajectory_run_state(
        self,
        status: str,
        current_index: int | None = None,
        last_video: Path | None = None,
        error: str = "",
    ) -> None:
        if self.current_trajectory_run_dir is None:
            return
        state_path = self.trajectory_state_path or (self.current_trajectory_run_dir / "trajectory_run_state.json")
        state_path.parent.mkdir(parents=True, exist_ok=True)
        planned = list(self.planned_trajectory_indices)
        completed = sorted(self.completed_trajectory_indices)
        remaining = [index for index in planned if index not in self.completed_trajectory_indices]
        payload = {
            "status": status,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "trajectory_label": self._trajectory_set_label(self.trajectory_set_id),
            "trajectory_json": str(self.trajectory_json),
            "run_dir": str(self.current_trajectory_run_dir),
            "restart_every": self.obs_restart_every,
            "current_index": current_index,
            "planned_total": len(planned),
            "completed_total": len(completed),
            "remaining_total": len(remaining),
            "next_index": remaining[0] if remaining else None,
            "planned_indices": planned,
            "completed_indices": completed,
            "last_video": str(last_video) if last_video else "",
            "error": error,
        }
        state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if remaining:
            text = (
                f"Resume: next missing trajectory {remaining[0]:02d}, "
                f"completed {len(completed)}/{len(planned)} in {self.current_trajectory_run_dir.name}"
            )
        else:
            text = f"Resume: latest run complete ({len(completed)}/{len(planned)})"
        self.root.after(0, lambda text=text: self.trajectory_resume_var.set(text))

    @staticmethod
    def _read_int_env(name: str, default: int) -> int:
        try:
            return max(0, int(os.environ.get(name, str(default)) or default))
        except ValueError:
            return default

    @staticmethod
    def _read_float_env(name: str, default: float) -> float:
        try:
            return max(0.0, float(os.environ.get(name, str(default)) or default))
        except ValueError:
            return default

    def _restart_obs_between_batches(self, completed: int, total: int) -> None:
        if not self.obs_restart_command:
            raise RuntimeError("RE9_OBS_RESTART_COMMAND must be set when RE9_OBS_RESTART_EVERY_N is enabled.")
        self.root.after(
            0,
            lambda: self.status_var.set(f"Restarting OBS to release GPU memory after {completed}/{total} trajectories..."),
        )
        self.root.after(0, lambda: self.file_var.set("Current mode: restarting OBS WebSocket"))
        self._terminate_obs_processes()
        self._clear_obs_sentinel_files()
        log_path = ensure_dir(self.config.output_dir) / "obs_restart.log"
        with log_path.open("ab") as log_handle:
            subprocess.Popen(
                shlex.split(self.obs_restart_command),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=os.environ.copy(),
            )
        self._wait_for_obs_websocket()

    def _terminate_obs_processes(self) -> None:
        try:
            subprocess.run(["pkill", "-x", "obs"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            result = subprocess.run(["pgrep", "-x", "obs"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if result.returncode != 0:
                return
            time.sleep(0.25)
        subprocess.run(["pkill", "-9", "-x", "obs"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.0)

    def _clear_obs_sentinel_files(self) -> None:
        sentinel_dir = Path.home() / ".config" / "obs-studio" / ".sentinel"
        if not sentinel_dir.exists():
            return
        backup_dir = sentinel_dir.with_name(".sentinel.backup-re9")
        backup_dir.mkdir(parents=True, exist_ok=True)
        suffix = timestamp_id()
        for path in sentinel_dir.glob("run_*"):
            try:
                path.rename(backup_dir / f"{path.name}.{suffix}")
            except OSError:
                pass

    def _wait_for_obs_websocket(self) -> None:
        from .obs_control import connect_obs

        obs_cfg = self.config.raw["obs"]
        password = self.obs_password or obs_cfg.get("password", "")
        deadline = time.monotonic() + self.obs_restart_wait_sec
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                client = connect_obs(obs_cfg["host"], int(obs_cfg["port"]), password)
                client.get_version()
                return
            except Exception as exc:
                last_error = exc
                time.sleep(0.5)
        raise RuntimeError(f"OBS did not reconnect after restart: {last_error}")

    def _set_qa_done(self, outputs: dict[str, Path]) -> None:
        self.qa_running = False
        self.status_var.set("Capture QA done. Broken original images were deleted.")
        self.file_var.set(f"QA report: {outputs['inaccessible_points_csv']}")
        self.output_var.set(f"Deleted image log: {outputs['deleted_images_csv']}")
        self.start_button.configure(state="normal")
        self._refresh_qa_button()

    def _set_qa_error(self, message: str, error_log: Path) -> None:
        self._notify_error_alerts("RE9 bad-image QA failed", message, "bad-image QA", error_log)
        self.qa_running = False
        self.status_var.set(f"Bad-image QA failed: {message}")
        self.output_var.set(f"Error log: {error_log}")
        self.start_button.configure(state="normal")
        self._refresh_qa_button()
        messagebox.showerror("Bad-image QA failed", message)

    def _refresh_qa_button(self) -> None:
        samples_csv = self.output_dir / "samples.csv" if self.output_dir is not None else None
        state = "normal" if samples_csv is not None and samples_csv.exists() and not self.running else "disabled"
        self.qa_button.configure(state=state)

    def _set_trajectory_buttons(self, state: str) -> None:
        if self.trajectory_count <= 0:
            state = "disabled"
        self.record_one_trajectory_button.configure(state=state)
        self.record_all_trajectories_button.configure(state=state)
        self.resume_trajectory_button.configure(state=state)
        self.trajectory_spinbox.configure(state=state)
        self.trajectory_set_combo.configure(state="disabled" if self.trajectory_running else "readonly")

    def _confirm_trajectory_ready(self, title: str) -> bool:
        return messagebox.askyesno(
            "Start trajectory video capture",
            (
                f"{title}\n\n"
                "Before clicking Yes:\n"
                "1. Open the game and enter the target scene.\n"
                "2. Enable FreeCam.\n"
                "3. Hide the REFramework/FreeCam UI with Insert.\n"
                "4. Keep OBS open and connected.\n\n"
                "The replay direction is low score -> high score."
            ),
        )

    def _load_trajectory_count(self) -> int:
        if not self.trajectory_json.exists():
            return 0
        try:
            with self.trajectory_json.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            trajectories = payload.get("trajectories")
            if isinstance(trajectories, list):
                return len(trajectories)
            if isinstance(payload.get("keyframes"), list):
                return 1
        except Exception:
            return 0
        return 0

    def _trajectory_set_choices(self) -> list[str]:
        return [str(item["label"]) for item in self.trajectory_sets.values()]

    def _trajectory_set_label(self, set_id: str) -> str:
        return str(self.trajectory_sets[set_id]["label"])

    def _trajectory_status_text(self) -> str:
        restart_text = (
            f"OBS restart every {self.obs_restart_every} completed paths"
            if self.obs_restart_every > 0
            else "OBS auto-restart disabled"
        )
        return (
            f"Trajectory replay: {self._trajectory_set_label(self.trajectory_set_id)} "
            f"loaded {self.trajectory_count} paths, direction low -> high, {restart_text}"
        )

    def _trajectory_resume_status_text(self) -> str:
        run_dir = self.current_trajectory_run_dir or self._latest_trajectory_run_dir()
        if run_dir is None:
            return "Resume: no previous run found"
        planned = self._load_planned_indices_for_run(run_dir)
        completed = self._detect_completed_trajectory_indices(run_dir, planned)
        remaining = [index for index in planned if index not in completed]
        if not remaining:
            return f"Resume: latest run complete ({len(completed)}/{len(planned)})"
        return f"Resume: next missing trajectory {remaining[0]:02d}, completed {len(completed)}/{len(planned)} in {run_dir.name}"

    def _on_trajectory_set_change(self, _event: object | None = None) -> None:
        if self.trajectory_running:
            self.trajectory_set_var.set(self._trajectory_set_label(self.trajectory_set_id))
            return
        label = self.trajectory_set_var.get()
        for set_id, item in self.trajectory_sets.items():
            if item["label"] == label:
                self._set_trajectory_set(set_id)
                return

    def _set_trajectory_set(self, set_id: str) -> None:
        self.trajectory_set_id = set_id
        item = self.trajectory_sets[set_id]
        self.trajectory_json = Path(item["json"])
        self.trajectory_output_dir = Path(item["output_dir"])
        self.trajectory_session_prefix = str(item["session_prefix"])
        self.current_trajectory_run_dir = None
        self.trajectory_state_path = None
        self.planned_trajectory_indices = []
        self.completed_trajectory_indices = set()
        self.trajectory_count = self._load_trajectory_count()
        self.trajectory_index_var.set(1)
        self.trajectory_spinbox.configure(to=max(1, self.trajectory_count))
        self.trajectory_var.set(self._trajectory_status_text())
        self.trajectory_output_var.set(f"Output: {self.trajectory_output_dir}")
        self.trajectory_resume_var.set(self._trajectory_resume_status_text())
        self.output_var.set("Output: -")
        self._set_trajectory_buttons("normal")


def run_still_scan_gui(
    config: AppConfig,
    obs_password: str,
    x_min: float = 22.63,
    x_max: float = 153.83,
    z_min: float = -6.16,
    z_max: float = 11.49,
    y_values: str = "9.41,10.10,10.78",
    points_x: int = 5,
    points_z: int = 3,
    settle_seconds: float = 0.6,
    source_name: str | None = None,
    image_format: str = "jpg",
    image_width: int = 1920,
    image_height: int = 1080,
    image_quality: int = 100,
    max_samples: int | None = None,
    session_id: str | None = None,
    layers_config: Path | None = None,
    resume_from_layer: str | None = None,
    pose_plan_config: Path | None = None,
    trajectory_set_id: str = DEFAULT_TRAJECTORY_SET_ID,
    trajectory_json: Path | None = None,
    trajectory_output_dir: Path | None = None,
    trajectory_label: str | None = None,
    trajectory_session_prefix: str | None = None,
    topmost: bool = True,
) -> None:
    StillScanApp(
        config,
        obs_password=obs_password,
        x_min=x_min,
        x_max=x_max,
        z_min=z_min,
        z_max=z_max,
        y_values=y_values,
        points_x=points_x,
        points_z=points_z,
        settle_seconds=settle_seconds,
        source_name=source_name,
        image_format=image_format,
        image_width=image_width,
        image_height=image_height,
        image_quality=image_quality,
        max_samples=max_samples,
        session_id=session_id,
        layers_config=layers_config,
        resume_from_layer=resume_from_layer,
        pose_plan_config=pose_plan_config,
        trajectory_set_id=trajectory_set_id,
        trajectory_json=trajectory_json,
        trajectory_output_dir=trajectory_output_dir,
        trajectory_label=trajectory_label,
        trajectory_session_prefix=trajectory_session_prefix,
        topmost=topmost,
    ).run()

