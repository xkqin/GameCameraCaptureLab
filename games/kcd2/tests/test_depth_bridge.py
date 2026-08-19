from __future__ import annotations

from array import array
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

from kcd2_capture_studio.depth_bridge import (
    DepthBridge,
    decode_device_depth,
    write_float32_npy,
    write_depth_preview_png,
)


class DepthBridgeTests(unittest.TestCase):
    def test_decode_d32_with_row_padding(self) -> None:
        raw = struct.pack("<ff", 0.25, 0.75) + b"padding!"
        values = decode_device_depth(raw, 2, 1, len(raw), "d32_float")
        self.assertEqual(len(values), 2)
        self.assertAlmostEqual(values[0], 0.25)
        self.assertAlmostEqual(values[1], 0.75)

    def test_decode_d24_ignores_stencil_byte(self) -> None:
        raw = struct.pack("<II", 0xFF000000, 0xFFFFFFFF)
        values = decode_device_depth(raw, 2, 1, 8, "d24_unorm_s8_uint")
        self.assertAlmostEqual(values[0], 0.0)
        self.assertAlmostEqual(values[1], 1.0)

    def test_decode_typed_d24_shader_view(self) -> None:
        raw = struct.pack("<II", 0x12000000, 0x34FFFFFF)
        values = decode_device_depth(raw, 2, 1, 8, "r24_unorm_x8_uint")
        self.assertAlmostEqual(values[0], 0.0)
        self.assertAlmostEqual(values[1], 1.0)

    def test_decode_typed_d32s8_shader_view(self) -> None:
        raw = struct.pack("<fI", 0.125, 0xAB) + struct.pack("<fI", 0.875, 0xCD)
        values = decode_device_depth(raw, 2, 1, 16, "r32_float_x8_uint")
        self.assertAlmostEqual(values[0], 0.125)
        self.assertAlmostEqual(values[1], 0.875)

    def test_decode_typed_d16_shader_view(self) -> None:
        raw = struct.pack("<HH", 0, 65535)
        values = decode_device_depth(raw, 2, 1, 4, "r16_unorm")
        self.assertAlmostEqual(values[0], 0.0)
        self.assertAlmostEqual(values[1], 1.0)

    def test_decode_r16_float_fallback(self) -> None:
        raw = struct.pack("<ee", 0.25, 0.75)
        values = decode_device_depth(raw, 2, 1, 4, "r16_float")
        self.assertAlmostEqual(values[0], 0.25)
        self.assertAlmostEqual(values[1], 0.75)

    def test_response_conversion_writes_npy_png_and_raw_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bridge = DepthBridge(root / "ipc")
            ticket = bridge.begin_capture()
            ticket.raw_path.write_bytes(struct.pack("<ffff", 0.0, 0.2, 0.8, 1.0))
            ticket.response_path.write_text(
                json.dumps(
                    {
                        "protocol": "game-camera-depth-bridge/v1",
                        "request_id": ticket.request_id,
                        "status": "completed",
                        "captured_unix_ns": 1,
                        "width": 2,
                        "height": 2,
                        "row_pitch": 8,
                        "slice_pitch": 16,
                        "format": "r32_float",
                        "raw_path": ticket.raw_path.name,
                    }
                ),
                encoding="utf-8",
            )
            result = bridge.wait_capture(ticket, root / "sample", timeout=0.2)
            self.assertTrue(Path(result["depth_path"]).exists())
            self.assertTrue(Path(result["preview_path"]).exists())
            self.assertEqual(Path(result["depth_path"]).read_bytes()[:6], b"\x93NUMPY")
            self.assertEqual(
                Path(result["preview_path"]).read_bytes()[:8],
                b"\x89PNG\r\n\x1a\n",
            )
            self.assertEqual(result["depth_space"], "raw_device_depth")
            self.assertFalse(result["metric_depth"])
            self.assertFalse(ticket.raw_path.exists())
            self.assertEqual(
                bridge.status()["last_capture"]["request_id"],
                ticket.request_id,
            )

    def test_unverified_reshade_default_does_not_override_depth_inference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bridge = DepthBridge(root / "ipc")
            ticket = bridge.begin_capture()
            ticket.raw_path.write_bytes(struct.pack("<ffff", 0.1, 0.2, 0.3, 0.4))
            ticket.response_path.write_text(
                json.dumps(
                    {
                        "protocol": "game-camera-depth-bridge/v1",
                        "request_id": ticket.request_id,
                        "status": "completed",
                        "width": 2,
                        "height": 2,
                        "row_pitch": 8,
                        "format": "r32_float",
                        "raw_path": ticket.raw_path.name,
                        "reversed_z": False,
                        "reversed_z_source": "reshade_preprocessor_definition_unverified",
                    }
                ),
                encoding="utf-8",
            )
            result = bridge.wait_capture(ticket, root / "sample", timeout=0.2)
            self.assertTrue(result["reversed_z"])
            self.assertEqual(
                result["reversed_z_source"],
                "clear_value_distribution_heuristic",
            )
            self.assertFalse(result["configured_reversed_z"])

    def test_standalone_writers_validate_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            values = array("f", [0.0, 1.0])
            write_float32_npy(root / "depth.npy", values, (1, 2))
            write_depth_preview_png(
                root / "preview.png", values, 2, 1, reversed_z=False
            )
            self.assertTrue((root / "depth.npy").exists())
            self.assertTrue((root / "preview.png").exists())

    def test_preview_stretches_tiny_reversed_z_range_instead_of_looking_black(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            preview = Path(temp) / "preview.png"
            values = array("f", [0.0, 0.0005, 0.001, 0.002])
            write_depth_preview_png(
                preview,
                values,
                4,
                1,
                reversed_z=True,
            )
            encoded = preview.read_bytes()
            offset = 8
            compressed = bytearray()
            while offset < len(encoded):
                size = struct.unpack_from(">I", encoded, offset)[0]
                kind = encoded[offset + 4 : offset + 8]
                payload = encoded[offset + 8 : offset + 8 + size]
                if kind == b"IDAT":
                    compressed.extend(payload)
                offset += 12 + size
            scanline = zlib.decompress(bytes(compressed))
            self.assertEqual(scanline[0], 0)
            pixels = tuple(scanline[1:5])
            self.assertEqual(min(pixels), 0)
            self.assertEqual(max(pixels), 255)
            self.assertGreater(len(set(pixels)), 2)

    def test_response_rejects_payload_outside_ipc_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bridge = DepthBridge(root / "ipc")
            ticket = bridge.begin_capture()
            outside = root / "outside.raw"
            outside.write_bytes(struct.pack("<f", 0.5))
            ticket.response_path.write_text(
                json.dumps(
                    {
                        "protocol": "game-camera-depth-bridge/v1",
                        "request_id": ticket.request_id,
                        "status": "completed",
                        "width": 1,
                        "height": 1,
                        "row_pitch": 4,
                        "format": "r32_float",
                        "raw_path": str(outside),
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "outside"):
                bridge.wait_capture(ticket, root / "sample", timeout=0.2)
            self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
