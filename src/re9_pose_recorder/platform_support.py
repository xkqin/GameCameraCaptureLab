from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from ctypes import (
    CDLL,
    POINTER,
    Structure,
    Union,
    byref,
    c_char,
    c_char_p,
    c_int,
    c_long,
    c_short,
    c_ulong,
    c_void_p,
)
from ctypes.util import find_library
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal


PlatformKey = Literal["windows", "linux", "other"]


_RE9_WINDOW_TITLE = re.compile(
    r"resident\s+evil|biohazard|requiem|steam_app_3764200",
    re.IGNORECASE,
)
_CLIENT_MESSAGE = 33
_SUBSTRUCTURE_NOTIFY_MASK = 1 << 19
_SUBSTRUCTURE_REDIRECT_MASK = 1 << 20


class _XClientMessageData(Union):
    _fields_ = [
        ("b", c_char * 20),
        ("s", c_short * 10),
        ("l", c_long * 5),
    ]


class _XClientMessageEvent(Structure):
    _fields_ = [
        ("type", c_int),
        ("serial", c_ulong),
        ("send_event", c_int),
        ("display", c_void_p),
        ("window", c_ulong),
        ("message_type", c_ulong),
        ("format", c_int),
        ("data", _XClientMessageData),
    ]


class _XEvent(Union):
    _fields_ = [
        ("xclient", _XClientMessageEvent),
        ("pad", c_long * 24),
    ]


def _re9_window_id_from_xwininfo(tree: str) -> int | None:
    """Return the largest matching RE9 window, ignoring helper windows."""
    best_window_id: int | None = None
    best_area = -1
    for line in tree.splitlines():
        if not _RE9_WINDOW_TITLE.search(line):
            continue
        match = re.match(r"\s*(0x[0-9a-f]+)\s+", line, re.IGNORECASE)
        if match is None:
            continue
        geometry = re.search(r"\s(\d+)x(\d+)[+-]", line)
        area = int(geometry.group(1)) * int(geometry.group(2)) if geometry else 0
        if area > best_area:
            best_window_id = int(match.group(1), 16)
            best_area = area
    return best_window_id


def _request_x11_window_activation(x11: object, display: int, window_id: int) -> bool:
    """Ask an EWMH window manager to activate *window_id*."""
    root = x11.XDefaultRootWindow(display)
    active_atom = x11.XInternAtom(display, b"_NET_ACTIVE_WINDOW", 0)
    if not root or not active_atom:
        return False

    event = _XEvent()
    event.xclient.type = _CLIENT_MESSAGE
    event.xclient.serial = 0
    event.xclient.send_event = 1
    event.xclient.display = display
    event.xclient.window = window_id
    event.xclient.message_type = active_atom
    event.xclient.format = 32
    # EWMH source indication 2 means a pager/automation request.
    event.xclient.data.l[0] = 2
    event.xclient.data.l[1] = 0
    event.xclient.data.l[2] = 0
    mask = _SUBSTRUCTURE_REDIRECT_MASK | _SUBSTRUCTURE_NOTIFY_MASK
    return bool(x11.XSendEvent(display, root, 0, mask, byref(event)))


def activate_re9_window(value: str | None = None) -> bool:
    """Best-effort X11 focus recovery when RE9 stops rendering in background.

    On Linux, OBS can take focus while it restarts and RE9 may then stop
    presenting frames.  This helper is deliberately optional and silent so
    Windows, Wayland, and minimal Linux installs retain their existing
    behavior.
    """
    if platform_key(value) != "linux" or not os.environ.get("DISPLAY"):
        return False
    try:
        result = subprocess.run(
            ["xwininfo", "-root", "-tree"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    window_id = _re9_window_id_from_xwininfo(result.stdout)
    if result.returncode != 0 or window_id is None:
        return False

    library = find_library("X11")
    if not library:
        return False
    try:
        x11 = CDLL(library)
        x11.XOpenDisplay.argtypes = [c_char_p]
        x11.XOpenDisplay.restype = c_void_p
        x11.XDefaultRootWindow.argtypes = [c_void_p]
        x11.XDefaultRootWindow.restype = c_ulong
        x11.XInternAtom.argtypes = [c_void_p, c_char_p, c_int]
        x11.XInternAtom.restype = c_ulong
        x11.XSendEvent.argtypes = [c_void_p, c_ulong, c_int, c_long, POINTER(_XEvent)]
        x11.XSendEvent.restype = c_int
        x11.XMapRaised.argtypes = [c_void_p, c_ulong]
        x11.XMapRaised.restype = c_int
        x11.XSync.argtypes = [c_void_p, c_int]
        x11.XSync.restype = c_int
        x11.XCloseDisplay.argtypes = [c_void_p]
        x11.XCloseDisplay.restype = c_int
        display = x11.XOpenDisplay(None)
    except (AttributeError, OSError):
        return False
    if not display:
        return False
    try:
        activated = _request_x11_window_activation(x11, display, window_id)
        x11.XMapRaised(display, window_id)
        # Wine advertises the game window with WM_HINTS.input=False and uses
        # WM_TAKE_FOCUS.  Calling XSetInputFocus directly on that window makes
        # Mutter clear _NET_ACTIVE_WINDOW (0x0), undoing the successful EWMH
        # request above.  Let the window manager negotiate focus instead.
        x11.XSync(display, 0)
    finally:
        x11.XCloseDisplay(display)
    return activated


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
