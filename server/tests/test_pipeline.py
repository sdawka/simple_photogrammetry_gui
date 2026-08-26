import shutil
import sqlite3
import struct
import tempfile
import unittest
import zipfile
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from server.pipeline import Pipeline, PipelineError, Toolchain
from server.store import JobStore


class FakeRunner:
    def __init__(self):
        self.commands = []
        self.disconnect_nearby = False

    def cancel(self):
        pass

    def run(self, executable, args, *, cwd=None):
        args = [str(arg) for arg in args]
        self.commands.append((Path(executable).name, args, cwd))
        command = args[0] if Path(executable).name == "colmap" else Path(executable).name
        if command == "fast_downscaler":
            source, destination = Path(args[0]), Path(args[1])
            for image in source.iterdir():
                shutil.copy2(image, destination / f"{image.stem}.png")
        elif command == "feature_extractor":
            database = Path(args[args.index("--database_path") + 1])
            if "--FeatureExtraction.type" in args:
                image_path = Path(args[args.index("--image_path") + 1])
                with closing(sqlite3.connect(database)) as connection, connection:
                    connection.executescript(
                        """
                        CREATE TABLE images(image_id INTEGER PRIMARY KEY, name TEXT NOT NULL);
                        CREATE TABLE two_view_geometries(
                            pair_id INTEGER PRIMARY KEY, rows INTEGER NOT NULL,
                            cols INTEGER NOT NULL, data BLOB, config INTEGER NOT NULL
                        );
                        """
                    )
                    connection.executemany(
                        "INSERT INTO images(image_id, name) VALUES (?, ?)",
                        [
                            (index, image.name)
                            for index, image in enumerate(sorted(image_path.iterdir()), start=1)
                            if image.is_file()
                        ],
                    )
            else:
                database.touch()
        elif command == "matches_importer":
            database = Path(args[args.index("--database_path") + 1])
            pair_file = Path(args[args.index("--match_list_path") + 1])
            with closing(sqlite3.connect(database)) as connection, connection:
                names = dict(connection.execute("SELECT name, image_id FROM images"))
                for line in pair_file.read_text().splitlines():
                    first_name, second_name = line.split()
                    first, second = sorted((names[first_name], names[second_name]))
                    crosses_test_split = first <= 3 < second
                    verified = not (
                        self.disconnect_nearby
                        and pair_file.name == "nearby.pending"
                        and crosses_test_split
                    )
                    pair_id = first * 2_147_483_647 + second
                    connection.execute(
                        "INSERT OR REPLACE INTO two_view_geometries "
                        "(pair_id, rows, cols, data, config) VALUES (?, ?, 2, NULL, ?)",
                        (pair_id, 25 if verified else 0, 2 if verified else 0),
                    )
        elif command == "global_mapper":
            output = Path(args[args.index("--output_path") + 1]) / "sparse" / "0" if Path(args[args.index("--output_path") + 1]).name == "images_scaled" else Path(args[args.index("--output_path") + 1]) / "0"
            output.mkdir(parents=True, exist_ok=True)
            database = Path(args[args.index("--database_path") + 1])
            with closing(sqlite3.connect(database)) as connection, connection:
                try:
                    image_count = connection.execute("SELECT COUNT(*) FROM images").fetchone()[0]
                except sqlite3.OperationalError:
                    image_count = 0
            (output / "images.bin").write_bytes(struct.pack("<Q", image_count))
            (output / "points3D.bin").write_bytes(struct.pack("<Q", 2500))
        elif command == "image_undistorter":
            output = Path(args[args.index("--output_path") + 1])
            (output / "sparse").mkdir(parents=True, exist_ok=True)
            (output / "images").mkdir(parents=True, exist_ok=True)
        elif command == "model_converter" and args[args.index("--output_type") + 1] == "NVM":
            Path(args[args.index("--output_path") + 1]).touch()
        elif command == "InterfaceCOLMAP":
            Path(args[args.index("--output-file") + 1]).touch()
        elif command == "DensifyPointCloud":
            output = Path(args[args.index("--output-file") + 1])
            output.touch()
            output.with_suffix(".ply").touch()
        elif command == "PoissonRecon":
            Path(args[args.index("--out") + 1]).touch()
        elif command == "SurfaceTrimmer":
            Path(args[args.index("--out") + 1]).touch()
        elif command == "decimateMesh":
            (Path(args[args.index("-o") + 1]) / "model_surface_decimated.ply").touch()
        elif command == "texrecon":
            output = Path(args[-1])
            output.with_suffix(".obj").touch()
            output.with_suffix(".mtl").touch()
            Path(f"{output}_material0000_map_Kd.png").touch()
            Path(f"{output}_data_costs.spt").touch()
        elif command == "brush_app":
            (Path(args[args.index("--export-path") + 1]) / "splat_30000.ply").touch()


class PipelineTests(unittest.TestCase):
    def test_toolchain_resolves_immutable_models_from_package_root(self):
        tools = Toolchain.from_bin_dir(Path("/nix/store/example-package/usr/bin"))
        self.assertEqual(
            Path(
                "/nix/store/example-package/share/photogrammetry-server/models/"
                "aliked-n16rot.onnx"
            ),
            tools.aliked_n16rot_model,
        )
        self.assertEqual(
            Path(
                "/nix/store/example-package/share/photogrammetry-server/models/"
                "aliked-lightglue.onnx"
            ),
            tools.aliked_lightglue_model,
        )

    def make_pipeline(self, kind="mesh", matcher="exhaustive_matcher", image_count=3):
        temporary = tempfile.TemporaryDirectory()
        store = JobStore(Path(temporary.name))
        job = store.create_job(
            name="test",
            kind=kind,
            quality="medium",
            settings={
                "cpu_threads": -1,
                "feature_matcher": matcher,
                "mesh_type": "poissonrecon",
                "splat_steps": 30000,
            },
        )
        for number in range(image_count):
            (store.job_dir(job["id"]) / "input" / f"{number}.jpg").write_bytes(b"x")
        tools = Toolchain.from_bin_dir(Path("/tools"))
        model_dir = store.job_dir(job["id"]) / "test-models"
        model_dir.mkdir()
        aliked = model_dir / "aliked-n16rot.onnx"
        lightglue = model_dir / "aliked-lightglue.onnx"
        aliked.touch()
        lightglue.touch()
        tools = replace(
            tools,
            aliked_n16rot_model=aliked,
            aliked_lightglue_model=lightglue,
        )
        pipeline = Pipeline(store, tools, job["id"])
        fake = FakeRunner()
        pipeline.runner = fake
        return temporary, pipeline, fake

    def test_mesh_uses_proven_poisson_and_texture_options(self):
        temporary, pipeline, fake = self.make_pipeline()
        try:
            pipeline.run()
            poisson = next(args for name, args, _ in fake.commands if name == "PoissonRecon")
            self.assertIn("--pointWeight", poisson)
            self.assertEqual("11", poisson[poisson.index("--depth") + 1])
            texture = next(args for name, args, _ in fake.commands if name == "texrecon")
            self.assertEqual("--keep_unseen_faces", texture[0])
            archive = pipeline.results / "textured_mesh.zip"
            self.assertTrue(archive.exists())
            self.assertTrue((pipeline.results / "preview.png").exists())
            with zipfile.ZipFile(archive) as bundle:
                self.assertEqual(
                    {
                        "textured.obj",
                        "textured.mtl",
                        "textured_material0000_map_Kd.png",
                    },
                    set(bundle.namelist()),
                )
            self.assertEqual(
                {"preview.png", "textured_mesh.zip"},
                {path.name for path in pipeline.results.iterdir()},
            )
        finally:
            temporary.cleanup()

    def test_splat_uses_brush_export_and_steps(self):
        temporary, pipeline, fake = self.make_pipeline("splat")
        try:
            pipeline.run()
            brush, cwd = next(
                (args, cwd)
                for name, args, cwd in fake.commands
                if name == "brush_app"
            )
            self.assertEqual("--export-path", brush[1])
            self.assertEqual("--total-steps", brush[3])
            self.assertEqual("30000", brush[4])
            self.assertEqual(pipeline.work, cwd)
            self.assertNotEqual(pipeline.results, cwd)
        finally:
            temporary.cleanup()

    def test_standard_matcher_keeps_existing_sift_gpu_commands(self):
        temporary, pipeline, fake = self.make_pipeline("splat")
        try:
            pipeline.run()
            feature = next(
                args for name, args, _ in fake.commands
                if name == "colmap" and args[0] == "feature_extractor"
            )
            matcher = next(
                args for name, args, _ in fake.commands
                if name == "colmap" and args[0] == "exhaustive_matcher"
            )
            self.assertNotIn("--FeatureExtraction.type", feature)
            self.assertEqual("1", feature[feature.index("--FeatureExtraction.use_gpu") + 1])
            self.assertEqual("1", matcher[matcher.index("--FeatureMatching.use_gpu") + 1])
        finally:
            temporary.cleanup()

    def test_learned_matcher_uses_cpu_models_and_verified_bridge_search(self):
        temporary, pipeline, fake = self.make_pipeline(
            "splat", matcher="learned_matcher", image_count=5,
        )
        fake.disconnect_nearby = True
        try:
            pipeline.run()
            feature = next(
                args for name, args, _ in fake.commands
                if name == "colmap" and args[0] == "feature_extractor"
            )
            self.assertEqual(
                "ALIKED_N16ROT",
                feature[feature.index("--FeatureExtraction.type") + 1],
            )
            self.assertEqual("0", feature[feature.index("--FeatureExtraction.use_gpu") + 1])
            self.assertEqual("1", feature[feature.index("--ImageReader.single_camera") + 1])
            self.assertTrue(
                Path(
                    feature[
                        feature.index("--AlikedExtraction.n16rot_model_path") + 1
                    ]
                ).is_file()
            )

            matches = [
                args for name, args, _ in fake.commands
                if name == "colmap" and args[0] == "matches_importer"
            ]
            self.assertGreaterEqual(len(matches), 2)
            for args in matches:
                self.assertEqual(
                    "ALIKED_LIGHTGLUE",
                    args[args.index("--FeatureMatching.type") + 1],
                )
                self.assertEqual("0", args[args.index("--FeatureMatching.use_gpu") + 1])
                self.assertEqual(
                    "0",
                    args[
                        args.index("--FeatureMatching.skip_geometric_verification") + 1
                    ],
                )
                self.assertEqual(
                    "15",
                    args[args.index("--TwoViewGeometry.min_num_inliers") + 1],
                )
                self.assertEqual("4", args[args.index("--TwoViewGeometry.max_error") + 1])
                self.assertEqual("0.25", args[args.index("--TwoViewGeometry.min_inlier_ratio") + 1])
            self.assertTrue((pipeline.work / "learned_pairs" / "nearby.txt").is_file())
            self.assertTrue(list((pipeline.work / "learned_pairs").glob("bridge_*.txt")))
            self.assertFalse(list((pipeline.work / "learned_pairs").glob("*.pending")))
            log = pipeline.log_path.read_text()
            self.assertIn("fallback_reason=none", log)
            self.assertIn("registered_view_count=5", log)
        finally:
            temporary.cleanup()

    def test_learned_runtime_failure_does_not_fall_back_to_sift(self):
        temporary, pipeline, fake = self.make_pipeline(
            "splat", matcher="learned_matcher",
        )
        pipeline.tools.aliked_n16rot_model.unlink()
        try:
            with self.assertRaisesRegex(PipelineError, "missing ALIKED_N16ROT model"):
                pipeline.run()
            colmap_commands = [args[0] for name, args, _ in fake.commands if name == "colmap"]
            self.assertNotIn("exhaustive_matcher", colmap_commands)
            self.assertNotIn("sequential_matcher", colmap_commands)
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
