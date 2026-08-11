from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from .registry import GameAdapter, RegistryError, current_platform, load_registry
from .validate import validate_repository


MATURITY_LABELS = {
    "stable": "稳定",
    "beta": "测试中",
    "experimental": "实验性",
}


class CaptureHub:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Game Camera Capture Lab · 多游戏采集中心")
        self.root.geometry("1040x690")
        self.root.minsize(900, 600)
        self.adapters = load_registry()
        self.selected: GameAdapter | None = None
        self.action_buttons: list[ttk.Button] = []
        self.status = tk.StringVar(value="选择一个游戏适配器")
        self._configure_style()
        self._build()
        self.game_list.selection_set(0)
        self._on_select()

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 21, "bold"))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 10), foreground="#596579")
        style.configure("Game.TLabel", font=("Segoe UI", 17, "bold"))
        style.configure("Status.TLabel", padding=(10, 7), foreground="#334155")

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=22)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Game Camera Capture Lab", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="统一管理不同游戏的相机位姿、点位、静态扫描与轨迹采集",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 18))

        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body, padding=(0, 0, 16, 0))
        right = ttk.Frame(body, padding=(16, 0, 0, 0))
        body.add(left, weight=1)
        body.add(right, weight=3)

        ttk.Label(left, text="游戏适配器").pack(anchor="w", pady=(0, 7))
        self.game_list = tk.Listbox(
            left,
            activestyle="none",
            exportselection=False,
            font=("Segoe UI", 11),
            relief="solid",
            borderwidth=1,
        )
        self.game_list.pack(fill="both", expand=True)
        self.game_list.bind("<<ListboxSelect>>", lambda _event: self._on_select())
        for adapter in self.adapters:
            maturity = MATURITY_LABELS.get(adapter.maturity, adapter.maturity)
            self.game_list.insert(
                "end",
                f"  {adapter.short_name}  ·  {maturity}  ·  {adapter.engine}",
            )

        header = ttk.Frame(right)
        header.pack(fill="x")
        self.name_label = ttk.Label(header, text="", style="Game.TLabel")
        self.name_label.pack(side="left", anchor="w")
        self.maturity_label = ttk.Label(header, text="")
        self.maturity_label.pack(side="right", anchor="e")

        self.summary_label = ttk.Label(right, text="", wraplength=680, justify="left")
        self.summary_label.pack(fill="x", anchor="w", pady=(8, 15))

        ttk.Label(right, text="能力状态").pack(anchor="w", pady=(0, 6))
        self.capability_table = ttk.Treeview(
            right,
            columns=("capability", "status"),
            show="headings",
            height=9,
        )
        self.capability_table.heading("capability", text="能力")
        self.capability_table.heading("status", text="状态")
        self.capability_table.column("capability", width=240, anchor="w")
        self.capability_table.column("status", width=330, anchor="w")
        self.capability_table.pack(fill="both", expand=True)

        self.actions_frame = ttk.LabelFrame(right, text="操作", padding=12)
        self.actions_frame.pack(fill="x", pady=(15, 0))

        utility = ttk.Frame(right)
        utility.pack(fill="x", pady=(10, 0))
        ttk.Button(utility, text="打开说明", command=self._open_documentation).pack(side="left")
        ttk.Button(utility, text="打开示例文件", command=self._open_examples).pack(side="left", padx=8)
        ttk.Button(utility, text="检查仓库", command=self._validate).pack(side="left")

        ttk.Separator(outer).pack(fill="x", pady=(15, 0))
        ttk.Label(outer, textvariable=self.status, style="Status.TLabel").pack(fill="x")

    def _on_select(self) -> None:
        selection = self.game_list.curselection()
        if not selection:
            return
        self.selected = self.adapters[selection[0]]
        adapter = self.selected
        self.name_label.configure(text=adapter.name)
        self.maturity_label.configure(
            text=f"{MATURITY_LABELS.get(adapter.maturity, adapter.maturity)} · {adapter.engine}"
        )
        self.summary_label.configure(text=adapter.summary)
        for row in self.capability_table.get_children():
            self.capability_table.delete(row)
        for name, value in adapter.capabilities.items():
            self.capability_table.insert("", "end", values=(name, value))
        for button in self.action_buttons:
            button.destroy()
        self.action_buttons.clear()
        for action in adapter.actions:
            supported = action.is_supported()
            button = ttk.Button(
                self.actions_frame,
                text=action.label,
                state="normal" if supported else "disabled",
                command=lambda action_id=action.id: self._launch(action_id),
            )
            button.pack(side="left", padx=(0, 9))
            self.action_buttons.append(button)
        self.status.set(f"已选择 {adapter.short_name} · 当前平台 {current_platform()}")

    def _launch(self, action_id: str) -> None:
        if self.selected is None:
            return
        try:
            action = self.selected.action(action_id)
            if not action.is_supported():
                raise RegistryError(f"当前平台不支持 {action.label}")
            command = self.selected.command_for(action_id)
            subprocess.Popen(command, cwd=action.working_directory)
            self.status.set(f"已启动：{self.selected.short_name} / {action.label}")
        except (OSError, RegistryError) as exc:
            messagebox.showerror("启动失败", str(exc), parent=self.root)
            self.status.set(f"启动失败：{exc}")

    def _open_documentation(self) -> None:
        if self.selected is not None:
            self._open_path(self.selected.documentation)

    def _open_examples(self) -> None:
        if self.selected is not None:
            self._open_path(self.selected.examples)

    def _open_path(self, path: Path) -> None:
        try:
            if os.name == "nt":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
            self.status.set(f"已打开：{path}")
        except OSError as exc:
            messagebox.showerror("打开失败", str(exc), parent=self.root)

    def _validate(self) -> None:
        errors = validate_repository()
        if errors:
            messagebox.showerror(
                "仓库检查失败",
                "\n".join(f"• {item}" for item in errors),
                parent=self.root,
            )
            self.status.set(f"仓库检查失败：{len(errors)} 个问题")
            return
        messagebox.showinfo(
            "仓库检查完成",
            f"{len(self.adapters)} 个游戏适配器和 3 个统一格式均有效。",
            parent=self.root,
        )
        self.status.set("仓库检查通过")

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    try:
        CaptureHub().run()
    except RegistryError as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Game Camera Capture Lab", str(exc), parent=root)
        root.destroy()
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
