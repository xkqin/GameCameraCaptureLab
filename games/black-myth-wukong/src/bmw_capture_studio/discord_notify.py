from __future__ import annotations

import os
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

try:
    import requests
except ImportError:  # pragma: no cover - packaged installs include requests
    requests = None  # type: ignore[assignment]

from .config import SharedConfig
from .paths import LOGS_DIR


DISCORD_WEBHOOK_ENV = "RE9_DISCORD_WEBHOOK_URL"
DISCORD_MENTION_ENV = "RE9_DISCORD_MENTION"
DISCORD_USERNAME_ENV = "RE9_DISCORD_USERNAME"
DISCORD_TIMEOUT_ENV = "RE9_DISCORD_TIMEOUT_SEC"
UNIFIED_DISCORD_WEBHOOK_ENV = "UNIFIED_DISCORD_WEBHOOK_URL"
UNIFIED_DISCORD_MENTION_ENV = "UNIFIED_DISCORD_MENTION"
UNIFIED_DISCORD_USERNAME_ENV = "UNIFIED_DISCORD_USERNAME"
UNIFIED_DISCORD_TIMEOUT_ENV = "UNIFIED_DISCORD_TIMEOUT_SEC"

_MAX_FIELDS = 25
_REQUEST_EXCEPTION = requests.RequestException if requests is not None else RuntimeError


def _discord_config(raw: dict[str, object]) -> dict[str, object]:
    notifications = raw.get("notifications")
    if not isinstance(notifications, dict):
        return {}
    discord = notifications.get("discord")
    return dict(discord) if isinstance(discord, dict) else {}


def _float_setting(value: object, default: float) -> float:
    try:
        return max(0.5, float(value))
    except (TypeError, ValueError):
        return default


def _truncate(value: object, limit: int) -> str:
    text = str(value)
    return text if len(text) <= limit else f"{text[: max(0, limit - 1)]}…"


def _environment_value(primary: str, legacy: str) -> str | None:
    return os.environ.get(primary) if primary in os.environ else os.environ.get(legacy)


@dataclass
class DiscordNotifier:
    webhook_url: str = ""
    mention: str = ""
    username: str = "Unified Camera Capture"
    timeout_sec: float = 5.0
    log_path: Path | None = None
    source: str = "disabled"

    @classmethod
    def from_config(cls, config: SharedConfig) -> DiscordNotifier:
        settings = _discord_config(config.raw)
        webhook_from_env = _environment_value(
            UNIFIED_DISCORD_WEBHOOK_ENV, DISCORD_WEBHOOK_ENV
        )
        if webhook_from_env is None:
            webhook_url = str(settings.get("webhook_url") or "").strip()
            source = "config" if webhook_url else "disabled"
        else:
            webhook_url = webhook_from_env.strip()
            source = "environment" if webhook_url else "disabled"
        mention = _environment_value(UNIFIED_DISCORD_MENTION_ENV, DISCORD_MENTION_ENV)
        if mention is None:
            mention = str(settings.get("mention") or "")
        username = _environment_value(UNIFIED_DISCORD_USERNAME_ENV, DISCORD_USERNAME_ENV)
        if username is None:
            username = str(settings.get("username") or "Unified Camera Capture")
        timeout: object = _environment_value(
            UNIFIED_DISCORD_TIMEOUT_ENV, DISCORD_TIMEOUT_ENV
        )
        if timeout is None:
            timeout = settings.get("timeout_sec", 5.0)
        return cls(
            webhook_url=webhook_url,
            mention=mention.strip(),
            username=_truncate(username.strip() or "Unified Camera Capture", 80),
            timeout_sec=_float_setting(timeout, 5.0),
            log_path=LOGS_DIR / "discord_notifications.log",
            source=source,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    @property
    def status_text(self) -> str:
        if not self.enabled:
            return f"Discord：未启用 / Disabled（{UNIFIED_DISCORD_WEBHOOK_ENV}）"
        if requests is None:
            return "Discord：不可用 / Unavailable（requests missing）"
        return f"Discord：已启用 / Enabled（{self.source}）"

    def notify_error(
        self,
        title: str,
        message: str,
        *,
        fields: Mapping[str, object] | None = None,
    ) -> bool:
        if not self.enabled or requests is None:
            return False
        threading.Thread(
            target=self.send_error,
            args=(title, message),
            kwargs={"fields": fields},
            name="unified-discord-notifier",
            daemon=True,
        ).start()
        return True

    def send_error(
        self,
        title: str,
        message: str,
        *,
        fields: Mapping[str, object] | None = None,
    ) -> bool:
        if not self.enabled or requests is None:
            return False
        payload = self._payload(title, message, fields=fields)
        last_error = "unknown error"
        for attempt in range(3):
            try:
                response = requests.post(
                    self.webhook_url,
                    json=payload,
                    timeout=self.timeout_sec,
                )
                response.raise_for_status()
                return True
            except _REQUEST_EXCEPTION as exc:
                # Never persist exception text: it can contain the webhook token.
                last_error = type(exc).__name__
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        self._log_failure(last_error)
        return False

    def _payload(
        self,
        title: str,
        message: str,
        *,
        fields: Mapping[str, object] | None,
    ) -> dict[str, object]:
        embed_fields = [
            {
                "name": _truncate(name, 256),
                "value": _truncate(value, 1024),
                "inline": False,
            }
            for name, value in list((fields or {}).items())[:_MAX_FIELDS]
            if value is not None and str(value) != ""
        ]
        embed: dict[str, object] = {
            "title": _truncate(title, 256),
            "description": _truncate(message, 3800),
            "color": 0xE74C3C,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": _truncate(f"Host: {socket.gethostname()}", 2048)},
        }
        if embed_fields:
            embed["fields"] = embed_fields
        payload: dict[str, object] = {"username": self.username, "embeds": [embed]}
        if self.mention:
            payload["content"] = _truncate(self.mention, 2000)
        return payload

    def _log_failure(self, error_name: str) -> None:
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    f"{datetime.now().astimezone().isoformat(timespec='seconds')} "
                    f"Discord notification failed: {error_name}\n"
                )
        except OSError:
            return
