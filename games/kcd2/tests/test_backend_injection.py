from __future__ import annotations

import unittest
from unittest.mock import patch

from kcd2_capture_studio.backend import CameraBackend


class FakeClientManager:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def status(self):
        self.calls.append("status")
        return {
            "pids": [77],
            "running": True,
            "dll_to_client_pipe": True,
            "client_to_dll_pipe": True,
        }

    def ensure_server_ready(self, *, progress):
        self.calls.append("ensure_server_ready")
        progress("server-ready")
        return {"pid": 77, "started": True}

    def wait_for_bidirectional_pipes(self, *, progress):
        self.calls.append("wait_for_bidirectional_pipes")
        progress("pipes-ready")
        return {
            "dll_to_client_pipe": True,
            "client_to_dll_pipe": True,
        }


class BackendInjectionTests(unittest.TestCase):
    def test_client_is_prepared_before_dll_injection(self) -> None:
        client = FakeClientManager()
        backend = CameraBackend(client)
        events: list[str] = []

        def inject():
            events.append("inject_dll")
            return {"pid": 42, "already_loaded": False}

        with (
            patch(
                "kcd2_capture_studio.backend.engine.find_process_id",
                return_value=42,
            ),
            patch(
                "kcd2_capture_studio.backend.engine.find_module",
                side_effect=RuntimeError("not loaded"),
            ),
            patch(
                "kcd2_capture_studio.backend.engine.inject_camera_dll",
                side_effect=inject,
            ),
            patch.object(
                backend,
                "_latest_log_has_pipe_error",
                return_value=False,
            ),
        ):
            result = backend.inject(events.append)

        self.assertEqual(
            [
                "ensure_server_ready",
                "wait_for_bidirectional_pipes",
            ],
            client.calls,
        )
        self.assertLess(
            events.index("server-ready"),
            events.index("inject_dll"),
        )
        self.assertTrue(result["pipe_verified"])

    def test_previously_failed_loaded_dll_requires_game_restart(self) -> None:
        client = FakeClientManager()
        backend = CameraBackend(client)

        with (
            patch(
                "kcd2_capture_studio.backend.engine.find_process_id",
                return_value=42,
            ),
            patch(
                "kcd2_capture_studio.backend.engine.find_module",
                return_value={"base": 123},
            ),
            patch.object(
                backend,
                "_latest_log_has_pipe_error",
                return_value=True,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "重新启动 KCD2"):
                backend.inject()


if __name__ == "__main__":
    unittest.main()
