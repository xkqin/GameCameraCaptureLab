from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import AppConfig
from .paths import ensure_dir
from .utils import timestamp_id


_CONTROL_WRITE_TIMEOUT_SEC = 3.0
_NTFS_FILESYSTEM_TYPES = frozenset({"ntfs", "ntfs3", "fuseblk"})


def make_session_id() -> str:
    return timestamp_id()


def _unescape_mountinfo_path(value: str) -> str:
    """Decode the octal escapes used by /proc/self/mountinfo."""
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


@lru_cache(maxsize=64)
def _filesystem_type_for_path(path: str) -> str:
    """Return the Linux mount filesystem type containing *path*, if known."""
    if os.name != "posix":
        return ""
    # Keep detection lexical: the purpose of this check is to choose a bounded
    # helper before making any call into a potentially stalled NTFS mount.
    candidate_text = os.path.dirname(os.path.abspath(os.path.expanduser(path)))
    best_mount = ""
    best_type = ""
    try:
        with Path("/proc/self/mountinfo").open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                fields = raw_line.rstrip("\n").split()
                try:
                    separator = fields.index("-")
                except ValueError:
                    continue
                if separator + 1 >= len(fields) or len(fields) < 5:
                    continue
                mount_point = _unescape_mountinfo_path(fields[4]).rstrip("/") or "/"
                if candidate_text != mount_point and not candidate_text.startswith(mount_point.rstrip("/") + "/"):
                    continue
                if len(mount_point) >= len(best_mount):
                    best_mount = mount_point
                    best_type = fields[separator + 1].lower()
    except OSError:
        return ""
    return best_type


def _reap_process_later(process: subprocess.Popen[bytes]) -> None:
    """Avoid blocking the UI if a killed helper remains stuck in kernel I/O."""
    threading.Thread(target=process.wait, name="re9-control-writer-reaper", daemon=True).start()


def _write_control_in_bounded_helper(control_file: Path, content: bytes, timeout_sec: float) -> None:
    """Write through a killable helper so an NTFS kernel stall cannot freeze the UI."""
    helper = Path(__file__).with_name("_control_writer.py")
    process = subprocess.Popen(
        [sys.executable, str(helper), str(control_file)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        _, stderr = process.communicate(input=content, timeout=max(0.1, float(timeout_sec)))
    except subprocess.TimeoutExpired as exc:
        process.kill()
        try:
            process.communicate(timeout=0.2)
        except subprocess.TimeoutExpired:
            _reap_process_later(process)
        raise TimeoutError(
            f"Timed out writing REFramework control file after {timeout_sec:.1f}s: {control_file}"
        ) from exc
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise OSError(
            f"REFramework control writer exited with code {process.returncode}: "
            f"{detail or control_file}"
        )


class LuaControl:
    """File-based control channel for the REFramework Lua logger."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.control_file = config.control_file
        self.status_file = config.status_file
        self.last_written_command_id = ""

    def write_start_control(self, session_id: str, pose_log_file: str | Path, interval_sec: float) -> Path:
        payload = {
            "command": "start",
            "command_id": f"start:{session_id}:{time.time():.6f}",
            "session_id": session_id,
            "pose_log_file": str(Path(pose_log_file).as_posix()),
            "interval_sec": float(interval_sec),
        }
        return self._write_control(payload)

    def write_stop_control(self, session_id: str) -> Path:
        return self._write_control({"command": "stop", "command_id": f"stop:{session_id}:{time.time():.6f}", "session_id": session_id})

    def write_set_pose_control(
        self,
        session_id: str,
        x: float,
        y: float,
        z: float,
        yaw: float,
        pitch: float,
        fov: float | None = None,
        segment_id: str = "",
        x_end: float | None = None,
        y_end: float | None = None,
        z_end: float | None = None,
        yaw_end: float | None = None,
        pitch_end: float | None = None,
        fov_end: float | None = None,
        duration_sec: float | None = None,
    ) -> Path:
        payload: dict[str, Any] = {
            "command": "set_pose",
            "command_id": f"set_pose:{session_id}:{segment_id}:{time.time():.6f}",
            "session_id": session_id,
            "x": float(x),
            "y": float(y),
            "z": float(z),
            "yaw": float(yaw),
            "pitch": float(pitch),
            "segment_id": segment_id,
        }
        if x_end is not None:
            payload["x_end"] = float(x_end)
        if y_end is not None:
            payload["y_end"] = float(y_end)
        if z_end is not None:
            payload["z_end"] = float(z_end)
        if yaw_end is not None:
            payload["yaw_end"] = float(yaw_end)
        if pitch_end is not None:
            payload["pitch_end"] = float(pitch_end)
        if fov_end is not None:
            payload["fov_end"] = float(fov_end)
        if duration_sec is not None:
            payload["duration_sec"] = float(duration_sec)
        if fov is not None:
            payload["fov"] = float(fov)
        return self._write_control(payload)

    def write_clear_pose_control(self, session_id: str) -> Path:
        return self._write_control(
            {"command": "clear_pose", "command_id": f"clear_pose:{session_id}:{time.time():.6f}", "session_id": session_id}
        )

    def write_play_trajectory_control(
        self,
        session_id: str,
        keyframes: list[dict[str, float | int | None]],
        trajectory_id: str = "",
    ) -> Path:
        payload = {
            "command": "play_trajectory",
            "command_id": f"play_trajectory:{session_id}:{trajectory_id}:{time.time():.6f}",
            "session_id": session_id,
            "trajectory_id": trajectory_id,
            "keyframes": keyframes,
        }
        return self._write_control(payload)

    def write_physics_probe_control(self, session_id: str = "manual") -> Path:
        return self._write_control(
            {"command": "physics_probe", "command_id": f"physics_probe:{session_id}:{time.time():.6f}", "session_id": session_id}
        )

    def read_status(self) -> dict[str, Any] | None:
        if not self.status_file.exists():
            return None
        try:
            with self.status_file.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _status_matches_command(status: dict[str, Any], command_id: str) -> bool:
        if not command_id:
            return True
        # Older loaded Lua scripts do not publish this field.  Keep semantic
        # acknowledgement compatibility until the next script reload; once
        # present, require an exact command id.
        actual = status.get("last_command_id")
        return actual is None or str(actual) == command_id

    def wait_until_lua_logging_started(
        self,
        session_id: str,
        timeout_sec: float = 5,
        command_id: str = "",
    ) -> bool:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            status = self.read_status()
            if (
                status
                and status.get("session_id") == session_id
                and status.get("logging") is True
                and self._status_matches_command(status, command_id)
            ):
                return True
            time.sleep(0.25)
        return False

    def wait_until_lua_logging_stopped(
        self,
        session_id: str,
        timeout_sec: float = 5,
        command_id: str = "",
    ) -> bool:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            status = self.read_status()
            if (
                status
                and status.get("session_id") == session_id
                and status.get("logging") is False
                and self._status_matches_command(status, command_id)
            ):
                return True
            time.sleep(0.25)
        return False

    def wait_until_scan_pose(
        self,
        segment_id: str,
        timeout_sec: float = 3.0,
        poll_interval_sec: float = 0.05,
        stable_polls: int = 2,
        command_id: str = "",
    ) -> bool:
        """Wait until REFramework confirms that a scan pose was accepted.

        The control channel is file based and is polled from the game thread, so
        returning from ``write_set_pose_control`` does not mean that the pose has
        reached FreeCam yet.  A unique segment id lets us reject a stale status
        file from an earlier shot or an earlier scan session.
        """
        expected = str(segment_id)
        if not expected:
            raise ValueError("segment_id must not be empty.")
        deadline = time.monotonic() + max(0.0, float(timeout_sec))
        interval = max(0.01, float(poll_interval_sec))
        required_matches = max(1, int(stable_polls))
        matches = 0
        while True:
            status = self.read_status()
            if (
                status
                and status.get("scan_pose_enabled") is True
                and str(status.get("scan_segment_id") or "") == expected
                and self._status_matches_command(status, command_id)
            ):
                matches += 1
                if matches >= required_matches:
                    return True
            else:
                matches = 0

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(interval, remaining))

    def copy_pose_log_to_outputs(self, pose_log_file: str | Path, output_dir: str | Path) -> Path | None:
        source = Path(pose_log_file)
        if not source.exists():
            return None
        out_dir = ensure_dir(output_dir)
        destination = out_dir / "pose_log.csv"
        shutil.copy2(source, destination)
        return destination

    def _write_control(self, payload: dict[str, Any]) -> Path:
        payload = dict(payload)
        # Lua uses this to reject a delayed command from a helper that was
        # killed while the NTFS driver was stalled in kernel I/O.
        payload.setdefault("issued_at", time.time())
        content = json.dumps(payload, indent=2).encode("utf-8")
        filesystem_type = _filesystem_type_for_path(str(self.control_file))
        if filesystem_type in _NTFS_FILESYSTEM_TYPES:
            # Wine readers can hold the destination inode without allowing
            # delete sharing. os.replace() on ntfs3 may then sleep forever
            # inside the kernel rather than raising PermissionError. Direct
            # overwrite avoids the rename, and the helper gives the GUI a hard
            # timeout if open/write/close ever blocks as well.
            _write_control_in_bounded_helper(
                self.control_file,
                content,
                timeout_sec=_CONTROL_WRITE_TIMEOUT_SEC,
            )
            self.last_written_command_id = str(payload.get("command_id") or "")
            return self.control_file

        self.control_file.parent.mkdir(parents=True, exist_ok=True)
        # Do not reuse a fixed .tmp pathname here.  On ntfs3 a process can be
        # left blocked in truncate(2) if Wine/REFramework still has the old
        # temporary inode open.  Reusing that pathname then blocks every later
        # capture UI at startup.  A per-write pathname keeps an orphaned temp
        # file from poisoning subsequent control writes.
        tmp_path = self.control_file.with_name(
            f".{self.control_file.name}.{os.getpid()}.{time.time_ns()}.tmp"
        )
        last_error: OSError | None = None
        for attempt in range(20):
            try:
                tmp_path.write_bytes(content)
                os.replace(tmp_path, self.control_file)
                self.last_written_command_id = str(payload.get("command_id") or "")
                return self.control_file
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.05 + attempt * 0.02)
            except OSError as exc:
                last_error = exc
                time.sleep(0.05 + attempt * 0.02)
        if last_error is not None:
            raise last_error
        return self.control_file


def write_start_control(
    config: AppConfig, session_id: str, pose_log_file: str | Path, interval_sec: float
) -> Path:
    return LuaControl(config).write_start_control(session_id, pose_log_file, interval_sec)


def write_stop_control(config: AppConfig, session_id: str) -> Path:
    return LuaControl(config).write_stop_control(session_id)


def read_status(config: AppConfig) -> dict[str, Any] | None:
    return LuaControl(config).read_status()


def wait_until_lua_logging_started(config: AppConfig, session_id: str, timeout_sec: float = 5) -> bool:
    return LuaControl(config).wait_until_lua_logging_started(session_id, timeout_sec)


def wait_until_lua_logging_stopped(config: AppConfig, session_id: str, timeout_sec: float = 5) -> bool:
    return LuaControl(config).wait_until_lua_logging_stopped(session_id, timeout_sec)


def copy_pose_log_to_outputs(pose_log_file: str | Path, output_dir: str | Path) -> Path | None:
    source = Path(pose_log_file)
    if not source.exists():
        return None
    out_dir = ensure_dir(output_dir)
    destination = out_dir / "pose_log.csv"
    shutil.copy2(source, destination)
    return destination
