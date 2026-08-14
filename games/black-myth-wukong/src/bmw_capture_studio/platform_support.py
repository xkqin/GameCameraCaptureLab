from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


def is_windows() -> bool:
    return sys.platform == "win32"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def native_uuu_supported() -> bool:
    """UUU 5.8.21 injection/bridge support is currently Windows-only."""

    return is_windows()


def platform_name() -> str:
    if is_windows():
        return "Windows"
    if is_linux():
        return "Linux"
    return sys.platform


def open_path(path: str | Path) -> None:
    """Open a file or directory with the native desktop opener."""

    target = str(Path(path).resolve())
    if is_windows():
        os.startfile(target)  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        command = "open"
    else:
        command = shutil.which("xdg-open")
        if not command:
            raise RuntimeError("Linux 未找到 xdg-open，无法打开文件管理器")
    subprocess.Popen([command, target])


def detached_process_kwargs(*, hide_console: bool = False) -> dict[str, object]:
    """Return portable Popen options for an alert/repair worker."""

    if not is_windows():
        return {"start_new_session": True}
    flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
    if hide_console:
        flags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return {"creationflags": flags}
