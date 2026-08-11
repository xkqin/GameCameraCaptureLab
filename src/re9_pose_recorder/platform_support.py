from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal


PlatformKey = Literal["windows", "linux", "other"]


def platform_key(value: str | None = None) -> PlatformKey:
    """Return the platform family used by launch and process adapters."""
    selected = (value or sys.platform).lower()
    if selected == "nt" or selected.startswith(("win", "cygwin", "msys")):
        return "windows"
    if selected == "posix" or selected.startswith("linux"):
        return "linux"
    return "other"


def platform_config_names(value: str | None = None) -> tuple[str, ...]:
    """Return config precedence without hiding the legacy default template."""
    selected = platform_key(value)
    if selected == "windows":
        return ("windows.local.yaml", "windows.yaml", "default.yaml")
    if selected == "linux":
        return ("linux.local.yaml", "linux.yaml", "default.yaml")
    return ("default.yaml",)


def obs_process_names(value: str | None = None) -> tuple[str, ...]:
    if platform_key(value) == "windows":
        return ("obs64.exe", "obs32.exe")
    return ("obs",)


def obs_sentinel_dir(
    value: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    selected = platform_key(value)
    variables = os.environ if environ is None else environ
    user_home = Path.home() if home is None else home
    if selected == "windows":
        config_root = Path(
            variables.get("APPDATA") or user_home / "AppData" / "Roaming"
        )
    else:
        config_root = Path(variables.get("XDG_CONFIG_HOME") or user_home / ".config")
    return config_root / "obs-studio" / ".sentinel"


def default_obs_restart_command(
    value: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> str:
    """Find a conventional OBS executable and build a portable restart command."""
    selected = platform_key(value)
    variables = os.environ if environ is None else environ
    executable: Path | None = None
    discovered: str | None = None
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
                and (candidate == Path(discovered) if discovered else candidate.exists())
            ),
            None,
        )
    elif selected == "linux":
        discovered = which("obs")
        candidate = Path(discovered) if discovered else Path("/usr/bin/obs")
        if discovered or candidate.exists():
            executable = candidate

    if executable is None:
        return ""
    # Preserve the exact path returned by shutil.which. On Windows, Path()
    # normalizes forward slashes to backslashes and needlessly changes a valid
    # caller-provided command line (and makes diagnostics harder to compare).
    executable_text = discovered if discovered else str(executable)
    arguments = [
        executable_text,
        "--collection",
        "RE9_Still_Scan",
        "--profile",
        "Untitled",
        "--disable-missing-files-check",
    ]
    if selected == "windows":
        return subprocess.list2cmdline(arguments)
    return shlex.join(arguments)


def command_for_popen(
    command: str,
    value: str | None = None,
) -> str | list[str]:
    """Keep the native Windows command line; split POSIX commands safely."""
    if platform_key(value) == "windows":
        return command
    return shlex.split(command)


def detached_process_kwargs(
    value: str | None = None,
    *,
    hide_console: bool = False,
) -> dict[str, object]:
    """Return Popen options that let a child survive its launcher."""
    if platform_key(value) != "windows":
        return {"start_new_session": True}
    flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
    if hide_console:
        flags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return {"creationflags": flags}
