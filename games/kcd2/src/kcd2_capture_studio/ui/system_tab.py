from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
from typing import Any

from ..backend import CameraBackend
from ..models import Pose
from ..paths import DATA_ROOT
from .common import AppHost, open_in_explorer, pose_summary


class SystemTab(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        app: AppHost,
        backend: CameraBackend,
    ) -> None:
        super().__init__(parent, padding=12)
        self.app = app
        self.backend = backend
        self.polling = True
        self.pose_request_active = False
        self.pose_lock = threading.Lock()

        self.game_var = tk.StringVar(value="未检测")
        self.client_var = tk.StringVar(value="未检测")
        self.dll_var = tk.StringVar(value="未检测")
        self.camera_var = tk.StringVar(value="未知")
        self.pose_var = tk.StringVar(value="等待游戏和相机 DLL…")
        self.quaternion_var = tk.StringVar(value="Quaternion: -")
        self.runtime_var = tk.StringVar(value="")

        status_box = ttk.LabelFrame(self, text="运行状态", padding=10)
        status_box.pack(fill="x")
        for row, (name, variable) in enumerate(
            (
                ("游戏进程", self.game_var),
                ("IGCS Client / 管道", self.client_var),
                ("KCD2 Camera Tools", self.dll_var),
                ("自由相机", self.camera_var),
            )
        ):
            ttk.Label(status_box, text=f"{name}:").grid(
                row=row, column=0, sticky="w", padx=(0, 10), pady=3
            )
            ttk.Label(status_box, textvariable=variable).grid(
                row=row, column=1, sticky="w", pady=3
            )
        ttk.Label(
            status_box,
            textvariable=self.runtime_var,
            foreground="#b45309",
            wraplength=900,
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(5, 0))

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", pady=10)
        ttk.Button(
            buttons, text="刷新状态", command=self.refresh_status
        ).pack(side="left", padx=(0, 6))
        self.inject_button = ttk.Button(
            buttons,
            text="一键准备 Client + 注入",
            command=self.inject,
        )
        self.inject_button.pack(side="left", padx=6)
        ttk.Button(
            buttons,
            text="切换自由相机 (Insert)",
            command=lambda: self.send_action("toggle_camera", 80),
        ).pack(side="left", padx=6)
        ttk.Button(
            buttons,
            text="打开数据目录",
            command=lambda: open_in_explorer(DATA_ROOT),
        ).pack(side="right")

        pose_box = ttk.LabelFrame(self, text="实时 Camera Pose", padding=10)
        pose_box.pack(fill="x", pady=(0, 10))
        ttk.Label(
            pose_box,
            textvariable=self.pose_var,
            font=("Segoe UI", 11, "bold"),
            wraplength=950,
        ).pack(anchor="w", pady=(0, 5))
        ttk.Label(
            pose_box,
            textvariable=self.quaternion_var,
            wraplength=950,
        ).pack(anchor="w")

        controls = ttk.LabelFrame(
            self, text="相对控制（发送官方热键）", padding=10
        )
        controls.pack(fill="x")
        layout = (
            ("前进 W", "forward"),
            ("后退 S", "backward"),
            ("左移 A", "left"),
            ("右移 D", "right"),
            ("上升 Num7", "up"),
            ("下降 Num9", "down"),
            ("抬头 ↑", "rotate_up"),
            ("低头 ↓", "rotate_down"),
            ("左转 ←", "rotate_left"),
            ("右转 →", "rotate_right"),
        )
        for index, (label, action) in enumerate(layout):
            ttk.Button(
                controls,
                text=label,
                command=lambda selected=action: self.send_action(selected, 120),
                width=14,
            ).grid(
                row=index // 5,
                column=index % 5,
                sticky="ew",
                padx=4,
                pady=4,
            )
        for column in range(5):
            controls.columnconfigure(column, weight=1)

        ttk.Label(
            self,
            text=(
                "当前结论：pose 可实时读取；官方导出可做相对平移、升降、"
                "panorama yaw 和随机运镜。任意绝对 XYZ + yaw/pitch/roll "
                "精确 setPose 尚未验收。"
            ),
            foreground="#9a3412",
            wraplength=960,
        ).pack(anchor="w", pady=(12, 0))

        self.after(200, self.refresh_status)
        self.after(300, self._poll_pose)

    def refresh_status(self) -> None:
        self.app.run_async("刷新相机状态", self.backend.status, self._show_status)

    def inject(self) -> None:
        self.inject_button.state(["disabled"])
        self.runtime_var.set("准备一键注入…")

        def progress(message: str) -> None:
            self.app.root.after(
                0,
                lambda text=message: self._show_inject_progress(text),
            )

        self.app.run_async(
            "准备 IGCS Client 并注入相机 DLL",
            lambda: self.backend.inject(progress),
            self._after_inject,
            self._inject_failed,
        )

    def send_action(self, action: str, duration_ms: int) -> None:
        self.app.run_async(
            f"发送相机控制 {action}",
            lambda: self.backend.send_action(action, duration_ms),
            lambda _: self.after(250, self.refresh_status),
        )

    def close(self) -> None:
        self.polling = False

    def _after_inject(self, result: dict[str, Any]) -> None:
        self.inject_button.state(["!disabled"])
        state = "已加载" if result.get("already_loaded") else "注入成功"
        pipe_state = (
            "，双向管道已验证"
            if result.get("pipe_verified")
            else ""
        )
        self.runtime_var.set(f"{state}{pipe_state}")
        self.app.log(f"{state}，PID={result.get('pid')}{pipe_state}")
        self.after(250, self.refresh_status)

    def _inject_failed(self, exc: Exception) -> None:
        self.inject_button.state(["!disabled"])
        self.runtime_var.set(str(exc))

    def _show_inject_progress(self, message: str) -> None:
        self.runtime_var.set(message)
        self.app.log(message)

    def _show_status(self, status: dict[str, Any]) -> None:
        pid = status.get("pid")
        self.game_var.set(f"运行中 (PID {pid})" if pid else "未运行")
        client = status.get("igcs_client") or {}
        client_pids = client.get("pids") or []
        if (
            client.get("dll_to_client_pipe")
            and client.get("client_to_dll_pipe")
        ):
            self.client_var.set(
                f"双向已连接 (PID {client_pids[0]})"
                if client_pids
                else "双向已连接"
            )
        elif client.get("dll_to_client_pipe"):
            self.client_var.set(
                f"等待 DLL 回连 (PID {client_pids[0]})"
                if client_pids
                else "Client 管道已就绪"
            )
        elif client.get("running"):
            self.client_var.set(
                f"运行中，管道未就绪 (PID {client_pids[0]})"
            )
        elif client.get("exe_exists"):
            self.client_var.set("文件就绪，尚未启动")
        else:
            self.client_var.set("IGCSClient.exe 缺失")
        if status.get("module"):
            self.dll_var.set("已注入，v1.0.5 校验通过")
        elif status.get("dll_matches_v105"):
            self.dll_var.set("文件校验通过，尚未注入")
        elif status.get("dll_exists"):
            self.dll_var.set("文件存在，但哈希不匹配")
        else:
            self.dll_var.set("DLL 文件缺失")
        enabled = status.get("latest_log_camera_enabled")
        self.camera_var.set(
            "已启用" if enabled is True else "未启用" if enabled is False else "未知"
        )
        runtime_error = str(status.get("runtime_error") or "")
        if (
            pid
            and status.get("module")
            and status.get("igcs_pipe_error")
        ):
            runtime_error = (
                "当前 DLL 会话曾在 Client 管道建立前加载；"
                "请重启游戏后重新一键注入。"
            )
        self.runtime_var.set(runtime_error)

    def _poll_pose(self) -> None:
        if not self.polling:
            return
        if not self.pose_request_active:
            self.pose_request_active = True
            threading.Thread(target=self._read_pose_worker, daemon=True).start()
        self.after(250, self._poll_pose)

    def _read_pose_worker(self) -> None:
        try:
            pose = self.backend.pose()
        except Exception as exc:
            error = str(exc)
            self.app.root.after(
                0, lambda message=error: self.pose_var.set(f"Pose 暂不可用：{message}")
            )
        else:
            self.app.root.after(0, lambda: self._show_pose(pose))
        finally:
            self.pose_request_active = False

    def _show_pose(self, pose: Pose) -> None:
        self.pose_var.set(pose_summary(pose))
        self.quaternion_var.set(
            "Quaternion "
            f"({pose.q0:.6f}, {pose.q1:.6f}, {pose.q2:.6f}, {pose.q3:.6f})"
        )
