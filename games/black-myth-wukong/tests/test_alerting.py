from __future__ import annotations

import base64
import hashlib
import hmac
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from bmw_capture_studio.config import SharedConfig
from bmw_capture_studio import discord_notify, feishu_notify, repair
from bmw_capture_studio.discord_notify import DiscordNotifier
from bmw_capture_studio.feishu_notify import FeishuNotifier
from bmw_capture_studio.repair import CodexRecoveryTrigger


class AlertingTests(unittest.TestCase):
    def test_unified_environment_has_priority_over_legacy_notification_values(self) -> None:
        config = SharedConfig(
            raw={"notifications": {"discord": {}, "feishu": {}}},
            path=Path("configs/windows.local.yaml"),
        )
        with patch.dict(
            "os.environ",
            {
                "UNIFIED_DISCORD_WEBHOOK_URL": "https://unified.invalid/discord",
                "RE9_DISCORD_WEBHOOK_URL": "https://legacy.invalid/discord",
                "UNIFIED_FEISHU_WEBHOOK_URL": "https://unified.invalid/feishu",
                "RE9_FEISHU_WEBHOOK_URL": "https://legacy.invalid/feishu",
            },
            clear=False,
        ):
            discord = DiscordNotifier.from_config(config)
            feishu = FeishuNotifier.from_config(config)

        self.assertEqual(discord.webhook_url, "https://unified.invalid/discord")
        self.assertEqual(feishu.webhook_url, "https://unified.invalid/feishu")

    def test_discord_payload_and_failure_log_do_not_expose_webhook(self) -> None:
        response = Mock()
        response.raise_for_status.side_effect = RuntimeError(
            "failed https://discord.invalid/webhook-secret"
        )
        fake_requests = Mock()
        fake_requests.post.return_value = response
        notifier = DiscordNotifier(
            webhook_url="https://discord.invalid/webhook-secret",
            mention="@here",
        )
        with tempfile.TemporaryDirectory() as directory, patch.object(
            discord_notify, "requests", fake_requests
        ), patch.object(
            discord_notify, "_REQUEST_EXCEPTION", RuntimeError
        ), patch.object(discord_notify.time, "sleep"):
            notifier.log_path = Path(directory) / "discord.log"
            self.assertFalse(notifier.send_error("Failure", "Broken", fields={"Adapter": "x"}))
            log_text = notifier.log_path.read_text(encoding="utf-8")

        self.assertNotIn("webhook-secret", log_text)
        self.assertIn("RuntimeError", log_text)
        payload = notifier._payload("Test", "Message", fields={"Adapter": "x"})
        self.assertEqual(payload["content"], "@here")
        self.assertEqual(payload["embeds"][0]["fields"][0]["name"], "Adapter")

    def test_feishu_uses_re9_config_shape_and_environment_override(self) -> None:
        config = SharedConfig(
            raw={
                "notifications": {
                    "feishu": {
                        "webhook_url": "https://config.invalid/webhook",
                        "secret": "config-secret",
                        "mention_open_id": "all",
                    }
                }
            },
            path=Path("configs/linux.yaml"),
        )
        with patch.dict(
            "os.environ",
            {
                "RE9_FEISHU_WEBHOOK_URL": "https://environment.invalid/webhook",
                "RE9_FEISHU_SECRET": "environment-secret",
                "RE9_FEISHU_MENTION_OPEN_ID": "ou_environment",
            },
            clear=False,
        ):
            notifier = FeishuNotifier.from_config(config)

        self.assertEqual(notifier.source, "environment")
        self.assertEqual(notifier.webhook_url, "https://environment.invalid/webhook")
        self.assertEqual(notifier.secret, "environment-secret")
        self.assertEqual(notifier.mention_open_id, "ou_environment")
        self.assertNotIn("environment-secret", notifier.status_text)

    def test_feishu_signature_and_payload_are_compatible_with_re9(self) -> None:
        notifier = FeishuNotifier(
            webhook_url="https://open.feishu.invalid/webhook",
            secret="signing-secret",
            mention_open_id="all",
        )
        with patch.object(feishu_notify.time, "time", return_value=1_723_456_789):
            payload = notifier._payload("Failure", "Something broke", fields={"Next": 9})

        expected = base64.b64encode(
            hmac.new(
                b"1723456789\nsigning-secret",
                digestmod=hashlib.sha256,
            ).digest()
        ).decode("ascii")
        self.assertEqual(payload["timestamp"], "1723456789")
        self.assertEqual(payload["sign"], expected)
        self.assertIn('<at user_id="all">所有人</at>', payload["content"]["text"])

    def test_feishu_send_is_async_safe_and_does_not_log_secret(self) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"code": 19021, "msg": "secret"}
        fake_requests = Mock()
        fake_requests.post.return_value = response
        fake_requests.RequestException = RuntimeError
        notifier = FeishuNotifier(
            webhook_url="https://open.feishu.invalid/webhook",
            secret="signing-secret",
        )
        with tempfile.TemporaryDirectory() as directory, patch.object(
            feishu_notify, "requests", fake_requests
        ), patch.object(feishu_notify, "_REQUEST_EXCEPTION", RuntimeError), patch.object(
            feishu_notify.time, "sleep"
        ):
            notifier.log_path = Path(directory) / "feishu.log"
            self.assertFalse(notifier.send_error("Failure", "Broken"))
            log_text = notifier.log_path.read_text(encoding="utf-8")

        self.assertIn("FeishuResponseError", log_text)
        self.assertNotIn("signing-secret", log_text)
        self.assertEqual(fake_requests.post.call_count, 3)

    def test_repair_is_disabled_by_default_and_uses_re9_fields(self) -> None:
        config = SharedConfig(
            raw={"automation": {"codex_recovery": {"enabled": False}}},
            path=Path("configs/linux.yaml"),
        )
        trigger = CodexRecoveryTrigger.from_config(config)
        self.assertFalse(trigger.configured_enabled)
        self.assertFalse(trigger.enabled)
        self.assertIn("RE9_CODEX_RECOVERY_ENABLED", trigger.status_text)

    def test_unified_recovery_environment_has_priority_over_legacy_values(self) -> None:
        config = SharedConfig(raw={}, path=None)
        with patch.dict(
            "os.environ",
            {
                "UNIFIED_CODEX_RECOVERY_ENABLED": "1",
                "RE9_CODEX_RECOVERY_ENABLED": "0",
                "UNIFIED_CODEX_BIN": "unified-codex",
                "RE9_CODEX_BIN": "legacy-codex",
            },
            clear=False,
        ):
            trigger = CodexRecoveryTrigger.from_config(config)

        self.assertTrue(trigger.configured_enabled)
        self.assertEqual(trigger.codex_bin, "unified-codex")
        self.assertEqual(trigger.source, "environment")

    def test_repair_trigger_writes_private_request_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trigger = CodexRecoveryTrigger(
                configured_enabled=True,
                codex_bin="codex",
                state_dir=root / "state",
                log_path=root / "repair.log",
            )
            with patch.object(repair.subprocess, "Popen") as popen:
                self.assertTrue(
                    trigger.trigger(
                        "Capture failed",
                        "OBS did not return a frame",
                        fields={"Progress": "3/22"},
                    )
                )

            request_files = list((root / "state").glob("bmw_recovery_request_*.json"))
            self.assertEqual(len(request_files), 1)
            self.assertEqual(popen.call_count, 1)
            self.assertIn("bmw_capture_studio.repair", popen.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
