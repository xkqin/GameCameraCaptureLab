from __future__ import annotations

import base64
import csv
import io
import traceback
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import cv2
from PIL import Image

from .config import AppConfig
from .laion_scorer import LAIONAestheticScorer
from .lua_control import LuaControl, make_session_id
from .obs_control import OBSController, find_latest_video_file
from .paths import ensure_dir


class OneClickRecordApp:
    """Tiny Tkinter controller for one-click OBS recording plus Lua pose logging."""

    def __init__(
        self,
        config: AppConfig,
        obs_password: str = "",
        live_score: bool = True,
        live_score_interval: float = 2.0,
        live_summary_window_sec: float = 10.0,
        segment_window_sec: float = 5.0,
        device: str = "auto",
        topmost: bool = True,
    ) -> None:
        self.config = config
        self.obs_password = obs_password or str(config.raw["obs"].get("password", ""))
        self.live_score_enabled = live_score
        self.live_score_interval = live_score_interval
        self.live_summary_window_sec = live_summary_window_sec
        self.segment_window_sec = segment_window_sec
        self.device = device
        self.topmost = topmost
        self.control = LuaControl(config)
        self.controller: OBSController | None = None
        self.scorer: LAIONAestheticScorer | None = None
        self.session_id = ""
        self.pose_log: Path | None = None
        self.live_score_csv: Path | None = None
        self.live_best_csv: Path | None = None
        self.segment_best_csv: Path | None = None
        self.started_at = 0.0
        self.current_segment_start_elapsed = 0.0
        self.record_dir: Path | None = None
        self.recording = False
        self.score_run_id = 0
        self.score_stop_event = threading.Event()
        self.score_rows: list[dict[str, float | str]] = []
        self.live_best_rows: list[dict[str, float | str]] = []
        self.segment_best_rows: list[dict[str, float | str]] = []
        self.processed_segment_files: set[str] = set()
        self.last_saved_rolling_best_key = ""
        self.score_error_log = ensure_dir(self.config.output_dir) / "live_score_errors.log"

        self.root = tk.Tk()
        self.root.title("RE9 One-Click Recorder + LAION Live Score")
        self.root.geometry("700x430")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", self.topmost)

        self.status_var = tk.StringVar(value="Ready")
        self.session_var = tk.StringVar(value="Session: -")
        self.pose_var = tk.StringVar(value="Pose log: -")
        self.video_var = tk.StringVar(value="Video: -")
        self.score_var = tk.StringVar(value="Live LAION score: idle")
        self.score_source_var = tk.StringVar(value="OBS source: current Program scene")
        self.window_best_var = tk.StringVar(value="5s segment best: waiting")

        padding = {"padx": 16, "pady": 5}
        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        self.button = ttk.Button(frame, text="Start Recording", command=self.toggle_recording)
        self.button.pack(fill="x", **padding)

        score_frame = ttk.LabelFrame(frame, text="Live LAION score")
        score_frame.pack(fill="x", padx=16, pady=6)
        ttk.Label(score_frame, textvariable=self.score_var, wraplength=640, font=("Segoe UI", 11, "bold")).pack(
            anchor="w", padx=10, pady=4
        )
        ttk.Label(score_frame, textvariable=self.score_source_var, wraplength=640).pack(anchor="w", padx=10, pady=4)
        ttk.Label(score_frame, textvariable=self.window_best_var, wraplength=640).pack(anchor="w", padx=10, pady=4)

        ttk.Label(frame, textvariable=self.status_var, wraplength=480).pack(anchor="w", **padding)
        ttk.Label(frame, textvariable=self.session_var, wraplength=480).pack(anchor="w", **padding)
        ttk.Label(frame, textvariable=self.pose_var, wraplength=650).pack(anchor="w", **padding)
        ttk.Label(frame, textvariable=self.video_var, wraplength=650).pack(anchor="w", **padding)
        ttk.Separator(frame).pack(fill="x", pady=8)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def run(self) -> None:
        self.root.mainloop()

    def toggle_recording(self) -> None:
        self.button.configure(state="disabled")
        target = self.stop_recording if self.recording else self.start_recording
        threading.Thread(target=target, daemon=True).start()

    def start_recording(self) -> None:
        try:
            obs_cfg = self.config.raw["obs"]
            self._set_status("Connecting to OBS...")
            self.controller = OBSController(obs_cfg["host"], int(obs_cfg["port"]), self.obs_password)
            self._set_record_directory()

            self.session_id = make_session_id()
            base = self.config.pose_log_file
            self.pose_log = base.with_name(f"{base.stem}_{self.session_id}{base.suffix}")
            self.live_score_csv = ensure_dir(self.config.output_dir) / f"live_scores_{self.session_id}.csv"
            self.live_best_csv = ensure_dir(self.config.output_dir) / f"live_best_5s_{self.session_id}.csv"
            self.segment_best_csv = ensure_dir(self.config.output_dir) / f"segment_best_5s_{self.session_id}.csv"
            self.score_rows.clear()
            self.live_best_rows.clear()
            self.segment_best_rows.clear()
            self.processed_segment_files.clear()
            self.last_saved_rolling_best_key = ""
            self.score_run_id += 1
            if self.live_score_enabled:
                self._set_labels(
                    status="Loading LAION model before starting OBS recording...",
                    score="Live LAION score: loading model before recording...",
                    score_source="Startup warmup",
                )
                self._ensure_scorer_loaded()
            self.control.write_start_control(
                self.session_id,
                self.pose_log,
                float(self.config.raw["lua_logger"]["default_interval_sec"]),
            )
            self.control.wait_until_lua_logging_started(self.session_id, timeout_sec=2)

            self.current_segment_start_elapsed = 0.0
            self.record_dir = self._get_record_dir()
            self.controller.start_recording()
            self.started_at = time.time()
            self.recording = True
            if self.live_score_enabled:
                self._start_live_scoring(self.score_run_id)
            self._set_labels(
                status="Recording. Click Stop Recording when you are done.",
                session=f"Session: {self.session_id}",
                pose=f"Pose log: {self.pose_log.name if self.pose_log else '-'}",
                button="Stop Recording",
                button_enabled=True,
            )
        except Exception as exc:
            self.recording = False
            self._set_labels(status=f"Start failed: {exc}", button="Start Recording", button_enabled=True)
            messagebox.showerror("Start failed", str(exc))

    def stop_recording(self) -> None:
        try:
            if self.controller is None:
                raise RuntimeError("OBS controller is not connected.")
            self._set_status("Stopping OBS recording...")
            output_path = self.controller.stop_recording()

            if self.session_id:
                self.control.write_stop_control(self.session_id)
                self.control.wait_until_lua_logging_stopped(self.session_id, timeout_sec=2)
            self._stop_live_scoring()
            final_path = Path(output_path) if output_path else None
            if final_path and final_path.exists() and str(final_path) not in self.processed_segment_files and self.scorer:
                self._score_video_segment(final_path, self.current_segment_start_elapsed, time.time() - self.started_at)
            wrote_scores = self._write_live_scores()
            wrote_best = self._write_live_best()

            video_path = Path(output_path) if output_path else None
            if video_path is None or not video_path.exists():
                obs_cfg = self.config.raw["obs"]
                configured_dir = obs_cfg.get("recording_output_dir") or ""
                record_dir = Path(configured_dir) if configured_dir else self.controller.get_record_directory()
                if record_dir:
                    video_path = find_latest_video_file(
                        record_dir,
                        before_time=self.started_at,
                        supported_extensions=self.config.supported_video_extensions,
                    )

            self.recording = False
            self._set_labels(
                status="Stopped. Video and pose log are ready for analysis.",
                video=f"Video: {_compact_path(video_path) if video_path else 'Could not auto-detect'}",
                score=(
                    f"{self.score_var.get()} | saved: {self.live_score_csv.name if self.live_score_csv else '-'}"
                    if wrote_scores
                    else "Live LAION score: no live samples were captured."
                ),
                window_best=(
                    f"{self.window_best_var.get()} | saved: {self.live_best_csv.name if self.live_best_csv else '-'}"
                    if wrote_best
                    else self.window_best_var.get()
                ),
                button="Start Recording",
                button_enabled=True,
            )
        except Exception as exc:
            self.recording = False
            self._stop_live_scoring()
            self.score_run_id += 1
            self._set_labels(status=f"Stop failed: {exc}", button="Start Recording", button_enabled=True)
            messagebox.showerror("Stop failed", str(exc))

    def on_close(self) -> None:
        if self.recording:
            should_stop = messagebox.askyesno("Recording active", "Stop recording before closing?")
            if should_stop:
                self.toggle_recording()
                return
        self.root.destroy()

    def _set_status(self, status: str) -> None:
        self.root.after(0, lambda: self.status_var.set(status))

    def _set_labels(
        self,
        status: str | None = None,
        session: str | None = None,
        pose: str | None = None,
        video: str | None = None,
        score: str | None = None,
        score_source: str | None = None,
        window_best: str | None = None,
        button: str | None = None,
        button_enabled: bool | None = None,
    ) -> None:
        def apply() -> None:
            if status is not None:
                self.status_var.set(status)
            if session is not None:
                self.session_var.set(session)
            if pose is not None:
                self.pose_var.set(pose)
            if video is not None:
                self.video_var.set(video)
            if score is not None:
                self.score_var.set(score)
            if score_source is not None:
                self.score_source_var.set(score_source)
            if window_best is not None:
                self.window_best_var.set(window_best)
            if button is not None:
                self.button.configure(text=button)
            if button_enabled is not None:
                self.button.configure(state="normal" if button_enabled else "disabled")

        self.root.after(0, apply)

    def _start_live_scoring(self, run_id: int) -> None:
        self.score_stop_event.clear()
        if self.scorer is None:
            self._set_labels(score="Live LAION score: loading model...")
        else:
            self._set_labels(score=f"Live LAION score: model ready on {self.scorer.device_name}")
        threading.Thread(target=self._live_score_loop, args=(run_id,), daemon=True).start()

    def _stop_live_scoring(self) -> None:
        self.score_stop_event.set()

    def _live_score_loop(self, run_id: int) -> None:
        try:
            self._ensure_scorer_loaded()
            if self.score_stop_event.is_set() or not self.recording or run_id != self.score_run_id:
                self._set_labels(score="Live LAION score: model loaded after recording stopped. Start again for live scoring.")
                return
            self._set_labels(score=f"Live LAION score: model ready on {self.scorer.device_name}")
        except Exception as exc:
            self._append_score_error(exc)
            self._set_labels(score=f"Live LAION score unavailable: {exc}")
            return

        while not self.score_stop_event.is_set() and self.recording and run_id == self.score_run_id:
            try:
                best_row = self._score_next_obs_split_segment()
                if best_row:
                    self._set_labels(
                        score=f"5s segment best: score {_fmt_value(best_row['score'])} | t={_fmt_value(best_row['timestamp_sec'])}",
                        score_source=f"Segment source: {best_row.get('source_name', '')}",
                        window_best=_format_segment_best(best_row),
                    )
            except Exception as exc:
                self._append_score_error(exc)
                self._set_labels(score=f"Video segment scoring stopped: {exc}")
                break

    def _ensure_scorer_loaded(self) -> None:
        if self.scorer is not None:
            return
        self.scorer = LAIONAestheticScorer(
            model_name=str(self.config.raw["laion"]["model"]),
            device=self.device,
            repo_dir=self.config.laion_repo_dir,
            cache_dir=self.config.raw["laion"]["cache_dir"],
            hf_cache_dir=self.config.raw["laion"].get("hf_cache_dir", "core/third_party/huggingface_cache"),
        ).load_model()

    def _capture_obs_image(self) -> tuple[Image.Image, str]:
        if self.controller is None:
            raise RuntimeError("OBS is not connected.")
        scene_response = self.controller.client.get_current_program_scene()
        source_name = _response_value(scene_response, "current_program_scene_name", "currentProgramSceneName")
        if not source_name:
            raise RuntimeError("Could not determine current OBS Program scene.")
        screenshot = self.controller.client.get_source_screenshot(str(source_name), "jpg", 512, 288, 80)
        image_data = _response_value(screenshot, "image_data", "imageData")
        if not image_data:
            raise RuntimeError("OBS did not return screenshot image data.")
        encoded = str(image_data)
        if "," in encoded:
            encoded = encoded.split(",", 1)[1]
        raw = base64.b64decode(encoded)
        return Image.open(io.BytesIO(raw)).convert("RGB"), str(source_name)

    def _score_next_obs_split_segment(self) -> dict[str, float | str] | None:
        if self.score_stop_event.wait(self.segment_window_sec):
            return None
        segment_start = self.current_segment_start_elapsed
        split_at = time.time()
        segment_end = split_at - self.started_at
        self._set_labels(
            score=f"Scoring 5s video segment {segment_start:.1f}-{segment_end:.1f}s...",
            score_source="OBS: splitting current recording file",
        )
        status = self.controller.get_record_status()  # type: ignore[union-attr]
        is_recording = bool(_response_value(status, "output_active", "outputActive"))
        if not is_recording:
            raise RuntimeError("OBS says recording is not active, so it cannot split the current video file.")
        try:
            self.controller.client.split_record_file()  # type: ignore[union-attr]
        except Exception as exc:
            raise RuntimeError(
                "OBS refused SplitRecordFile. Keep Recording > Automatically Split File enabled and set it to manual split only, "
                "then restart this recorder window. Original error: "
                f"{exc}"
            ) from exc
        time.sleep(1.0)
        segment_path = self._find_next_segment_file(split_at - 10.0)
        if segment_path is None:
            raise RuntimeError("Could not locate the finalized 5s OBS segment after split_record_file.")
        best_row = self._score_video_segment(segment_path, segment_start, segment_end)
        self.current_segment_start_elapsed = segment_end
        return best_row

    def _write_live_scores(self) -> bool:
        if not self.live_score_csv or not self.score_rows:
            return False
        self.live_score_csv.parent.mkdir(parents=True, exist_ok=True)
        with self.live_score_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "session_id",
                    "segment_file",
                    "frame_index",
                    "segment_timestamp_sec",
                    "timestamp_sec",
                    "score",
                    "source_name",
                    "pose_timestamp_sec",
                    "x",
                    "y",
                    "z",
                    "yaw",
                    "pitch",
                    "fov",
                ],
            )
            writer.writeheader()
            writer.writerows(self.score_rows)
        return True

    def _write_live_best(self) -> bool:
        if not self.live_best_csv or not self.live_best_rows:
            return False
        self.live_best_csv.parent.mkdir(parents=True, exist_ok=True)
        with self.live_best_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "session_id",
                    "window_start_sec",
                    "window_end_sec",
                    "best_timestamp_sec",
                    "score",
                    "x",
                    "y",
                    "z",
                    "yaw",
                    "pitch",
                    "fov",
                    "source_name",
                    "pose_timestamp_sec",
                    "segment_file",
                    "frame_index",
                    "segment_timestamp_sec",
                ],
            )
            writer.writeheader()
            writer.writerows(self.live_best_rows)
        return True

    def _score_video_segment(self, segment_path: Path, segment_start: float, segment_end: float) -> dict[str, float | str] | None:
        if self.scorer is None:
            return None
        segment_key = str(segment_path.resolve())
        if segment_key in self.processed_segment_files:
            return None
        self.processed_segment_files.add(segment_key)

        capture = cv2.VideoCapture(str(segment_path))
        if not capture.isOpened():
            raise RuntimeError(f"Could not open OBS segment: {segment_path}")
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps <= 0:
            fps = 60.0
        max_segment_duration = max(0.0, segment_end - segment_start) + 0.1
        frame_index = 0
        best_row: dict[str, float | str] | None = None
        source_name = segment_path.name
        batch_images: list[Image.Image] = []
        batch_rows: list[dict[str, float | str]] = []
        batch_size = int(self.config.raw["laion"].get("batch_size", 32))

        def flush_batch() -> None:
            nonlocal best_row
            if not batch_images:
                return
            scores = self.scorer.score_pil_images(batch_images, batch_size=batch_size)
            for row, score in zip(batch_rows, scores, strict=True):
                row["score"] = round(score, 6)
                self.score_rows.append(row)
                if best_row is None or float(row["score"]) > float(best_row["score"]):
                    best_row = row
            self._set_labels(
                score=f"Scoring video frames: {len(self.score_rows)} total | current segment frame {batch_rows[-1]['frame_index']}",
                score_source=f"Segment source: {segment_path.name}",
            )
            batch_images.clear()
            batch_rows.clear()

        try:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                segment_ts = frame_index / fps
                if segment_ts > max_segment_duration:
                    break
                absolute_ts = segment_start + segment_ts
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                image = Image.fromarray(rgb)
                pose = self._nearest_pose(absolute_ts)
                batch_images.append(image)
                batch_rows.append(
                    {
                        "session_id": self.session_id,
                        "segment_file": segment_path.name,
                        "frame_index": frame_index,
                        "segment_timestamp_sec": round(segment_ts, 3),
                        "timestamp_sec": round(absolute_ts, 3),
                        "score": "",
                        "source_name": source_name,
                        "pose_timestamp_sec": pose.get("timestamp_sec", ""),
                        "x": pose.get("x", ""),
                        "y": pose.get("y", ""),
                        "z": pose.get("z", ""),
                        "yaw": pose.get("yaw", ""),
                        "pitch": pose.get("pitch", ""),
                        "fov": pose.get("fov", ""),
                    }
                )
                frame_index += 1
                if len(batch_images) >= batch_size:
                    flush_batch()
            flush_batch()
        finally:
            capture.release()

        if best_row is not None:
            summary = {
                **self._summary_from_best(best_row, segment_start, segment_end),
                "segment_file": segment_path.name,
            }
            self.live_best_rows.append(summary)
            self.segment_best_rows.append(summary)
            self._write_live_scores()
            self._write_live_best()
            self._write_segment_best()
        return best_row

    def _summary_from_best(
        self, best_row: dict[str, float | str], segment_start: float, segment_end: float
    ) -> dict[str, float | str]:
        return {
            "session_id": self.session_id,
            "window_start_sec": round(segment_start, 3),
            "window_end_sec": round(segment_end, 3),
            "best_timestamp_sec": best_row["timestamp_sec"],
            "score": best_row["score"],
            "x": best_row.get("x", ""),
            "y": best_row.get("y", ""),
            "z": best_row.get("z", ""),
            "yaw": best_row.get("yaw", ""),
            "pitch": best_row.get("pitch", ""),
            "fov": best_row.get("fov", ""),
            "source_name": best_row.get("source_name", ""),
            "pose_timestamp_sec": best_row.get("pose_timestamp_sec", ""),
            "segment_file": best_row.get("segment_file", ""),
            "frame_index": best_row.get("frame_index", ""),
            "segment_timestamp_sec": best_row.get("segment_timestamp_sec", ""),
        }

    def _write_segment_best(self) -> bool:
        if not self.segment_best_csv or not self.segment_best_rows:
            return False
        self.segment_best_csv.parent.mkdir(parents=True, exist_ok=True)
        with self.segment_best_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "session_id",
                    "window_start_sec",
                    "window_end_sec",
                    "segment_file",
                    "frame_index",
                    "segment_timestamp_sec",
                    "best_timestamp_sec",
                    "score",
                    "x",
                    "y",
                    "z",
                    "yaw",
                    "pitch",
                    "fov",
                    "source_name",
                    "pose_timestamp_sec",
                ],
            )
            writer.writeheader()
            writer.writerows(self.segment_best_rows)
        return True

    def _update_rolling_window_best(self, elapsed: float) -> None:
        window_end = elapsed
        window_start = max(0.0, elapsed - self.live_summary_window_sec)
        candidates = [
            row
            for row in self.score_rows
            if window_start <= float(row["timestamp_sec"]) <= window_end
        ]
        if not candidates:
            return
        best = max(candidates, key=lambda item: float(item["score"]))
        summary = {
            "session_id": self.session_id,
            "window_start_sec": round(window_start, 3),
            "window_end_sec": round(window_end, 3),
            "best_timestamp_sec": best["timestamp_sec"],
            "score": best["score"],
            "x": best.get("x", ""),
            "y": best.get("y", ""),
            "z": best.get("z", ""),
            "yaw": best.get("yaw", ""),
            "pitch": best.get("pitch", ""),
            "fov": best.get("fov", ""),
            "source_name": best.get("source_name", ""),
            "pose_timestamp_sec": best.get("pose_timestamp_sec", ""),
        }
        self._set_labels(window_best=_format_window_best(summary))
        key = f"{summary['window_end_sec']}:{summary['best_timestamp_sec']}:{summary['score']}"
        if key != self.last_saved_rolling_best_key:
            self.live_best_rows.append(summary)
            self.last_saved_rolling_best_key = key
            self._write_live_best()

    def _nearest_pose(self, elapsed: float) -> dict[str, float | str]:
        if self.pose_log is None or not self.pose_log.exists():
            return {}
        try:
            best_row: dict[str, float | str] = {}
            best_diff: float | None = None
            with self.pose_log.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    timestamp = _to_float(row.get("timestamp_sec"))
                    if timestamp is None:
                        continue
                    diff = abs(timestamp - elapsed)
                    if best_diff is None or diff < best_diff:
                        best_diff = diff
                        best_row = {key: _coerce_number(value) for key, value in row.items()}
            return best_row
        except OSError:
            return {}

    def _get_record_dir(self) -> Path | None:
        obs_cfg = self.config.raw["obs"]
        configured_dir = obs_cfg.get("recording_output_dir") or ""
        if configured_dir:
            return self.config.obs_recording_output_dir
        if self.controller is None:
            return None
        return self.controller.get_record_directory()

    def _set_record_directory(self) -> None:
        if self.controller is None:
            return
        target = ensure_dir(self.config.obs_recording_output_dir)
        try:
            self.controller.set_record_directory(target)
            self.record_dir = target
            self._set_labels(video=f"Video folder: {target}")
        except Exception as exc:
            self._append_score_error(exc)
            self.record_dir = target
            self._set_labels(
                video=f"Video folder: {target}",
                status=f"OBS did not accept SetRecordDirectory; using configured folder for lookup: {target}",
            )

    def _find_next_segment_file(self, since_time: float) -> Path | None:
        if self.record_dir is None:
            self.record_dir = self._get_record_dir()
        if self.record_dir is None or not self.record_dir.exists():
            return None
        candidates = [
            path
            for path in self.record_dir.iterdir()
            if path.is_file()
            and path.suffix.lower() in self.config.supported_video_extensions
            and path.stat().st_size > 4096
            and str(path.resolve()) not in self.processed_segment_files
            and path.stat().st_mtime >= since_time
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.stat().st_mtime)
        for candidate in candidates:
            if _is_stable_file(candidate):
                return candidate
        return None

    def _append_score_error(self, exc: Exception) -> None:
        with self.score_error_log.open("a", encoding="utf-8") as handle:
            handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {exc}\n")
            handle.write(traceback.format_exc())
            handle.write("\n")


def _response_value(response: object, *names: str) -> object:
    for name in names:
        if hasattr(response, name):
            return getattr(response, name)
    data = getattr(response, "responseData", None) or getattr(response, "datain", None)
    if isinstance(data, dict):
        for name in names:
            if name in data:
                return data[name]
    return None


def _compact_path(path: Path | str | None) -> str:
    if path is None:
        return "-"
    value = Path(path)
    parent = value.parent.name
    return f"{parent}\\{value.name}" if parent else value.name


def _to_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_number(value: object) -> float | str:
    converted = _to_float(value)
    return converted if converted is not None else str(value or "")


def _fmt_value(value: object) -> str:
    number = _to_float(value)
    return f"{number:.3f}" if number is not None else "?"


def _format_window_best(summary: dict[str, float | str]) -> str:
    return (
        f"rolling 10s best [{_fmt_value(summary['window_start_sec'])}-{_fmt_value(summary['window_end_sec'])}s]: "
        f"score {_fmt_value(summary['score'])} | "
        f"t={_fmt_value(summary['best_timestamp_sec'])} | "
        f"x={_fmt_value(summary.get('x'))} y={_fmt_value(summary.get('y'))} "
        f"z={_fmt_value(summary.get('z'))} yaw={_fmt_value(summary.get('yaw'))}"
    )


def _format_segment_best(summary: dict[str, float | str]) -> str:
    return (
        f"5s video best [{_fmt_value(summary.get('timestamp_sec'))}s]: "
        f"score {_fmt_value(summary.get('score'))} | "
        f"x={_fmt_value(summary.get('x'))} y={_fmt_value(summary.get('y'))} "
        f"z={_fmt_value(summary.get('z'))} yaw={_fmt_value(summary.get('yaw'))}"
    )


def _is_stable_file(path: Path) -> bool:
    try:
        first = path.stat().st_size
        time.sleep(0.35)
        second = path.stat().st_size
        return first > 4096 and first == second
    except OSError:
        return False


def run_one_click_recorder(
    config: AppConfig,
    obs_password: str = "",
    live_score: bool = True,
    live_score_interval: float = 2.0,
    live_summary_window_sec: float = 10.0,
    segment_window_sec: float = 5.0,
    device: str = "auto",
    topmost: bool = True,
) -> None:
    OneClickRecordApp(
        config,
        obs_password=obs_password,
        live_score=live_score,
        live_score_interval=live_score_interval,
        live_summary_window_sec=live_summary_window_sec,
        segment_window_sec=segment_window_sec,
        device=device,
        topmost=topmost,
    ).run()
