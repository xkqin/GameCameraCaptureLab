from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from ..backend import CameraBackend
from ..planner import (
    build_22_view_plan,
    build_spatial_grid,
    estimate_path_length,
    save_scan_plan,
)
from ..storage import PointStore
from .common import (
    AppHost,
    configure_tree_columns,
    labeled_entry,
    open_in_explorer,
    set_tree_rows,
)


class PointsTab(ttk.Frame):
    COLUMNS = ("index", "label", "x", "y", "z", "yaw", "pitch", "roll", "fov")

    def __init__(
        self,
        parent: tk.Misc,
        app: AppHost,
        backend: CameraBackend,
        scene_var: tk.StringVar,
        settings: dict,
    ) -> None:
        super().__init__(parent, padding=12)
        self.app = app
        self.backend = backend
        self.scene_var = scene_var
        self.store = PointStore(scene_var.get())
        self.label_var = tk.StringVar()
        self.target_var = tk.IntVar(value=int(settings.get("target_point_count", 8)))
        grid = settings.get("grid", {})
        self.count_x_var = tk.IntVar(value=int(grid.get("x", 5)))
        self.count_y_var = tk.IntVar(value=int(grid.get("y", 5)))
        self.count_z_var = tk.IntVar(value=int(grid.get("z", 3)))
        self.fov_var = tk.DoubleVar(value=63.0)
        self.count_var = tk.StringVar(value="0 / 8")
        self.plan_var = tk.StringVar(value="尚未生成空间扫描计划")

        top = ttk.LabelFrame(self, text="手动场景边界点", padding=10)
        top.pack(fill="x")
        labeled_entry(top, 0, "Scene ID", self.scene_var, width=22)
        labeled_entry(top, 0, "点位标签", self.label_var, column=2, width=22)
        labeled_entry(top, 0, "目标点数", self.target_var, column=4, width=8)
        ttk.Button(top, text="载入场景", command=self.load_scene).grid(
            row=0, column=6, padx=4
        )
        ttk.Button(top, text="Capture Point", command=self.capture_point).grid(
            row=0, column=7, padx=4
        )
        ttk.Button(top, text="Reset（自动备份）", command=self.reset_points).grid(
            row=0, column=8, padx=4
        )
        ttk.Label(
            top,
            textvariable=self.count_var,
            font=("Segoe UI", 11, "bold"),
        ).grid(row=0, column=9, padx=(12, 0))
        for column in (1, 3):
            top.columnconfigure(column, weight=1)

        table_frame = ttk.Frame(self)
        table_frame.pack(fill="both", expand=True, pady=10)
        self.tree = ttk.Treeview(
            table_frame,
            columns=self.COLUMNS,
            show="headings",
            height=10,
        )
        configure_tree_columns(
            self.tree,
            self.COLUMNS,
            ("#", "标签", "X", "Y", "Z", "Yaw", "Pitch", "Roll", "FOV"),
            (45, 120, 105, 105, 105, 80, 80, 80, 70),
        )
        scrollbar = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        plan = ttk.LabelFrame(self, text="空间网格与每点 22 方向", padding=10)
        plan.pack(fill="x")
        labeled_entry(plan, 0, "X 数量", self.count_x_var, width=8)
        labeled_entry(plan, 0, "Y 数量", self.count_y_var, column=2, width=8)
        labeled_entry(plan, 0, "Z 数量", self.count_z_var, column=4, width=8)
        labeled_entry(plan, 0, "FOV", self.fov_var, column=6, width=8)
        ttk.Button(plan, text="生成并保存计划", command=self.generate_plan).grid(
            row=0, column=8, padx=6
        )
        ttk.Label(
            plan,
            textvariable=self.plan_var,
            wraplength=930,
            foreground="#1d4ed8",
        ).grid(row=1, column=0, columnspan=9, sticky="w", pady=(8, 0))

        self.refresh()

    def load_scene(self) -> None:
        self.store = PointStore(self.scene_var.get())
        normalized = self.store.scene_id
        self.scene_var.set(normalized)
        self.refresh()
        self.app.log(f"已载入场景点位：{normalized}")

    def capture_point(self) -> None:
        self.load_scene()
        self.app.run_async(
            "采集当前场景点",
            self.backend.pose,
            self._append_pose,
        )

    def reset_points(self) -> None:
        self.load_scene()
        if not messagebox.askyesno(
            "重置场景点",
            f"清空 {self.store.scene_id} 的点位？现有 CSV/JSON 会先备份。",
            parent=self,
        ):
            return
        backups = self.store.reset()
        self.refresh()
        saved = ", ".join(str(path) for path in backups if path) or "无旧文件"
        self.app.log(f"场景点已重置；备份：{saved}")

    def refresh(self) -> None:
        points = self.store.load()
        rows = [
            (
                point.index,
                point.label,
                f"{point.pose.x:.4f}",
                f"{point.pose.y:.4f}",
                f"{point.pose.z:.4f}",
                f"{point.pose.yaw_degrees:.2f}",
                f"{point.pose.pitch_degrees:.2f}",
                f"{point.pose.roll_degrees:.2f}",
                f"{point.pose.fov_degrees:.2f}",
            )
            for point in points
        ]
        set_tree_rows(self.tree, rows)
        self.count_var.set(f"{len(points)} / {max(1, self.target_var.get())}")

    def generate_plan(self) -> None:
        self.load_scene()
        try:
            count_x = int(self.count_x_var.get())
            count_y = int(self.count_y_var.get())
            count_z = int(self.count_z_var.get())
            fov = float(self.fov_var.get())
            points = self.store.load()
            bounds, positions = build_spatial_grid(
                points,
                count_x=count_x,
                count_y=count_y,
                count_z=count_z,
            )
            samples = build_22_view_plan(positions, fov_degrees=fov)
            outputs = save_scan_plan(
                self.store.scene_id,
                bounds,
                positions,
                samples,
                count_x=count_x,
                count_y=count_y,
                count_z=count_z,
            )
        except Exception as exc:
            messagebox.showerror("计划生成失败", str(exc), parent=self)
            return
        length = estimate_path_length(positions)
        self.plan_var.set(
            f"{len(positions)} 个空间点 × 22 = {len(samples)} 张；"
            f"顺序路径约 {length:.2f}；清单：{outputs['manifest'].name}"
        )
        self.app.log(f"扫描计划已保存：{outputs['manifest']}")

    def settings_payload(self) -> dict:
        return {
            "target_point_count": int(self.target_var.get()),
            "grid": {
                "x": int(self.count_x_var.get()),
                "y": int(self.count_y_var.get()),
                "z": int(self.count_z_var.get()),
            },
        }

    def _append_pose(self, pose) -> None:
        point = self.store.append(pose, self.label_var.get())
        self.label_var.set("")
        self.refresh()
        self.app.log(
            f"已采集点 #{point.index}: "
            f"({point.pose.x:.4f}, {point.pose.y:.4f}, {point.pose.z:.4f})"
        )
