from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from bmw_capture_studio.depth_bridge import DepthBridge


class NativeDepthBridgeTests(unittest.TestCase):
    def test_status_reads_repository_owned_runtime_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            channel = Path(temporary)
            (channel / "runtime_status.json").write_text(
                json.dumps(
                    {
                        "protocol": "game-camera-depth-bridge/v2",
                        "backend": "native_d3d12_runtime",
                        "process_id": 1234,
                        "state": "idle",
                    }
                ),
                encoding="utf-8",
            )
            status = DepthBridge(channel).status()

        self.assertEqual(status["backend"], "native_d3d12_runtime")
        self.assertEqual(status["protocol"], "game-camera-depth-bridge/v2")
        self.assertEqual(status["runtime"]["state"], "idle")

    def test_request_uses_native_v2_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bridge = DepthBridge(temporary)
            ticket = bridge.begin_capture()
            request = json.loads(ticket.request_path.read_text(encoding="utf-8"))
            bridge.cancel(ticket)

        self.assertEqual(request["protocol"], "game-camera-depth-bridge/v2")
        self.assertEqual(request["request_id"], ticket.request_id)

    def test_runtime_build_has_no_reshade_dependency(self) -> None:
        native_root = Path(__file__).resolve().parents[1] / "native"
        cmake = (native_root / "CMakeLists.txt").read_text(encoding="utf-8")
        source = (
            native_root / "standalone" / "BmwNativeDepth.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn("BmwNativeDepth.cpp", cmake)
        self.assertIn("native_d3d12_runtime", source)
        self.assertNotIn("reshade", cmake.casefold())
        self.assertNotIn("reshade", source.casefold())

    def test_present_hook_never_waits_for_or_maps_gpu_readback(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "native"
            / "standalone"
            / "BmwNativeDepth.cpp"
        ).read_text(encoding="utf-8")
        present_start = source.index("void trySubmitDepthFromPresent")
        present_path = source[
            present_start :
            source.index(
                "void STDMETHODCALLTYPE depthExecuteCommandListsHook",
                present_start,
            )
        ]

        self.assertNotIn("WaitForSingleObject", present_path)
        self.assertNotIn("SetEventOnCompletion", present_path)
        self.assertNotIn("->Map(", present_path)
        self.assertIn("submitDepthReadback", present_path)
        self.assertIn("g_inFlightCapture.emplace", present_path)

    def test_runtime_requires_dsv_evidence_and_reports_hook_counts(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "native"
            / "standalone"
            / "BmwNativeDepth.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn("dsv_binding_verified", source)
        self.assertIn("binding_evidence", source)
        self.assertIn("execute_command_lists", source)
        self.assertIn("transition_barriers", source)
        self.assertIn("enhanced barriers", source)
        self.assertIn("candidate.bindingEvidence == DepthBindingEvidence::none", source)


if __name__ == "__main__":
    unittest.main()
