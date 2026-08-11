from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from bmw_capture_studio.bridge import (
    BRIDGE_METADATA,
    CAMERA_DATA,
    FLAG_BRIDGE_LOADED,
    FLAG_BUFFER_REQUESTED,
    FLAG_CONNECT_CALLED,
    METADATA_VERSION,
    BridgeMetadata,
    UuuPoseBridge,
)
from bmw_capture_studio.capture_runner import CaptureRunner
from bmw_capture_studio.connection import classify_connection
from bmw_capture_studio.files import load_points, save_points
from bmw_capture_studio.input_control import INPUT
from bmw_capture_studio.models import CameraPose, CapturePoint
from bmw_capture_studio.paths import BRIDGE_PATH


def pose(x: float = 1.0) -> CameraPose:
    return CameraPose(
        x=x,
        y=2.0,
        z=3.0,
        yaw_degrees=10.0,
        pitch_degrees=-5.0,
        roll_degrees=0.0,
        fov_degrees=63.0,
    )


class FakeBridge:
    def __init__(self) -> None:
        self.current = pose()

    def read_pose(self) -> CameraPose:
        return self.current


class FakeMover:
    def __init__(self, bridge: FakeBridge) -> None:
        self.bridge = bridge

    def move_to(self, target, *, stop_requested, on_update):
        if stop_requested():
            raise InterruptedError
        self.bridge.current = target
        return target


class CoreTests(unittest.TestCase):
    def test_camera_structure_is_84_bytes(self) -> None:
        self.assertEqual(CAMERA_DATA.size, 84)

    def test_send_input_structure_is_40_bytes_on_x64(self) -> None:
        import ctypes

        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(INPUT), 40)

    def test_bridge_metadata_structure_is_40_bytes(self) -> None:
        self.assertEqual(BRIDGE_METADATA.size, 40)

    @unittest.skipUnless(
        sys.platform == "win32" and BRIDGE_PATH.exists(),
        "compiled Windows bridge is not available",
    )
    def test_native_bridge_records_connector_handshake(self) -> None:
        import ctypes

        dll = ctypes.WinDLL(str(BRIDGE_PATH))
        dll.connectFromCameraTools.argtypes = []
        dll.connectFromCameraTools.restype = ctypes.c_bool
        dll.getDataFromCameraToolsBuffer.argtypes = []
        dll.getDataFromCameraToolsBuffer.restype = ctypes.c_void_p
        bridge = UuuPoseBridge()
        try:
            initial = bridge.read_metadata()
            self.assertIsNotNone(initial)
            assert initial is not None
            self.assertEqual(initial.process_id, os.getpid())
            self.assertEqual(initial.buffer_request_count, 0)
            self.assertTrue(dll.connectFromCameraTools())
            self.assertTrue(dll.getDataFromCameraToolsBuffer())
            connected = bridge.read_metadata()
            self.assertIsNotNone(connected)
            assert connected is not None
            self.assertGreaterEqual(connected.connect_call_count, 1)
            self.assertGreaterEqual(connected.buffer_request_count, 1)
            self.assertTrue(connected.connector_called)
            self.assertTrue(connected.buffer_requested)
        finally:
            bridge.close()

    def test_connection_blocks_uuu_before_bridge(self) -> None:
        report = classify_connection(
            {
                "game_running": True,
                "module_scan_ok": True,
                "pid": 42,
                "bridge_loaded": False,
                "uuu_loaded": True,
            },
            None,
            {"connected": False},
        )
        self.assertEqual(report.code, "restart_required")

    def test_connection_requires_real_connector_handshake(self) -> None:
        metadata = BridgeMetadata(
            version=METADATA_VERSION,
            size=BRIDGE_METADATA.size,
            process_id=42,
            connect_call_count=0,
            buffer_request_count=0,
            flags=FLAG_BRIDGE_LOADED,
            load_tick_milliseconds=1,
        )
        report = classify_connection(
            {
                "game_running": True,
                "module_scan_ok": True,
                "pid": 42,
                "bridge_loaded": True,
                "uuu_loaded": True,
            },
            metadata,
            {"connected": False},
        )
        self.assertEqual(report.code, "handshake_missing")

    def test_connection_ready_needs_pose_and_camera(self) -> None:
        metadata = BridgeMetadata(
            version=METADATA_VERSION,
            size=BRIDGE_METADATA.size,
            process_id=42,
            connect_call_count=1,
            buffer_request_count=1,
            flags=(
                FLAG_BRIDGE_LOADED | FLAG_CONNECT_CALLED | FLAG_BUFFER_REQUESTED
            ),
            load_tick_milliseconds=1,
        )
        current = pose()
        report = classify_connection(
            {
                "game_running": True,
                "module_scan_ok": True,
                "pid": 42,
                "bridge_loaded": True,
                "uuu_loaded": True,
            },
            metadata,
            {"connected": True, "pose": current},
        )
        self.assertEqual(report.code, "ready")
        self.assertIs(report.pose, current)

    def test_point_json_round_trip(self) -> None:
        points = [CapturePoint(index=1, label="a", pose=pose())]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "points.json"
            save_points(path, points, kind="points")
            loaded = load_points(path)
        self.assertEqual(loaded, points)

    def test_capture_runner_writes_images_and_manifest(self) -> None:
        bridge = FakeBridge()
        mover = FakeMover(bridge)

        def screenshotter(_pid, target):
            path = Path(target)
            path.write_bytes(b"test-image")
            return path

        runner = CaptureRunner(
            bridge=bridge,
            mover=mover,
            pid=123,
            settle_seconds=0,
            screenshotter=screenshotter,
        )
        points = [
            CapturePoint(index=1, label="first", pose=pose(1.0)),
            CapturePoint(index=2, label="second", pose=pose(4.0)),
        ]
        with tempfile.TemporaryDirectory() as directory:
            result = runner.run(points, directory, mode="test")
            payload = json.loads(result.manifest_json.read_text(encoding="utf-8"))
            self.assertEqual(result.captured_count, 2)
            self.assertEqual(payload["status"], "complete")
            self.assertEqual(len(list((result.session_dir / "images").glob("*.png"))), 2)


if __name__ == "__main__":
    unittest.main()
