from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from re9_pose_recorder.codex_recovery import (
    CodexRecoveryTrigger,
    _build_recovery_prompt,
    _codex_command,
    _codex_environment,
    _worker,
)
from re9_pose_recorder.config import AppConfig


class CodexRecoveryTests(unittest.TestCase):
    def _config(self, root: Path, enabled: bool = True) -> AppConfig:
        return AppConfig(
            raw={
                "automation": {
                    "codex_recovery": {
                        "enabled": enabled,
                        "codex_bin": "/usr/local/bin/codex",
                        "proxy_url": "http://127.0.0.1:7890",
                        "prompt": "请修复问题并且重新开始采集",
                        "cooldown_sec": 900,
                        "timeout_sec": 3600,
                    }
                },
                "report": {"output_dir": str(root / "outputs")},
            },
            path=root / "config.yaml",
        )

    def test_config_enables_recovery_without_exposing_prompt_in_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            trigger = CodexRecoveryTrigger.from_config(
                self._config(Path(temp_dir))
            )

        self.assertTrue(trigger.enabled)
        self.assertEqual(trigger.codex_bin, "/usr/local/bin/codex")
        self.assertEqual(trigger.proxy_url, "http://127.0.0.1:7890")
        self.assertNotIn(trigger.base_prompt, trigger.status_text)
        self.assertIn("cooldown 15 min", trigger.status_text)

    @patch("re9_pose_recorder.codex_recovery.subprocess.Popen")
    def test_trigger_writes_private_request_and_starts_detached_worker(
        self,
        popen_mock: Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            trigger = CodexRecoveryTrigger(
                configured_enabled=True,
                codex_bin="/usr/local/bin/codex",
                state_dir=root / "runtime",
                log_path=root / "outputs" / "codex.log",
            )
            self.assertTrue(
                trigger.trigger(
                    "Trajectory failed",
                    "Lua did not acknowledge logging",
                    fields={"Next trajectory": 8875},
                )
            )
            request_paths = list(
                trigger.state_dir.glob("re9_pose_codex_recovery_request_*.json")
            )
            self.assertEqual(len(request_paths), 1)
            request = json.loads(request_paths[0].read_text(encoding="utf-8"))
            mode = os.stat(request_paths[0]).st_mode & 0o777

        self.assertEqual(mode, 0o600)
        self.assertEqual(request["base_prompt"], "请修复问题并且重新开始采集")
        self.assertEqual(request["fields"]["Next trajectory"], "8875")
        command = popen_mock.call_args.args[0]
        self.assertIn("re9_pose_recorder.codex_recovery", command)
        self.assertNotIn(request["base_prompt"], command)
        self.assertTrue(popen_mock.call_args.kwargs["start_new_session"])

    @patch("re9_pose_recorder.codex_recovery.subprocess.Popen")
    def test_disabled_or_cooling_down_trigger_does_not_spawn(
        self,
        popen_mock: Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            disabled = CodexRecoveryTrigger(
                configured_enabled=False,
                codex_bin="/usr/local/bin/codex",
                state_dir=root / "disabled",
            )
            self.assertFalse(disabled.trigger("Failure", "Broken"))

            cooling = CodexRecoveryTrigger(
                configured_enabled=True,
                codex_bin="/usr/local/bin/codex",
                state_dir=root / "cooling",
                cooldown_sec=900,
            )
            cooling.state_dir.mkdir(parents=True)
            cooling.state_path.write_text(
                json.dumps({"started_at_epoch": time.time()}),
                encoding="utf-8",
            )
            self.assertFalse(cooling.trigger("Failure", "Broken"))

        popen_mock.assert_not_called()

    def test_command_uses_noninteractive_approval_and_stdin_prompt(self) -> None:
        request = {
            "codex_bin": "/usr/local/bin/codex",
            "proxy_url": "http://127.0.0.1:7890",
            "project_root": "/workspace/project",
        }

        command = _codex_command(request)

        self.assertIn("never", command)
        self.assertIn("danger-full-access", command)
        self.assertIn("features.apps=false", command)
        self.assertEqual(command[-1], "-")
        self.assertNotIn("请修复问题并且重新开始采集", command)

    @patch.dict(
        os.environ,
        {
            "ALL_PROXY": "socks5://old-uppercase",
            "all_proxy": "socks5://old-lowercase",
            "HTTP_PROXY": "http://old-uppercase",
            "HTTPS_PROXY": "http://old-secure-uppercase",
            "http_proxy": "http://old-lowercase",
            "https_proxy": "http://old-secure-lowercase",
        },
    )
    def test_environment_matches_requested_proxy_wrapper(self) -> None:
        proxy_url = "http://127.0.0.1:7890"
        names = (
            "ALL_PROXY",
            "all_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "http_proxy",
            "https_proxy",
        )
        parent_environment = {name: os.environ[name] for name in names}

        environment = _codex_environment({"proxy_url": proxy_url})

        self.assertNotIn("ALL_PROXY", environment)
        self.assertNotIn("all_proxy", environment)
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            self.assertEqual(environment[name], proxy_url)
        self.assertEqual(
            {name: os.environ[name] for name in names},
            parent_environment,
        )

    def test_prompt_contains_error_context_and_recovery_constraints(self) -> None:
        prompt = _build_recovery_prompt(
            {
                "base_prompt": "请修复问题并且重新开始采集",
                "title": "Trajectory failed",
                "message": "Lua timeout",
                "fields": {"Next trajectory": "8875"},
            }
        )

        self.assertTrue(prompt.startswith("请修复问题并且重新开始采集"))
        self.assertIn("Lua timeout", prompt)
        self.assertIn("Next trajectory: 8875", prompt)
        self.assertIn("不要输出、提交或上传", prompt)
        self.assertIn("验证至少一条新轨迹", prompt)

    @patch("re9_pose_recorder.codex_recovery.subprocess.Popen")
    def test_worker_holds_lock_runs_codex_and_records_completion(
        self,
        popen_mock: Mock,
    ) -> None:
        process = Mock()
        process.returncode = 0
        process.communicate.return_value = (None, None)
        popen_mock.return_value = process

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            request_path = root / "request.json"
            state_path = root / "state.json"
            request_path.write_text(
                json.dumps(
                    {
                        "codex_bin": "/usr/local/bin/codex",
                        "proxy_url": "http://127.0.0.1:7890",
                        "base_prompt": "请修复问题并且重新开始采集",
                        "cooldown_sec": 900,
                        "timeout_sec": 600,
                        "project_root": str(root),
                        "log_path": str(root / "codex.log"),
                        "state_path": str(state_path),
                        "lock_path": str(root / "worker.lock"),
                        "title": "Trajectory failed",
                        "message": "Lua timeout",
                        "fields": {"Next trajectory": "8875"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            self.assertEqual(_worker(request_path), 0)
            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(state["status"], "completed")
        self.assertIn(
            "请修复问题并且重新开始采集",
            process.communicate.call_args.kwargs["input"],
        )
        self.assertEqual(process.communicate.call_args.kwargs["timeout"], 600)
        self.assertEqual(popen_mock.call_args.args[0][-1], "-")
        worker_environment = popen_mock.call_args.kwargs["env"]
        self.assertNotIn("ALL_PROXY", worker_environment)
        self.assertNotIn("all_proxy", worker_environment)
        self.assertEqual(
            worker_environment["HTTPS_PROXY"],
            "http://127.0.0.1:7890",
        )


if __name__ == "__main__":
    unittest.main()
