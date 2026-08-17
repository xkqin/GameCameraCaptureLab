from __future__ import annotations

import json
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from .config import AppConfig
from .paths import PROJECT_ROOT, ensure_dir
from .trajectory_replay import load_replay_trajectory, replay_trajectory_to_obs
from .utils import timestamp_id


DEFAULT_TRAJECTORY_JSON = PROJECT_ROOT / "data" / "trajectories" / "scene_1.1" / "scene_1_1_trajectories.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "videos" / "trajectories" / "scene_1.1_low_to_high"


class TrajectoryReplayApp:
    """Tkinter UI for replaying selected score trajectories through FreeCam and OBS."""

    def __init__(
        self,
        config: AppConfig,
        obs_password: str,
        trajectory_json: Path = DEFAULT_TRAJECTORY_JSON,
        output_dir: Path = DEFAULT_OUTPUT_DIR,
        countdown_sec: float = 3.0,
        settle_sec: float = 1.0,
        post_roll_sec: float = 1.0,
        speed: float = 1.0,
        duration_sec: float | None = None,
        topmost: bool = True,
    ) -> None:
        self.config = config
        self.obs_password = obs_password
        self.trajectory_json = trajectory_json
        self.output_dir = output_dir
        self.countdown_sec = countdown_sec
        self.settle_sec = settle_sec
        self.post_roll_sec = post_roll_sec
        self.speed = speed
        self.duration_sec = duration_sec
        self.running = False
        self.stop_after_current = threading.Event()
        self.trajectories = self._load_trajectory_index()
        self.current_run_dir: Path | None = None

        self.root = tk.Tk()
        self.root.title("RE9 Trajectory Video Recorder")
        self.root.geometry("820x520")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", topmost)

        self.status_var = tk.StringVar(value="Ready. Open game + enable FreeCam + open OBS, then record.")
        self.json_var = tk.StringVar(value=str(self.trajectory_json))
        self.output_var = tk.StringVar(value=str(self.output_dir))
        self.detail_var = tk.StringVar(value=self._selected_detail_text(0))
        self.progress_var = tk.IntVar(value=0)
        self.total_var = tk.StringVar(value=f"{len(self.trajectories)} trajectories loaded")

        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True, padx=16, pady=14)

        ttk.Label(outer, text="Preloaded trajectory set", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(outer, textvariable=self.json_var, wraplength=780).pack(anchor="w", pady=(2, 8))

        ttk.Label(outer, text="Output folder", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(outer, textvariable=self.output_var, wraplength=780).pack(anchor="w", pady=(2, 8))

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=False)
        ttk.Label(left, textvariable=self.total_var).pack(anchor="w")
        self.listbox = tk.Listbox(left, width=34, height=15, exportselection=False)
        self.listbox.pack(fill="y", expand=False, pady=6)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        for index, item in enumerate(self.trajectories, start=1):
            label = item.get("trajectory_id") or f"trajectory_{index:03d}"
            self.listbox.insert("end", f"{index:02d}  {label}")
        if self.trajectories:
            self.listbox.selection_set(0)

        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(16, 0))
        ttk.Label(right, textvariable=self.status_var, font=("Segoe UI", 11, "bold"), wraplength=500).pack(anchor="w", pady=(0, 8))
        ttk.Label(right, textvariable=self.detail_var, wraplength=500).pack(anchor="w", pady=(0, 8))

        ttk.Label(
            right,
            text=(
                "Checklist: game is open, REFramework FreeCam is enabled, REFramework menu is hidden, "
                "OBS WebSocket is connected, and OBS captures only the game window."
            ),
            wraplength=500,
        ).pack(anchor="w", pady=(0, 10))

        controls = ttk.Frame(right)
        controls.pack(fill="x", pady=4)
        self.record_selected_button = ttk.Button(controls, text="Record Selected", command=self.record_selected)
        self.record_selected_button.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.record_all_button = ttk.Button(controls, text="Record All Low -> High", command=self.record_all)
        self.record_all_button.pack(side="left", fill="x", expand=True, padx=(6, 0))

        self.stop_button = ttk.Button(right, text="Stop After Current Trajectory", command=self.stop_after_current_trajectory, state="disabled")
        self.stop_button.pack(fill="x", pady=5)

        self.progress = ttk.Progressbar(right, maximum=max(1, len(self.trajectories)), variable=self.progress_var)
        self.progress.pack(fill="x", pady=10)

        ttk.Label(
            right,
            text=(
                "Direction: forward low -> high. This UI does not start until you click a record button."
            ),
            wraplength=500,
        ).pack(anchor="w")

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def run(self) -> None:
        self.root.mainloop()

    def record_selected(self) -> None:
        selection = self.listbox.curselection()
        if not selection:
            messagebox.showerror("No trajectory selected", "Select one trajectory first.")
            return
        self._start_worker([selection[0] + 1])

    def record_all(self) -> None:
        if not self.trajectories:
            messagebox.showerror("No trajectories", "No trajectories were loaded.")
            return
        if not messagebox.askyesno(
            "Start trajectory recording",
            "Start recording all loaded trajectories low -> high?\n\nOpen the game, enable FreeCam, hide the REFramework UI, and keep OBS ready first.",
        ):
            return
        self._start_worker(list(range(1, len(self.trajectories) + 1)))

    def stop_after_current_trajectory(self) -> None:
        self.stop_after_current.set()
        self.status_var.set("Will stop after the current trajectory finishes.")

    def on_close(self) -> None:
        if self.running:
            messagebox.showinfo("Recording active", "Use Stop After Current Trajectory first, then close after it finishes.")
            return
        self.root.destroy()

    def _start_worker(self, indices: list[int]) -> None:
        if self.running:
            return
        self.running = True
        label = "all" if len(indices) > 1 else f"traj_{indices[0]:02d}"
        self.current_run_dir = ensure_dir(self.output_dir / f"run_{timestamp_id()}_{label}_low_to_high")
        self.output_var.set(str(self.current_run_dir))
        self.stop_after_current.clear()
        self.progress_var.set(0)
        self.record_selected_button.configure(state="disabled")
        self.record_all_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        threading.Thread(target=self._worker, args=(indices,), daemon=True).start()

    def _worker(self, indices: list[int]) -> None:
        run_dir = self.current_run_dir or ensure_dir(self.output_dir / f"run_{timestamp_id()}_low_to_high")
        completed = 0
        try:
            for position, trajectory_index in enumerate(indices, start=1):
                if self.stop_after_current.is_set():
                    break
                trajectory = load_replay_trajectory(
                    self.trajectory_json,
                    trajectory_index=trajectory_index,
                    reverse=False,
                )
                session = f"scene_1_1_low_to_high_traj_{trajectory_index:02d}"
                out = run_dir / f"traj_{trajectory_index:02d}"
                self._set_status(
                    f"Recording {position}/{len(indices)}: {trajectory.trajectory_id} low -> high"
                )
                replay_trajectory_to_obs(
                    self.config,
                    trajectory,
                    obs_password=self.obs_password,
                    output_dir=out,
                    session_id=session,
                    countdown_sec=self.countdown_sec,
                    settle_sec=self.settle_sec,
                    post_roll_sec=self.post_roll_sec,
                    speed=self.speed,
                    duration_sec=self.duration_sec,
                    record=True,
                    write_pose_log=True,
                )
                completed += 1
                self.root.after(0, lambda value=completed: self.progress_var.set(value))
            done_text = f"Done. Recorded {completed}/{len(indices)} trajectories."
            if self.stop_after_current.is_set():
                done_text = f"Stopped. Recorded {completed}/{len(indices)} trajectories."
            self.root.after(0, lambda: self._set_done(done_text))
        except Exception as exc:
            error_log = ensure_dir(self.config.output_dir) / "trajectory_replay_gui_errors.log"
            with error_log.open("a", encoding="utf-8") as handle:
                handle.write(traceback.format_exc())
                handle.write("\n")
            self.root.after(0, lambda: self._set_error(str(exc), error_log))

    def _set_status(self, text: str) -> None:
        self.root.after(0, lambda: self.status_var.set(text))

    def _set_done(self, text: str) -> None:
        self.running = False
        self.status_var.set(text)
        self.record_selected_button.configure(state="normal")
        self.record_all_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def _set_error(self, text: str, error_log: Path) -> None:
        self.running = False
        self.status_var.set(f"Error: {text}")
        self.record_selected_button.configure(state="normal")
        self.record_all_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        messagebox.showerror("Trajectory replay failed", f"{text}\n\nLog:\n{error_log}")

    def _on_select(self, _event: object | None = None) -> None:
        selection = self.listbox.curselection()
        index = selection[0] if selection else 0
        self.detail_var.set(self._selected_detail_text(index))

    def _selected_detail_text(self, index: int) -> str:
        if not self.trajectories:
            return "No trajectories loaded."
        item = self.trajectories[max(0, min(index, len(self.trajectories) - 1))]
        keyframes = item.get("keyframes") or []
        scores = [float(frame["score"]) for frame in keyframes if frame.get("score") is not None]
        duration = 0.0
        if keyframes:
            duration = max(float(frame.get("time_sec") or i * 0.2) for i, frame in enumerate(keyframes))
        if scores:
            return (
                f"Selected: {item.get('trajectory_id')} | keyframes={len(keyframes)} | "
                f"duration={duration:.1f}s | score {min(scores):.3f} -> {max(scores):.3f}"
            )
        return f"Selected: {item.get('trajectory_id')} | keyframes={len(keyframes)} | duration={duration:.1f}s"

    def _load_trajectory_index(self) -> list[dict[str, object]]:
        if not self.trajectory_json.exists():
            return []
        with self.trajectory_json.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        trajectories = payload.get("trajectories")
        if isinstance(trajectories, list):
            return trajectories
        if isinstance(payload.get("keyframes"), list):
            return [payload]
        return []


def run_trajectory_replay_gui(
    config: AppConfig,
    obs_password: str = "",
    trajectory_json: Path = DEFAULT_TRAJECTORY_JSON,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    countdown_sec: float = 3.0,
    settle_sec: float = 1.0,
    post_roll_sec: float = 1.0,
    speed: float = 1.0,
    duration_sec: float | None = None,
    topmost: bool = True,
) -> None:
    app = TrajectoryReplayApp(
        config,
        obs_password=obs_password,
        trajectory_json=trajectory_json,
        output_dir=output_dir,
        countdown_sec=countdown_sec,
        settle_sec=settle_sec,
        post_roll_sec=post_roll_sec,
        speed=speed,
        duration_sec=duration_sec,
        topmost=topmost,
    )
    app.run()
