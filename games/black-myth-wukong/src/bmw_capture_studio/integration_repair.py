from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from .game_context import AUTO_DETECT, GAME_ID, GAME_NAME, HUD_REQUIRED
from .injection import (
    CameraIntegrationError,
    find_game_pid,
    inject_bridge,
    integration_status,
    process_executable_path,
)
from .paths import (
    ACTIVE_RUNTIME_CONFIG_PATH,
    NATIVE_BUILD_SCRIPT_PATH,
    NATIVE_DIR,
    REPOSITORY_ROOT,
    PREFLIGHT_DIAGNOSTIC_PATH,
    UE_INJECTOR_PATH,
    UE_PROFILE_DIR,
    UE_RUNTIME_PATH,
)

# The capture studio has a deliberately small standalone environment. Reuse
# the monorepo's dependency-free profile scanner without installing the root
# package (whose optional ML dependencies are much larger than this UI needs).
_REPOSITORY_SRC = REPOSITORY_ROOT / "src"
if str(_REPOSITORY_SRC) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_SRC))

from game_camera_capture_lab.ue_runtime import (  # noqa: E402
    UeRuntimeProfileError,
    load_profiles,
    profile_for_process,
    scan_executable_hooks,
)


@dataclass(frozen=True)
class PreflightIssue:
    code: str
    message: str
    repairable: bool = False


@dataclass(frozen=True)
class CameraPreflightReport:
    pid: int | None
    executable: str | None
    profile_id: str | None
    profile_path: str | None
    camera_match_count: int | None
    hud_match_count: int | None
    bridge_loaded: bool
    runtime_path: str
    injector_path: str
    issues: tuple[PreflightIssue, ...]

    @property
    def ready(self) -> bool:
        return not self.issues

    @property
    def can_auto_repair(self) -> bool:
        return bool(self.issues) and all(issue.repairable for issue in self.issues)

    def summary(self) -> str:
        if self.ready:
            return (
                f"预检通过 / Preflight passed：{self.profile_id} · Camera signatures "
                f"{self.camera_match_count}"
            )
        return "；".join(issue.message for issue in self.issues)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["ready"] = self.ready
        value["can_auto_repair"] = self.can_auto_repair
        return value


def _native_sources() -> tuple[Path, ...]:
    roots = (
        NATIVE_DIR,
        REPOSITORY_ROOT / "runtime" / "ue-camera-runtime" / "native",
    )
    suffixes = {".cpp", ".h", ".asm", ".txt", ".ps1"}
    return tuple(
        path
        for root in roots
        if root.is_dir()
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.casefold() in suffixes
        and "build_standalone_v1" not in path.parts
    )


def _native_build_needed() -> tuple[bool, str]:
    outputs = (UE_RUNTIME_PATH, UE_INJECTOR_PATH)
    missing = [path.name for path in outputs if not path.is_file()]
    if missing:
        return True, f"缺少原生组件 / Missing native components: {', '.join(missing)}"
    sources = _native_sources()
    if sources:
        newest_source = max(path.stat().st_mtime for path in sources)
        oldest_output = min(path.stat().st_mtime for path in outputs)
        if newest_source > oldest_output:
            return True, "原生源码已更新，需要重新构建 / Native sources changed; rebuild DLL/Injector"
    return False, "原生组件已就绪 / Native components ready"


def _valid_count(hook: Any, count: int) -> bool:
    return hook.min_matches <= count <= hook.max_matches


def preflight_camera_integration(pid: int | None = None) -> CameraPreflightReport:
    if sys.platform != "win32":
        return CameraPreflightReport(
            pid=None,
            executable=None,
            profile_id=None,
            profile_path=None,
            camera_match_count=None,
            hud_match_count=None,
            bridge_loaded=False,
            runtime_path=str(UE_RUNTIME_PATH),
            injector_path=str(UE_INJECTOR_PATH),
            issues=(
                PreflightIssue(
                    "linux_relay_required",
                    "Linux/Proton 需要在 Proton 内运行 Injector 并配置 Relay。 / "
                    "Run the injector inside Proton and configure the relay.",
                ),
            ),
        )

    issues: list[PreflightIssue] = []
    status = integration_status()
    if pid is None:
        if not status.get("game_running"):
            issues.append(
                PreflightIssue(
                    "game_missing",
                    "请先启动一个已支持的游戏。 / Start a supported game first.",
                )
            )
            return CameraPreflightReport(
                None, None, None, None, None, None, False,
                str(UE_RUNTIME_PATH), str(UE_INJECTOR_PATH), tuple(issues)
            )
        pid = int(status["pid"])
    if not status.get("module_scan_ok", True):
        issues.append(
            PreflightIssue(
                "module_scan_failed",
                str(status.get("message", "无法读取游戏模块，请检查权限。 / Unable to read game modules; check permissions.")),
            )
        )
    if status.get("conflicting_camera_tool"):
        names = ", ".join(status.get("conflicting_modules") or [])
        issues.append(
            PreflightIssue(
                "camera_tool_conflict",
                f"游戏已加载冲突模块 {names}；请重启。 / Conflicting camera modules are loaded; restart the game.",
            )
        )

    executable = process_executable_path(pid)
    profile_id: str | None = None
    profile_path: Path | None = None
    camera_count: int | None = None
    hud_count: int | None = None
    try:
        profiles = load_profiles(UE_PROFILE_DIR)
        profile = profile_for_process(executable.name, profiles)
        if profile is None:
            issues.append(
                PreflightIssue(
                    "profile_missing",
                    f"没有与 {executable.name} 匹配的已验证 profile。 / No validated profile matches; automatic injection stopped.",
                )
            )
        else:
            profile_id = profile.id
            candidate = UE_PROFILE_DIR / f"{profile.id}.json"
            profile_path = candidate if candidate.is_file() else None
            hooks = {"camera": profile.camera_hook}
            if profile.hud_hook is not None:
                hooks["hud"] = profile.hud_hook
            matches = scan_executable_hooks(executable, hooks)
            camera_count = len(matches["camera"])
            if not _valid_count(profile.camera_hook, camera_count):
                issues.append(
                    PreflightIssue(
                        "camera_signature_mismatch",
                        f"Camera 签名匹配 / matches {camera_count}，要求 / expected "
                        f"{profile.camera_hook.min_matches}–{profile.camera_hook.max_matches}；"
                        "需要新签名 / this build needs a new validated signature.",
                    )
                )
            if profile.hud_hook is not None:
                hud_count = len(matches["hud"])
                if HUD_REQUIRED and not _valid_count(profile.hud_hook, hud_count):
                    issues.append(
                        PreflightIssue(
                            "hud_signature_mismatch",
                            f"HUD 签名匹配 / matches {hud_count}；当前版本不安全 / HUD control is unsafe for this build.",
                        )
                    )
            elif HUD_REQUIRED:
                issues.append(
                    PreflightIssue(
                        "hud_profile_missing",
                        "当前 profile 没有已验证的 HUD Hook。 / This profile has no validated HUD hook.",
                    )
                )
    except UeRuntimeProfileError as exc:
        issues.append(PreflightIssue("profile_invalid", f"相机 profile 无效 / Invalid camera profile: {exc}"))

    build_needed, build_reason = _native_build_needed()
    if build_needed:
        if status.get("bridge_loaded"):
            issues.append(
                PreflightIssue(
                    "restart_required_for_native_build",
                    f"{build_reason}；旧 DLL 已加载，请重启。 / The old DLL is loaded; restart before repair.",
                )
            )
        else:
            issues.append(
                PreflightIssue(
                    "native_build_required",
                    build_reason,
                    repairable=NATIVE_BUILD_SCRIPT_PATH.is_file(),
                )
            )
    if profile_id is not None and profile_id != GAME_ID and not AUTO_DETECT:
        issues.append(
            PreflightIssue(
                "adapter_profile_mismatch",
                f"适配器 {GAME_ID} 与扫描 profile {profile_id} 不匹配。 / Adapter/profile mismatch; injection stopped.",
            )
        )

    return CameraPreflightReport(
        pid=pid,
        executable=str(executable),
        profile_id=profile_id,
        profile_path=str(profile_path) if profile_path is not None else None,
        camera_match_count=camera_count,
        hud_match_count=hud_count,
        bridge_loaded=bool(status.get("bridge_loaded")),
        runtime_path=str(UE_RUNTIME_PATH),
        injector_path=str(UE_INJECTOR_PATH),
        issues=tuple(issues),
    )


def _build_native_runtime() -> str:
    if not NATIVE_BUILD_SCRIPT_PATH.is_file():
        raise CameraIntegrationError(
            f"缺少自动构建脚本 / Build script missing: {NATIVE_BUILD_SCRIPT_PATH}"
        )
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(NATIVE_BUILD_SCRIPT_PATH),
        "-Configuration",
        "Release",
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    completed = subprocess.run(
        command,
        cwd=NATIVE_DIR,
        capture_output=True,
        text=True,
        check=False,
        timeout=360,
        creationflags=creationflags,
    )
    output = "\n".join(
        value.strip()
        for value in (completed.stdout, completed.stderr)
        if value and value.strip()
    )
    if completed.returncode != 0:
        raise CameraIntegrationError(output or "Camera Runtime 自动构建失败 / Automatic build failed")
    if not UE_RUNTIME_PATH.is_file() or not UE_INJECTOR_PATH.is_file():
        raise CameraIntegrationError("构建后组件不完整 / Build finished without complete Runtime/Injector")
    return output


def _write_active_config(report: CameraPreflightReport) -> Path:
    ACTIVE_RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "game_id": GAME_ID,
        "profile_id": report.profile_id,
        "profile_path": report.profile_path,
        "process_id": report.pid,
        "executable": report.executable,
        "camera_match_count": report.camera_match_count,
        "hud_match_count": report.hud_match_count,
        "runtime_path": report.runtime_path,
        "injector_path": report.injector_path,
        "validation": "offline_signature_preflight_passed",
    }
    ACTIVE_RUNTIME_CONFIG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return ACTIVE_RUNTIME_CONFIG_PATH


def _write_preflight_diagnostic(report: CameraPreflightReport) -> Path:
    PREFLIGHT_DIAGNOSTIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    payload["checked_at"] = datetime.now(timezone.utc).isoformat()
    PREFLIGHT_DIAGNOSTIC_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return PREFLIGHT_DIAGNOSTIC_PATH


def repair_and_inject(*, auto_repair: bool) -> dict[str, Any]:
    if sys.platform != "win32":
        result = inject_bridge()
        return {
            **result,
            "profile": GAME_ID,
            "camera_match_count": None,
            "hud_match_count": None,
            "active_config": None,
            "native_rebuilt": False,
            "linux_helper": True,
        }
    report = preflight_camera_integration()
    _write_preflight_diagnostic(report)
    build_output = ""
    if report.issues:
        if not auto_repair or not report.can_auto_repair:
            raise CameraIntegrationError(report.summary())
        build_output = _build_native_runtime()
        report = preflight_camera_integration(report.pid)
        _write_preflight_diagnostic(report)
    if not report.ready:
        raise CameraIntegrationError(report.summary())
    config_path = _write_active_config(report)
    result = inject_bridge(report.pid, UE_RUNTIME_PATH)
    return {
        **result,
        "profile": report.profile_id,
        "camera_match_count": report.camera_match_count,
        "hud_match_count": report.hud_match_count,
        "active_config": str(config_path),
        "native_rebuilt": bool(build_output),
    }
