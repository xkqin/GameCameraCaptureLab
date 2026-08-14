from __future__ import annotations

from collections import Counter
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from bmw_capture_studio.bridge import (
    BRIDGE_METADATA,
    CAMERA_DATA,
    CONTROL_COMMAND,
    CONTROL_HEADER,
    CONTROL_OFFSET,
    TRAJECTORY_HEADER,
    TRAJECTORY_KEYFRAME,
    TRAJECTORY_OFFSET,
    FLAG_BRIDGE_LOADED,
    FLAG_BUFFER_REQUESTED,
    FLAG_CONNECT_CALLED,
    METADATA_VERSION,
    BridgeMetadata,
    NativeControlStatus,
    UuuPoseBridge,
)
from bmw_capture_studio.capture_runner import CaptureRunner
from bmw_capture_studio.connection import classify_connection
from bmw_capture_studio.files import load_points, save_points
from bmw_capture_studio.input_control import ClosedLoopMover, INPUT
from bmw_capture_studio.models import CameraPose, CapturePoint
from bmw_capture_studio.paths import BRIDGE_PATH
from bmw_capture_studio.settings import load_settings
from bmw_capture_studio.still_scan import (
    build_22_view_plan,
    find_latest_resumable_static_run,
    view_pattern_manifest,
)
from bmw_capture_studio.trajectory_catalog import (
    build_trajectory_choice_map,
    discover_trajectory_files,
)


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


class FailingCaptureMover(FakeMover):
    def __init__(self, bridge: FakeBridge) -> None:
        super().__init__(bridge)
        self.targets: list[CameraPose] = []

    def move_to(self, target, *, stop_requested, on_update):
        self.targets.append(target)
        if len(self.targets) == 1:
            self.bridge.current = pose(500.0)
            raise RuntimeError("simulated capture move failure")
        self.bridge.current = target
        return target


class FakeControl:
    def __init__(self, ready: bool) -> None:
        self.ready = ready
        self.error_message = "test status"


class FakeTrajectory:
    pass


class FakeNativeBridge(FakeBridge):
    """Small UUU camera model used to exercise the absolute-pose loop."""

    def __init__(self, *, movement_response: float = 1.0) -> None:
        super().__init__()
        self.commands: list[dict[str, float | bool]] = []
        self.movement_response = movement_response

    def read_control_status(self) -> FakeControl:
        return FakeControl(True)

    def apply_native_step(self, **command) -> FakeControl:
        self.commands.append(command)
        self._apply_command(command)
        return FakeControl(True)

    def _apply_command(self, command) -> None:
        current = self.current
        # The default CameraPose basis is right=X, forward=Y, up=Z. Model the
        # public command semantics after the in-process bridge has translated
        # UUU's inverted internal roll argument.
        self.current = CameraPose(
            x=current.x + float(command["move_right"]) * self.movement_response,
            y=current.y + float(command["move_forward"]) * self.movement_response,
            z=current.z + float(command["move_up"]) * self.movement_response,
            yaw_degrees=current.yaw_degrees
            + math.degrees(float(command["yaw_radians"])),
            pitch_degrees=current.pitch_degrees
            + math.degrees(float(command["pitch_radians"])),
            roll_degrees=current.roll_degrees
            + math.degrees(float(command["roll_radians"])),
            fov_degrees=(
                float(command["fov_degrees"])
                if bool(command["set_fov"])
                else current.fov_degrees
            ),
        )


class DelayedFeedbackBridge(FakeNativeBridge):
    """Apply each command only after several pose reads, like a later frame."""

    def __init__(self) -> None:
        super().__init__()
        self.pending: tuple[dict[str, float | bool], int] | None = None

    def apply_native_step(self, **command) -> FakeControl:
        if self.pending is not None:
            raise AssertionError("a second command was sent before pose feedback changed")
        self.commands.append(command)
        self.pending = (command, 3)
        return FakeControl(True)

    def read_pose(self) -> CameraPose:
        if self.pending is not None:
            command, remaining = self.pending
            remaining -= 1
            if remaining <= 0:
                self.pending = None
                self._apply_command(command)
            else:
                self.pending = (command, remaining)
        return self.current


class FrozenFeedbackBridge(FakeNativeBridge):
    """Accept native commands without ever publishing a changed pose."""

    def apply_native_step(self, **command) -> FakeControl:
        self.commands.append(command)
        return FakeControl(True)


class CoreTests(unittest.TestCase):
    def test_static_resume_uses_global_plan_total_and_next_sample(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "scene_1_static22_partial"
            run.mkdir()
            frames = [
                {"sample_index": 22, "image": "images/00001.png"},
                {"sample_index": 23, "image": "images/00002.png"},
            ]
            (run / "manifest.json").write_text(
                json.dumps(
                    {
                        "status": "stopped",
                        "requested_count": 2179,
                        "captured_count": 2,
                        "frames": frames,
                        "capture_plan": {
                            "scene_id": "scene_1",
                            "expected_image_count": 2200,
                            "selected_start_ordinal": 1,
                            "selected_start_sample": 22,
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = find_latest_resumable_static_run(
                root, scene_id="scene_1"
            )
            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result["requested_count"], 2200)
            self.assertEqual(result["last_sample"], 23)
            self.assertEqual(result["next_sample"], 24)

    def test_static_resume_hides_source_after_continuation_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "old"
            child = root / "child"
            source.mkdir()
            child.mkdir()
            source_manifest = source / "manifest.json"
            source_manifest.write_text(
                json.dumps(
                    {
                        "status": "stopped",
                        "requested_count": 2200,
                        "captured_count": 21,
                        "frames": [{"sample_index": 21}],
                        "capture_plan": {
                            "scene_id": "scene_1",
                            "expected_image_count": 2200,
                            "selected_start_ordinal": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            child_manifest = child / "manifest.json"
            child_manifest.write_text(
                json.dumps(
                    {
                        "status": "stopped",
                        "requested_count": 2179,
                        "captured_count": 0,
                        "frames": [],
                        "capture_plan": {
                            "scene_id": "scene_1",
                            "expected_image_count": 2200,
                            "selected_start_ordinal": 1,
                            "selected_start_sample": 22,
                            "resume_source_manifest": str(source_manifest),
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = find_latest_resumable_static_run(
                root, scene_id="scene_1"
            )
            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(
                Path(str(result["manifest_path"])), child_manifest.resolve()
            )
            self.assertEqual(result["next_sample"], 22)

    def test_capture_window_is_pinned_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missing_settings = Path(temp_dir) / "settings.json"
            self.assertTrue(load_settings(missing_settings)["always_on_top"])

    def test_trajectory_catalog_discovers_supported_files_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "nested"
            nested.mkdir()
            first = root / "alpha.json"
            second = nested / "beta.csv"
            ignored = root / "notes.txt"
            first.write_text("{}", encoding="utf-8")
            second.write_text("x,y\n1,2\n", encoding="utf-8")
            ignored.write_text("ignore", encoding="utf-8")

            files = discover_trajectory_files((root,), extra_paths=(first,))

            self.assertEqual(set(files), {first.resolve(), second.resolve()})

    def test_trajectory_choice_map_uses_short_filename_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "examples" / "trajectory_files" / "demo.json"
            path.parent.mkdir(parents=True)
            path.write_text("{}", encoding="utf-8")

            choices = build_trajectory_choice_map((path,), project_root=root)

            self.assertEqual(list(choices), ["demo.json"])
            self.assertEqual(list(choices.values()), [path.resolve()])

    def test_trajectory_choice_map_disambiguates_duplicate_filenames(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "examples" / "trajectory_files" / "demo.json"
            second = root / "capture_data" / "trajectory_files" / "demo.json"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_text("{}", encoding="utf-8")
            second.write_text("{}", encoding="utf-8")

            choices = build_trajectory_choice_map((first, second), project_root=root)

            self.assertEqual(
                list(choices),
                [
                    str(Path("trajectory_files") / "demo.json"),
                    str(second.relative_to(root)),
                ],
            )
            self.assertEqual(list(choices.values()), [first.resolve(), second.resolve()])

    def test_v7_timed_playback_clears_smoothing_and_has_no_terminal_feedback(self) -> None:
        source = (
            Path(__file__).resolve().parents[1] / "native" / "BmwUuuPoseBridge.cpp"
        ).read_text(encoding="utf-8")
        self.assertEqual(METADATA_VERSION, 7)
        self.assertIn("constexpr std::uint32_t kMetadataVersion = 7;", source)
        self.assertIn("kMoveRightSmoothingOffset = 0x5C", source)
        self.assertIn("kMoveForwardSmoothingOffset = 0x60", source)
        self.assertIn("kMoveUpSmoothingOffset = 0x64", source)
        self.assertIn("kPitchSmoothingOffset = 0x68", source)
        self.assertIn("kYawSmoothingOffset = 0x6C", source)
        self.assertIn("kRollSmoothingOffset = 0x70", source)
        self.assertIn("clearUuuSmoothingState(feature)", source)
        self.assertIn("Start from rest.", source)
        self.assertIn("Finish at rest", source)
        self.assertIn("mid-path and terminal feedback are deliberately excluded", source)
        self.assertNotIn("worldX += (target.x - camera.x) * 0.25f;", source)
        self.assertNotIn("worldX = (target.x - camera.x) * 0.85f;", source)

    def test_camera_structure_is_84_bytes(self) -> None:
        self.assertEqual(CAMERA_DATA.size, 84)

    def test_send_input_structure_is_40_bytes_on_x64(self) -> None:
        import ctypes

        if ctypes.sizeof(ctypes.c_void_p) == 8:
            self.assertEqual(ctypes.sizeof(INPUT), 40)

    def test_bridge_metadata_structure_is_40_bytes(self) -> None:
        self.assertEqual(BRIDGE_METADATA.size, 40)

    def test_native_control_structure_is_64_bytes(self) -> None:
        self.assertEqual(CONTROL_HEADER.size + CONTROL_COMMAND.size, 64)
        self.assertEqual(CONTROL_OFFSET, 512)

    def test_native_trajectory_structure_is_64_bytes(self) -> None:
        self.assertEqual(TRAJECTORY_HEADER.size, 64)
        self.assertEqual(TRAJECTORY_KEYFRAME.size, 32)
        self.assertEqual(TRAJECTORY_OFFSET, 1024)

    def test_ready_control_status_hides_stale_readiness_error(self) -> None:
        control = NativeControlStatus(
            request_sequence=0,
            acknowledge_sequence=0,
            state=0,
            error_code=3,
            capabilities=0x7F,
        )
        self.assertTrue(control.ready)
        self.assertEqual(control.error_message, "no error")

    @unittest.skipUnless(
        sys.platform == "win32"
        and BRIDGE_PATH.exists()
        and os.environ.get("BMW_RUN_DLL_INTEGRATION_TESTS") == "1",
        "set BMW_RUN_DLL_INTEGRATION_TESTS=1 to load the compiled bridge",
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
            control = bridge.read_control_status()
            self.assertIsNotNone(control)
            assert control is not None
            # Unit tests load the bridge without UUU; native control must stay
            # safely unavailable instead of dereferencing version offsets.
            self.assertFalse(control.ready)
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
            {
                "connected": True,
                "pose": current,
                "control": FakeControl(True),
                "trajectory": FakeTrajectory(),
            },
        )
        self.assertEqual(report.code, "ready")
        self.assertIs(report.pose, current)

    def test_connection_rejects_old_read_only_bridge(self) -> None:
        metadata = BridgeMetadata(
            version=METADATA_VERSION,
            size=BRIDGE_METADATA.size,
            process_id=42,
            connect_call_count=1,
            buffer_request_count=1,
            flags=FLAG_BRIDGE_LOADED | FLAG_CONNECT_CALLED | FLAG_BUFFER_REQUESTED,
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
            {"connected": True, "pose": pose()},
        )
        self.assertEqual(report.code, "native_control_outdated")

    def test_connection_rejects_bridge_without_smooth_trajectory_protocol(self) -> None:
        metadata = BridgeMetadata(
            version=METADATA_VERSION,
            size=BRIDGE_METADATA.size,
            process_id=42,
            connect_call_count=1,
            buffer_request_count=1,
            flags=FLAG_BRIDGE_LOADED | FLAG_CONNECT_CALLED | FLAG_BUFFER_REQUESTED,
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
            {"connected": True, "pose": pose(), "control": FakeControl(True)},
        )
        self.assertEqual(report.code, "smooth_trajectory_outdated")

    def test_native_closed_loop_converges_xyz_angles_roll_and_fov(self) -> None:
        bridge = FakeNativeBridge()
        target = CameraPose(
            x=5.0,
            y=-3.0,
            z=7.0,
            yaw_degrees=14.0,
            pitch_degrees=-9.0,
            roll_degrees=6.0,
            fov_degrees=75.0,
        )
        mover = ClosedLoopMover(
            bridge,
            position_tolerance=0.05,
            angle_tolerance=0.05,
            fov_tolerance=0.05,
            move_pulse_sec=0.01,
            rotate_pulse_sec=0.01,
            max_seconds=2.0,
            focus_game=lambda: None,
        )

        with patch("bmw_capture_studio.input_control.time.sleep", return_value=None):
            actual = mover.move_to(target)

        self.assertLessEqual(abs(actual.x - target.x), 0.05)
        self.assertLessEqual(abs(actual.y - target.y), 0.05)
        self.assertLessEqual(abs(actual.z - target.z), 0.05)
        self.assertLessEqual(abs(actual.yaw_degrees - target.yaw_degrees), 0.05)
        self.assertLessEqual(abs(actual.pitch_degrees - target.pitch_degrees), 0.05)
        self.assertLessEqual(abs(actual.roll_degrees - target.roll_degrees), 0.05)
        self.assertLessEqual(abs(actual.fov_degrees - target.fov_degrees), 0.05)
        self.assertGreater(len(bridge.commands), 0)
        first = bridge.commands[0]
        self.assertGreater(float(first["move_right"]), 0.0)
        self.assertLess(float(first["move_forward"]), 0.0)
        self.assertGreater(float(first["move_up"]), 0.0)
        self.assertEqual(float(first["yaw_radians"]), 0.0)
        self.assertEqual(float(first["pitch_radians"]), 0.0)
        self.assertEqual(float(first["roll_radians"]), 0.0)
        self.assertFalse(bool(first["set_fov"]))

        first_orientation = next(
            index
            for index, command in enumerate(bridge.commands)
            if any(
                abs(float(command[name])) > 0.0
                for name in ("yaw_radians", "pitch_radians", "roll_radians")
            )
            or bool(command["set_fov"])
        )
        for command in bridge.commands[:first_orientation]:
            self.assertEqual(float(command["yaw_radians"]), 0.0)
            self.assertEqual(float(command["pitch_radians"]), 0.0)
            self.assertEqual(float(command["roll_radians"]), 0.0)
            self.assertFalse(bool(command["set_fov"]))
        for command in bridge.commands[first_orientation:]:
            self.assertEqual(float(command["move_forward"]), 0.0)
            self.assertEqual(float(command["move_right"]), 0.0)
            self.assertEqual(float(command["move_up"]), 0.0)

    def test_native_closed_loop_converges_over_800_units_in_two_phases(self) -> None:
        # Model UUU movement being applied as a per-frame fraction of the
        # requested amount. This is the regime in which the old fixed 8-unit
        # cap could not complete a long move within the default timeout.
        bridge = FakeNativeBridge(movement_response=1.0 / 60.0)
        target = CameraPose(
            x=801.0,
            y=2.0,
            z=3.0,
            yaw_degrees=22.0,
            pitch_degrees=-5.0,
            roll_degrees=0.0,
            fov_degrees=70.0,
        )
        mover = ClosedLoopMover(
            bridge,
            position_tolerance=4.0,
            angle_tolerance=1.5,
            fov_tolerance=0.5,
            move_pulse_sec=0.01,
            rotate_pulse_sec=0.01,
            max_seconds=25.0,
            focus_game=lambda: None,
        )

        with patch("bmw_capture_studio.input_control.time.sleep", return_value=None):
            actual = mover.move_to(target)

        self.assertLessEqual(abs(actual.x - target.x), 4.0)
        self.assertLessEqual(abs(actual.yaw_degrees - target.yaw_degrees), 1.5)
        self.assertLessEqual(abs(actual.fov_degrees - target.fov_degrees), 0.5)
        self.assertGreater(float(bridge.commands[0]["move_right"]), 8.0)
        self.assertLess(len(bridge.commands), 1000)
        orientation_started = False
        for command in bridge.commands:
            rotates = any(
                abs(float(command[name])) > 0.0
                for name in ("yaw_radians", "pitch_radians", "roll_radians")
            ) or bool(command["set_fov"])
            translates = any(
                abs(float(command[name])) > 0.0
                for name in ("move_forward", "move_right", "move_up")
            )
            orientation_started = orientation_started or rotates
            if orientation_started:
                self.assertFalse(translates)
            else:
                self.assertFalse(rotates)

    def test_native_closed_loop_waits_for_new_pose_before_next_command(self) -> None:
        bridge = DelayedFeedbackBridge()
        target = CameraPose(
            x=20.0,
            y=2.0,
            z=3.0,
            yaw_degrees=10.0,
            pitch_degrees=-5.0,
            roll_degrees=0.0,
            fov_degrees=63.0,
        )
        mover = ClosedLoopMover(
            bridge,
            position_tolerance=0.05,
            angle_tolerance=0.05,
            fov_tolerance=0.05,
            move_pulse_sec=0.01,
            rotate_pulse_sec=0.01,
            max_seconds=2.0,
            focus_game=lambda: None,
        )

        with patch("bmw_capture_studio.input_control.time.sleep", return_value=None):
            actual = mover.move_to(target)

        self.assertLessEqual(abs(actual.x - target.x), 0.05)
        self.assertIsNone(bridge.pending)
        self.assertGreater(len(bridge.commands), 1)

    def test_native_closed_loop_stops_before_repeating_stale_feedback(self) -> None:
        bridge = FrozenFeedbackBridge()
        target = CameraPose(
            x=20.0,
            y=2.0,
            z=3.0,
            yaw_degrees=10.0,
            pitch_degrees=-5.0,
            roll_degrees=0.0,
            fov_degrees=63.0,
        )
        mover = ClosedLoopMover(
            bridge,
            position_tolerance=0.05,
            angle_tolerance=0.05,
            fov_tolerance=0.05,
            move_pulse_sec=0.01,
            rotate_pulse_sec=0.01,
            max_seconds=2.0,
            focus_game=lambda: None,
        )

        with self.assertRaisesRegex(TimeoutError, "no new Pose feedback"):
            mover.move_to(target)

        self.assertEqual(len(bridge.commands), 1)

    def test_point_json_round_trip(self) -> None:
        points = [CapturePoint(index=1, label="a", pose=pose())]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "points.json"
            save_points(path, points, kind="points")
            loaded = load_points(path)
        self.assertEqual(loaded, points)

    def test_empty_active_point_map_can_be_restored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "current_point_map.json"
            save_points(path, [], kind="points")
            self.assertEqual(load_points(path, allow_empty=True), [])
            self.assertFalse(path.with_name(f".{path.name}.tmp").exists())

    def test_22_view_plan_matches_re9_and_kcd2_pattern(self) -> None:
        base = CapturePoint(index=7, label="room_corner", pose=pose())
        samples = build_22_view_plan([base])

        self.assertEqual(len(samples), 22)
        self.assertEqual(
            Counter(sample.pattern for sample in samples),
            {
                "middle": 8,
                "upper": 6,
                "lower": 6,
                "ceiling": 1,
                "floor": 1,
            },
        )
        self.assertEqual(
            [sample.pose.yaw_degrees for sample in samples if sample.pattern == "middle"],
            [0.0, 45.0, 90.0, 135.0, 180.0, 225.0, 270.0, 315.0],
        )
        self.assertEqual(
            [sample.pose.yaw_degrees for sample in samples if sample.pattern == "upper"],
            [0.0, 60.0, 120.0, 180.0, 240.0, 300.0],
        )
        self.assertTrue(all(sample.point_index == 7 for sample in samples))
        self.assertTrue(all(sample.source_point_label == "room_corner" for sample in samples))
        self.assertTrue(all(sample.pose.x == base.pose.x for sample in samples))
        self.assertTrue(all(sample.pose.y == base.pose.y for sample in samples))
        self.assertTrue(all(sample.pose.z == base.pose.z for sample in samples))
        self.assertTrue(all(sample.pose.fov_degrees == base.pose.fov_degrees for sample in samples))
        self.assertTrue(all(sample.pose.roll_degrees == 0.0 for sample in samples))
        self.assertEqual(sum(item["view_count"] for item in view_pattern_manifest()), 22)

    def test_22_view_plan_preserves_spatial_point_grouping(self) -> None:
        points = [
            CapturePoint(index=3, label="left", pose=pose(10.0)),
            CapturePoint(index=9, label="right", pose=pose(20.0)),
        ]

        samples = build_22_view_plan(points)

        self.assertEqual(len(samples), 44)
        self.assertEqual([sample.point_index for sample in samples[:22]], [3] * 22)
        self.assertEqual([sample.point_index for sample in samples[22:]], [9] * 22)
        self.assertEqual([sample.view_index for sample in samples[:22]], list(range(1, 23)))
        self.assertEqual([sample.sample_index for sample in samples], list(range(1, 45)))

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
            self.assertTrue(payload["absolute_target_pose"])
            self.assertFalse(payload["atomic_absolute_set_pose"])
            self.assertFalse(payload["restore_attempted"])
            self.assertEqual(len(list((result.session_dir / "images").glob("*.png"))), 2)

    def test_capture_runner_writes_22_view_sample_metadata(self) -> None:
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
        sample = build_22_view_plan(
            [CapturePoint(index=4, label="anchor", pose=pose())]
        )[0]
        with tempfile.TemporaryDirectory() as directory:
            result = runner.run(
                [sample],
                directory,
                mode="static22",
                run_metadata={"views_per_point": 22, "spatial_point_count": 1},
            )
            payload = json.loads(result.manifest_json.read_text(encoding="utf-8"))

        self.assertEqual(payload["capture_plan"]["views_per_point"], 22)
        self.assertEqual(payload["frames"][0]["sample_index"], 1)
        self.assertEqual(payload["frames"][0]["point_index"], 4)
        self.assertEqual(payload["frames"][0]["view_index"], 1)
        self.assertEqual(payload["frames"][0]["pattern"], "middle")
        self.assertEqual(payload["frames"][0]["source_point_label"], "anchor")

    def test_capture_runner_restores_start_pose_after_move_failure(self) -> None:
        bridge = FakeBridge()
        start_pose = bridge.current
        mover = FailingCaptureMover(bridge)
        runner = CaptureRunner(
            bridge=bridge,
            mover=mover,
            pid=123,
            settle_seconds=0,
            screenshotter=lambda _pid, target: Path(target),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "simulated capture move failure"):
                runner.run(
                    [CapturePoint(index=1, label="first", pose=pose(10.0))],
                    root,
                    mode="test",
                )
            session = next(root.iterdir())
            payload = json.loads((session / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(len(mover.targets), 2)
        self.assertEqual(mover.targets[1], start_pose)
        self.assertEqual(bridge.current, start_pose)
        self.assertTrue(payload["restore_attempted"])
        self.assertTrue(payload["restore_succeeded"])
        self.assertIsNone(payload["restore_error"])


if __name__ == "__main__":
    unittest.main()
