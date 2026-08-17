from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping

import requests

from .config import AppConfig
from .paths import ensure_dir


FEISHU_WEBHOOK_ENV = "RE9_FEISHU_WEBHOOK_URL"
FEISHU_SECRET_ENV = "RE9_FEISHU_SECRET"
FEISHU_MENTION_OPEN_ID_ENV = "RE9_FEISHU_MENTION_OPEN_ID"
FEISHU_TIMEOUT_ENV = "RE9_FEISHU_TIMEOUT_SEC"

_MAX_PAYLOAD_BYTES = 20_000
_MAX_TEXT_BYTES = 18_000
_MAX_FIELDS = 25
_OPEN_ID_PATTERN = re.compile(r"^(?:all|ou_[A-Za-z0-9_-]+)$")


class FeishuResponseError(Exception):
    """A successful HTTP request rejected by the Feishu bot API."""


def _feishu_config(raw: dict[str, object]) -> dict[str, object]:
    notifications = raw.get("notifications")
    if not isinstance(notifications, dict):
        return {}
    feishu = notifications.get("feishu")
    return dict(feishu) if isinstance(feishu, dict) else {}


def _float_setting(value: object, default: float) -> float:
    try:
        return max(0.5, float(value))
    except (TypeError, ValueError):
        return default


def _truncate_utf8(value: object, max_bytes: int) -> str:
    text = str(value)
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    suffix = "…".encode("utf-8")
    return encoded[: max(0, max_bytes - len(suffix))].decode(
        "utf-8",
        errors="ignore",
    ) + "…"


@dataclass
class FeishuNotifier:
    webhook_url: str = ""
    secret: str = ""
    mention_open_id: str = ""
    timeout_sec: float = 5.0
    log_path: Path | None = None
    source: str = "disabled"

    @classmethod
    def from_config(cls, config: AppConfig) -> FeishuNotifier:
        settings = _feishu_config(config.raw)

        webhook_from_env = os.environ.get(FEISHU_WEBHOOK_ENV)
        if webhook_from_env is None:
            webhook_url = str(settings.get("webhook_url") or "").strip()
            source = "config" if webhook_url else "disabled"
        else:
            webhook_url = webhook_from_env.strip()
            source = "environment" if webhook_url else "disabled"

        secret = os.environ.get(FEISHU_SECRET_ENV)
        if secret is None:
            secret = str(settings.get("secret") or "")

        mention_open_id = os.environ.get(FEISHU_MENTION_OPEN_ID_ENV)
        if mention_open_id is None:
            mention_open_id = str(settings.get("mention_open_id") or "")

        timeout_value: object = os.environ.get(FEISHU_TIMEOUT_ENV)
        if timeout_value is None:
            timeout_value = settings.get("timeout_sec", 5.0)

        return cls(
            webhook_url=webhook_url,
            secret=secret.strip(),
            mention_open_id=mention_open_id.strip(),
            timeout_sec=_float_setting(timeout_value, 5.0),
            log_path=config.output_dir / "feishu_notifications.log",
            source=source,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    @property
    def status_text(self) -> str:
        if not self.enabled:
            return f"Feishu error alerts: disabled (set {FEISHU_WEBHOOK_ENV})"
        signature = ", signed" if self.secret else ""
        return f"Feishu error alerts: enabled via {self.source}{signature}"

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
            name="re9-feishu-notifier",
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

        last_error_name = "unknown error"
        for attempt in range(3):
            try:
                payload = self._payload(title, message, fields=fields)
                response = requests.post(
                    self.webhook_url,
                    json=payload,
                    timeout=self.timeout_sec,
                )
                response.raise_for_status()
                self._validate_response(response)
                return True
            except (
                requests.RequestException,
                FeishuResponseError,
                TypeError,
                ValueError,
            ) as exc:
                # Exception strings can contain the webhook or signing secret.
                # Record only the exception class so secrets never reach logs.
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
        lines = [
            f"[RE9 ERROR] {title}",
            str(message),
        ]
        for name, value in list((fields or {}).items())[:_MAX_FIELDS]:
            if value is None or str(value) == "":
                continue
            lines.append(f"{name}: {value}")
        lines.extend(
            [
                f"Host: {socket.gethostname()}",
                f"Time: {datetime.now().astimezone().isoformat(timespec='seconds')}",
            ]
        )

        mention = self._mention_tag()
        if mention:
            lines.insert(0, mention)
        text = _truncate_utf8("\n".join(lines), _MAX_TEXT_BYTES)

        payload: dict[str, object] = {
            "msg_type": "text",
            "content": {"text": text},
        }
        if self.secret:
            timestamp = str(int(time.time()))
            string_to_sign = f"{timestamp}\n{self.secret}"
            signature = hmac.new(
                string_to_sign.encode("utf-8"),
                digestmod=hashlib.sha256,
            ).digest()
            payload["timestamp"] = timestamp
            payload["sign"] = base64.b64encode(signature).decode("ascii")

        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > _MAX_PAYLOAD_BYTES:
            raise ValueError("Feishu payload exceeds its maximum size")
        return payload

    def _mention_tag(self) -> str:
        if not self.mention_open_id or not _OPEN_ID_PATTERN.fullmatch(
            self.mention_open_id
        ):
            return ""
        if self.mention_open_id == "all":
            return '<at user_id="all">所有人</at>'
        return f'<at user_id="{self.mention_open_id}">用户</at>'

    @staticmethod
    def _validate_response(response: requests.Response) -> None:
        result = response.json()
        if not isinstance(result, dict):
            raise FeishuResponseError("Unexpected Feishu response")

        code = result.get("code")
        if code is None:
            code = result.get("StatusCode")
        if code is None:
            code = result.get("status_code")
        if code is None or int(code) != 0:
            raise FeishuResponseError("Feishu rejected the notification")

    def _log_failure(self, error_name: str) -> None:
        if self.log_path is None:
            return
        try:
            log_path = ensure_dir(self.log_path.parent) / self.log_path.name
            timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{timestamp} Feishu notification failed: {error_name}\n")
        except OSError:
            # Notifications are best effort and must never take down capture.
            return
