import json
import struct
import tempfile
import unittest
from pathlib import Path

from server.reconstruction import (
    capture_diagnostics,
    ensure_viewer_settings,
    ply_bounds,
)


class ReconstructionMetadataTests(unittest.TestCase):
    def test_capture_diagnostics_classify_sparse_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = root / "work" / "images_scaled" / "sparse" / "0"
            model.mkdir(parents=True)
            (model / "images.bin").write_bytes(struct.pack("<Q", 7))
            (model / "points3D.bin").write_bytes(struct.pack("<Q", 31))

            diagnostics = capture_diagnostics(root, uploaded_views=11)

            self.assertEqual(
                {
                    "uploaded_views": 11,
                    "registered_views": 7,
                    "reliable_tracks": 31,
                    "level": "poor",
                    "notes": ["low_view_registration", "low_track_count"],
                },
                diagnostics,
            )

    def test_capture_diagnostics_support_glomap_output_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = root / "work" / "images_scaled" / "0"
            model.mkdir(parents=True)
            (model / "images.bin").write_bytes(struct.pack("<Q", 5))
            (model / "points3D.bin").write_bytes(struct.pack("<Q", 44))

            diagnostics = capture_diagnostics(root, uploaded_views=11)

            self.assertEqual(5, diagnostics["registered_views"])
            self.assertEqual(44, diagnostics["reliable_tracks"])
            self.assertEqual("poor", diagnostics["level"])

    def test_binary_ply_bounds_generate_framed_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            results = Path(temporary)
            splat = results / "export_30000.ply"
            header = (
                "ply\nformat binary_little_endian 1.0\n"
                "element vertex 3\n"
                "property float x\nproperty float y\nproperty float z\n"
                "property float opacity\nend_header\n"
            ).encode()
            vertices = b"".join(
                struct.pack("<ffff", *row)
                for row in ((-2, 0, 1, 1), (2, 6, 5, 1), (0, 3, 2, 1))
            )
            splat.write_bytes(header + vertices)

            self.assertEqual(((-2.0, 0.0, 1.0), (2.0, 6.0, 5.0)), ply_bounds(splat))
            settings_path = ensure_viewer_settings(results)
            self.assertIsNotNone(settings_path)
            settings = json.loads(settings_path.read_text())
            camera = settings["cameras"][0]["initial"]
            self.assertEqual([0.0, 3.0, 3.0], camera["target"])
            self.assertEqual(55, camera["fov"])
            self.assertLess(camera["position"][2], -5)
            self.assertEqual("bounds", settings["photogrammetry"]["framing"])

    def test_ascii_ply_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            splat = Path(temporary) / "small.ply"
            splat.write_text(
                "ply\nformat ascii 1.0\nelement vertex 2\n"
                "property float x\nproperty float y\nproperty float z\nend_header\n"
                "1 2 3\n-1 -2 -3\n"
            )
            self.assertEqual(((-1.0, -2.0, -3.0), (1.0, 2.0, 3.0)), ply_bounds(splat))


if __name__ == "__main__":
    unittest.main()
