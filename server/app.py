from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import unicodedata
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .pipeline import Toolchain, Worker
from .reconstruction import ensure_viewer_settings
from .store import JobStore, utc_now


API_PREFIX = "/api/v1"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
DEFAULT_MAX_IMAGE_BYTES = 200 * 1024 * 1024
DEFAULT_MAX_JOB_BYTES = 20 * 1024 * 1024 * 1024
DEFAULT_MIN_FREE_BYTES = 20 * 1024 * 1024 * 1024


def normalize_upload_name(raw_name: str) -> str:
    name = unicodedata.normalize("NFKC", unquote(raw_name))
    if name != Path(name).name or name in {"", ".", ".."} or "\x00" in name:
        raise ValueError("Invalid image filename")
    name = SAFE_NAME.sub("_", name).strip("._")
    if not name or Path(name).suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError("Only .jpg, .jpeg, and .png images are accepted")
    return name


def validate_job_payload(payload: dict) -> tuple[str, str, str, dict]:
    name = str(payload.get("name", "Untitled scan")).strip()[:120]
    kind = payload.get("kind", "mesh")
    quality = payload.get("quality", "medium")
    settings = payload.get("settings") or {}
    if not name:
        raise ValueError("Job name cannot be empty")
    if kind not in {"mesh", "splat"}:
        raise ValueError("kind must be mesh or splat")
    if quality not in {"low", "medium", "high"}:
        raise ValueError("quality must be low, medium, or high")
    if not isinstance(settings, dict):
        raise ValueError("settings must be an object")
    allowed = {"cpu_threads", "feature_matcher", "mesh_type", "splat_steps"}
    if settings.keys() - allowed:
        raise ValueError(f"Unknown settings: {sorted(settings.keys() - allowed)}")
    normalized = {
        "cpu_threads": int(settings.get("cpu_threads", -1)),
        "feature_matcher": settings.get("feature_matcher", "exhaustive_matcher"),
        "mesh_type": settings.get("mesh_type", "poissonrecon"),
        "splat_steps": int(settings.get("splat_steps", 30000)),
    }
    if normalized["cpu_threads"] < -1 or normalized["cpu_threads"] == 0:
        raise ValueError("cpu_threads must be -1 or a positive integer")
    if normalized["feature_matcher"] not in {"exhaustive_matcher", "sequential_matcher"}:
        raise ValueError("Unsupported feature matcher")
    if normalized["mesh_type"] not in {"poissonrecon", "openmvs"}:
        raise ValueError("mesh_type must be poissonrecon or openmvs")
    if not 1 <= normalized["splat_steps"] <= 1_000_000:
        raise ValueError("splat_steps must be between 1 and 1000000")
    return name, kind, quality, normalized


class PhotogrammetryServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store: JobStore, worker, web_dir: Path | None):
        super().__init__(address, RequestHandler)
        self.store = store
        self.worker = worker
        self.web_dir = web_dir
        self.max_image_bytes = int(os.environ.get("PHOTOGRAMMETRY_MAX_IMAGE_BYTES", DEFAULT_MAX_IMAGE_BYTES))
        self.max_job_bytes = int(os.environ.get("PHOTOGRAMMETRY_MAX_JOB_BYTES", DEFAULT_MAX_JOB_BYTES))
        self.min_free_bytes = int(os.environ.get("PHOTOGRAMMETRY_MIN_FREE_BYTES", DEFAULT_MIN_FREE_BYTES))


class RemoteWorkerController:
    """Queue/cancel control used when the worker is a separate process."""

    def __init__(self, store: JobStore):
        self.store = store

    def notify(self) -> None:
        # The worker polls SQLite. Five seconds is short beside reconstruction time.
        pass

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
            return self.store.update(job_id, cancel_requested=True, stage="Cancelling")
        return job


class RequestHandler(BaseHTTPRequestHandler):
    server: PhotogrammetryServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def _json(self, status: int, value) -> None:
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _body_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 1024 * 1024:
            raise ValueError("A small JSON request body is required")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def _parts(self) -> list[str]:
        path = urlsplit(self.path).path
        suffix = path[len(API_PREFIX):].strip("/")
        return [unquote(part) for part in suffix.split("/") if part]

    def do_GET(self) -> None:
        try:
            path = urlsplit(self.path).path
            if path == f"{API_PREFIX}/health":
                self._json(HTTPStatus.OK, {"status": "ok"})
                return
            if path.startswith(API_PREFIX):
                self._api_get()
                return
            self._static(path)
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND, "Job not found")
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def _api_get(self) -> None:
        parts = self._parts()
        if parts == ["jobs"]:
            self._json(HTTPStatus.OK, {"jobs": self.server.store.list_jobs()})
            return
        if len(parts) == 2 and parts[0] == "jobs":
            self._json(HTTPStatus.OK, self.server.store.get_job(parts[1]))
            return
        if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "logs":
            query = urlsplit(self.path).query
            after = 0
            for item in query.split("&"):
                if item.startswith("after="):
                    after = max(0, int(item[6:]))
            log_path = self.server.store.job_dir(parts[1]) / "job.log"
            lines: list[str] = []
            total = 0
            if log_path.exists():
                with log_path.open(encoding="utf-8", errors="replace") as stream:
                    for total, line in enumerate(stream, start=1):
                        if total > after and len(lines) < 1000:
                            lines.append(line.rstrip("\r\n"))
            next_line = min(total, after + len(lines))
            self._json(HTTPStatus.OK, {"lines": lines, "next": next_line})
            return
        if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "artifacts":
            job = self.server.store.get_job(parts[1])
            result_dir = self.server.store.job_dir(parts[1]) / "results"
            if job["kind"] == "splat":
                ensure_viewer_settings(result_dir)
            artifacts = [
                {"name": p.relative_to(result_dir).as_posix(), "bytes": p.stat().st_size}
                for p in sorted(result_dir.rglob("*")) if p.is_file()
            ]
            self._json(HTTPStatus.OK, {"artifacts": artifacts})
            return
        if len(parts) >= 4 and parts[0] == "jobs" and parts[2] == "artifacts":
            self.server.store.get_job(parts[1])
            result_dir = (self.server.store.job_dir(parts[1]) / "results").resolve()
            relative = Path(*parts[3:])
            target = (result_dir / relative).resolve()
            if result_dir not in target.parents or not target.is_file():
                raise KeyError("artifact")
            self._send_file(target, attachment=True)
            return
        self._error(HTTPStatus.NOT_FOUND, "Endpoint not found")

    def do_POST(self) -> None:
        try:
            parts = self._parts()
            if parts == ["jobs"]:
                name, kind, quality, settings = validate_job_payload(self._body_json())
                if os.statvfs(self.server.store.data_dir).f_bavail * os.statvfs(self.server.store.data_dir).f_frsize < self.server.min_free_bytes:
                    self._error(HTTPStatus.INSUFFICIENT_STORAGE, "Not enough free disk space to create a job")
                    return
                job = self.server.store.create_job(name=name, kind=kind, quality=quality, settings=settings)
                self._json(HTTPStatus.CREATED, job)
                return
            if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "start":
                job = self.server.store.get_job(parts[1])
                if job["state"] not in {"uploading", "interrupted", "failed", "cancelled"}:
                    raise ValueError("Job cannot be started from its current state")
                if job["uploaded_images"] < 3:
                    raise ValueError("Upload at least three images before starting")
                job = self.server.store.queue(parts[1])
                self.server.worker.notify()
                self._json(HTTPStatus.ACCEPTED, job)
                return
            if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "cancel":
                self._json(HTTPStatus.ACCEPTED, self.server.worker.cancel(parts[1]))
                return
            self._error(HTTPStatus.NOT_FOUND, "Endpoint not found")
        except KeyError:
            self._error(HTTPStatus.NOT_FOUND, "Job not found")
        except (ValueError, json.JSONDecodeError) as exc:
            self.close_connection = True
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_PUT(self) -> None:
        try:
            parts = self._parts()
            if len(parts) != 4 or parts[0] != "jobs" or parts[2] != "images":
                self._error(HTTPStatus.NOT_FOUND, "Endpoint not found")
                return
            job = self.server.store.get_job(parts[1])
            if job["state"] != "uploading":
                raise ValueError("Images can only be uploaded while the job is uploading")
            filename = normalize_upload_name(parts[3])
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise ValueError("Content-Length is required")
            if length > self.server.max_image_bytes:
                self._discard_body(length)
                self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Image exceeds the per-file limit")
                return
            input_dir = self.server.store.job_dir(parts[1]) / "input"
            destination = input_dir / filename
            previous_size = destination.stat().st_size if destination.exists() else 0
            if job["uploaded_bytes"] - previous_size + length > self.server.max_job_bytes:
                self._discard_body(length)
                self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Job exceeds its upload limit")
                return
            disk = os.statvfs(input_dir)
            if disk.f_bavail * disk.f_frsize - length < self.server.min_free_bytes:
                self._discard_body(length)
                self._error(HTTPStatus.INSUFFICIENT_STORAGE, "Not enough free disk space for this upload")
                return
            temporary = input_dir / f".{filename}.{threading.get_ident()}.upload"
            remaining = length
            try:
                with temporary.open("xb") as stream:
                    while remaining:
                        block = self.rfile.read(min(1024 * 1024, remaining))
                        if not block:
                            raise ValueError("Upload ended before Content-Length bytes arrived")
                        stream.write(block)
                        remaining -= len(block)
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)
            self._json(HTTPStatus.OK, {"name": filename, "bytes": length})
        except FileExistsError:
            self.close_connection = True
            self._error(HTTPStatus.CONFLICT, "An upload for this filename is already in progress")
        except KeyError:
            self.close_connection = True
            self._error(HTTPStatus.NOT_FOUND, "Job not found")
        except ValueError as exc:
            self.close_connection = True
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            self.close_connection = True
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def _discard_body(self, remaining: int) -> None:
        while remaining:
            block = self.rfile.read(min(1024 * 1024, remaining))
            if not block:
                break
            remaining -= len(block)

    def _static(self, request_path: str) -> None:
        if self.server.web_dir is None:
            self._error(HTTPStatus.NOT_FOUND, "Web UI is not installed")
            return
        relative = "index.html" if request_path == "/" else request_path.lstrip("/")
        root = self.server.web_dir.resolve()
        target = (root / relative).resolve()
        if root not in target.parents or not target.is_file():
            target = root / "index.html"
        if not target.is_file():
            self._error(HTTPStatus.NOT_FOUND, "Web UI is not installed")
            return
        self._send_file(target)

    def _send_file(self, path: Path, *, attachment: bool = False) -> None:
        size = path.stat().st_size
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(size))
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                self.wfile.write(block)


def create_server(
    host: str | None = None,
    port: int | None = None,
    *,
    data_dir: Path | None = None,
    bin_dir: Path | None = None,
    web_dir: Path | None = None,
) -> PhotogrammetryServer:
    data_dir = data_dir or Path(os.environ.get("PHOTOGRAMMETRY_DATA_DIR", "/var/lib/photogrammetry"))
    bin_dir = bin_dir or Path(os.environ.get("PHOTOGRAMMETRY_BIN_DIR", "/usr/bin"))
    web_dir = resolve_web_dir(web_dir)
    store = JobStore(data_dir)
    worker = Worker(store, Toolchain.from_bin_dir(bin_dir))
    server = PhotogrammetryServer(
        (host or os.environ.get("PHOTOGRAMMETRY_HOST", "0.0.0.0"),
         port if port is not None else int(os.environ.get("PHOTOGRAMMETRY_PORT", "8080"))),
        store,
        worker,
        web_dir,
    )
    worker.start()
    return server


def create_web_server(
    host: str,
    port: int,
    *,
    data_dir: Path,
    web_dir: Path | None,
) -> PhotogrammetryServer:
    store = JobStore(data_dir)
    web_dir = resolve_web_dir(web_dir)
    return PhotogrammetryServer((host, port), store, RemoteWorkerController(store), web_dir)


def resolve_web_dir(web_dir: Path | None) -> Path | None:
    if web_dir is None:
        configured = os.environ.get("PHOTOGRAMMETRY_WEB_DIR")
        web_dir = Path(configured) if configured else Path(__file__).parent / "static"
    return web_dir if web_dir.is_dir() else None
