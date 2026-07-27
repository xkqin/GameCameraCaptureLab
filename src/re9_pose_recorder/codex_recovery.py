from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Mapping

from .config import AppConfig
from .paths import PROJECT_ROOT, ensure_dir


CODEX_RECOVERY_ENABLED_ENV = "RE9_CODEX_RECOVERY_ENABLED"
CODEX_RECOVERY_BIN_ENV = "RE9_CODEX_BIN"
CODEX_RECOVERY_PROMPT_ENV = "RE9_CODEX_RECOVERY_PROMPT"
CODEX_RECOVERY_COOLDOWN_ENV = "RE9_CODEX_RECOVERY_COOLDOWN_SEC"
CODEX_RECOVERY_TIMEOUT_ENV = "RE9_CODEX_RECOVERY_TIMEOUT_SEC"

DEFAULT_RECOVERY_PROMPT = "请修复问题并且重新开始采集"
_MAX_MESSAGE_LENGTH = 8_000
_MAX_FIELD_LENGTH = 2_000


def _recovery_config(raw: dict[str, object]) -> dict[str, object]:
    automation = raw.get("automation")
    if not isinstance(automation, dict):
        return {}
    recovery = automation.get("codex_recovery")
    return dict(recovery) if isinstance(recovery, dict) else {}


def _bool_setting(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _float_setting(
    value: object,
    default: float,
    *,
    minimum: float,
) -> float:
    try:
        return max(minimum, float(value))
    except (TypeError, ValueError):
        return default


def _truncate(value: object, limit: int) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}…"


def _read_state(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_private_json(path: Path, payload: Mapping[str, object]) -> None:
    ensure_dir(path.parent)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        temporary.write_text(
            json.dumps(dict(payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
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
    configured_enabled: bool = False
    codex_bin: str = ""
    base_prompt: str = DEFAULT_RECOVERY_PROMPT
    cooldown_sec: float = 900.0
    timeout_sec: float = 3_600.0
    state_dir: Path = PROJECT_ROOT / "runtime"
    log_path: Path = PROJECT_ROOT / "outputs" / "codex_recovery.log"
    source: str = "disabled"

    @classmethod
    def from_config(cls, config: AppConfig) -> CodexRecoveryTrigger:
        settings = _recovery_config(config.raw)

        enabled_from_env = os.environ.get(CODEX_RECOVERY_ENABLED_ENV)
        if enabled_from_env is None:
            configured_enabled = _bool_setting(settings.get("enabled"), False)
            source = "config" if configured_enabled else "disabled"
        else:
            configured_enabled = _bool_setting(enabled_from_env, False)
            source = "environment" if configured_enabled else "disabled"

        codex_bin = os.environ.get(CODEX_RECOVERY_BIN_ENV)
        if codex_bin is None:
            codex_bin = str(settings.get("codex_bin") or "")
        codex_bin = codex_bin.strip() or str(shutil.which("codex") or "")

        base_prompt = os.environ.get(CODEX_RECOVERY_PROMPT_ENV)
        if base_prompt is None:
            base_prompt = str(settings.get("prompt") or DEFAULT_RECOVERY_PROMPT)

        cooldown_value: object = os.environ.get(CODEX_RECOVERY_COOLDOWN_ENV)
        if cooldown_value is None:
            cooldown_value = settings.get("cooldown_sec", 900.0)

        timeout_value: object = os.environ.get(CODEX_RECOVERY_TIMEOUT_ENV)
        if timeout_value is None:
            timeout_value = settings.get("timeout_sec", 3_600.0)

        return cls(
            configured_enabled=configured_enabled,
            codex_bin=codex_bin,
            base_prompt=base_prompt.strip() or DEFAULT_RECOVERY_PROMPT,
            cooldown_sec=_float_setting(cooldown_value, 900.0, minimum=60.0),
            timeout_sec=_float_setting(timeout_value, 3_600.0, minimum=300.0),
            state_dir=PROJECT_ROOT / "runtime",
            log_path=config.output_dir / "codex_recovery.log",
            source=source,
        )

    @property
    def enabled(self) -> bool:
        return self.configured_enabled and bool(self.codex_bin)

    @property
    def state_path(self) -> Path:
        return self.state_dir / "re9_pose_codex_recovery_state.json"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "re9_pose_codex_recovery.lock"

    @property
    def status_text(self) -> str:
        if not self.configured_enabled:
            return (
                "Codex auto recovery: disabled "
                f"(set {CODEX_RECOVERY_ENABLED_ENV}=1)"
            )
        if not self.codex_bin:
            return "Codex auto recovery: unavailable (codex CLI not found)"
        minutes = int(round(self.cooldown_sec / 60.0))
        return (
            f"Codex auto recovery: enabled via {self.source}; "
            f"cooldown {minutes} min"
        )

    def trigger(
        self,
        title: str,
        message: str,
        *,
        fields: Mapping[str, object] | None = None,
    ) -> bool:
        """Queue one detached recovery worker without blocking the GUI."""
        if not self.enabled or _cooldown_active(self.state_path, self.cooldown_sec):
            return False

        ensure_dir(self.state_dir)
        request_path = self.state_dir / (
            f"re9_pose_codex_recovery_request_{os.getpid()}_{time.time_ns()}.json"
        )
        request = {
            "codex_bin": self.codex_bin,
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
            "requested_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
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
                [
                    sys.executable,
                    "-m",
                    "re9_pose_recorder.codex_recovery",
                    "--worker",
                    str(request_path),
                ],
                cwd=PROJECT_ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
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
        "这是 RE9_Still_Scan 无人值守采集程序自动发出的最终错误恢复任务。\n"
        f"错误标题：{request.get('title') or 'RE9 capture failure'}\n"
        f"错误内容：{request.get('message') or '-'}\n"
        f"上下文：\n{context}\n\n"
        "请持续处理直到满足以下全部条件：\n"
        "1. 检查 trajectory_run_state.json、最新错误日志、Lua 状态、OBS 和 GPU 状态，定位根因。\n"
        "2. 做最小且可靠的代码或运行态修复，并补充/运行相关测试。\n"
        "3. 保留所有已完成轨迹，从第一个缺失索引安全重启采集；不要覆盖有效视频。\n"
        "4. 验证至少一条新轨迹完整落盘、状态继续增长且错误日志不再更新。\n"
        "5. 保持每 30 条重启 OBS、Discord/飞书告警和 @全体配置有效。\n"
        "6. 不要输出、提交或上传 configs/linux.local.yaml、Webhook、签名密钥、GitHub token、日志或数据集。\n"
        "7. 不要强推 Git，不要删除用户数据；除非修复本身需要，否则不要扩大改动范围。\n"
    )


def _codex_command(request: Mapping[str, object]) -> list[str]:
    return [
        str(request["codex_bin"]),
        "--ask-for-approval",
        "never",
        "--sandbox",
        "danger-full-access",
        "exec",
        "--cd",
        str(request["project_root"]),
        "--color",
        "never",
        "-",
    ]


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

    lock_path = Path(str(request["lock_path"]))
    state_path = Path(str(request["state_path"]))
    log_path = Path(str(request["log_path"]))
    cooldown_sec = float(request.get("cooldown_sec") or 900.0)
    timeout_sec = float(request.get("timeout_sec") or 3_600.0)

    ensure_dir(lock_path.parent)
    lock_handle = lock_path.open("a+", encoding="utf-8")
    os.chmod(lock_path, 0o600)
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
                "started_at": datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
                "title": request.get("title") or "",
                "worker_pid": os.getpid(),
            },
        )

        ensure_dir(log_path.parent)
        with log_path.open("a", encoding="utf-8") as log_handle:
            os.chmod(log_path, 0o600)
            log_handle.write(
                "\n"
                f"{datetime.now().astimezone().isoformat(timespec='seconds')} "
                f"Codex recovery started: {request.get('title') or '-'}\n"
            )
            log_handle.flush()
            process = subprocess.Popen(
                _codex_command(request),
                cwd=str(request["project_root"]),
                env=os.environ.copy(),
                stdin=subprocess.PIPE,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            timed_out = False
            try:
                process.communicate(
                    input=_build_recovery_prompt(request),
                    timeout=timeout_sec,
                )
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_process(process)

        finished_at = time.time()
        return_code = process.returncode
        status = "timed_out" if timed_out else (
            "completed" if return_code == 0 else "failed"
        )
        _write_private_json(
            state_path,
            {
                "status": status,
                "started_at_epoch": started_at,
                "finished_at_epoch": finished_at,
                "finished_at": datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
                "return_code": return_code,
                "title": request.get("title") or "",
            },
        )
        return 0 if status == "completed" else 1
    except Exception as exc:
        _write_private_json(
            state_path,
            {
                "status": "worker_failed",
                "started_at_epoch": time.time(),
                "finished_at": datetime.now().astimezone().isoformat(
                    timespec="seconds"
                ),
                "error_type": type(exc).__name__,
            },
        )
        return 1
    finally:
        request_path.unlink(missing_ok=True)
        lock_handle.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path, required=True)
    args = parser.parse_args()
    return _worker(args.worker)


if __name__ == "__main__":
    raise SystemExit(main())
