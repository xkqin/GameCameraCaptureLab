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

try:
    import requests
except ImportError:  # pragma: no cover - packaged installs include requests
    requests = None  # type: ignore[assignment]

from .config import SharedConfig
from .paths import LOGS_DIR


# These names intentionally match the existing RE9 configuration and
# environment variables so one local config can serve both adapters.
FEISHU_WEBHOOK_ENV = "RE9_FEISHU_WEBHOOK_URL"
FEISHU_SECRET_ENV = "RE9_FEISHU_SECRET"
FEISHU_MENTION_OPEN_ID_ENV = "RE9_FEISHU_MENTION_OPEN_ID"
FEISHU_TIMEOUT_ENV = "RE9_FEISHU_TIMEOUT_SEC"
UNIFIED_FEISHU_WEBHOOK_ENV = "UNIFIED_FEISHU_WEBHOOK_URL"
UNIFIED_FEISHU_SECRET_ENV = "UNIFIED_FEISHU_SECRET"
UNIFIED_FEISHU_MENTION_OPEN_ID_ENV = "UNIFIED_FEISHU_MENTION_OPEN_ID"
UNIFIED_FEISHU_TIMEOUT_ENV = "UNIFIED_FEISHU_TIMEOUT_SEC"

_MAX_PAYLOAD_BYTES = 20_000
_MAX_TEXT_BYTES = 18_000
_MAX_FIELDS = 25
_OPEN_ID_PATTERN = re.compile(r"^(?:all|ou_[A-Za-z0-9_-]+)$")


class FeishuResponseError(Exception):
    """A successful HTTP request rejected by the Feishu bot API."""


_REQUEST_EXCEPTION = (
    requests.RequestException if requests is not None else RuntimeError
)


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


def _environment_value(primary: str, legacy: str) -> str | None:
    return os.environ.get(primary) if primary in os.environ else os.environ.get(legacy)


def _truncate_utf8(value: object, max_bytes: int) -> str:
    text = str(value)
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    suffix = "…".encode("utf-8")
    return encoded[: max(0, max_bytes - len(suffix))].decode(
        "utf-8", errors="ignore"
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
    def from_config(cls, config: SharedConfig) -> FeishuNotifier:
        settings = _feishu_config(config.raw)

        webhook_from_env = _environment_value(
            UNIFIED_FEISHU_WEBHOOK_ENV, FEISHU_WEBHOOK_ENV
        )
        if webhook_from_env is None:
            webhook_url = str(settings.get("webhook_url") or "").strip()
            source = "config" if webhook_url else "disabled"
        else:
            webhook_url = webhook_from_env.strip()
            source = "environment" if webhook_url else "disabled"

        secret = _environment_value(UNIFIED_FEISHU_SECRET_ENV, FEISHU_SECRET_ENV)
        if secret is None:
            secret = str(settings.get("secret") or "")
        mention_open_id = _environment_value(
            UNIFIED_FEISHU_MENTION_OPEN_ID_ENV, FEISHU_MENTION_OPEN_ID_ENV
        )
        if mention_open_id is None:
            mention_open_id = str(settings.get("mention_open_id") or "")
        timeout_value: object = _environment_value(
            UNIFIED_FEISHU_TIMEOUT_ENV, FEISHU_TIMEOUT_ENV
        )
        if timeout_value is None:
            timeout_value = settings.get("timeout_sec", 5.0)

        return cls(
            webhook_url=webhook_url,
            secret=secret.strip(),
            mention_open_id=mention_open_id.strip(),
            timeout_sec=_float_setting(timeout_value, 5.0),
            log_path=LOGS_DIR / "feishu_notifications.log",
            source=source,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    @property
    def status_text(self) -> str:
        if not self.enabled:
            return f"飞书 / Feishu：未启用 / Disabled（{UNIFIED_FEISHU_WEBHOOK_ENV}）"
        if requests is None:
            return "飞书 / Feishu：不可用 / Unavailable（requests missing）"
        signature = "，签名已启用 / signed" if self.secret else ""
        return f"飞书 / Feishu：已启用 / Enabled（{self.source}{signature}）"

    def notify_error(
        self,
        title: str,
        message: str,
        *,
        fields: Mapping[str, object] | None = None,
    ) -> bool:
        """Queue an error notification without blocking the capture/UI thread."""

        if not self.enabled or requests is None:
            return False
        threading.Thread(
            target=self.send_error,
            args=(title, message),
            kwargs={"fields": fields},
            name="unified-feishu-notifier",
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
            if self.enabled and requests is None:
                self._log_failure("requests_not_installed")
            return False
        last_error_name = "unknown error"
        for attempt in range(3):
            try:
                response = requests.post(
                    self.webhook_url,
                    json=self._payload(title, message, fields=fields),
                    timeout=self.timeout_sec,
                )
                response.raise_for_status()
                self._validate_response(response)
                return True
            except (
                _REQUEST_EXCEPTION,
                FeishuResponseError,
                TypeError,
                ValueError,
            ) as exc:
                # Exception text can contain a webhook or signing secret.
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
        lines = [f"[UNIFIED CAMERA ERROR] {title}", str(message)]
        for name, value in list((fields or {}).items())[:_MAX_FIELDS]:
            if value is not None and str(value) != "":
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
            signature = hmac.new(
                f"{timestamp}\n{self.secret}".encode("utf-8"),
                digestmod=hashlib.sha256,
            ).digest()
            payload["timestamp"] = timestamp
            payload["sign"] = base64.b64encode(signature).decode("ascii")
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > _MAX_PAYLOAD_BYTES:
            raise ValueError("Feishu payload exceeds its maximum size")
        return payload

    def _mention_tag(self) -> str:
        if not _OPEN_ID_PATTERN.fullmatch(self.mention_open_id):
            return ""
        if self.mention_open_id == "all":
            return '<at user_id="all">所有人</at>'
        return f'<at user_id="{self.mention_open_id}">用户</at>'

    @staticmethod
    def _validate_response(response: requests.Response) -> None:
        result = response.json()
        if not isinstance(result, dict):
            raise FeishuResponseError("Unexpected Feishu response")
        code = result.get("code", result.get("StatusCode", result.get("status_code")))
        if code is None or int(code) != 0:
            raise FeishuResponseError("Feishu rejected the notification")

    def _log_failure(self, error_name: str) -> None:
        if self.log_path is None:
            return
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    f"{datetime.now().astimezone().isoformat(timespec='seconds')} "
                    f"Feishu notification failed: {error_name}\n"
                )
        except OSError:
            return
