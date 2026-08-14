from __future__ import annotations

from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from bmw_capture_studio import bridge, config, global_hotkey, platform_support, screen_capture, uuu
from bmw_capture_studio.connection import classify_connection, probe_connection
from bmw_capture_studio.global_hotkey import F8_VK, GlobalHotkey


class LinuxCompatibilityTests(unittest.TestCase):
    def test_bridge_endpoint_accepts_only_loopback_hosts(self) -> None:
        self.assertEqual(
            bridge.parse_bridge_endpoint("127.0.0.1:28791"),
            ("127.0.0.1", 28791),
        )
        self.assertEqual(
            bridge.parse_bridge_endpoint("[::1]:28792"),
            ("::1", 28792),
        )
        self.assertEqual(
            bridge.parse_bridge_endpoint("localhost"),
            ("localhost", bridge.RELAY_DEFAULT_PORT),
        )
        for value in ("example.com:28791", "0.0.0.0:28791", "*:28791"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "loopback"):
                bridge.parse_bridge_endpoint(value)

    def test_linux_factory_selects_relay_only_when_endpoint_is_configured(self) -> None:
        with patch.object(sys, "platform", "linux"), patch.dict(
            bridge.os.environ, {"BMW_BRIDGE_ENDPOINT": ""}
        ):
            self.assertIsInstance(
                bridge.create_pose_bridge("127.0.0.1:28791"),
                bridge.LinuxRelayUuuPoseBridge,
            )
            self.assertIsInstance(bridge.create_pose_bridge(), bridge.UuuPoseBridge)

    def test_linux_relay_unavailable_is_reported_as_waiting(self) -> None:
        relay = bridge.LinuxRelayUuuPoseBridge("127.0.0.1:1", timeout_seconds=0.05)
        with patch.object(sys, "platform", "linux"):
            report = probe_connection(relay)
        self.assertEqual(report.code, "linux_bridge_waiting")
        self.assertIsNone(report.pose)

    def test_linux_relay_round_trips_native_control_command(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(4)
        server.settimeout(1.0)
        port = int(server.getsockname()[1])
        received: list[tuple[int, bytes]] = []
        errors: list[Exception] = []
        control_ack = 0

        metadata = bridge.BRIDGE_METADATA.pack(
            bridge.METADATA_MAGIC,
            bridge.METADATA_VERSION,
            bridge.BRIDGE_METADATA.size,
            1234,
            1,
            1,
            bridge.FLAG_BRIDGE_LOADED
            | bridge.FLAG_BUFFER_REQUESTED
            | bridge.FLAG_NATIVE_CONTROL_READY,
            0,
            99,
        )
        camera = bridge.CAMERA_DATA.pack(
            1,
            0,
            0,
            0,
            60.0,
            1.0,
            2.0,
            3.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            1.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
        )
        trajectory = bridge.TRAJECTORY_HEADER.pack(
            bridge.TRAJECTORY_MAGIC,
            bridge.TRAJECTORY_VERSION,
            bridge.TRAJECTORY_HEADER.size,
            0,
            0,
            bridge.TRAJECTORY_STATE_IDLE,
            0,
            0,
            0.0,
            60.0,
            0,
            0.0,
            0,
            0,
            0,
            0,
        )

        def state_payload() -> bytes:
            control = bridge.CONTROL_HEADER.pack(
                bridge.CONTROL_MAGIC,
                bridge.CONTROL_VERSION,
                bridge.CONTROL_HEADER.size + bridge.CONTROL_COMMAND.size,
                control_ack,
                control_ack,
                bridge.CONTROL_STATE_APPLIED if control_ack else bridge.CONTROL_STATE_IDLE,
                0,
                bridge.CONTROL_CAP_SET_POSE,
            )
            return metadata + camera + control + trajectory

        def receive_exact(client: socket.socket, size: int) -> bytes:
            chunks: list[bytes] = []
            while size:
                chunk = client.recv(size)
                if not chunk:
                    raise ConnectionError("fake relay client closed")
                chunks.append(chunk)
                size -= len(chunk)
            return b"".join(chunks)

        def serve() -> None:
            nonlocal control_ack
            try:
                for _ in range(3):
                    client, _address = server.accept()
                    with client:
                        header = receive_exact(client, bridge.RELAY_HEADER.size)
                        self.assertEqual(len(header), bridge.RELAY_HEADER.size)
                        magic, version, operation, _status, size = bridge.RELAY_HEADER.unpack(header)
                        self.assertEqual(magic, bridge.RELAY_MAGIC)
                        self.assertEqual(version, bridge.RELAY_VERSION)
                        payload = receive_exact(client, size) if size else b""
                        received.append((operation, payload))
                        if operation == bridge.RELAY_APPLY_CONTROL:
                            self.assertEqual(len(payload), 4 + bridge.CONTROL_COMMAND.size)
                            control_ack = struct.unpack("<I", payload[:4])[0]
                            response_payload = b""
                        else:
                            self.assertEqual(operation, bridge.RELAY_READ_STATE)
                            response_payload = state_payload()
                        response = bridge.RELAY_HEADER.pack(
                            bridge.RELAY_MAGIC,
                            bridge.RELAY_VERSION,
                            operation,
                            bridge.RELAY_STATUS_OK,
                            len(response_payload),
                        )
                        client.sendall(response + response_payload)
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)
            finally:
                server.close()

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        relay = bridge.LinuxRelayUuuPoseBridge(
            f"127.0.0.1:{port}", timeout_seconds=0.5
        )
        status = relay.apply_native_step(move_forward=1.0)
        worker.join(timeout=2.0)

        self.assertFalse(errors, errors)
        self.assertEqual(status.acknowledge_sequence, 1)
        self.assertEqual([item[0] for item in received], [
            bridge.RELAY_READ_STATE,
            bridge.RELAY_APPLY_CONTROL,
            bridge.RELAY_READ_STATE,
        ])
        command = bridge.CONTROL_COMMAND.unpack(received[1][1][4:])
        self.assertEqual(command[0], 1.0)

    def test_linux_integration_status_is_explicitly_unsupported_for_uuu(self) -> None:
        with patch.object(sys, "platform", "linux"):
            status = uuu.integration_status()

        self.assertTrue(status["platform_unsupported"])
        self.assertEqual(status["platform"], "linux")
        self.assertIn("Windows", str(status["message"]))

        report = classify_connection(status, None, {"connected": False})
        self.assertEqual(report.code, "platform_unsupported")
        self.assertIsNone(report.pose)

    def test_linux_pose_bridge_fails_before_opening_windows_shared_memory(self) -> None:
        bridge_instance = bridge.UuuPoseBridge()
        with patch.object(sys, "platform", "linux"), self.assertRaisesRegex(
            bridge.PoseUnavailableError, "Linux"
        ):
            bridge_instance.connect()

    def test_linux_dpi_awareness_does_not_call_win32(self) -> None:
        with patch.object(sys, "platform", "linux"), patch.object(
            screen_capture, "_user32"
        ) as user32:
            screen_capture.enable_dpi_awareness()
        user32.assert_not_called()

    def test_linux_global_hotkey_is_a_noop_and_does_not_start_a_thread(self) -> None:
        hotkey = GlobalHotkey(F8_VK)
        with patch.object(sys, "platform", "linux"), patch.object(
            global_hotkey.ctypes, "WinDLL", side_effect=AssertionError("Win32 called")
        ):
            self.assertFalse(hotkey.supported)
            hotkey.start()
            hotkey.stop()
        self.assertIsNone(hotkey._thread)

    def test_linux_open_path_uses_xdg_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "outputs"
            with patch.object(sys, "platform", "linux"), patch.object(
                platform_support.shutil, "which", return_value="/usr/bin/xdg-open"
            ), patch.object(platform_support.subprocess, "Popen") as popen:
                platform_support.open_path(target)
        popen.assert_called_once_with(["/usr/bin/xdg-open", str(target.resolve())])

    def test_linux_open_path_reports_missing_desktop_opener(self) -> None:
        with patch.object(sys, "platform", "linux"), patch.object(
            platform_support.shutil, "which", return_value=None
        ), self.assertRaisesRegex(RuntimeError, "xdg-open"):
            platform_support.open_path(Path("/tmp/outputs"))

    def test_linux_reuses_re9_config_precedence_and_detaches_workers(self) -> None:
        with patch.object(sys, "platform", "linux"):
            shared = config.load_shared_config()
            process_options = platform_support.detached_process_kwargs()

        self.assertTrue(
            shared.path is None
            or shared.path.name in {"linux.local.yaml", "linux.yaml", "default.yaml"}
        )
        self.assertEqual(process_options, {"start_new_session": True})


if __name__ == "__main__":
    unittest.main()
