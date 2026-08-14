from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bridge import BridgeMetadata, UuuPoseBridge
from .models import CameraPose
from .uuu import integration_status


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
            title="Linux 兼容模式",
            detail=str(
                integration.get(
                    "message",
                    "UUU 原生位姿控制需要 Windows；当前可使用文件管理和 OBS 功能。",
                )
            ),
        )

    if not integration.get("game_running"):
        return ConnectionReport(
            code="game_missing",
            level="error",
            title="未检测到游戏",
            detail="请先启动《黑神话：悟空》并进入游戏画面。",
        )
    if not integration.get("module_scan_ok", True):
        return ConnectionReport(
            code="module_scan_failed",
            level="error",
            title="无法检查游戏模块",
            detail=str(integration.get("message", "请检查程序权限。")),
            pid=pid,
        )

    bridge_loaded = bool(integration.get("bridge_loaded"))
    uuu_loaded = bool(integration.get("uuu_loaded"))
    if uuu_loaded and not bridge_loaded:
        return ConnectionReport(
            code="restart_required",
            level="error",
            title="需要彻底重启游戏",
            detail="UUU 已经先注入，程序不会再补注入位姿桥。退出游戏后按 1 → 2 重试。",
            pid=pid,
        )
    if not bridge_loaded:
        return ConnectionReport(
            code="bridge_needed",
            level="warning",
            title=f"游戏运行中 · PID {pid}",
            detail="下一步：点击“1 准备位姿桥”。",
            pid=pid,
        )
    if metadata is None:
        return ConnectionReport(
            code="bridge_outdated",
            level="error",
            title="位姿桥版本不匹配",
            detail="游戏中加载的是旧版位姿桥。彻底退出游戏，再重新点击“1 准备位姿桥”。",
            pid=pid,
        )
    if metadata.process_id != pid:
        return ConnectionReport(
            code="stale_bridge",
            level="error",
            title="检测到上一局残留状态",
            detail="当前共享数据不属于这个游戏进程。退出游戏和采集工具后重新启动。",
            pid=pid,
            metadata=metadata,
        )
    if not uuu_loaded:
        return ConnectionReport(
            code="uuu_needed",
            level="warning",
            title="位姿桥已就绪",
            detail="下一步：点击“2 打开 UUU”，在 UUU 中选择游戏并 Inject。",
            pid=pid,
            metadata=metadata,
        )
    if not metadata.buffer_requested:
        return ConnectionReport(
            code="handshake_missing",
            level="error",
            title="UUU 未连接位姿桥",
            detail="DLL 都已加载但没有 Connector 握手。请彻底退出游戏，再按 1 → 2 重试。",
            pid=pid,
            metadata=metadata,
        )
    if not pose_status.get("connected"):
        return ConnectionReport(
            code="pose_waiting",
            level="warning",
            title="位姿桥握手完成",
            detail="正在等待 UUU 输出有效 Pose；回游戏按 Insert，并等待游戏画面稳定。",
            pid=pid,
            metadata=metadata,
        )

    pose = pose_status.get("pose")
    if not isinstance(pose, CameraPose):
        return ConnectionReport(
            code="pose_invalid",
            level="error",
            title="Pose 数据格式异常",
            detail="请彻底退出游戏后重试；若重复出现，请保留 UUU 日志。",
            pid=pid,
            metadata=metadata,
        )
    if not pose.camera_enabled:
        return ConnectionReport(
            code="camera_off",
            level="warning",
            title="Pose 已连接 · Camera OFF",
            detail="回到游戏按 Insert 启用自由相机。",
            pid=pid,
            pose=pose,
            metadata=metadata,
        )
    if pose.movement_locked:
        return ConnectionReport(
            code="camera_locked",
            level="warning",
            title="Camera ON · Movement Locked",
            detail="按 Home 解锁相机移动后再采集。",
            pid=pid,
            pose=pose,
            metadata=metadata,
        )
    control = pose_status.get("control")
    if control is None:
        return ConnectionReport(
            code="native_control_outdated",
            level="error",
            title="Pose 已连接 · 原生控制桥过旧",
            detail=(
                "当前游戏中加载的 Connector 不含 UUU 原生相机控制。"
                "请彻底退出游戏，重新准备位姿桥，再注入 UUU 5.8.21。"
            ),
            pid=pid,
            pose=pose,
            metadata=metadata,
        )
    if not control.ready:
        error_message = getattr(control, "error_message", "native control unavailable")
        return ConnectionReport(
            code="native_control_waiting",
            level="warning",
            title="Pose 已连接 · 等待原生控制",
            detail=(
                "请确认 UUU 版本为 5.8.21、按 Insert 启用 Camera，并等待日志出现 "
                f"Camera found。当前状态：{error_message}。"
            ),
            pid=pid,
            pose=pose,
            metadata=metadata,
        )
    trajectory = pose_status.get("trajectory")
    if trajectory is None:
        return ConnectionReport(
            code="smooth_trajectory_outdated",
            level="error",
            title="原生平滑轨迹桥过旧",
            detail=(
                "当前游戏进程仍加载旧版 Bridge，不能保证轨迹连续播放。"
                "请彻底退出游戏和采集器，重新启动后按 1 → 2 注入新版 Bridge。"
            ),
            pid=pid,
            pose=pose,
            metadata=metadata,
        )
    return ConnectionReport(
        code="ready",
        level="success",
        title="Pose 已连接 · Camera ON",
        detail="可以记录点位、Load 文件并开始采集。",
        pid=pid,
        pose=pose,
        metadata=metadata,
    )


def probe_connection(bridge: UuuPoseBridge) -> ConnectionReport:
    integration = integration_status()
    if integration.get("platform_unsupported"):
        return classify_connection(integration, None, {"connected": False})
    metadata = bridge.read_metadata()
    pose_status = bridge.status()
    return classify_connection(integration, metadata, pose_status)
