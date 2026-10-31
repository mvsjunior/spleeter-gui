"""Job history in SQLite."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from spleeter_gui.core.models import Job, JobStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    input_path TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    engine_id TEXT NOT NULL,
    model TEXT NOT NULL,
    source_duration REAL NOT NULL,
    codec TEXT NOT NULL,
    bitrate TEXT NOT NULL,
    offset_s REAL NOT NULL,
    duration_s REAL,
    options TEXT NOT NULL,
    status TEXT NOT NULL,
    result_dir TEXT,
    error TEXT,
    elapsed_s REAL,
    created_at TEXT NOT NULL,
    downloaded_model INTEGER NOT NULL
)
"""
INTERRUPTED_ERROR = "Interrompida: o aplicativo foi encerrado durante a separação."


class History:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._open(path)
        except sqlite3.DatabaseError:
            # A damaged history must not stop the app; the file is kept for inspection.
            self._db.close()
            path.replace(path.with_name(path.name + ".corrompido"))
            self._open(path)

    def _open(self, path: Path) -> None:
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        with self._db:
            self._db.execute(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def add(self, job: Job) -> int:
        """Stores the job and fills in its `id` and `created_at`."""
        job.created_at = datetime.now(UTC).isoformat()
        with self._db:
            cursor = self._db.execute(
                """
                INSERT INTO jobs (
                    input_path, output_dir, engine_id, model, source_duration, codec, bitrate,
                    offset_s, duration_s, options, status, result_dir, error, elapsed_s, created_at,
                    downloaded_model
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(job.input_path),
                    str(job.output_dir),
                    job.engine_id,
                    job.model,
                    job.source_duration,
                    job.codec,
                    job.bitrate,
                    job.offset,
                    job.duration,
                    json.dumps(job.options),
                    job.status.value,
                    str(job.result_dir) if job.result_dir else None,
                    job.error,
                    job.elapsed_s,
                    job.created_at,
                    job.downloaded_model,
                ),
            )
        job.id = cursor.lastrowid
        return job.id

    def update_result(self, job: Job) -> None:
        """Saves the job's status and outcome."""
        if job.id is None:
            raise ValueError("O job ainda não foi adicionado ao histórico.")
        with self._db:
            self._db.execute(
                "UPDATE jobs SET status = ?, result_dir = ?, error = ?, elapsed_s = ? WHERE id = ?",
                (
                    job.status.value,
                    str(job.result_dir) if job.result_dir else None,
                    job.error,
                    job.elapsed_s,
                    job.id,
                ),
            )

    def get(self, job_id: int) -> Job | None:
        row = self._db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _to_job(row) if row else None

    def recent(self, limit: int = 100) -> list[Job]:
        """Most recent jobs first."""
        rows = self._db.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
        return [_to_job(row) for row in rows]

    def completed_runs(self, engine_id: str, model: str, limit: int = 50) -> list[Job]:
        """Most recent finished separations of a model that did not include its download."""
        rows = self._db.execute(
            """
            SELECT * FROM jobs
            WHERE engine_id = ? AND model = ? AND status = ? AND NOT downloaded_model
            ORDER BY id DESC LIMIT ?
            """,
            (engine_id, model, JobStatus.DONE.value, limit),
        )
        return [_to_job(row) for row in rows]

    def mark_interrupted(self) -> set[Path]:
        """Closes the jobs left running by a crash. Returns the destinations they were using."""
        running = JobStatus.RUNNING.value
        rows = self._db.execute("SELECT output_dir FROM jobs WHERE status = ?", (running,))
        destinations = {Path(row["output_dir"]) for row in rows}
        with self._db:
            self._db.execute(
                "UPDATE jobs SET status = ?, error = ? WHERE status = ?",
                (JobStatus.FAILED.value, INTERRUPTED_ERROR, running),
            )
        return destinations


def _to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        input_path=Path(row["input_path"]),
        output_dir=Path(row["output_dir"]),
        engine_id=row["engine_id"],
        model=row["model"],
        source_duration=row["source_duration"],
        codec=row["codec"],
        bitrate=row["bitrate"],
        offset=row["offset_s"],
        duration=row["duration_s"],
        options=json.loads(row["options"]),
        status=JobStatus(row["status"]),
        result_dir=Path(row["result_dir"]) if row["result_dir"] else None,
        error=row["error"],
        elapsed_s=row["elapsed_s"],
        created_at=row["created_at"],
        downloaded_model=bool(row["downloaded_model"]),
    )
