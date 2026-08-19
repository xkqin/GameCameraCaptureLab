from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bridge import BridgeMetadata, CameraPoseBridge, PoseUnavailableError
from .game_context import HUD_REQUIRED
from .injection import integration_status
from .models import CameraPose


@dataclass(frozen=True)
class ConnectionReport:
    code: str
    level: str
    title: str
    detail: str
    pid: int | None = None
    pose: CameraPose | None = None
    metadata: BridgeMetadata | None = None

    @property
    def ready(self) -> bool:
        return self.code == "ready"


def classify_connection(
    integration: dict[str, Any],
    metadata: BridgeMetadata | None,
    pose_status: dict[str, Any],
) -> ConnectionReport:
    pid_value = integration.get("pid")
    pid = int(pid_value) if isinstance(pid_value, int) else None

    if integration.get("platform_unsupported"):
        return ConnectionReport(
            code="platform_unsupported",
            level="warning",
            title="等待 Proton Relay / Waiting for Proton Relay",
            detail=str(
                integration.get(
                    "message",
                    "请配置 BMW_BRIDGE_ENDPOINT。 / Configure BMW_BRIDGE_ENDPOINT.",
                )
            ),
        )
    if not integration.get("game_running"):
        return ConnectionReport(
            code="game_missing",
            level="error",
            title="未检测到游戏 / Game Not Detected",
            detail=(
                "请启动一个已支持的游戏并进入可渲染画面。 / "
                "Start a supported game and enter a rendered scene."
            ),
        )
    if not integration.get("module_scan_ok", True):
        return ConnectionReport(
            code="module_scan_failed",
            level="error",
            title="无法检查游戏模块 / Module Check Failed",
            detail=str(
                integration.get(
                    "message",
                    "请检查程序权限。 / Check application permissions.",
                )
            ),
            pid=pid,
        )
    if integration.get("conflicting_camera_tool"):
        names = ", ".join(integration.get("conflicting_modules") or [])
        return ConnectionReport(
            code="camera_tool_conflict",
            level="error",
            title="相机工具冲突 / Camera Tool Conflict",
            detail=(
                f"当前游戏已加载 {names or '第三方相机模块'}。"
                "请彻底退出游戏和其他相机工具，重开后只注入统一 Runtime。 / "
                f"The game loaded {names or 'another camera module'}; restart it and inject only the Unified Runtime."
            ),
            pid=pid,
        )
    if not integration.get("bridge_loaded") and not integration.get(
        "native_artifacts_ready", True
    ):
        return ConnectionReport(
            code="integration_repair_needed",
            level="error",
            title="相机组件不完整 / Camera Components Incomplete",
            detail=(
                "点击“自动修复并注入”完成构建和签名检查。 / "
                "Use Repair & Inject to build and validate signatures."
            ),
            pid=pid,
        )
    if not integration.get("bridge_loaded"):
        return ConnectionReport(
            code="bridge_needed",
            level="warning",
            title=f"游戏运行中 / Game Running · PID {pid}",
            detail="点击“检查并注入”载入 Runtime。 / Click Check & Inject to load the runtime.",
            pid=pid,
        )
    if metadata is None:
        return ConnectionReport(
            code="bridge_starting",
            level="warning",
            title="Camera Runtime 正在启动 / Starting",
            detail="DLL 已加载，正在建立共享内存并扫描 Hook。 / Initializing shared memory and camera hooks.",
            pid=pid,
        )
    if metadata.process_id != pid:
        return ConnectionReport(
            code="stale_bridge",
            level="error",
            title="检测到残留状态 / Stale Runtime State",
            detail="共享内存不属于当前游戏；请重开采集器。 / Shared memory belongs to another process; restart the studio.",
            pid=pid,
            metadata=metadata,
        )
    if not metadata.hooks_installed:
        return ConnectionReport(
            code="hook_unavailable",
            level="error",
            title="相机 Hook 未安装 / Camera Hook Missing",
            detail=(
                "当前游戏版本没有匹配到已验证的 LWC Camera View 签名。"
                "不要继续采集；请更新签名。 / "
                "No validated LWC Camera View signature matched; update the profile before capture."
            ),
            pid=pid,
            metadata=metadata,
        )
    if not metadata.input_capture_ready:
        return ConnectionReport(
            code="input_capture_unavailable",
            level="error",
            title="相机输入独占未就绪 / Input Capture Not Ready",
            detail=(
                "低级键盘 Hook 安装失败；当前版本不能保证 WASD/Space/Q/E 不再控制角色。"
                "请以管理员身份重启后再注入。 / "
                "The keyboard hook failed; restart the game and studio as administrator."
            ),
            pid=pid,
            metadata=metadata,
        )
    if HUD_REQUIRED and not metadata.hud_control_ready:
        return ConnectionReport(
            code="hud_control_unavailable",
            level="error",
            title="HUD Hook 未安装 / HUD Hook Missing",
            detail=(
                "当前游戏版本没有匹配到已验证的 HUD 签名；Delete 和界面按钮不可用。"
                "请先更新签名。 / No validated HUD signature matched; update it before capture."
            ),
            pid=pid,
            metadata=metadata,
        )
    if not metadata.pose_observed:
        return ConnectionReport(
            code="pose_waiting",
            level="warning",
            title="Hook 已安装 · 等待首帧 / Waiting for First Frame",
            detail="回到游戏等待一两秒，再按 Insert。 / Return to the game, wait briefly, then press Insert.",
            pid=pid,
            metadata=metadata,
        )
    if not pose_status.get("connected"):
        return ConnectionReport(
            code="pose_invalid",
            level="error",
            title="Pose 数据异常 / Invalid Pose Data",
            detail=str(
                pose_status.get(
                    "message",
                    "Camera Runtime 未返回有效 Pose。 / No valid pose returned.",
                )
            ),
            pid=pid,
            metadata=metadata,
        )
    pose = pose_status.get("pose")
    if not isinstance(pose, CameraPose):
        return ConnectionReport(
            code="pose_invalid",
            level="error",
            title="Pose 格式异常 / Invalid Pose Format",
            detail="请重启游戏和采集器。 / Restart the game and studio.",
            pid=pid,
            metadata=metadata,
        )
    control = pose_status.get("control")
    absolute_pose = pose_status.get("absolute_pose")
    hud = pose_status.get("hud")
    trajectory = pose_status.get("trajectory")
    if control is None:
        return ConnectionReport(
            code="native_control_outdated",
            level="error",
            title="缺少相机控制协议 / Camera Control Protocol Missing",
            detail="请重启游戏并重新注入当前 Runtime。 / Restart the game and inject the current runtime.",
            pid=pid,
            pose=pose,
            metadata=metadata,
        )
    if not control.ready:
        return ConnectionReport(
            code="native_control_waiting",
            level="warning",
            title="Pose 已连接 · 控制未就绪 / Control Not Ready",
            detail=getattr(
                control,
                "error_message",
                "等待 Camera Runtime 控制能力。 / Waiting for runtime control.",
            ),
            pid=pid,
            pose=pose,
            metadata=metadata,
        )
    if absolute_pose is None or not absolute_pose.ready:
        return ConnectionReport(
            code="absolute_pose_outdated",
            level="error",
            title="缺少绝对 setPose / Absolute setPose Missing",
            detail="请重启游戏并重新注入当前 Runtime。 / Restart the game and inject the current runtime.",
            pid=pid,
            pose=pose,
            metadata=metadata,
        )
    if HUD_REQUIRED and (hud is None or not hud.ready):
        return ConnectionReport(
            code="hud_control_outdated",
            level="error",
            title="缺少 HUD 控制 / HUD Control Missing",
            detail="请重启游戏并重新注入当前 Runtime。 / Restart the game and inject the current runtime.",
            pid=pid,
            pose=pose,
            metadata=metadata,
        )
    if trajectory is None:
        return ConnectionReport(
            code="smooth_trajectory_outdated",
            level="error",
            title="缺少进程内平滑轨迹 / Smooth Trajectory Missing",
            detail="请重启游戏并注入当前 Runtime。 / Restart the game and inject the current runtime.",
            pid=pid,
            pose=pose,
            metadata=metadata,
        )
    if not pose.camera_enabled:
        title = "Unified Camera Runtime 已连接 / Connected · Camera OFF"
        detail = "Insert 开启相机；鼠标观察；Shift 5× 加速。 / Insert enables camera; mouse looks; Shift accelerates."
        if metadata.hud_control_ready:
            detail += " Delete 隐藏 HUD / hides HUD."
    elif pose.movement_locked:
        title = "Unified Camera Runtime 已连接 / Connected · Movement Locked"
        detail = "自动控制可用；Home 解锁手动移动。 / Automated control is ready; press Home to unlock movement."
    else:
        title = "Unified Camera Runtime 已连接 / Connected · Camera ON"
        detail = (
            "WASD/Space/Q 和鼠标视角已由相机独占，E 记录点位，Shift 5× 加速；"
            "setPose、点位和轨迹已就绪。 / Camera input is captured; E records a point; setPose and trajectories are ready."
        )
        if metadata.hud_control_ready:
            detail += " Delete 切换 HUD / toggles HUD."
    return ConnectionReport(
        code="ready",
        level="success",
        title=title,
        detail=detail,
        pid=pid,
        pose=pose,
        metadata=metadata,
    )


def probe_connection(bridge: CameraPoseBridge) -> ConnectionReport:
    integration = integration_status()
    if getattr(bridge, "is_linux_relay", False):
        integration = dict(integration)
        integration.update(
            {
                "platform_unsupported": False,
                "linux_relay": True,
                "message": f"Linux/Proton Bridge Relay {getattr(bridge, 'relay_endpoint', '')}",
            }
        )
        try:
            metadata = bridge.read_metadata()
            if metadata is None:
                return ConnectionReport(
                    code="linux_bridge_waiting",
                    level="warning",
                    title="Relay 已连接 · 等待 Runtime / Waiting for Runtime",
                    detail="Relay 可达，但尚无有效元数据。 / Relay is reachable but runtime metadata is not ready.",
                )
            pose_status = bridge.status()
        except (PoseUnavailableError, OSError, TimeoutError, ConnectionError) as exc:
            return ConnectionReport(
                code="linux_bridge_waiting",
                level="warning",
                title="等待 Camera Runtime Relay / Waiting for Relay",
                detail=f"无法连接 Relay / Relay connection failed: {exc}",
            )
        integration.update(
            {
                "game_running": True,
                "module_scan_ok": True,
                "bridge_loaded": True,
                "conflicting_camera_tool": False,
                "pid": metadata.process_id,
            }
        )
        return classify_connection(integration, metadata, pose_status)
    if integration.get("platform_unsupported"):
        return classify_connection(integration, None, {"connected": False})
    metadata = bridge.read_metadata()
    pose_status = bridge.status()
    return classify_connection(integration, metadata, pose_status)
