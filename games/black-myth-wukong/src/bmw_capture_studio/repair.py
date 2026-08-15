from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from typing import IO, Mapping

from .config import SharedConfig
from .paths import LOGS_DIR, PROJECT_ROOT
from .platform_support import detached_process_kwargs


UNIFIED_RECOVERY_ENABLED_ENV = "UNIFIED_CODEX_RECOVERY_ENABLED"
UNIFIED_RECOVERY_BIN_ENV = "UNIFIED_CODEX_BIN"
UNIFIED_RECOVERY_PROXY_ENV = "UNIFIED_CODEX_PROXY_URL"
UNIFIED_RECOVERY_PROMPT_ENV = "UNIFIED_CODEX_RECOVERY_PROMPT"
UNIFIED_RECOVERY_COOLDOWN_ENV = "UNIFIED_CODEX_RECOVERY_COOLDOWN_SEC"
UNIFIED_RECOVERY_TIMEOUT_ENV = "UNIFIED_CODEX_RECOVERY_TIMEOUT_SEC"
RECOVERY_ENABLED_ENV = "RE9_CODEX_RECOVERY_ENABLED"
RECOVERY_BIN_ENV = "RE9_CODEX_BIN"
RECOVERY_PROXY_ENV = "RE9_CODEX_PROXY_URL"
RECOVERY_PROMPT_ENV = "RE9_CODEX_RECOVERY_PROMPT"
RECOVERY_COOLDOWN_ENV = "RE9_CODEX_RECOVERY_COOLDOWN_SEC"
RECOVERY_TIMEOUT_ENV = "RE9_CODEX_RECOVERY_TIMEOUT_SEC"
DEFAULT_RECOVERY_PROMPT = "请修复问题并且重新开始采集"
_MAX_MESSAGE_LENGTH = 8_000
_MAX_FIELD_LENGTH = 2_000


def _repair_config(raw: dict[str, object]) -> dict[str, object]:
    automation = raw.get("automation")
    if not isinstance(automation, dict):
        return {}
    recovery = automation.get("codex_recovery")
    return dict(recovery) if isinstance(recovery, dict) else {}


def _environment_value(primary: str, legacy: str) -> str | None:
    value = os.environ.get(primary)
    return value if value is not None else os.environ.get(legacy)


def _bool_setting(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _float_setting(value: object, default: float, *, minimum: float) -> float:
    try:
        return max(minimum, float(value))
    except (TypeError, ValueError):
        return default


def _truncate(value: object, limit: int) -> str:
    text = str(value)
    return text if len(text) <= limit else f"{text[: max(0, limit - 1)]}…"


def _read_state(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_private_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        temporary.write_text(
            json.dumps(dict(payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _cooldown_active(state_path: Path, cooldown_sec: float) -> bool:
    state = _read_state(state_path)
    try:
        started_at = float(state.get("started_at_epoch") or 0.0)
    except (TypeError, ValueError):
        return False
    return started_at > 0 and time.time() - started_at < cooldown_sec


@dataclass
class CodexRecoveryTrigger:
    """Opt-in, detached Codex repair worker compatible with RE9 settings."""

    configured_enabled: bool = False
    codex_bin: str = ""
    proxy_url: str = ""
    base_prompt: str = DEFAULT_RECOVERY_PROMPT
    cooldown_sec: float = 900.0
    timeout_sec: float = 3_600.0
    state_dir: Path = LOGS_DIR / "codex_recovery"
    log_path: Path = LOGS_DIR / "codex_recovery.log"
    source: str = "disabled"

    @classmethod
    def from_config(cls, config: SharedConfig) -> CodexRecoveryTrigger:
        settings = _repair_config(config.raw)
        enabled_from_env = _environment_value(
            UNIFIED_RECOVERY_ENABLED_ENV, RECOVERY_ENABLED_ENV
        )
        if enabled_from_env is None:
            configured_enabled = _bool_setting(settings.get("enabled"), False)
            source = "config" if configured_enabled else "disabled"
        else:
            configured_enabled = _bool_setting(enabled_from_env, False)
            source = "environment" if configured_enabled else "disabled"

        codex_bin = _environment_value(UNIFIED_RECOVERY_BIN_ENV, RECOVERY_BIN_ENV)
        if codex_bin is None:
            codex_bin = str(settings.get("codex_bin") or "")
        codex_bin = codex_bin.strip() or str(shutil.which("codex") or "")
        proxy_url = _environment_value(UNIFIED_RECOVERY_PROXY_ENV, RECOVERY_PROXY_ENV)
        if proxy_url is None:
            proxy_url = str(settings.get("proxy_url") or "")
        base_prompt = _environment_value(UNIFIED_RECOVERY_PROMPT_ENV, RECOVERY_PROMPT_ENV)
        if base_prompt is None:
            base_prompt = str(settings.get("prompt") or DEFAULT_RECOVERY_PROMPT)
        cooldown_value: object = _environment_value(
            UNIFIED_RECOVERY_COOLDOWN_ENV, RECOVERY_COOLDOWN_ENV
        )
        if cooldown_value is None:
            cooldown_value = settings.get("cooldown_sec", 900.0)
        timeout_value: object = _environment_value(
            UNIFIED_RECOVERY_TIMEOUT_ENV, RECOVERY_TIMEOUT_ENV
        )
        if timeout_value is None:
            timeout_value = settings.get("timeout_sec", 3_600.0)

        return cls(
            configured_enabled=configured_enabled,
            codex_bin=codex_bin,
            proxy_url=proxy_url.strip(),
            base_prompt=base_prompt.strip() or DEFAULT_RECOVERY_PROMPT,
            cooldown_sec=_float_setting(cooldown_value, 900.0, minimum=60.0),
            timeout_sec=_float_setting(timeout_value, 3_600.0, minimum=300.0),
            source=source,
        )

    @property
    def enabled(self) -> bool:
        return self.configured_enabled and bool(self.codex_bin)

    @property
    def state_path(self) -> Path:
        return self.state_dir / "bmw_codex_recovery_state.json"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "bmw_codex_recovery.lock"

    @property
    def status_text(self) -> str:
        if not self.configured_enabled:
            return (
                f"自动修复 / Recovery：未启用（设置 {UNIFIED_RECOVERY_ENABLED_ENV}=1；"
                f"兼容 {RECOVERY_ENABLED_ENV}）"
            )
        if not self.codex_bin:
            return "自动修复 / Recovery：不可用 / unavailable（Codex CLI not found）"
        return (
            f"自动修复 / Recovery：已启用 / enabled（{self.source}，"
            f"冷却 / cooldown {int(round(self.cooldown_sec / 60))} min）"
        )

    def trigger(
        self,
        title: str,
        message: str,
        *,
        fields: Mapping[str, object] | None = None,
    ) -> bool:
        """Queue one detached repair worker without blocking the UI."""

        if not self.enabled or _cooldown_active(self.state_path, self.cooldown_sec):
            return False
        self.state_dir.mkdir(parents=True, exist_ok=True)
        request_path = self.state_dir / (
            f"bmw_recovery_request_{os.getpid()}_{time.time_ns()}.json"
        )
        request = {
            "codex_bin": self.codex_bin,
            "proxy_url": self.proxy_url,
            "base_prompt": self.base_prompt,
            "cooldown_sec": self.cooldown_sec,
            "timeout_sec": self.timeout_sec,
            "project_root": str(PROJECT_ROOT),
            "log_path": str(self.log_path),
            "state_path": str(self.state_path),
            "lock_path": str(self.lock_path),
            "title": _truncate(title, 1_000),
            "message": _truncate(message, _MAX_MESSAGE_LENGTH),
            "fields": {
                _truncate(name, 200): _truncate(value, _MAX_FIELD_LENGTH)
                for name, value in (fields or {}).items()
            },
            "requested_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        _write_private_json(request_path, request)
        env = os.environ.copy()
        source_root = str(PROJECT_ROOT / "src")
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            source_root
            if not existing_pythonpath
            else f"{source_root}{os.pathsep}{existing_pythonpath}"
        )
        try:
            subprocess.Popen(
                [sys.executable, "-m", "bmw_capture_studio.repair", "--worker", str(request_path)],
                cwd=PROJECT_ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **detached_process_kwargs(hide_console=True),
            )
        except OSError:
            request_path.unlink(missing_ok=True)
            return False
        return True


def _try_lock(handle: IO[str]) -> bool:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        handle.write("0")
        handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _build_recovery_prompt(request: Mapping[str, object]) -> str:
    fields = request.get("fields")
    field_lines = []
    if isinstance(fields, dict):
        field_lines = [
            f"- {name}: {value}"
            for name, value in fields.items()
            if value is not None and str(value) != ""
        ]
    context = "\n".join(field_lines) or "- no additional fields"
    return (
        f"{request.get('base_prompt') or DEFAULT_RECOVERY_PROMPT}\n\n"
        "这是统一游戏相机采集器自动发出的错误恢复任务。\n"
        f"错误标题：{request.get('title') or 'Unified camera capture failure'}\n"
        f"错误内容：{request.get('message') or '-'}\n"
        f"上下文：\n{context}\n\n"
        "请持续处理直到满足以下条件：\n"
        "1. 先检查当前游戏适配器、统一采集器源码、最新错误日志、轨迹/静态采集清单和 OBS 状态，定位根因。\n"
        "2. 先做离线检查和最小可靠修复；不要启动游戏或自动采集，除非用户明确授权。\n"
        "3. 保留所有已完成数据，从第一个缺失索引安全恢复；不要覆盖有效视频或图片。\n"
        "4. 运行相关编译和单元测试，并在日志中记录修复结果。\n"
        "5. 不要输出、提交或上传 configs/*.local.yaml、Webhook、签名密钥、GitHub token、日志或数据集。\n"
        "6. 不要强推 Git，不要删除用户数据；除非修复本身需要，否则不要扩大改动范围。\n"
    )


def _codex_command(request: Mapping[str, object]) -> list[str]:
    return [
        str(request["codex_bin"]),
        "--sandbox",
        "danger-full-access",
        "--ask-for-approval",
        "never",
        "-c",
        "features.apps=false",
        "exec",
        "--cd",
        str(request["project_root"]),
        "--color",
        "never",
        "-",
    ]


def _codex_environment(request: Mapping[str, object]) -> dict[str, str]:
    environment = os.environ.copy()
    proxy_url = str(request.get("proxy_url") or "").strip()
    if proxy_url:
        environment.pop("ALL_PROXY", None)
        environment.pop("all_proxy", None)
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            environment[name] = proxy_url
    return environment


def _terminate_process(process: subprocess.Popen[str]) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=10)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except OSError:
        pass


def _worker(request_path: Path) -> int:
    request = _read_state(request_path)
    if not request:
        return 2
    state_path = Path(str(request["state_path"]))
    lock_path = Path(str(request["lock_path"]))
    log_path = Path(str(request["log_path"]))
    cooldown_sec = float(request.get("cooldown_sec") or 900.0)
    timeout_sec = float(request.get("timeout_sec") or 3_600.0)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = lock_path.open("a+", encoding="utf-8")
    try:
        os.chmod(lock_path, 0o600)
    except OSError:
        pass
    if not _try_lock(lock_handle):
        request_path.unlink(missing_ok=True)
        lock_handle.close()
        return 0
    try:
        if _cooldown_active(state_path, cooldown_sec):
            return 0
        started_at = time.time()
        _write_private_json(
            state_path,
            {
                "status": "running",
                "started_at_epoch": started_at,
                "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "title": request.get("title") or "",
                "worker_pid": os.getpid(),
            },
        )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log_handle:
            try:
                os.chmod(log_path, 0o600)
            except OSError:
                pass
            log_handle.write(
                f"\n{datetime.now().astimezone().isoformat(timespec='seconds')} "
                f"Unified camera recovery started: {request.get('title') or '-'}\n"
            )
            log_handle.flush()
            process = subprocess.Popen(
                _codex_command(request),
                cwd=str(request["project_root"]),
                env=_codex_environment(request),
                stdin=subprocess.PIPE,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                **detached_process_kwargs(hide_console=True),
            )
            timed_out = False
            try:
                process.communicate(input=_build_recovery_prompt(request), timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_process(process)
        return_code = process.returncode
        finished_at = time.time()
        _write_private_json(
            state_path,
            {
                "status": "timeout" if timed_out else ("completed" if return_code == 0 else "failed"),
                "started_at_epoch": started_at,
                "finished_at_epoch": finished_at,
                "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "title": request.get("title") or "",
                "return_code": return_code,
                "timed_out": timed_out,
                "log_path": str(log_path),
            },
        )
        return 0 if not timed_out and return_code == 0 else 1
    finally:
        request_path.unlink(missing_ok=True)
        lock_handle.close()


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        return _worker(Path(sys.argv[2]))
    print("This module is started by the opt-in repair worker.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
