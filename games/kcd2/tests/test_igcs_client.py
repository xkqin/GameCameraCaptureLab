from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from kcd2_capture_studio.igcs_client import (
    CLIENT_TO_DLL_PIPE,
    DLL_TO_CLIENT_PIPE,
    IGCSClientError,
    IGCSClientManager,
)


class FakeProcess:
    def __init__(self, pid: int = 4321, returncode=None) -> None:
        self.pid = pid
        self.returncode = returncode

    def poll(self):
        return self.returncode


class TickClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        self.value += 0.1
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class IGCSClientManagerTests(unittest.TestCase):
    def test_starts_client_then_waits_for_server_pipe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            client = Path(temp) / "IGCSClient.exe"
            client.touch()
            launched = []
            hidden = []
            process_calls = iter(([], [4321], [4321], [4321]))
            pipe_calls = iter(
                (
                    set(),
                    {DLL_TO_CLIENT_PIPE.lower()},
                    {DLL_TO_CLIENT_PIPE.lower()},
                )
            )
            clock = TickClock()
            manager = IGCSClientManager(
                client_path=client,
                process_finder=lambda: next(process_calls, [4321]),
                pipe_lister=lambda: next(
                    pipe_calls,
                    {DLL_TO_CLIENT_PIPE.lower()},
                ),
                launcher=lambda path: launched.append(path) or FakeProcess(),
                window_hider=hidden.append,
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
            )

            result = manager.ensure_server_ready(timeout=2.0)

            self.assertTrue(result["started"])
            self.assertEqual([client], launched)
            self.assertEqual([4321], hidden)

    def test_existing_ready_client_is_not_relaunched(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            client = Path(temp) / "IGCSClient.exe"
            client.touch()
            launched = []
            manager = IGCSClientManager(
                client_path=client,
                process_finder=lambda: [99],
                pipe_lister=lambda: {DLL_TO_CLIENT_PIPE.lower()},
                launcher=lambda path: launched.append(path),
            )

            result = manager.ensure_server_ready(timeout=0.2)

            self.assertFalse(result["started"])
            self.assertEqual([], launched)

    def test_bidirectional_pipe_timeout_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            client = Path(temp) / "IGCSClient.exe"
            client.touch()
            clock = TickClock()
            manager = IGCSClientManager(
                client_path=client,
                process_finder=lambda: [99],
                pipe_lister=lambda: {DLL_TO_CLIENT_PIPE.lower()},
                monotonic=clock.monotonic,
                sleeper=clock.sleep,
            )

            with self.assertRaisesRegex(
                IGCSClientError,
                "双向管道",
            ):
                manager.wait_for_bidirectional_pipes(timeout=0.3)

    def test_bidirectional_pipe_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            client = Path(temp) / "IGCSClient.exe"
            client.touch()
            manager = IGCSClientManager(
                client_path=client,
                process_finder=lambda: [99],
                pipe_lister=lambda: {
                    DLL_TO_CLIENT_PIPE.lower(),
                    CLIENT_TO_DLL_PIPE.lower(),
                },
            )

            result = manager.wait_for_bidirectional_pipes(timeout=0.2)

            self.assertTrue(result["dll_to_client_pipe"])
            self.assertTrue(result["client_to_dll_pipe"])


if __name__ == "__main__":
    unittest.main()
