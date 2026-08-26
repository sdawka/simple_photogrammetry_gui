from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .reconstruction import capture_diagnostics


TERMINAL_STATES = {"completed", "failed", "cancelled"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class JobStore:
    """SQLite-backed job state. Connections are short-lived for HTTP threads."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.jobs_dir = data_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = data_dir / "jobs.sqlite3"
        self._write_lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def _db(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('mesh', 'splat')),
                    quality TEXT NOT NULL CHECK(quality IN ('low', 'medium', 'high')),
                    settings_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    stage TEXT NOT NULL DEFAULT '',
                    stage_index INTEGER NOT NULL DEFAULT 0,
                    stage_total INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    queued_at TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_queue
                    ON jobs(state, queued_at, created_at);
                """
            )

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def create_job(
        self,
        *,
        name: str,
        kind: str,
        quality: str,
        settings: dict[str, Any],
    ) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        now = utc_now()
        with self._write_lock, self._db() as db:
            db.execute(
                """
                INSERT INTO jobs
                    (id, name, kind, quality, settings_json, state, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'uploading', ?, ?)
                """,
                (job_id, name, kind, quality, json.dumps(settings, sort_keys=True), now, now),
            )
        root = self.job_dir(job_id)
        for child in ("input", "work", "results", "checkpoints"):
            (root / child).mkdir(parents=True, exist_ok=True)
        return self.get_job(job_id)

    def _row_to_job(self, row: sqlite3.Row) -> dict[str, Any]:
        job = dict(row)
        job["settings"] = json.loads(job.pop("settings_json"))
        job["cancel_requested"] = bool(job["cancel_requested"])
        input_dir = self.job_dir(job["id"]) / "input"
        files = [
            p for p in input_dir.iterdir()
            if p.is_file() and not p.name.startswith(".")
        ] if input_dir.exists() else []
        job["uploaded_images"] = len(files)
        job["uploaded_bytes"] = sum(p.stat().st_size for p in files)
        diagnostics = capture_diagnostics(self.job_dir(job["id"]), len(files))
        if diagnostics is not None:
            job["capture_diagnostics"] = diagnostics
        return job

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return self._row_to_job(row)

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [self._row_to_job(row) for row in rows]

    def update(self, job_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {
            "state",
            "stage",
            "stage_index",
            "stage_total",
            "error",
            "cancel_requested",
            "queued_at",
            "started_at",
            "finished_at",
        }
        unknown = changes.keys() - allowed
        if unknown:
            raise ValueError(f"Unsupported job fields: {sorted(unknown)}")
        changes["updated_at"] = utc_now()
        columns = ", ".join(f"{key} = ?" for key in changes)
        values = [int(value) if key == "cancel_requested" else value for key, value in changes.items()]
        with self._write_lock, self._db() as db:
            cursor = db.execute(
                f"UPDATE jobs SET {columns} WHERE id = ?",  # columns are allow-listed above
                (*values, job_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(job_id)
        return self.get_job(job_id)

    def queue(self, job_id: str) -> dict[str, Any]:
        with self._write_lock, self._db() as db:
            now = utc_now()
            cursor = db.execute(
                """
                UPDATE jobs
                SET state = 'queued', queued_at = ?, error = NULL,
                    stage = 'Queued', cancel_requested = 0,
                    started_at = NULL, finished_at = NULL, updated_at = ?
                WHERE id = ? AND state IN ('uploading', 'interrupted', 'failed', 'cancelled')
                """,
                (now, now, job_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Job cannot be queued from its current state")
        return self.get_job(job_id)

    def claim_next(self) -> dict[str, Any] | None:
        with self._write_lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT id FROM jobs WHERE state = 'queued'
                ORDER BY queued_at ASC, created_at ASC LIMIT 1
                """
            ).fetchone()
            if row is None:
                db.commit()
                return None
            now = utc_now()
            db.execute(
                """
                UPDATE jobs SET state = 'running', started_at = COALESCE(started_at, ?),
                    cancel_requested = 0, updated_at = ? WHERE id = ?
                """,
                (now, now, row["id"]),
            )
            db.commit()
        return self.get_job(row["id"])

    def recover_after_restart(self) -> int:
        """Requeue interrupted work; checkpoints make completed stages resumable."""
        with self._write_lock, self._db() as db:
            now = utc_now()
            cursor = db.execute(
                """
                UPDATE jobs SET state = 'queued', queued_at = COALESCE(queued_at, ?),
                    stage = 'Recovering after service restart', cancel_requested = 0,
                    updated_at = ? WHERE state = 'running'
                """,
                (now, now),
            )
            return cursor.rowcount
