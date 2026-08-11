from __future__ import annotations

import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from typing import Any, Protocol

from ..models import Pose


class AppHost(Protocol):
    root: tk.Tk

    def log(self, message: str) -> None: ...

    def run_async(
        self,
        label: str,
        function: Any,
        on_success: Any | None = None,
        on_error: Any | None = None,
    ) -> None: ...


def labeled_entry(
    parent: tk.Misc,
    row: int,
    label: str,
    variable: tk.Variable,
    *,
    column: int = 0,
    width: int = 14,
    show: str | None = None,
) -> ttk.Entry:
    ttk.Label(parent, text=label).grid(
        row=row, column=column, sticky="w", padx=(0, 6), pady=4
    )
    entry = ttk.Entry(parent, textvariable=variable, width=width)
    if show:
        entry.configure(show=show)
    entry.grid(row=row, column=column + 1, sticky="ew", padx=(0, 12), pady=4)
    return entry


def pose_summary(pose: Pose) -> str:
    return (
        f"XYZ ({pose.x:.4f}, {pose.y:.4f}, {pose.z:.4f})   "
        f"Yaw/Pitch/Roll ({pose.yaw_degrees:.2f}, "
        f"{pose.pitch_degrees:.2f}, {pose.roll_degrees:.2f})   "
        f"FOV {pose.fov_degrees:.2f}°"
    )


def open_in_explorer(path: str | Path) -> None:
    target = Path(path).resolve()
    target.mkdir(parents=True, exist_ok=True)
    os.startfile(str(target))


def set_tree_rows(tree: ttk.Treeview, rows: list[tuple[Any, ...]]) -> None:
    tree.delete(*tree.get_children())
    for row in rows:
        tree.insert("", "end", values=row)


def configure_tree_columns(
    tree: ttk.Treeview,
    columns: tuple[str, ...],
    headings: tuple[str, ...],
    widths: tuple[int, ...],
) -> None:
    for name, heading, width in zip(columns, headings, widths):
        tree.heading(name, text=heading)
        tree.column(name, width=width, minwidth=50, anchor="center")
