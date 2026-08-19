from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import locale
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
    NATIVE_BUILD_STAMP_PATH,
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
        REPOSITORY_ROOT / "core" / "runtime" / "ue-camera-runtime" / "native",
    )
    suffixes = {".cpp", ".h", ".asm", ".txt", ".ps1"}
    return tuple(
        path
        for root in roots
        if root.is_dir()
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.casefold() in suffixes
        and not any(
            part.casefold().startswith("build")
            for part in path.relative_to(root).parts[:-1]
        )
    )


def _native_source_groups() -> dict[str, tuple[Path, ...]]:
    sources = _native_sources()
    injector_names = {
        "cmakelists.txt",
        "build_standalone.ps1",
        "uecamerainjector.cpp",
        "uecameraprofile.cpp",
        "uecameraprofile.h",
    }
    return {
        "runtime": tuple(
            path
            for path in sources
            if not path.name.casefold().endswith("injector.cpp")
        ),
        "injector": tuple(
            path for path in sources if path.name.casefold() in injector_names
        ),
    }


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_digest(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda value: str(value.resolve()).casefold()):
        try:
            identity = path.resolve().relative_to(REPOSITORY_ROOT.resolve()).as_posix()
        except ValueError:
            identity = str(path.resolve())
        digest.update(identity.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_file_digest(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _native_build_stamp_valid(
    source_groups: dict[str, tuple[Path, ...]],
) -> bool:
    try:
        payload = json.loads(NATIVE_BUILD_STAMP_PATH.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1:
            return False
        recorded_sources = payload["source_digests"]
        recorded_outputs = payload["outputs"]
        outputs = {
            "runtime": UE_RUNTIME_PATH,
            "injector": UE_INJECTOR_PATH,
        }
        return all(
            recorded_sources.get(name) == _source_digest(source_groups[name])
            and recorded_outputs.get(name, {}).get("sha256") == _file_digest(output)
            and int(recorded_outputs.get(name, {}).get("size", -1))
            == output.stat().st_size
            for name, output in outputs.items()
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _write_native_build_stamp() -> Path:
    source_groups = _native_source_groups()
    outputs = {
        "runtime": UE_RUNTIME_PATH,
        "injector": UE_INJECTOR_PATH,
    }
    payload = {
        "schema_version": 1,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "source_digests": {
            name: _source_digest(paths) for name, paths in source_groups.items()
        },
        "outputs": {
            name: {
                "path": str(path.resolve()),
                "size": path.stat().st_size,
                "sha256": _file_digest(path),
            }
            for name, path in outputs.items()
        },
    }
    NATIVE_BUILD_STAMP_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = NATIVE_BUILD_STAMP_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(NATIVE_BUILD_STAMP_PATH)
    return NATIVE_BUILD_STAMP_PATH


def _native_build_needed() -> tuple[bool, str]:
    outputs = (UE_RUNTIME_PATH, UE_INJECTOR_PATH)
    missing = [path.name for path in outputs if not path.is_file()]
    if missing:
        return True, f"缺少原生组件 / Missing native components: {', '.join(missing)}"
    groups = _native_source_groups()
    if any(groups.values()):
        # A successful build can legitimately leave an unchanged target's
        # timestamp untouched. Validate the exact source/output content first;
        # mtime remains only the conservative fallback for old worktrees that
        # do not yet have a verified build stamp.
        if _native_build_stamp_valid(groups):
            return False, "原生组件已通过构建验证 / Native components verified"
        source_groups = (
            (UE_RUNTIME_PATH, groups["runtime"]),
            (UE_INJECTOR_PATH, groups["injector"]),
        )
        stale_outputs = [
            output.name
            for output, relevant_sources in source_groups
            if relevant_sources
            and max(path.stat().st_mtime for path in relevant_sources)
            > output.stat().st_mtime
        ]
        if stale_outputs:
            return True, (
                "原生源码已更新，需要重新构建 / Native sources changed; rebuild "
                + ", ".join(stale_outputs)
            )
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
        check=False,
        timeout=360,
        creationflags=creationflags,
    )

    def decode_output(value: bytes | str | None) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        encodings = ("utf-8-sig", locale.getpreferredencoding(False))
        for encoding in dict.fromkeys(encodings):
            try:
                return value.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue
        return value.decode("utf-8", errors="replace")

    output = "\n".join(
        value.strip()
        for value in (
            decode_output(completed.stdout),
            decode_output(completed.stderr),
        )
        if value and value.strip()
    )
    if completed.returncode != 0:
        raise CameraIntegrationError(output or "Camera Runtime 自动构建失败 / Automatic build failed")
    if not UE_RUNTIME_PATH.is_file() or not UE_INJECTOR_PATH.is_file():
        raise CameraIntegrationError("构建后组件不完整 / Build finished without complete Runtime/Injector")
    _write_native_build_stamp()
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
