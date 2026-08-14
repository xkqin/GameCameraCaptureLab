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
