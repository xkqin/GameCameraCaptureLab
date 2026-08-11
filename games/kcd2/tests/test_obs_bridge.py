from __future__ import annotations

import unittest

from kcd2_capture_studio.obs_bridge import OBSBridge


class Response:
    def __init__(self, *, active: bool, output_path: str | None = None) -> None:
        self.output_active = active
        self.output_paused = False
        self.output_timecode = "00:00:00.000"
        self.output_path = output_path


class FakeClient:
    def __init__(self, statuses: list[bool]) -> None:
        self.statuses = statuses
        self.started = 0
        self.stopped = 0

    def start_record(self) -> None:
        self.started += 1

    def get_record_status(self) -> Response:
        active = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return Response(active=active)

    def stop_record(self) -> Response:
        self.stopped += 1
        return Response(active=False, output_path="video.mp4")


class OBSBridgeTests(unittest.TestCase):
    def test_start_waits_until_recording_is_active(self) -> None:
        bridge = OBSBridge.__new__(OBSBridge)
        bridge.client = FakeClient([False, True])
        bridge.start_recording()
        self.assertEqual(bridge.client.started, 1)

    def test_stop_skips_request_when_recording_never_became_active(self) -> None:
        bridge = OBSBridge.__new__(OBSBridge)
        bridge.client = FakeClient([False])
        self.assertIsNone(bridge.stop_recording())
        self.assertEqual(bridge.client.stopped, 0)

    def test_stop_returns_output_when_active(self) -> None:
        bridge = OBSBridge.__new__(OBSBridge)
        bridge.client = FakeClient([True])
        self.assertEqual(bridge.stop_recording(), "video.mp4")
        self.assertEqual(bridge.client.stopped, 1)


if __name__ == "__main__":
    unittest.main()
