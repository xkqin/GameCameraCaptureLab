from __future__ import annotations

from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from bmw_capture_studio import bridge, config, global_hotkey, injection, platform_support, screen_capture
from bmw_capture_studio.connection import classify_connection, probe_connection
from bmw_capture_studio.global_hotkey import E_VK, GlobalHotkey


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
                bridge.LinuxRelayCameraPoseBridge,
            )
            self.assertIsInstance(bridge.create_pose_bridge(), bridge.CameraPoseBridge)

    def test_linux_relay_unavailable_is_reported_as_waiting(self) -> None:
        relay = bridge.LinuxRelayCameraPoseBridge("127.0.0.1:1", timeout_seconds=0.05)
        with patch.object(sys, "platform", "linux"):
            report = probe_connection(relay)
        self.assertEqual(report.code, "linux_bridge_waiting")
        self.assertIsNone(report.pose)

    def test_linux_relay_round_trips_native_control_and_hud(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(4)
        server.settimeout(1.0)
        port = int(server.getsockname()[1])
        received: list[tuple[int, bytes]] = []
        errors: list[Exception] = []
        control_ack = 0
        hud_ack = 0
        hud_hidden = 0

        metadata = bridge.BRIDGE_METADATA.pack(
            bridge.METADATA_MAGIC,
            bridge.METADATA_VERSION,
            bridge.BRIDGE_METADATA.size,
            1234,
            1,
            1,
            bridge.FLAG_BRIDGE_LOADED
            | bridge.FLAG_CONNECT_CALLED
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
        precise = bridge.PRECISE_POSE.pack(
            bridge.PRECISE_POSE_MAGIC,
            bridge.PRECISE_POSE_VERSION,
            bridge.PRECISE_POSE.size,
            2,
            1.0,
            2.0,
            3.0,
            0.0,
            0.0,
            0.0,
            60.0,
            1,
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
            absolute = bridge.CONTROL_HEADER.pack(
                bridge.ABSOLUTE_POSE_MAGIC,
                bridge.ABSOLUTE_POSE_VERSION,
                bridge.ABSOLUTE_POSE.size,
                0,
                0,
                bridge.CONTROL_STATE_IDLE,
                0,
                bridge.ABSOLUTE_POSE_CAPABILITY,
            )
            hud = bridge.HUD_CONTROL.pack(
                bridge.HUD_CONTROL_MAGIC,
                bridge.HUD_CONTROL_VERSION,
                bridge.HUD_CONTROL.size,
                hud_ack,
                hud_ack,
                bridge.CONTROL_STATE_APPLIED if hud_ack else bridge.CONTROL_STATE_IDLE,
                0,
                bridge.HUD_CONTROL_CAPABILITY,
                hud_hidden,
                0, 0, 0, 0, 0, 0, 0,
            )
            return metadata + camera + precise + control + absolute + hud + trajectory

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
            nonlocal control_ack, hud_ack, hud_hidden
            try:
                for _ in range(6):
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
                        elif operation == bridge.RELAY_SET_HUD:
                            values = bridge.HUD_CONTROL.unpack(payload)
                            hud_ack = int(values[3])
                            hud_hidden = int(values[8])
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
        relay = bridge.LinuxRelayCameraPoseBridge(
            f"127.0.0.1:{port}", timeout_seconds=0.5
        )
        status = relay.apply_native_step(move_forward=1.0)
        hud_status = relay.set_hud_hidden(True)
        worker.join(timeout=2.0)

        self.assertFalse(errors, errors)
        self.assertEqual(status.acknowledge_sequence, 1)
        self.assertEqual(hud_status.acknowledge_sequence, 1)
        self.assertTrue(hud_status.hidden)
        self.assertEqual([item[0] for item in received], [
            bridge.RELAY_READ_STATE,
            bridge.RELAY_APPLY_CONTROL,
            bridge.RELAY_READ_STATE,
            bridge.RELAY_READ_STATE,
            bridge.RELAY_SET_HUD,
            bridge.RELAY_READ_STATE,
        ])
        command = bridge.CONTROL_COMMAND.unpack(received[1][1][4:])
        self.assertEqual(command[0], 1.0)

    def test_linux_relay_round_trips_absolute_set_pose(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(("127.0.0.1", 0))
        server.listen(4)
        server.settimeout(1.0)
        port = int(server.getsockname()[1])
        errors: list[Exception] = []
        absolute_ack = 0
        target = [1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 60.0]

        metadata = bridge.BRIDGE_METADATA.pack(
            bridge.METADATA_MAGIC,
            bridge.METADATA_VERSION,
            bridge.BRIDGE_METADATA.size,
            1234,
            1,
            1,
            bridge.FLAG_BRIDGE_LOADED
            | bridge.FLAG_CONNECT_CALLED
            | bridge.FLAG_BUFFER_REQUESTED
            | bridge.FLAG_NATIVE_CONTROL_READY,
            0,
            99,
        )
        camera = bridge.CAMERA_DATA.pack(
            1, 0, 0, 0, 60.0,
            1.0, 2.0, 3.0,
            0.0, 0.0, 0.0, 1.0,
            0.0, 0.0, 1.0,
            0.0, 1.0, 0.0,
            1.0, 0.0, 0.0,
            0.0, 0.0, 0.0,
        )
        control = bridge.CONTROL_HEADER.pack(
            bridge.CONTROL_MAGIC,
            bridge.CONTROL_VERSION,
            bridge.CONTROL_HEADER.size + bridge.CONTROL_COMMAND.size,
            0, 0, bridge.CONTROL_STATE_IDLE, 0,
            bridge.CONTROL_CAP_SET_POSE,
        )
        trajectory = bridge.TRAJECTORY_HEADER.pack(
            bridge.TRAJECTORY_MAGIC,
            bridge.TRAJECTORY_VERSION,
            bridge.TRAJECTORY_HEADER.size,
            0, 0, bridge.TRAJECTORY_STATE_IDLE, 0, 0,
            0.0, 60.0, 0, 0.0, 0, 0, 0, 0,
        )
        def state_payload() -> bytes:
            precise = bridge.PRECISE_POSE.pack(
                bridge.PRECISE_POSE_MAGIC,
                bridge.PRECISE_POSE_VERSION,
                bridge.PRECISE_POSE.size,
                2,
                target[0], target[1], target[2],
                target[4], target[3], target[5],
                target[6],
                1,
            )
            absolute = bridge.CONTROL_HEADER.pack(
                bridge.ABSOLUTE_POSE_MAGIC,
                bridge.ABSOLUTE_POSE_VERSION,
                bridge.ABSOLUTE_POSE.size,
                absolute_ack,
                absolute_ack,
                bridge.CONTROL_STATE_APPLIED if absolute_ack else bridge.CONTROL_STATE_IDLE,
                0,
                bridge.ABSOLUTE_POSE_CAPABILITY,
            )
            hud = bridge.HUD_CONTROL.pack(
                bridge.HUD_CONTROL_MAGIC,
                bridge.HUD_CONTROL_VERSION,
                bridge.HUD_CONTROL.size,
                0, 0, bridge.CONTROL_STATE_IDLE, 0,
                bridge.HUD_CONTROL_CAPABILITY,
                0, 0, 0, 0, 0, 0, 0, 0,
            )
            return metadata + camera + precise + control + absolute + hud + trajectory

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
            nonlocal absolute_ack
            try:
                for _ in range(4):
                    client, _address = server.accept()
                    with client:
                        header = receive_exact(client, bridge.RELAY_HEADER.size)
                        magic, version, operation, _status, size = bridge.RELAY_HEADER.unpack(header)
                        self.assertEqual((magic, version), (bridge.RELAY_MAGIC, bridge.RELAY_VERSION))
                        payload = receive_exact(client, size) if size else b""
                        response_payload = b""
                        if operation == bridge.RELAY_SET_POSE:
                            values = bridge.ABSOLUTE_POSE.unpack(payload)
                            absolute_ack = int(values[3])
                            target[:] = [
                                float(values[8]), float(values[9]), float(values[10]),
                                float(values[11]), float(values[12]), float(values[13]),
                                float(values[14]),
                            ]
                        else:
                            self.assertEqual(operation, bridge.RELAY_READ_STATE)
                            response_payload = state_payload()
                        client.sendall(
                            bridge.RELAY_HEADER.pack(
                                bridge.RELAY_MAGIC,
                                bridge.RELAY_VERSION,
                                operation,
                                bridge.RELAY_STATUS_OK,
                                len(response_payload),
                            )
                            + response_payload
                        )
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)
            finally:
                server.close()

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        relay = bridge.LinuxRelayCameraPoseBridge(
            f"127.0.0.1:{port}", timeout_seconds=0.5
        )
        requested = bridge.CameraPose(
            x=12345678.125,
            y=-23456789.25,
            z=34567890.5,
            yaw_degrees=135.0,
            pitch_degrees=-22.5,
            roll_degrees=3.0,
            fov_degrees=72.0,
        )
        feedback = relay.set_pose(requested)
        worker.join(timeout=2.0)

        self.assertFalse(errors, errors)
        self.assertEqual(absolute_ack, 1)
        self.assertEqual(
            (feedback.x, feedback.y, feedback.z),
            (requested.x, requested.y, requested.z),
        )
        self.assertEqual(feedback.yaw_degrees, requested.yaw_degrees)

    def test_linux_integration_status_waits_for_standalone_relay(self) -> None:
        with patch.object(sys, "platform", "linux"):
            status = injection.integration_status()

        self.assertTrue(status["platform_unsupported"])
        self.assertEqual(status["platform"], "linux")
        self.assertIn("BMW_BRIDGE_ENDPOINT", str(status["message"]))

        report = classify_connection(status, None, {"connected": False})
        self.assertEqual(report.code, "platform_unsupported")
        self.assertIsNone(report.pose)

    def test_linux_pose_bridge_fails_before_opening_windows_shared_memory(self) -> None:
        bridge_instance = bridge.CameraPoseBridge()
        with patch.object(sys, "platform", "linux"), self.assertRaisesRegex(
            bridge.PoseUnavailableError, "Linux"
        ):
            bridge_instance.connect()

    def test_linux_injector_uses_configured_proton_command_without_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bridge_dll = root / "BmwCameraBridge.dll"
            injector_exe = root / "BmwCameraInjector.exe"
            bridge_dll.touch()
            injector_exe.touch()
            completed = injection.subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="BMW_CAMERA_INJECT_OK pid=4321 already_loaded=0\n",
                stderr="",
            )
            with patch.object(sys, "platform", "linux"), patch.object(
                injection, "INJECTOR_PATH", injector_exe
            ), patch.dict(
                injection.os.environ,
                {"BMW_CAMERA_INJECT_COMMAND": "proton run {injector}"},
                clear=True,
            ), patch.object(
                injection.subprocess, "run", return_value=completed
            ) as run:
                result = injection.inject_bridge(bridge_path=bridge_dll)

        self.assertEqual(result["pid"], 4321)
        self.assertFalse(result["already_loaded"])
        command = run.call_args.args[0]
        self.assertEqual(command[:2], ["proton", "run"])
        self.assertEqual(command[2], str(injector_exe.resolve()))
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_linux_dpi_awareness_does_not_call_win32(self) -> None:
        with patch.object(sys, "platform", "linux"), patch.object(
            screen_capture, "_user32"
        ) as user32:
            screen_capture.enable_dpi_awareness()
        user32.assert_not_called()

    def test_linux_global_hotkey_is_a_noop_and_does_not_start_a_thread(self) -> None:
        hotkey = GlobalHotkey(E_VK)
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
