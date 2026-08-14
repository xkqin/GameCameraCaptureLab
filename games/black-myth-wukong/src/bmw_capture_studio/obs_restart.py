from __future__ import annotations

"""Portable OBS process restart support used by long trajectory captures.

The RE9 recorder treats OBS as a restartable capture worker: stop the current
output, close the websocket, restart the process, wait for a healthy websocket,
and only then create the next recording segment.  This module keeps that
policy independent from the Tk UI so it can be exercised without a game or an
OBS process.
"""

import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import time
from typing import Callable, Mapping


def platform_key(value: str | None = None) -> str:
    if value:
        normalized = value.strip().lower()
        if normalized in {"windows", "win32", "nt"}:
            return "windows"
        if normalized in {"linux", "posix"}:
            return "linux"
    return "windows" if sys.platform == "win32" else "linux"


def obs_process_names(value: str | None = None) -> tuple[str, ...]:
    return ("obs64.exe", "obs32.exe") if platform_key(value) == "windows" else ("obs",)


def obs_sentinel_dir(
    value: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    variables = os.environ if environ is None else environ
    user_home = Path.home() if home is None else home
    if platform_key(value) == "windows":
        config_root = Path(variables.get("APPDATA") or user_home / "AppData" / "Roaming")
    else:
        config_root = Path(variables.get("XDG_CONFIG_HOME") or user_home / ".config")
    return config_root / "obs-studio" / ".sentinel"


def default_obs_restart_command(
    value: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    """Return a command that starts OBS with its last-used profile.

    Unlike the RE9-specific launcher, this adapter deliberately does not force
    a collection/profile name.  That preserves the user's existing OBS scene
    and WebSocket configuration.  An explicit command in ``settings.json``
    still takes precedence.
    """

    selected = platform_key(value)
    variables = os.environ if environ is None else environ
    discovered: str | None = None
    executable: Path | None = None
    if selected == "windows":
        discovered = which("obs64.exe") or which("obs32.exe")
        candidates = [
            Path(discovered) if discovered else None,
            *(
                Path(variables[name])
                / "obs-studio"
                / "bin"
                / "64bit"
                / "obs64.exe"
                for name in ("ProgramW6432", "ProgramFiles")
                if variables.get(name)
            ),
        ]
        executable = next(
            (
                candidate
                for candidate in candidates
                if candidate is not None
                and (candidate == Path(discovered) if discovered else candidate.is_file())
            ),
            None,
        )
    else:
        discovered = which("obs")
        candidate = Path(discovered) if discovered else Path("/usr/bin/obs")
        if discovered or candidate.is_file():
            executable = candidate

    if executable is None:
        return ""
    text = discovered if discovered else str(executable)
    if selected == "windows":
        return subprocess.list2cmdline([text])
    return shlex.join([text])


def command_for_popen(command: str, value: str | None = None) -> str | list[str]:
    if platform_key(value) == "windows":
        return command
    return shlex.split(command)


def detached_process_kwargs(
    value: str | None = None,
    *,
    hide_console: bool = False,
) -> dict[str, object]:
    if platform_key(value) != "windows":
        return {"start_new_session": True}
    flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
    if hide_console:
        flags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return {"creationflags": flags}


def host_is_local(host: str) -> bool:
    normalized = host.strip().lower()
    if normalized in {"", "localhost", "127.0.0.1", "::1"}:
        return True
    try:
        local_names = {
            socket.gethostname().lower(),
            socket.getfqdn().lower(),
        }
        if normalized in local_names:
            return True
        addresses = {
            info[4][0]
            for info in socket.getaddrinfo(socket.gethostname(), None)
            if info[4]
        }
        return normalized in {address.lower() for address in addresses}
    except OSError:
        return False


class OBSProcessRestarter:
    """Restart a local OBS process and return a newly connected OBS object."""

    def __init__(
        self,
        *,
        obs_factory: Callable[[], object],
        host: str,
        command: str = "",
        wait_seconds: float = 20.0,
        terminate_timeout_seconds: float = 10.0,
        platform: str | None = None,
        environ: Mapping[str, str] | None = None,
        home: Path | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.obs_factory = obs_factory
        self.host = host.strip()
        self.platform = platform_key(platform)
        self.environ = dict(os.environ if environ is None else environ)
        self.home = home
        self.command = command.strip() or default_obs_restart_command(
            self.platform,
            environ=self.environ,
        )
        self.explicit_command = bool(command.strip())
        self.local_process_scope = host_is_local(self.host)
        self.wait_seconds = max(0.5, float(wait_seconds))
        self.terminate_timeout_seconds = max(0.5, float(terminate_timeout_seconds))
        self.sleeper = sleeper
        self.monotonic = monotonic

    def validate(self) -> str:
        if not self.command:
            raise RuntimeError(
                "未找到 OBS 可执行文件；请在 games/black-myth-wukong/settings.json "
                "配置 obs_restart_command。"
            )
        if not self.explicit_command and not host_is_local(self.host):
            raise RuntimeError(
                f"OBS 主机 {self.host or '<empty>'} 不是本机，已阻止误杀本机 OBS；"
                "请配置能在目标主机执行的 obs_restart_command。"
            )
        return self.command

    def restart(self, *, log_path: str | Path | None = None) -> object:
        self.validate()
        if self.local_process_scope:
            self._terminate_obs_processes()
            self._clear_obs_sentinel_files()
        self._launch(log_path)
        return self._wait_for_obs()

    def _launch(self, log_path: str | Path | None) -> None:
        target = Path(log_path).resolve() if log_path else None
        handle = None
        try:
            if target is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
                handle = target.open("ab")
                stdout: object = handle
            else:
                stdout = subprocess.DEVNULL
            subprocess.Popen(
                command_for_popen(self.command, self.platform),
                stdout=stdout,
                stderr=subprocess.STDOUT,
                env=self.environ.copy(),
                **detached_process_kwargs(self.platform, hide_console=True),
            )
        except OSError as exc:
            raise RuntimeError(f"启动 OBS 失败：{exc}") from exc
        finally:
            if handle is not None:
                handle.close()

    def _wait_for_obs(self) -> object:
        deadline = self.monotonic() + self.wait_seconds
        last_error: Exception | None = None
        while self.monotonic() < deadline:
            candidate: object | None = None
            try:
                candidate = self.obs_factory()
                tester = getattr(candidate, "test", None)
                if callable(tester):
                    tester()
                return candidate
            except Exception as exc:
                last_error = exc
                closer = getattr(candidate, "close", None)
                if callable(closer):
                    try:
                        closer()
                    except Exception:
                        pass
                self.sleeper(0.5)
        raise RuntimeError(f"OBS 重启后 WebSocket 未在 {self.wait_seconds:.1f}s 内恢复：{last_error}")

    def _terminate_obs_processes(self) -> None:
        names = obs_process_names(self.platform)
        if self.platform == "windows":
            for name in names:
                try:
                    subprocess.run(
                        ["taskkill", "/IM", name, "/T"],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                except OSError:
                    return
            deadline = self.monotonic() + self.terminate_timeout_seconds
            while self.monotonic() < deadline:
                if not self._windows_obs_running(names):
                    return
                self.sleeper(0.25)
            for name in names:
                subprocess.run(
                    ["taskkill", "/F", "/IM", name, "/T"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            self.sleeper(1.0)
            return

        try:
            subprocess.run(
                ["pkill", "-x", "obs"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return
        deadline = self.monotonic() + self.terminate_timeout_seconds
        while self.monotonic() < deadline:
            try:
                running = subprocess.run(
                    ["pgrep", "-x", "obs"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode == 0
            except OSError:
                return
            if not running:
                return
            self.sleeper(0.25)
        subprocess.run(
            ["pkill", "-9", "-x", "obs"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.sleeper(1.0)

    @staticmethod
    def _windows_obs_running(names: tuple[str, ...]) -> bool:
        try:
            result = subprocess.run(
                ["tasklist", "/NH"],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return False
        output = result.stdout.lower()
        return any(name.lower() in output for name in names)

    def _clear_obs_sentinel_files(self) -> None:
        sentinel_dir = obs_sentinel_dir(
            self.platform,
            environ=self.environ,
            home=self.home,
        )
        if not sentinel_dir.is_dir():
            return
        backup_dir = sentinel_dir.with_name(".sentinel.backup-bmw")
        backup_dir.mkdir(parents=True, exist_ok=True)
        suffix = time.strftime("%Y%m%d_%H%M%S")
        for path in sentinel_dir.glob("run_*"):
            try:
                path.rename(backup_dir / f"{path.name}.{suffix}")
            except OSError:
                pass
