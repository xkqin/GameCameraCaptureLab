from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from re9_pose_recorder.config import AppConfig
from re9_pose_recorder.discord_notify import DiscordNotifier


class DiscordNotifierTests(unittest.TestCase):
    def _config(self, root: Path, webhook_url: str = "") -> AppConfig:
        return AppConfig(
            raw={
                "notifications": {
                    "discord": {
                        "webhook_url": webhook_url,
                        "mention": "<@123>",
                    }
                },
                "report": {"output_dir": str(root / "outputs")},
            },
            path=root / "config.yaml",
        )

    def test_environment_webhook_overrides_config_without_exposing_it_in_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._config(Path(temp_dir), "https://config.invalid/token")
            with patch.dict(
                "os.environ",
                {"RE9_DISCORD_WEBHOOK_URL": "https://environment.invalid/secret"},
            ):
                notifier = DiscordNotifier.from_config(config)

        self.assertEqual(notifier.webhook_url, "https://environment.invalid/secret")
        self.assertTrue(notifier.enabled)
        self.assertNotIn("secret", notifier.status_text)
        self.assertNotIn(notifier.webhook_url, notifier.status_text)

    @patch("re9_pose_recorder.discord_notify.requests.post")
    def test_send_error_posts_embed_and_mention(self, post_mock: Mock) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        post_mock.return_value = response
        notifier = DiscordNotifier(
            webhook_url="https://discord.invalid/webhook-secret",
            mention="<@123>",
            timeout_sec=2.0,
        )

        self.assertTrue(
            notifier.send_error(
                "Trajectory replay failed",
                "Lua acknowledgement timed out",
                fields={"Progress": "8313/13000", "Next": 8314},
            )
        )

        payload = post_mock.call_args.kwargs["json"]
        self.assertEqual(payload["content"], "<@123>")
        self.assertEqual(payload["embeds"][0]["title"], "Trajectory replay failed")
        self.assertEqual(payload["embeds"][0]["fields"][0]["value"], "8313/13000")
        self.assertEqual(post_mock.call_args.kwargs["timeout"], 2.0)

    @patch("re9_pose_recorder.discord_notify.time.sleep")
    @patch("re9_pose_recorder.discord_notify.requests.post")
    def test_failure_log_does_not_contain_webhook_secret(
        self,
        post_mock: Mock,
        sleep_mock: Mock,
    ) -> None:
        post_mock.side_effect = requests.ConnectionError(
            "failed URL https://discord.invalid/webhook-secret"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "discord.log"
            notifier = DiscordNotifier(
                webhook_url="https://discord.invalid/webhook-secret",
                log_path=log_path,
            )
            self.assertFalse(notifier.send_error("Failure", "Something broke"))
            log_text = log_path.read_text(encoding="utf-8")

        self.assertIn("ConnectionError", log_text)
        self.assertNotIn("webhook-secret", log_text)
        self.assertEqual(post_mock.call_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)

    def test_disabled_notifier_does_not_start_a_thread(self) -> None:
        notifier = DiscordNotifier()
        with patch("re9_pose_recorder.discord_notify.threading.Thread") as thread_mock:
            self.assertFalse(notifier.notify_error("Failure", "Something broke"))
        thread_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
