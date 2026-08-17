from __future__ import annotations

import csv
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from .config import AppConfig
from .lua_control import LuaControl
from .paths import ensure_dir


class ScanMonitorApp:
    """Small live monitor for scan-region output folders."""

    def __init__(
        self,
        config: AppConfig,
        session_id: str | None = None,
        total_segments: int = 180,
        refresh_sec: float = 2.0,
        topmost: bool = True,
    ) -> None:
        self.config = config
        self.total_segments = total_segments
        self.refresh_ms = max(250, int(refresh_sec * 1000))
        self.scan_root = ensure_dir("data/videos/scans")
        self.output_dir = self._resolve_output_dir(session_id)
        self.session_id = self.output_dir.name
        self.control = LuaControl(config)

        self.root = tk.Tk()
        self.root.title("RE9 Scan Progress Monitor")
        self.root.geometry("760x390")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", topmost)

        self.title_var = tk.StringVar(value=f"Session: {self.session_id}")
        self.count_var = tk.StringVar(value="Segments: 0 / 180")
        self.percent_var = tk.StringVar(value="0.0%")
        self.latest_var = tk.StringVar(value="Latest: -")
        self.pose_var = tk.StringVar(value="Pose: -")
        self.file_var = tk.StringVar(value="File: -")
        self.eta_var = tk.StringVar(value="ETA: -")
        self.status_var = tk.StringVar(value="Status: reading...")

        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=16, pady=14)

        ttk.Label(frame, textvariable=self.title_var, font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.progress = ttk.Progressbar(frame, maximum=self.total_segments, mode="determinate")
        self.progress.pack(fill="x", pady=(12, 4))

        row = ttk.Frame(frame)
        row.pack(fill="x", pady=4)
        ttk.Label(row, textvariable=self.count_var, font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Label(row, textvariable=self.percent_var).pack(side="right")

        ttk.Label(frame, textvariable=self.latest_var, wraplength=720).pack(anchor="w", pady=4)
        ttk.Label(frame, textvariable=self.pose_var, wraplength=720).pack(anchor="w", pady=4)
        ttk.Label(frame, textvariable=self.file_var, wraplength=720).pack(anchor="w", pady=4)
        ttk.Label(frame, textvariable=self.eta_var, wraplength=720).pack(anchor="w", pady=4)
        ttk.Separator(frame).pack(fill="x", pady=10)
        ttk.Label(frame, textvariable=self.status_var, wraplength=720).pack(anchor="w", pady=4)

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Refresh", command=self.refresh).pack(side="left")
        ttk.Button(buttons, text="Release FreeCam", command=self.release_freecam).pack(side="left", padx=8)

        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

    def run(self) -> None:
        self.refresh()
        self.root.mainloop()

    def refresh(self) -> None:
        rows = self._read_rows()
        clips_count = self._count_files(self.output_dir / "clips")
        raw_files = list((self.output_dir / "raw").glob("*")) if (self.output_dir / "raw").exists() else []
        done = len(rows)
        percent = (done / self.total_segments * 100.0) if self.total_segments else 0.0
        self.progress.configure(value=min(done, self.total_segments))
        self.count_var.set(f"Segments: {done} / {self.total_segments} | clips: {clips_count}")
        self.percent_var.set(f"{percent:.1f}%")

        if rows:
            last = rows[-1]
            self.latest_var.set(
                "Latest: "
                f"{last.get('segment_id', '-')} | point {last.get('point_index', '-')} | "
                f"yaw {last.get('yaw_deg', '-')} -> {last.get('yaw_end_deg', '-')} deg"
            )
            self.pose_var.set(
                "Pose: "
                f"x={_fmt(last.get('x'))} y={_fmt(last.get('y'))} z={_fmt(last.get('z'))} "
                f"pitch_rad={_fmt(last.get('pitch_rad'))}"
            )
            self.file_var.set(f"File: {last.get('video_path', '-')}")
            self.eta_var.set(self._eta_text(rows, done))
        else:
            self.latest_var.set("Latest: -")
            self.pose_var.set("Pose: -")
            self.file_var.set("File: -")
            self.eta_var.set("ETA: waiting for first segment")

        active_raw = [path.name for path in raw_files if path.is_file()]
        self.status_var.set(
            f"Folder: {self.output_dir} | raw active files: {', '.join(active_raw) if active_raw else 'none'}"
        )
        self.root.after(self.refresh_ms, self.refresh)

    def release_freecam(self) -> None:
        status = self.control.read_status() or {}
        session = str(status.get("session_id") or self.session_id)
        self.control.write_clear_pose_control(session)
        time.sleep(0.2)
        self.control.write_stop_control(session)
        time.sleep(0.2)
        self.control.write_clear_pose_control(session)
        self.status_var.set(f"Release command sent for session {session}.")

    def _resolve_output_dir(self, session_id: str | None) -> Path:
        if session_id:
            return self.scan_root / session_id
        candidates = [path for path in self.scan_root.iterdir() if path.is_dir()]
        if not candidates:
            return self.scan_root / "unknown"
        candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        return candidates[0]

    def _read_rows(self) -> list[dict[str, str]]:
        path = self.output_dir / "segments.csv"
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                return [dict(row) for row in csv.DictReader(handle)]
        except OSError:
            return []

    def _count_files(self, directory: Path) -> int:
        if not directory.exists():
            return 0
        return sum(1 for path in directory.iterdir() if path.is_file())

    def _eta_text(self, rows: list[dict[str, str]], done: int) -> str:
        if done <= 0:
            return "ETA: -"
        clips_dir = self.output_dir / "clips"
        clip_files = sorted((path for path in clips_dir.glob("*.mp4")), key=lambda item: item.stat().st_mtime)
        if len(clip_files) >= 2:
            elapsed = clip_files[-1].stat().st_mtime - clip_files[0].stat().st_mtime
            per_segment = elapsed / max(1, len(clip_files) - 1)
        else:
            per_segment = 10.8
        remaining = max(0, self.total_segments - done)
        eta_sec = remaining * per_segment
        return f"ETA: {_format_duration(eta_sec)} remaining | avg {_format_duration(per_segment)} / segment"


def _fmt(value: object) -> str:
    try:
        return f"{float(str(value)):.3f}"
    except (TypeError, ValueError):
        return str(value or "-")


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {sec}s"
    return f"{minutes}m {sec}s"


def run_scan_monitor(
    config: AppConfig,
    session_id: str | None = None,
    total_segments: int = 180,
    refresh_sec: float = 2.0,
    topmost: bool = True,
) -> None:
    ScanMonitorApp(
        config,
        session_id=session_id,
        total_segments=total_segments,
        refresh_sec=refresh_sec,
        topmost=topmost,
    ).run()
