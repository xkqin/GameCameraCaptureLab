from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from bmw_capture_studio.obs_bridge import OBSBridge


class SceneResponse:
    current_program_scene_name = "Program"


class FakeOBSClient:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def get_current_program_scene(self) -> SceneResponse:
        return SceneResponse()

    def save_source_screenshot(self, *args: object) -> None:
        self.calls.append(args)
        Path(str(args[2])).write_bytes(b"obs-image")


class OBSBridgeTests(unittest.TestCase):
    def test_save_screenshot_uses_program_source_and_waits_for_file(self) -> None:
        bridge = OBSBridge.__new__(OBSBridge)
        bridge.client = FakeOBSClient()

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "frame.png"
            target.write_bytes(b"stale-image")
            source = bridge.save_screenshot(target, image_format="png")

            self.assertEqual(source, "Program")
            self.assertEqual(target.read_bytes(), b"obs-image")
            self.assertEqual(
                bridge.client.calls,
                [("Program", "png", str(target.resolve()), 1920, 1080, 100)],
            )

    def test_save_screenshot_rejects_unknown_format_before_calling_obs(self) -> None:
        bridge = OBSBridge.__new__(OBSBridge)
        bridge.client = FakeOBSClient()

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                bridge.save_screenshot(Path(directory) / "frame.webp", image_format="webp")
        self.assertEqual(bridge.client.calls, [])

    def test_video_canvas_size_uses_obs_dimensions_and_falls_back_safely(self) -> None:
        bridge = OBSBridge.__new__(OBSBridge)
        bridge.client = FakeOBSClient()
        self.assertEqual(bridge.video_canvas_size(), (1920, 1080))

        class SizedClient(FakeOBSClient):
            def get_video_settings(self):
                return {"base_width": 2560, "base_height": 1440}

        bridge.client = SizedClient()
        self.assertEqual(bridge.video_canvas_size(), (2560, 1440))

    def test_capture_size_is_fixed_full_hd_even_when_obs_is_2k(self) -> None:
        bridge = OBSBridge.__new__(OBSBridge)
        bridge.client = FakeOBSClient()
        self.assertEqual(bridge.capture_size(), (1920, 1080))

        class SizedClient(FakeOBSClient):
            def get_video_settings(self):
                return {"base_width": 2560, "base_height": 1440}

        bridge.client = SizedClient()
        self.assertEqual(bridge.capture_size(), (1920, 1080))


if __name__ == "__main__":
    unittest.main()
