from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bridge import BridgeMetadata, CameraPoseBridge, PoseUnavailableError
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
            title="Linux 等待 Proton Relay",
            detail=str(integration.get("message", "请配置 BMW_BRIDGE_ENDPOINT。")),
        )
    if not integration.get("game_running"):
        return ConnectionReport(
            code="game_missing",
            level="error",
            title="未检测到游戏",
            detail="请先启动《黑神话：悟空》并进入可渲染的游戏画面。",
        )
    if not integration.get("module_scan_ok", True):
        return ConnectionReport(
            code="module_scan_failed",
            level="error",
            title="无法检查游戏模块",
            detail=str(integration.get("message", "请检查程序权限。")),
            pid=pid,
        )
    if integration.get("conflicting_camera_tool"):
        names = ", ".join(integration.get("conflicting_modules") or [])
        return ConnectionReport(
            code="camera_tool_conflict",
            level="error",
            title="检测到 UUU/旧 Connector 冲突",
            detail=(
                f"当前游戏已加载 {names or '第三方相机模块'}。"
                "请彻底退出游戏和 IGCSClient，重开游戏后只注入自研 Camera Bridge。"
            ),
            pid=pid,
        )
    if not integration.get("bridge_loaded"):
        return ConnectionReport(
            code="bridge_needed",
            level="warning",
            title=f"游戏运行中 · PID {pid}",
            detail="点击“注入 Camera Bridge”，不需要再打开 UUU。",
            pid=pid,
        )
    if metadata is None:
        return ConnectionReport(
            code="bridge_starting",
            level="warning",
            title="Camera Bridge 正在启动",
            detail="DLL 已加载，正在建立共享内存并扫描相机 hook。",
            pid=pid,
        )
    if metadata.process_id != pid:
        return ConnectionReport(
            code="stale_bridge",
            level="error",
            title="检测到上一局残留状态",
            detail="共享内存不属于当前游戏进程；请关闭采集器后重新打开。",
            pid=pid,
            metadata=metadata,
        )
    if not metadata.hooks_installed:
        return ConnectionReport(
            code="hook_unavailable",
            level="error",
            title="相机 hook 未安装",
            detail=(
                "当前游戏版本没有匹配到已验证的 LWC Camera View 签名。"
                "不要继续采集；请保留版本信息后更新签名。"
            ),
            pid=pid,
            metadata=metadata,
        )
    if not metadata.input_capture_ready:
        return ConnectionReport(
            code="input_capture_unavailable",
            level="error",
            title="相机输入独占未就绪",
            detail=(
                "低级键盘 Hook 安装失败；当前版本不能保证 WASD/QE 不再控制角色。"
                "请以管理员身份重新启动采集器和游戏后再次注入。"
            ),
            pid=pid,
            metadata=metadata,
        )
    if not metadata.hud_control_ready:
        return ConnectionReport(
            code="hud_control_unavailable",
            level="error",
            title="HUD 控制 Hook 未安装",
            detail=(
                "当前游戏版本没有匹配到已验证的 HUD 签名；Delete 和界面按钮不可用。"
                "不要开始正式采集，请先更新 HUD 签名。"
            ),
            pid=pid,
            metadata=metadata,
        )
    if not metadata.pose_observed:
        return ConnectionReport(
            code="pose_waiting",
            level="warning",
            title="hook 已安装 · 等待首帧相机",
            detail="回到游戏画面等待一两秒；首次有效 Pose 到达后可按 Insert 启用自由相机。",
            pid=pid,
            metadata=metadata,
        )
    if not pose_status.get("connected"):
        return ConnectionReport(
            code="pose_invalid",
            level="error",
            title="精确 Pose 数据异常",
            detail=str(pose_status.get("message", "Camera Bridge 未返回有效 Pose。")),
            pid=pid,
            metadata=metadata,
        )
    pose = pose_status.get("pose")
    if not isinstance(pose, CameraPose):
        return ConnectionReport(
            code="pose_invalid",
            level="error",
            title="Pose 数据格式异常",
            detail="请重启游戏和采集器后重试。",
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
            title="缺少相机控制协议",
            detail="游戏中加载的不是当前自研 Bridge；请彻底重启游戏后重新注入。",
            pid=pid,
            pose=pose,
            metadata=metadata,
        )
    if not control.ready:
        return ConnectionReport(
            code="native_control_waiting",
            level="warning",
            title="Pose 已连接 · 控制尚未就绪",
            detail=getattr(control, "error_message", "等待 Camera Bridge 控制能力。"),
            pid=pid,
            pose=pose,
            metadata=metadata,
        )
    if absolute_pose is None or not absolute_pose.ready:
        return ConnectionReport(
            code="absolute_pose_outdated",
            level="error",
            title="缺少绝对 setPose",
            detail="游戏中加载的不是当前自研 Bridge；请彻底重启游戏后重新注入。",
            pid=pid,
            pose=pose,
            metadata=metadata,
        )
    if hud is None or not hud.ready:
        return ConnectionReport(
            code="hud_control_outdated",
            level="error",
            title="缺少 HUD 显示控制",
            detail="游戏中加载的不是当前自研 Bridge；请彻底重启游戏后重新注入。",
            pid=pid,
            pose=pose,
            metadata=metadata,
        )
    if trajectory is None:
        return ConnectionReport(
            code="smooth_trajectory_outdated",
            level="error",
            title="缺少进程内平滑轨迹",
            detail="请彻底重启游戏后重新注入当前 BmwCameraBridge.dll。",
            pid=pid,
            pose=pose,
            metadata=metadata,
        )
    if not pose.camera_enabled:
        title = "自研 Camera Bridge 已连接 · Camera OFF"
        detail = "Insert 开启自由相机；鼠标观察；Delete 隐藏 HUD；按住 Shift 可 5× 加速。"
    elif pose.movement_locked:
        title = "自研 Camera Bridge 已连接 · Movement Locked"
        detail = "自动 setPose/轨迹可用；手动 WASD 移动请回到游戏按 Home 解锁。"
    else:
        title = "自研 Camera Bridge 已连接 · Camera ON"
        detail = (
            "WASD/QE 和鼠标视角已由相机独占，Shift 5× 加速，Delete 切换 HUD；"
            "绝对 setPose、点位和轨迹采集均已就绪。"
        )
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
                    title="Linux/Proton Relay 已连接，等待 Bridge",
                    detail="Relay 可达，但游戏内 Bridge 尚未发布有效元数据。",
                )
            pose_status = bridge.status()
        except (PoseUnavailableError, OSError, TimeoutError, ConnectionError) as exc:
            return ConnectionReport(
                code="linux_bridge_waiting",
                level="warning",
                title="等待 Linux/Proton Camera Bridge Relay",
                detail=f"无法连接 Relay：{exc}",
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
