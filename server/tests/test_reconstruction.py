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
            self.assertEqual("robust_bounds", settings["photogrammetry"]["framing"])
            self.assertEqual(2, settings["photogrammetry"]["settingsVersion"])

    def test_ascii_ply_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            splat = Path(temporary) / "small.ply"
            splat.write_text(
                "ply\nformat ascii 1.0\nelement vertex 2\n"
                "property float x\nproperty float y\nproperty float z\nend_header\n"
                "1 2 3\n-1 -2 -3\n"
            )
            self.assertEqual(((-1.0, -2.0, -3.0), (1.0, 2.0, 3.0)), ply_bounds(splat))

    def test_robust_bounds_ignore_sparse_floaters(self):
        with tempfile.TemporaryDirectory() as temporary:
            splat = Path(temporary) / "floaters.ply"
            rows = [(-100, -100, -100)]
            rows.extend((index / 50 - 1, index / 50 - 1, index / 50 - 1) for index in range(100))
            rows.append((100, 100, 100))
            splat.write_text(
                "ply\nformat ascii 1.0\nelement vertex 102\n"
                "property float x\nproperty float y\nproperty float z\nend_header\n"
                + "".join(f"{x} {y} {z}\n" for x, y, z in rows)
            )

            self.assertEqual(((-100.0, -100.0, -100.0), (100.0, 100.0, 100.0)), ply_bounds(splat))
            robust = ply_bounds(splat, trim_fraction=0.01)
            self.assertEqual((-1.0, -1.0, -1.0), robust[0])
            self.assertEqual((0.98, 0.98, 0.98), robust[1])

    def test_stale_viewer_settings_are_regenerated(self):
        with tempfile.TemporaryDirectory() as temporary:
            results = Path(temporary)
            splat = results / "final.ply"
            splat.write_text(
                "ply\nformat ascii 1.0\nelement vertex 2\n"
                "property float x\nproperty float y\nproperty float z\nend_header\n"
                "-1 -1 -1\n1 1 1\n"
            )
            settings = results / "viewer-settings.json"
            settings.write_text('{"photogrammetry":{"settingsVersion":1}}')
            settings.touch()

            ensure_viewer_settings(results)

            regenerated = json.loads(settings.read_text())
            self.assertEqual(2, regenerated["photogrammetry"]["settingsVersion"])


if __name__ == "__main__":
    unittest.main()
