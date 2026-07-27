from __future__ import annotations

import base64
import hashlib
import hmac
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from re9_pose_recorder.config import AppConfig
from re9_pose_recorder.feishu_notify import FeishuNotifier


class FeishuNotifierTests(unittest.TestCase):
    def _config(self, root: Path, webhook_url: str = "") -> AppConfig:
        return AppConfig(
            raw={
                "notifications": {
                    "feishu": {
                        "webhook_url": webhook_url,
                        "secret": "config-signing-secret",
                        "mention_open_id": "ou_configuser",
                    }
                },
                "report": {"output_dir": str(root / "outputs")},
            },
            path=root / "config.yaml",
        )

    def test_environment_settings_override_config_without_exposing_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self._config(Path(temp_dir), "https://config.invalid/token")
            with patch.dict(
                "os.environ",
                {
                    "RE9_FEISHU_WEBHOOK_URL": "https://environment.invalid/webhook-secret",
                    "RE9_FEISHU_SECRET": "environment-signing-secret",
                    "RE9_FEISHU_MENTION_OPEN_ID": "ou_environmentuser",
                },
            ):
                notifier = FeishuNotifier.from_config(config)

        self.assertEqual(
            notifier.webhook_url,
            "https://environment.invalid/webhook-secret",
        )
        self.assertEqual(notifier.secret, "environment-signing-secret")
        self.assertEqual(notifier.mention_open_id, "ou_environmentuser")
        self.assertTrue(notifier.enabled)
        self.assertNotIn("webhook-secret", notifier.status_text)
        self.assertNotIn("signing-secret", notifier.status_text)

    @patch("re9_pose_recorder.feishu_notify.requests.post")
    def test_send_error_posts_text_fields_and_mention(self, post_mock: Mock) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"code": 0, "msg": "success"}
        post_mock.return_value = response
        notifier = FeishuNotifier(
            webhook_url="https://open.feishu.invalid/webhook-secret",
            mention_open_id="ou_testuser",
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
        text = payload["content"]["text"]
        self.assertEqual(payload["msg_type"], "text")
        self.assertIn('<at user_id="ou_testuser">用户</at>', text)
        self.assertIn("Trajectory replay failed", text)
        self.assertIn("Progress: 8313/13000", text)
        self.assertEqual(post_mock.call_args.kwargs["timeout"], 2.0)

    @patch("re9_pose_recorder.feishu_notify.time.time", return_value=1_723_456_789)
    def test_signature_matches_feishu_algorithm(self, time_mock: Mock) -> None:
        secret = "signing-secret"
        notifier = FeishuNotifier(
            webhook_url="https://open.feishu.invalid/webhook-secret",
            secret=secret,
        )

        payload = notifier._payload("Failure", "Something broke", fields=None)

        timestamp = "1723456789"
        expected = base64.b64encode(
            hmac.new(
                f"{timestamp}\n{secret}".encode("utf-8"),
                digestmod=hashlib.sha256,
            ).digest()
        ).decode("ascii")
        self.assertEqual(payload["timestamp"], timestamp)
        self.assertEqual(payload["sign"], expected)
        time_mock.assert_called_once()

    @patch("re9_pose_recorder.feishu_notify.time.sleep")
    @patch("re9_pose_recorder.feishu_notify.requests.post")
    def test_api_failure_log_does_not_contain_secrets(
        self,
        post_mock: Mock,
        sleep_mock: Mock,
    ) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "code": 19021,
            "msg": "bad webhook-secret signing-secret",
        }
        post_mock.return_value = response

        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "feishu.log"
            notifier = FeishuNotifier(
                webhook_url="https://open.feishu.invalid/webhook-secret",
                secret="signing-secret",
                log_path=log_path,
            )
            self.assertFalse(notifier.send_error("Failure", "Something broke"))
            log_text = log_path.read_text(encoding="utf-8")

        self.assertIn("FeishuResponseError", log_text)
        self.assertNotIn("webhook-secret", log_text)
        self.assertNotIn("signing-secret", log_text)
        self.assertEqual(post_mock.call_count, 3)
        self.assertEqual(sleep_mock.call_count, 2)

    def test_disabled_notifier_does_not_start_a_thread(self) -> None:
        notifier = FeishuNotifier()
        with patch("re9_pose_recorder.feishu_notify.threading.Thread") as thread_mock:
            self.assertFalse(notifier.notify_error("Failure", "Something broke"))
        thread_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
