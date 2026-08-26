from __future__ import annotations

import os
import queue
import shlex
import shutil
import signal
import subprocess
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .learned_matching import (
    BRIDGE_BATCH_SIZE,
    MAX_BRIDGE_PAIRS,
    attempted_pairs_from_files,
    bridge_candidates,
    connected_components,
    nearby_pairs,
    read_database_images,
    verified_pairs,
    write_pair_file,
)
from .reconstruction import capture_diagnostics, ensure_viewer_settings
from .store import JobStore, utc_now


class PipelineError(RuntimeError):
    pass


class JobCancelled(PipelineError):
    pass


@dataclass(frozen=True)
class Toolchain:
    colmap: Path
    brush: Path
    interface_colmap: Path
    densify: Path
    reconstruct_mesh: Path
    poisson_recon: Path
    surface_trimmer: Path
    decimate_mesh: Path
    texrecon: Path
    downscaler: Path
    aliked_n16rot_model: Path
    aliked_lightglue_model: Path

    @classmethod
    def from_bin_dir(cls, bin_dir: Path) -> "Toolchain":
        package_root = bin_dir.parent.parent
        model_dir = package_root / "share" / "photogrammetry-server" / "models"
        return cls(
            colmap=bin_dir / "colmap",
            brush=bin_dir / "brush" / "brush_app",
            interface_colmap=bin_dir / "OpenMVS" / "InterfaceCOLMAP",
            densify=bin_dir / "OpenMVS" / "DensifyPointCloud",
            reconstruct_mesh=bin_dir / "OpenMVS" / "ReconstructMesh",
            poisson_recon=bin_dir / "PoissonRecon",
            surface_trimmer=bin_dir / "SurfaceTrimmer",
            decimate_mesh=bin_dir / "decimateMesh",
            texrecon=bin_dir / "texrecon",
            downscaler=bin_dir / "fast_downscaler",
            aliked_n16rot_model=model_dir / "aliked-n16rot.onnx",
            aliked_lightglue_model=model_dir / "aliked-lightglue.onnx",
        )


QUALITY = {
    "high": {"resize": -1, "dense": 2560, "poisson": 12, "decimate": 0.01},
    "medium": {"resize": 3000, "dense": 1920, "poisson": 11, "decimate": 0.03},
    "low": {"resize": 2000, "dense": 1024, "poisson": 10, "decimate": 0.1},
}


class CommandRunner:
    def __init__(self, log: Callable[[str], None], cancelled: Callable[[], bool]):
        self.log = log
        self.cancelled = cancelled
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None

    def run(self, executable: Path, args: Iterable[str], *, cwd: Path | None = None) -> None:
        command = [str(executable), *map(str, args)]
        self.log(f"$ {shlex.join(command)}")
        if self.cancelled():
            raise JobCancelled("Job cancelled")
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                bufsize=1,
                start_new_session=True,
            )
        except OSError as exc:
            raise PipelineError(f"Could not start {executable}: {exc}") from exc
        with self._lock:
            self._process = process
        lines: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                lines.put(line.rstrip("\n"))
            lines.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        output_finished = False
        try:
            while process.poll() is None or not output_finished:
                if self.cancelled() and process.poll() is None:
                    self._stop_process(process)
                try:
                    line = lines.get(timeout=0.2)
                    if line is None:
                        output_finished = True
                    else:
                        self.log(line)
                except queue.Empty:
                    pass
            exit_code = process.wait()
        finally:
            with self._lock:
                self._process = None
        if self.cancelled():
            raise JobCancelled("Job cancelled")
        if exit_code != 0:
            raise PipelineError(f"{executable.name} exited with status {exit_code}")

    def cancel(self) -> None:
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            self._stop_process(process)

    @staticmethod
    def _stop_process(process: subprocess.Popen[str]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class Pipeline:
    def __init__(
        self,
        store: JobStore,
        tools: Toolchain,
        job_id: str,
        stopping: Callable[[], bool] = lambda: False,
    ):
        self.store = store
        self.tools = tools
        self.job_id = job_id
        self.root = store.job_dir(job_id)
        self.input = self.root / "input"
        self.work = self.root / "work"
        self.results = self.root / "results"
        self.checkpoints = self.root / "checkpoints"
        self.log_path = self.root / "job.log"
        self.stopping = stopping
        self.runner = CommandRunner(self.log, self.cancelled)

    def log(self, message: str) -> None:
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"[{timestamp}] {message}\n")

    def cancelled(self) -> bool:
        if self.stopping():
            return True
        try:
            return self.store.get_job(self.job_id)["cancel_requested"]
        except KeyError:
            return True

    def cancel(self) -> None:
        self.runner.cancel()

    def _check_cancelled(self) -> None:
        if self.cancelled():
            raise JobCancelled("Job cancelled")

    def _stage(self, key: str, label: str, index: int, total: int, action: Callable[[], None]) -> None:
        self._check_cancelled()
        self.store.update(
            self.job_id,
            stage=label,
            stage_index=index,
            stage_total=total,
        )
        marker = self.checkpoints / key
        if marker.exists():
            self.log(f"Skipping completed stage: {label}")
            return
        self.log(f"Starting stage {index}/{total}: {label}")
        action()
        marker.touch()
        self.log(f"Completed stage {index}/{total}: {label}")

    def run(self) -> None:
        job = self.store.get_job(self.job_id)
        if job["kind"] == "splat":
            self._run_splat(job)
        else:
            self._run_mesh(job)

    def _prepare_images(self, quality: str) -> None:
        images = self.work / "images_scaled"
        if images.exists():
            shutil.rmtree(images)
        images.mkdir(parents=True)
        resize = QUALITY[quality]["resize"]
        if resize == -1:
            for source in sorted(self.input.iterdir()):
                self._check_cancelled()
                if source.is_file():
                    shutil.copy2(source, images / source.name.replace(" ", "_"))
        else:
            threads = max(1, (os.cpu_count() or 2) - 1)
            self.runner.run(
                self.tools.downscaler,
                [self.input, images, str(resize), str(threads)],
            )

    def _sfm(self, job: dict, *, total: int) -> Path:
        images = self.work / "images_scaled"
        database = self.work / "database.db"
        global_database = self.work / "global_database.db"
        threads = str(job["settings"].get("cpu_threads", -1))
        matcher = job["settings"].get("feature_matcher", "exhaustive_matcher")

        self._stage("prepare", "Copy and resize images", 1, total, lambda: self._prepare_images(job["quality"]))
        if matcher == "learned_matcher":
            self._stage(
                "features",
                "Learned feature extraction (ALIKED, CPU)",
                2,
                total,
                lambda: self._extract_learned_features(database, images, threads),
            )
            self._stage(
                "matching",
                "Learned feature matching (LightGlue, CPU)",
                3,
                total,
                lambda: self._match_learned_features(database, threads),
            )
        else:
            self._stage(
                "features",
                "SIFT feature extraction",
                2,
                total,
                lambda: self.runner.run(
                    self.tools.colmap,
                    [
                        "feature_extractor",
                        "--database_path", database,
                        "--image_path", images,
                        "--FeatureExtraction.use_gpu", "1",
                        "--FeatureExtraction.num_threads", threads,
                    ],
                ),
            )
            self._stage(
                "matching",
                "SIFT feature matching",
                3,
                total,
                lambda: self.runner.run(
                    self.tools.colmap,
                    [
                        matcher,
                        "--FeatureMatching.use_gpu", "1",
                        "--database_path", database,
                        "--FeatureMatching.num_threads", threads,
                    ],
                ),
            )

        def calibrate() -> None:
            shutil.copy2(database, global_database)
            self.runner.run(
                self.tools.colmap,
                ["view_graph_calibrator", "--database_path", global_database],
            )

        self._stage("calibrate", "Calibrate view graph", 4, total, calibrate)
        return images

    def _require_learned_model(self, path: Path, name: str) -> None:
        if not path.is_file():
            raise PipelineError(
                f"Learned matching runtime is unavailable: missing {name} model at {path}"
            )

    def _extract_learned_features(self, database: Path, images: Path, threads: str) -> None:
        model = self.tools.aliked_n16rot_model
        self._require_learned_model(model, "ALIKED_N16ROT")
        self.log(
            f"Learned feature model: ALIKED_N16ROT; execution_device=CPU; model={model}"
        )
        self.runner.run(
            self.tools.colmap,
            [
                "feature_extractor",
                "--database_path", database,
                "--image_path", images,
                "--ImageReader.camera_model", "SIMPLE_RADIAL",
                "--ImageReader.single_camera", "1",
                "--FeatureExtraction.type", "ALIKED_N16ROT",
                "--FeatureExtraction.use_gpu", "0",
                "--FeatureExtraction.num_threads", threads,
                "--AlikedExtraction.max_num_features", "4096",
                "--AlikedExtraction.n16rot_model_path", model,
            ],
        )

    def _run_learned_pair_batch(
        self,
        database: Path,
        images,
        pairs,
        pair_file: Path,
        threads: str,
        label: str,
    ) -> None:
        if not pairs:
            return
        pending_file = pair_file.with_suffix(".pending")
        write_pair_file(pending_file, images, pairs)
        self.log(f"Learned matching batch: {label}; candidate_pair_count={len(pairs)}")
        try:
            self.runner.run(
                self.tools.colmap,
                [
                    "matches_importer",
                    "--database_path", database,
                    "--match_list_path", pending_file,
                    "--match_type", "pairs",
                    "--FeatureMatching.type", "ALIKED_LIGHTGLUE",
                    "--FeatureMatching.use_gpu", "0",
                    "--FeatureMatching.num_threads", threads,
                    "--FeatureMatching.guided_matching", "0",
                    "--FeatureMatching.skip_geometric_verification", "0",
                    "--AlikedMatching.lightglue_model_path", self.tools.aliked_lightglue_model,
                    "--TwoViewGeometry.min_num_inliers", "15",
                    "--TwoViewGeometry.max_error", "4",
                    "--TwoViewGeometry.min_inlier_ratio", "0.25",
                ],
            )
        except Exception:
            pending_file.unlink(missing_ok=True)
            raise
        pending_file.replace(pair_file)

    def _match_learned_features(self, database: Path, threads: str) -> None:
        model = self.tools.aliked_lightglue_model
        self._require_learned_model(model, "ALIKED_LIGHTGLUE")
        images = read_database_images(database)
        if len(images) < 2:
            raise PipelineError("Learned matching needs at least two extracted images")

        pair_dir = self.work / "learned_pairs"
        for stale in pair_dir.glob("*.pending"):
            stale.unlink()
        attempted = attempted_pairs_from_files(pair_dir, images)
        initial = [pair for pair in nearby_pairs(images) if pair not in attempted]
        self.log(
            "Learned pair discovery: ordering=filename; nearby_radius=2; "
            f"bridge_batch_size={BRIDGE_BATCH_SIZE}; max_bridge_pairs={MAX_BRIDGE_PAIRS}"
        )
        self.log(
            f"Learned match model: ALIKED_LIGHTGLUE; execution_device=CPU; model={model}"
        )
        self._run_learned_pair_batch(
            database, images, initial, pair_dir / "nearby.txt", threads, "adjacent_and_nearby",
        )
        attempted.update(initial)

        fallback_reason = "none"
        bridge_count = sum(1 for pair in attempted if pair not in set(nearby_pairs(images)))
        batch_index = len(list(pair_dir.glob("bridge_*.txt")))
        while True:
            verified = verified_pairs(database)
            components = connected_components(
                (image.image_id for image in images), verified,
            )
            self.log(
                "Learned verified graph: "
                f"verified_pair_count={len(verified)}; component_count={len(components)}"
            )
            if len(components) <= 1:
                break
            remaining = MAX_BRIDGE_PAIRS - bridge_count
            if remaining <= 0:
                fallback_reason = f"verified_graph_disconnected_bridge_limit_{MAX_BRIDGE_PAIRS}"
                break
            candidates = bridge_candidates(
                images,
                components,
                attempted,
                limit=min(BRIDGE_BATCH_SIZE, remaining),
            )
            if not candidates:
                fallback_reason = "verified_graph_disconnected_candidates_exhausted"
                break
            batch_index += 1
            self._run_learned_pair_batch(
                database,
                images,
                candidates,
                pair_dir / f"bridge_{batch_index:03d}.txt",
                threads,
                f"cross_component_{batch_index}",
            )
            attempted.update(candidates)
            bridge_count += len(candidates)

        verified = verified_pairs(database)
        self.log(
            "Learned matching summary: models=ALIKED_N16ROT+ALIKED_LIGHTGLUE; "
            f"execution_device=CPU; candidate_pair_count={len(attempted)}; "
            f"verified_pair_count={len(verified)}; fallback_reason={fallback_reason}"
        )

    def _log_learned_registration(self, job: dict) -> None:
        if job["settings"].get("feature_matcher") != "learned_matcher":
            return
        diagnostics = capture_diagnostics(
            self.root, self.store.get_job(self.job_id)["uploaded_images"]
        )
        registered = diagnostics.get("registered_views") if diagnostics else None
        count = registered if registered is not None else "unknown"
        self.log(f"Learned mapping result: registered_view_count={count}")

    def _run_splat(self, job: dict) -> None:
        total = 6
        images = self._sfm(job, total=total)

        def map_scene() -> None:
            sparse = images / "sparse"
            if sparse.exists():
                shutil.rmtree(sparse)
            self.runner.run(
                self.tools.colmap,
                [
                    "global_mapper",
                    "--database_path", self.work / "global_database.db",
                    "--image_path", images,
                    "--output_path", images,
                ],
            )
            self._log_learned_registration(job)

        self._stage("map", "Align cameras with GLOMAP", 5, total, map_scene)

        def train() -> None:
            steps = str(job["settings"].get("splat_steps", 30000))
            self.runner.run(
                self.tools.brush,
                [images, "--export-path", self.results, "--total-steps", steps],
                # Brush writes its wgpu autotune cache beneath the current
                # directory. Keep that implementation detail in work/ so the
                # public results directory contains only downloadable output.
                cwd=self.work,
            )
            if not any(self.results.rglob("*.ply")):
                raise PipelineError("Brush completed without exporting a .ply splat")
            ensure_viewer_settings(self.results)

        self._stage("train", "Train Gaussian splat", 6, total, train)

    def _run_mesh(self, job: dict) -> None:
        total = 11
        images = self._sfm(job, total=total)
        sparse = self.work / "sparse"
        dense = self.work / "dense"

        def map_scene() -> None:
            if sparse.exists():
                shutil.rmtree(sparse)
            sparse.mkdir(parents=True)
            self.runner.run(
                self.tools.colmap,
                [
                    "global_mapper",
                    "--database_path", self.work / "global_database.db",
                    "--image_path", images,
                    "--output_path", sparse,
                ],
            )
            self._log_learned_registration(job)

        self._stage("map", "Align cameras with GLOMAP", 5, total, map_scene)

        def undistort() -> None:
            if dense.exists():
                shutil.rmtree(dense)
            self.runner.run(
                self.tools.colmap,
                [
                    "image_undistorter",
                    "--image_path", images,
                    "--input_path", sparse / "0",
                    "--output_path", dense,
                    "--output_type", "COLMAP",
                ],
            )

        self._stage("undistort", "Undistort images", 6, total, undistort)

        def convert() -> None:
            self.runner.run(
                self.tools.colmap,
                [
                    "model_converter", "--input_path", dense / "sparse",
                    "--output_path", dense / "sparse", "--output_type", "TXT",
                ],
            )
            self.runner.run(
                self.tools.colmap,
                [
                    "model_converter", "--input_path", dense / "sparse",
                    "--output_path", images / "project.nvm", "--output_type", "NVM",
                ],
            )

        self._stage("convert", "Convert COLMAP project", 7, total, convert)
        self._stage(
            "openmvs_import",
            "Import scene into OpenMVS",
            8,
            total,
            lambda: self.runner.run(
                self.tools.interface_colmap,
                [
                    "--working-folder", dense,
                    "--input-file", dense,
                    "--output-file", self.work / "model_colmap.mvs",
                ],
            ),
        )

        def densify() -> None:
            resolution = int(QUALITY[job["quality"]]["dense"])
            last_error: Exception | None = None
            for attempt in range(1, 6):
                for stale in self.work.glob("*.dmap"):
                    stale.unlink()
                for stale in (self.work / "model_dense.mvs", self.work / "model_dense.ply"):
                    stale.unlink(missing_ok=True)
                try:
                    self.runner.run(
                        self.tools.densify,
                        [
                            "--input-file", self.work / "model_colmap.mvs",
                            "--working-folder", self.work,
                            "--output-file", self.work / "model_dense.mvs",
                            "--max-resolution", str(resolution),
                            "--roi-border", "10",
                        ],
                    )
                    if (self.work / "model_dense.mvs").exists():
                        return
                    last_error = PipelineError("DensifyPointCloud produced no model_dense.mvs")
                except PipelineError as exc:
                    last_error = exc
                if isinstance(last_error, JobCancelled):
                    raise last_error
                self.log(f"Densification attempt {attempt} failed; reducing resolution")
                resolution = int(resolution * 0.7)
            raise PipelineError(f"Densification failed after 5 attempts: {last_error}")

        self._stage("densify", "Densify point cloud", 9, total, densify)

        def reconstruct() -> None:
            mesh_type = job["settings"].get("mesh_type", "poissonrecon")
            if mesh_type == "poissonrecon":
                self.runner.run(
                    self.tools.poisson_recon,
                    [
                        "--in", self.work / "model_dense.ply",
                        "--out", self.work / "model_surface.ply",
                        "--depth", str(QUALITY[job["quality"]]["poisson"]),
                        "--density", "--pointWeight", "10", "--samplesPerNode", "2", "--confidence",
                    ],
                )
                self.runner.run(
                    self.tools.surface_trimmer,
                    [
                        "--in", self.work / "model_surface.ply",
                        "--out", self.work / "model_surface_cleaned.ply",
                        "--trim", "4", "--ascii", "--removeIslands",
                    ],
                )
                self.runner.run(
                    self.tools.decimate_mesh,
                    [
                        "-m", self.work / "model_surface_cleaned.ply",
                        "-o", self.work,
                        "-t", str(QUALITY[job["quality"]]["decimate"]),
                    ],
                )
            else:
                last_error: Exception | None = None
                for attempt in range(1, 11):
                    try:
                        self.runner.run(
                            self.tools.reconstruct_mesh,
                            [
                                "--input-file", self.work / "model_dense.mvs",
                                "--working-folder", self.work,
                                "--output-file", "model_surface.mvs",
                                "-d", str(2.0 + attempt / 2),
                                "--target-face-num", "0", "--crop-to-roi", "1", "--roi-border", "10",
                            ],
                        )
                        if (self.work / "model_surface.ply").exists():
                            return
                        last_error = PipelineError("ReconstructMesh produced no model_surface.ply")
                    except PipelineError as exc:
                        last_error = exc
                    if isinstance(last_error, JobCancelled):
                        raise last_error
                raise PipelineError(f"Mesh reconstruction failed after 10 attempts: {last_error}")

        self._stage("mesh", "Reconstruct mesh", 10, total, reconstruct)

        def texture() -> None:
            texture_dir = self.work / "textured_output"
            if texture_dir.exists():
                shutil.rmtree(texture_dir)
            texture_dir.mkdir()
            mesh_type = job["settings"].get("mesh_type", "poissonrecon")
            mesh = self.work / ("model_surface_decimated.ply" if mesh_type == "poissonrecon" else "model_surface.ply")
            output_base = texture_dir / "textured"
            self.runner.run(
                self.tools.texrecon,
                ["--keep_unseen_faces", images / "project.nvm", mesh, output_base],
                cwd=dense / "images",
            )
            obj = output_base.with_suffix(".obj")
            mtl = output_base.with_suffix(".mtl")
            textures = sorted(texture_dir.glob("textured_material*_map_Kd.*"))
            if not obj.exists() or not mtl.exists() or not textures:
                raise PipelineError("texrecon completed without producing textured.obj")

            # An OBJ's MTL and texture atlases must travel together. Publish a
            # single portable archive and one browser preview, leaving solver
            # scratch files in work/ instead of exposing them as artifacts.
            archive = self.results / "textured_mesh.zip"
            archive.unlink(missing_ok=True)
            with zipfile.ZipFile(
                archive, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
            ) as bundle:
                for path in [obj, mtl, *textures]:
                    bundle.write(path, arcname=path.name)
            shutil.copy2(textures[0], self.results / "preview.png")

        self._stage("texture", "Texture mesh", 11, total, texture)


class Worker:
    def __init__(self, store: JobStore, tools: Toolchain):
        self.store = store
        self.tools = tools
        self.wakeup = threading.Event()
        self.shutdown = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="photogrammetry-worker", daemon=True)
        self._lock = threading.Lock()
        self._active: Pipeline | None = None

    def start(self) -> None:
        self.store.recover_after_restart()
        self.thread.start()

    def notify(self) -> None:
        self.wakeup.set()

    def stop(self) -> None:
        self.shutdown.set()
        with self._lock:
            active = self._active
        if active is not None:
            active.cancel()
        self.wakeup.set()
        self.thread.join(timeout=15)

    def cancel(self, job_id: str) -> dict:
        job = self.store.get_job(job_id)
        if job["state"] == "queued":
            return self.store.update(
                job_id,
                state="cancelled",
                stage="Cancelled",
                cancel_requested=True,
                finished_at=utc_now(),
            )
        if job["state"] == "running":
            result = self.store.update(job_id, cancel_requested=True, stage="Cancelling")
            with self._lock:
                active = self._active
            if active is not None and active.job_id == job_id:
                active.cancel()
            return result
        return job

    def _loop(self) -> None:
        while not self.shutdown.is_set():
            job = self.store.claim_next()
            if job is None:
                self.wakeup.wait(timeout=5)
                self.wakeup.clear()
                continue
            pipeline = Pipeline(self.store, self.tools, job["id"], self.shutdown.is_set)
            with self._lock:
                self._active = pipeline
            try:
                pipeline.run()
            except JobCancelled:
                if self.shutdown.is_set():
                    self.store.update(
                        job["id"], state="queued", stage="Paused for worker restart",
                        queued_at=job["queued_at"] or utc_now(), cancel_requested=False,
                    )
                else:
                    self.store.update(
                        job["id"], state="cancelled", stage="Cancelled",
                        finished_at=utc_now(), cancel_requested=True,
                    )
            except Exception as exc:
                if self.shutdown.is_set():
                    pipeline.log(f"Worker stopped during command: {exc}")
                    self.store.update(
                        job["id"], state="queued", stage="Paused for worker restart",
                        queued_at=job["queued_at"] or utc_now(), error=None,
                    )
                else:
                    pipeline.log(f"FAILED: {exc}")
                    self.store.update(
                        job["id"], state="failed", stage="Failed",
                        error=str(exc), finished_at=utc_now(),
                    )
            else:
                self.store.update(
                    job["id"], state="completed", stage="Done",
                    finished_at=utc_now(), error=None,
                )
            finally:
                with self._lock:
                    self._active = None
