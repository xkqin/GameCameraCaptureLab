from __future__ import annotations

import os
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import requests

from .config import AppConfig
from .paths import ensure_dir


DISCORD_WEBHOOK_ENV = "RE9_DISCORD_WEBHOOK_URL"
DISCORD_MENTION_ENV = "RE9_DISCORD_MENTION"
DISCORD_USERNAME_ENV = "RE9_DISCORD_USERNAME"
DISCORD_TIMEOUT_ENV = "RE9_DISCORD_TIMEOUT_SEC"

_MAX_TITLE_LENGTH = 256
_MAX_DESCRIPTION_LENGTH = 3_800
_MAX_FIELD_NAME_LENGTH = 256
_MAX_FIELD_VALUE_LENGTH = 1_024
_MAX_FIELDS = 25


def _truncate(value: object, limit: int) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}…"


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


@dataclass
class DiscordNotifier:
    webhook_url: str = ""
    mention: str = ""
    username: str = "RE9 Capture Monitor"
    timeout_sec: float = 5.0
    log_path: Path | None = None
    source: str = "disabled"

    @classmethod
    def from_config(cls, config: AppConfig) -> DiscordNotifier:
        settings = _discord_config(config.raw)

        webhook_from_env = os.environ.get(DISCORD_WEBHOOK_ENV)
        if webhook_from_env is None:
            webhook_url = str(settings.get("webhook_url") or "").strip()
            source = "config" if webhook_url else "disabled"
        else:
            webhook_url = webhook_from_env.strip()
            source = "environment" if webhook_url else "disabled"

        mention = os.environ.get(DISCORD_MENTION_ENV)
        if mention is None:
            mention = str(settings.get("mention") or "")

        username = os.environ.get(DISCORD_USERNAME_ENV)
        if username is None:
            username = str(settings.get("username") or "RE9 Capture Monitor")

        timeout_value: object = os.environ.get(DISCORD_TIMEOUT_ENV)
        if timeout_value is None:
            timeout_value = settings.get("timeout_sec", 5.0)

        return cls(
            webhook_url=webhook_url,
            mention=mention.strip(),
            username=_truncate(username.strip() or "RE9 Capture Monitor", 80),
            timeout_sec=_float_setting(timeout_value, 5.0),
            log_path=config.output_dir / "discord_notifications.log",
            source=source,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    @property
    def status_text(self) -> str:
        if not self.enabled:
            return f"Discord error alerts: disabled (set {DISCORD_WEBHOOK_ENV})"
        return f"Discord error alerts: enabled via {self.source}"

    def notify_error(
        self,
        title: str,
        message: str,
        *,
        fields: Mapping[str, object] | None = None,
    ) -> bool:
        """Queue an error notification without blocking the capture/UI thread."""
        if not self.enabled:
            return False
        thread = threading.Thread(
            target=self.send_error,
            args=(title, message),
            kwargs={"fields": fields},
            name="re9-discord-notifier",
            daemon=True,
        )
        thread.start()
        return True

    def send_error(
        self,
        title: str,
        message: str,
        *,
        fields: Mapping[str, object] | None = None,
    ) -> bool:
        """Send immediately. Intended for tests and the UI's test-alert worker."""
        if not self.enabled:
            return False

        payload = self._payload(title, message, fields=fields)
        last_error_name = "unknown error"
        for attempt in range(3):
            try:
                response = requests.post(
                    self.webhook_url,
                    json=payload,
                    timeout=self.timeout_sec,
                )
                response.raise_for_status()
                return True
            except requests.RequestException as exc:
                # Exception strings can contain the webhook token in the URL.
                # Record only the exception class so the secret never reaches logs.
                last_error_name = type(exc).__name__
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))

        self._log_failure(last_error_name)
        return False

    def _payload(
        self,
        title: str,
        message: str,
        *,
        fields: Mapping[str, object] | None,
    ) -> dict[str, object]:
        embed_fields = []
        for name, value in list((fields or {}).items())[:_MAX_FIELDS]:
            if value is None or str(value) == "":
                continue
            embed_fields.append(
                {
                    "name": _truncate(name, _MAX_FIELD_NAME_LENGTH),
                    "value": _truncate(value, _MAX_FIELD_VALUE_LENGTH),
                    "inline": False,
                }
            )

        embed: dict[str, object] = {
            "title": _truncate(title, _MAX_TITLE_LENGTH),
            "description": _truncate(message, _MAX_DESCRIPTION_LENGTH),
            "color": 0xE74C3C,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": _truncate(f"Host: {socket.gethostname()}", 2_048)},
        }
        if embed_fields:
            embed["fields"] = embed_fields

        payload: dict[str, object] = {
            "username": self.username,
            "embeds": [embed],
        }
        if self.mention:
            payload["content"] = _truncate(self.mention, 2_000)
        return payload

    def _log_failure(self, error_name: str) -> None:
        if self.log_path is None:
            return
        try:
            log_path = ensure_dir(self.log_path.parent) / self.log_path.name
            timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{timestamp} Discord notification failed: {error_name}\n")
        except OSError:
            # Notifications are best effort and must never take down capture.
            return
