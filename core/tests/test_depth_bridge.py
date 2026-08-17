from __future__ import annotations

import csv
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from re9_pose_recorder.config import AppConfig
from re9_pose_recorder.depth_bridge import (
    DEPTH_HEARTBEAT_FILENAME,
    DEPTH_REQUEST_FILENAME,
    DEPTH_STATUS_FILENAME,
    DepthBridge,
    DepthCaptureResult,
    decode_raw_depth,
    linearize_depth,
)
from re9_pose_recorder.still_scan import StillSample, _run_plan, _sample_fieldnames


def _projection(near: float, far: float, reversed_z: bool) -> tuple[float, ...]:
    if reversed_z:
        a = near / (near - far)
        b = -(near * far) / (near - far)
    else:
        a = far / (far - near)
        b = -(near * far) / (far - near)
    return (
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        a,
        b,
        0.0,
        0.0,
        1.0,
        0.0,
    )


def _raw_for_distance(distance: float, near: float, far: float, reversed_z: bool) -> float:
    matrix = np.asarray(_projection(near, far, reversed_z)).reshape(4, 4)
    return float((matrix[2, 2] * distance + matrix[2, 3]) / distance)


class DepthLinearizationTests(unittest.TestCase):
    def test_standard_z_linearizes_to_view_distance(self) -> None:
        near, far = 0.1, 100.0
        expected = np.asarray([[near, 1.0, 7.5, far]], dtype=np.float32)
        raw = np.vectorize(lambda value: _raw_for_distance(value, near, far, False))(expected)

        actual, reversed_z = linearize_depth(raw, _projection(near, far, False), near, far)

        self.assertFalse(reversed_z)
        np.testing.assert_allclose(actual, expected, rtol=2e-5, atol=2e-5)

    def test_reverse_z_linearizes_to_view_distance(self) -> None:
        near, far = 0.05, 500.0
        expected = np.asarray([[near, 2.0, 31.0, far]], dtype=np.float32)
        raw = np.vectorize(lambda value: _raw_for_distance(value, near, far, True))(expected)

        actual, reversed_z = linearize_depth(raw, _projection(near, far, True), near, far)

        self.assertTrue(reversed_z)
        np.testing.assert_allclose(actual, expected, rtol=2e-4, atol=2e-4)

    def test_transposed_projection_layout_is_detected(self) -> None:
        near, far = 0.1, 200.0
        expected = np.asarray([[0.5, 10.0]], dtype=np.float32)
        raw = np.vectorize(lambda value: _raw_for_distance(value, near, far, True))(expected)
        transposed = np.asarray(_projection(near, far, True)).reshape(4, 4).T.reshape(-1)

        actual, reversed_z = linearize_depth(raw, transposed.tolist(), near, far)

        self.assertTrue(reversed_z)
        np.testing.assert_allclose(actual, expected, rtol=2e-4, atol=2e-4)


class RawDepthDecodeTests(unittest.TestCase):
    def test_float32_rows_ignore_gpu_pitch_padding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "depth.raw"
            rows = bytearray(32)
            rows[0:8] = np.asarray([0.25, 0.5], dtype="<f4").tobytes()
            rows[16:24] = np.asarray([0.75, 1.0], dtype="<f4").tobytes()
            path.write_bytes(rows)

            decoded = decode_raw_depth(
                path,
                {
                    "width": 2,
                    "height": 2,
                    "row_pitch": 16,
                    "pixel_stride_bytes": 4,
                    "depth_encoding": "float32",
                },
            )

        np.testing.assert_allclose(decoded, [[0.25, 0.5], [0.75, 1.0]])

    def test_d24_unorm_ignores_stencil_byte(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "depth.raw"
            packed = np.asarray([0xAB000000, 0xCDFFFFFF], dtype="<u4")
            path.write_bytes(packed.tobytes())

            decoded = decode_raw_depth(
                path,
                {
                    "width": 2,
                    "height": 1,
                    "row_pitch": 8,
                    "pixel_stride_bytes": 4,
                    "depth_encoding": "d24_unorm",
                },
            )

        np.testing.assert_allclose(decoded, [[0.0, 1.0]])

    def test_d16_unorm_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "depth.raw"
            path.write_bytes(np.asarray([0, 32768, 65535], dtype="<u2").tobytes())

            decoded = decode_raw_depth(
                path,
                {
                    "width": 3,
                    "height": 1,
                    "row_pitch": 6,
                    "pixel_stride_bytes": 2,
                    "depth_encoding": "d16_unorm",
                },
            )

        np.testing.assert_allclose(decoded, [[0.0, 32768.0 / 65535.0, 1.0]])


class DepthProtocolTests(unittest.TestCase):
    def _config(self, root: Path) -> AppConfig:
        return AppConfig(
            raw={
                "game": {"reframework_data_dir": str(root)},
                "lua_logger": {
                    "control_file": str(root / "pose_control.json"),
                    "status_file": str(root / "pose_status.json"),
                },
            },
            path=root / "config.yaml",
        )

    def test_capture_rejects_stale_status_and_writes_all_products(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge = DepthBridge(self._config(root))
            near, far = 0.1, 100.0
            heartbeat = {
                "schema_version": 1,
                "status": "ready",
                "plugin_version": "test",
                "renderer": "D3D12",
                "updated_at_unix": time.time(),
            }
            (root / DEPTH_HEARTBEAT_FILENAME).write_text(json.dumps(heartbeat), encoding="utf-8")
            (root / DEPTH_STATUS_FILENAME).write_text(
                json.dumps({"capture_id": "stale", "status": "ok"}),
                encoding="utf-8",
            )

            def plugin() -> None:
                request_file = root / DEPTH_REQUEST_FILENAME
                deadline = time.monotonic() + 2.0
                while not request_file.exists() and time.monotonic() < deadline:
                    time.sleep(0.005)
                request = json.loads(request_file.read_text(encoding="utf-8"))
                raw_path = Path(request["raw_output_path"])
                raw_values = np.asarray(
                    [
                        _raw_for_distance(near, near, far, True),
                        _raw_for_distance(2.0, near, far, True),
                        _raw_for_distance(5.0, near, far, True),
                        _raw_for_distance(far, near, far, True),
                    ],
                    dtype="<f4",
                )
                raw_path.write_bytes(raw_values.tobytes())
                time.sleep(0.03)
                status = {
                    "schema_version": 1,
                    "capture_id": request["capture_id"],
                    "status": "ok",
                    "raw_path": str(raw_path.resolve()),
                    "width": 2,
                    "height": 2,
                    "row_pitch": 8,
                    "pixel_stride_bytes": 4,
                    "depth_encoding": "float32",
                    "near_clip": near,
                    "far_clip": far,
                    "projection_matrix": _projection(near, far, True),
                    "render_frame_id": 42,
                }
                (root / DEPTH_STATUS_FILENAME).write_text(json.dumps(status), encoding="utf-8")

            worker = threading.Thread(target=plugin)
            worker.start()
            try:
                result = bridge.capture(
                    capture_id="current",
                    dataset_dir=root / "dataset",
                    sample_id="sample",
                    timeout_sec=2.0,
                    expected_width=2,
                    expected_height=2,
                )
            finally:
                worker.join()

            self.assertEqual(result.capture_id, "current")
            self.assertTrue(result.reversed_z)
            self.assertEqual(result.render_frame_id, 42)
            self.assertEqual(result.valid_pixel_count, 3)
            self.assertTrue(result.depth_path.exists())
            self.assertTrue(result.preview_path.exists())
            self.assertTrue(result.valid_mask_path.exists())
            self.assertTrue(result.camera_metadata_path.exists())
            saved = np.load(result.depth_path, allow_pickle=False)
            self.assertEqual(saved.shape, (2, 2))
            self.assertTrue(np.isnan(saved[1, 1]))
            np.testing.assert_allclose(saved[0, 1], 2.0, rtol=2e-4)

    def test_wait_for_status_times_out_on_only_stale_capture_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bridge = DepthBridge(self._config(root))
            (root / DEPTH_STATUS_FILENAME).write_text(
                json.dumps({"capture_id": "stale", "status": "ok"}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(TimeoutError, "last plugin capture id was stale"):
                bridge._wait_for_capture_status("current", timeout_sec=0.05)


class StillScanDepthTransactionTests(unittest.TestCase):
    def test_failed_depth_removes_rgb_and_does_not_write_sample_row(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = AppConfig(
                raw={
                    "obs": {"host": "localhost", "port": 4455, "password": ""},
                    "lua_logger": {
                        "control_file": str(root / "pose_control.json"),
                        "status_file": str(root / "pose_status.json"),
                    },
                },
                path=root / "config.yaml",
            )
            sample = StillSample(
                sample_index=1,
                point_index=1,
                group_id="scene",
                layer_id="layer",
                zone_id="zone",
                height_index=1,
                pattern="middle",
                x=1.0,
                y=2.0,
                z=3.0,
                yaw_deg=0.0,
                yaw_rad=0.0,
                pitch_deg=0.0,
                pitch_rad=0.0,
            )

            class FakeOBS:
                def __init__(self, *_args: object, **_kwargs: object) -> None:
                    pass

                def save_source_screenshot(self, path: Path, **_kwargs: object) -> str:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"rgb")
                    return "Game"

            class FakeLua:
                def __init__(self, _config: AppConfig) -> None:
                    pass

                def write_set_pose_control(self, *_args: object, **_kwargs: object) -> None:
                    pass

                def wait_until_scan_pose(self, *_args: object, **_kwargs: object) -> bool:
                    return True

                def write_clear_pose_control(self, *_args: object, **_kwargs: object) -> None:
                    pass

            class FailingDepth:
                def wait_until_ready(self, timeout_sec: float) -> bool:
                    return True

                def capture(self, **_kwargs: object) -> None:
                    raise RuntimeError("depth failed")

            def local_ensure(path: str | Path) -> Path:
                candidate = Path(path)
                destination = candidate if candidate.is_absolute() else root / candidate
                destination.mkdir(parents=True, exist_ok=True)
                return destination

            with (
                patch("re9_pose_recorder.still_scan.OBSController", FakeOBS),
                patch("re9_pose_recorder.still_scan.LuaControl", FakeLua),
                patch("re9_pose_recorder.still_scan.ensure_dir", side_effect=local_ensure),
                self.assertRaisesRegex(RuntimeError, "depth failed"),
            ):
                _run_plan(
                    config=config,
                    obs_password="",
                    plan=[sample],
                    settle_seconds=0.0,
                    source_name=None,
                    image_format="jpg",
                    image_width=2,
                    image_height=2,
                    image_quality=100,
                    session_id="transaction",
                    max_samples=None,
                    progress_callback=None,
                    stop_event=None,
                    capture_depth=True,
                    depth_timeout=1.0,
                    depth_bridge=FailingDepth(),  # type: ignore[arg-type]
                )

            output = root / "data" / "stills" / "scans" / "transaction"
            image_files = list(output.rglob("*.jpg"))
            self.assertEqual(image_files, [])
            rows = (output / "samples.csv").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)

    def test_depth_resume_migrates_old_rgb_csv_without_losing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = AppConfig(
                raw={
                    "obs": {"host": "localhost", "port": 4455, "password": ""},
                    "lua_logger": {
                        "control_file": str(root / "pose_control.json"),
                        "status_file": str(root / "pose_status.json"),
                    },
                },
                path=root / "config.yaml",
            )
            sample = StillSample(
                sample_index=2,
                point_index=2,
                group_id="scene",
                layer_id="layer_2",
                zone_id="zone",
                height_index=2,
                pattern="middle",
                x=2.0,
                y=3.0,
                z=4.0,
                yaw_deg=45.0,
                yaw_rad=0.785398,
                pitch_deg=0.0,
                pitch_rad=0.0,
            )

            class FakeOBS:
                def __init__(self, *_args: object, **_kwargs: object) -> None:
                    pass

                def save_source_screenshot(self, path: Path, **_kwargs: object) -> str:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b"rgb")
                    return "Game"

            class FakeLua:
                def __init__(self, _config: AppConfig) -> None:
                    pass

                def write_set_pose_control(self, *_args: object, **_kwargs: object) -> None:
                    pass

                def wait_until_scan_pose(self, *_args: object, **_kwargs: object) -> bool:
                    return True

                def write_clear_pose_control(self, *_args: object, **_kwargs: object) -> None:
                    pass

            class SuccessfulDepth:
                def wait_until_ready(self, timeout_sec: float) -> bool:
                    return True

                def capture(self, *, capture_id: str, dataset_dir: Path, sample_id: str, **_kwargs: object) -> DepthCaptureResult:
                    paths = {
                        "depth_path": dataset_dir / "depth" / f"{sample_id}.npy",
                        "raw_path": dataset_dir / "depth_raw" / f"{sample_id}.raw",
                        "preview_path": dataset_dir / "depth_preview" / f"{sample_id}.png",
                        "valid_mask_path": dataset_dir / "valid_masks" / f"{sample_id}.png",
                        "camera_metadata_path": dataset_dir / "cameras" / f"{sample_id}.json",
                    }
                    for path in paths.values():
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(b"depth")
                    return DepthCaptureResult(
                        capture_id=capture_id,
                        width=2,
                        height=2,
                        near_clip=0.1,
                        far_clip=100.0,
                        projection_matrix=tuple(float(index) for index in range(16)),
                        render_frame_id=17,
                        reversed_z=True,
                        valid_pixel_count=4,
                        **paths,
                    )

            def local_ensure(path: str | Path) -> Path:
                candidate = Path(path)
                destination = candidate if candidate.is_absolute() else root / candidate
                destination.mkdir(parents=True, exist_ok=True)
                return destination

            output = root / "data" / "stills" / "scans" / "resume_depth"
            output.mkdir(parents=True)
            old_fields = _sample_fieldnames(False)
            old_row = {field: "" for field in old_fields}
            old_row.update(
                {
                    "session_id": "resume_depth",
                    "dataset_id": "layer_1_zone_y01_1p00",
                    "sample_index": "1",
                    "layer_id": "layer_1",
                    "image_path": "old.jpg",
                }
            )
            with (output / "samples.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=old_fields)
                writer.writeheader()
                writer.writerow(old_row)

            with (
                patch("re9_pose_recorder.still_scan.OBSController", FakeOBS),
                patch("re9_pose_recorder.still_scan.LuaControl", FakeLua),
                patch("re9_pose_recorder.still_scan.ensure_dir", side_effect=local_ensure),
            ):
                outputs = _run_plan(
                    config=config,
                    obs_password="",
                    plan=[sample],
                    settle_seconds=0.0,
                    source_name=None,
                    image_format="jpg",
                    image_width=2,
                    image_height=2,
                    image_quality=100,
                    session_id="resume_depth",
                    max_samples=None,
                    progress_callback=None,
                    stop_event=None,
                    append_existing=True,
                    capture_depth=True,
                    depth_timeout=1.0,
                    depth_bridge=SuccessfulDepth(),  # type: ignore[arg-type]
                )

            with (output / "samples.csv").open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
                self.assertEqual(reader.fieldnames, _sample_fieldnames(True))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["image_path"], "old.jpg")
            self.assertEqual(rows[0]["depth_path"], "")
            self.assertEqual(rows[1]["depth_status"], "ok")
            self.assertTrue(rows[1]["depth_path"].endswith(".npy"))
            self.assertTrue(outputs["samples_schema_backup"].exists())


if __name__ == "__main__":
    unittest.main()
