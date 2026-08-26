import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from server.pipeline import Pipeline, Toolchain
from server.store import JobStore


class FakeRunner:
    def __init__(self):
        self.commands = []

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
            Path(args[args.index("--database_path") + 1]).touch()
        elif command == "global_mapper":
            output = Path(args[args.index("--output_path") + 1]) / "sparse" / "0" if Path(args[args.index("--output_path") + 1]).name == "images_scaled" else Path(args[args.index("--output_path") + 1]) / "0"
            output.mkdir(parents=True, exist_ok=True)
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
    def make_pipeline(self, kind="mesh"):
        temporary = tempfile.TemporaryDirectory()
        store = JobStore(Path(temporary.name))
        job = store.create_job(
            name="test",
            kind=kind,
            quality="medium",
            settings={
                "cpu_threads": -1,
                "feature_matcher": "exhaustive_matcher",
                "mesh_type": "poissonrecon",
                "splat_steps": 30000,
            },
        )
        for number in range(3):
            (store.job_dir(job["id"]) / "input" / f"{number}.jpg").write_bytes(b"x")
        tools = Toolchain.from_bin_dir(Path("/tools"))
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


if __name__ == "__main__":
    unittest.main()
