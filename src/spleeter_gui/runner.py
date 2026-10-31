"""The queue, and the child process that runs one job at a time out of the interface's way."""

import os
import re
import shutil
import signal
import sqlite3
import uuid
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QObject, QProcess, QProcessEnvironment, QTimer, Signal

from spleeter_gui.core.models import CommandSpec, Job, JobStatus
from spleeter_gui.core.preflight import disk_shortage
from spleeter_gui.core.storage import History
from spleeter_gui.engines.base import Engine

NICE_LEVEL = "10"
TEMP_PREFIX = ".spleeter-gui-tmp-"
_MAX_NAME_BYTES = 200
_STOP_TIMEOUT_MS = 5000


def result_dir_for(job: Job) -> Path:
    """`<destination>/<song>-<model>`, with a numeric suffix instead of overwriting."""
    # File names are limited to 255 bytes, and the source name can already be close to it.
    stem = job.input_path.stem.encode()[:_MAX_NAME_BYTES].decode(errors="ignore")
    base = f"{stem}-{job.model}"
    candidate = job.output_dir / base
    number = 2
    while candidate.exists():
        candidate = job.output_dir / f"{base}-{number}"
        number += 1
    return candidate


class JobRunner(QObject):
    log_line = Signal(str)
    progress = Signal(float)
    job_finished = Signal(object)  # Job

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._process: QProcess | None = None
        self._job: Job | None = None
        self._engine: Engine | None = None
        self._workdir: Path | None = None
        self._steps: list[CommandSpec] = []
        self._parse_progress: Callable[[str], float | None] = lambda line: None
        self._buffer = b""
        self._last_line = ""
        self._stopping = False
        self._clock = QElapsedTimer()
        self._group = 0
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.setInterval(_STOP_TIMEOUT_MS)
        self._kill_timer.timeout.connect(lambda: self._signal_group(signal.SIGKILL))

    @property
    def is_running(self) -> bool:
        return self._process is not None

    def start(self, job: Job, engine: Engine) -> None:
        """Starts the job. Invalid options raise ValueError; failures arrive in `job_finished`."""
        if self.is_running:
            raise RuntimeError("Já existe uma separação em andamento.")
        workdir = job.output_dir / f"{TEMP_PREFIX}{uuid.uuid4().hex}"
        steps = [
            *engine.prepare_commands(job, workdir),
            engine.build_command(job, workdir),
            *engine.finish_commands(job, workdir),
        ]

        self._job = job
        self._engine = engine
        self._workdir = workdir
        self._steps = steps
        self._parse_progress = engine.progress_parser(job)
        self._buffer = b""
        self._last_line = ""
        self._stopping = False
        self._clock.start()
        job.status = JobStatus.RUNNING
        try:
            workdir.mkdir(parents=True)
        except OSError as error:
            self._finish(JobStatus.FAILED, f"Não foi possível criar a pasta de saída: {error}")
            return
        self._start_next_step()

    def _start_next_step(self) -> None:
        spec = self._steps.pop(0)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUNBUFFERED", "1")
        for key, value in spec.env.items():
            environment.insert(key, value)

        process = QProcess(self)
        process.setProcessEnvironment(environment)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.setStandardInputFile(QProcess.nullDevice())
        # Its own session, so that stopping reaches the whole tree, ffmpeg included.
        process.setUnixProcessParameters(QProcess.UnixProcessFlag.CreateNewSession)
        process.readyReadStandardOutput.connect(self._read_output)
        process.finished.connect(self._on_finished)
        process.errorOccurred.connect(self._on_error)
        self._process = process
        # nice replaces itself with the engine, so the process ID stays the engine's.
        process.start("nice", ["-n", NICE_LEVEL, *spec.argv])
        if self._process is process:
            self._group = process.processId()

    def cancel(self) -> None:
        """Asks the running job to end, without waiting. `job_finished` arrives when it does."""
        if self._process is None or self._stopping:
            return
        self._stopping = True
        self._signal_group(signal.SIGTERM)
        self._kill_timer.start()

    def stop(self) -> None:
        """Ends the running job and waits for it, leaving nothing behind."""
        process = self._process
        if process is None:
            return
        self.cancel()
        if not process.waitForFinished(_STOP_TIMEOUT_MS):
            self._signal_group(signal.SIGKILL)
            process.waitForFinished(_STOP_TIMEOUT_MS)

    def _signal_group(self, number: signal.Signals) -> None:
        if self._group:
            try:
                os.killpg(self._group, number)
            except ProcessLookupError:
                pass

    def _read_output(self) -> None:
        self._buffer += self._process.readAllStandardOutput().data()
        # Progress bars rewrite their line with \r, so it also ends a line.
        lines = self._buffer.replace(b"\r", b"\n").split(b"\n")
        self._buffer = lines.pop()
        for raw in lines:
            self._emit_line(raw)

    def _emit_line(self, raw: bytes) -> None:
        line = raw.decode(errors="replace").rstrip()
        if not line:
            return
        value = self._parse_progress(line)
        if value is not None:
            self.progress.emit(value)  # a progress bar would only fill the log
            return
        self._last_line = line
        self.log_line.emit(line)

    def _on_error(self, error: QProcess.ProcessError) -> None:
        # `finished` is not emitted when the process never started.
        if error == QProcess.ProcessError.FailedToStart:
            detail = self._process.errorString()
            self._finish(JobStatus.FAILED, f"Não foi possível iniciar o motor: {detail}")

    def _on_finished(self, code: int, exit_status: QProcess.ExitStatus) -> None:
        self._read_output()
        if self._buffer:
            self._emit_line(self._buffer)
            self._buffer = b""
        # A crashed engine, or one that ended first, can leave ffmpeg behind in its group.
        self._signal_group(signal.SIGKILL)
        if self._stopping:
            self._finish(JobStatus.CANCELLED)
        elif exit_status == QProcess.ExitStatus.CrashExit:
            self._finish(
                JobStatus.FAILED,
                "O motor foi encerrado inesperadamente. Pode ter faltado memória.",
            )
        elif code != 0:
            detail = f": {self._last_line}" if self._last_line else ""
            self._finish(JobStatus.FAILED, f"A separação falhou (código {code}){detail}")
        elif self._steps:
            self._process.deleteLater()
            self._start_next_step()
        else:
            self._publish_result()

    def _publish_result(self) -> None:
        try:
            self._engine.collect_outputs(self._job, self._workdir)
            result_dir = result_dir_for(self._job)
            self._workdir.rename(result_dir)
        except OSError as error:
            self._finish(JobStatus.FAILED, f"O resultado não foi gerado corretamente: {error}")
            return
        self._job.result_dir = result_dir
        self._finish(JobStatus.DONE)

    def _finish(self, status: JobStatus, error: str | None = None) -> None:
        job = self._job
        job.status = status
        job.error = error
        job.elapsed_s = self._clock.elapsed() / 1000
        self._kill_timer.stop()
        self._group = 0
        if status is not JobStatus.DONE:
            shutil.rmtree(self._workdir, ignore_errors=True)
        if self._process is not None:
            self._process.deleteLater()
        self._process = None
        self._job = None
        self._engine = None
        self._workdir = None
        self.job_finished.emit(job)


class JobQueue(QObject):
    """Runs jobs in order. A job that fails or is cancelled does not stop the next ones."""

    log_line = Signal(str)
    progress = Signal(float)
    job_started = Signal(object)  # Job
    job_finished = Signal(object)  # Job
    changed = Signal()  # the waiting jobs are different now
    drained = Signal()

    def __init__(self, history: History, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._history = history
        self._waiting: list[tuple[Job, Engine]] = []
        self._current: tuple[Job, Engine] | None = None
        self._runner = JobRunner(self)
        self._runner.log_line.connect(self.log_line)
        self._runner.progress.connect(self.progress)
        self._runner.job_finished.connect(self._on_job_finished)

    @property
    def pending(self) -> list[Job]:
        return [job for job, _ in self._waiting]

    @property
    def current(self) -> Job | None:
        return self._current[0] if self._current else None

    @property
    def is_active(self) -> bool:
        return self._current is not None

    def current_peak_mb(self) -> float:
        """The memory the running job is expected to take from what is available now."""
        if self._current is None:
            return 0.0
        job, engine = self._current
        return engine.estimate_peak_mb(job)

    def add(self, items: list[tuple[Job, Engine]]) -> None:
        """Takes a whole batch at once: a first job that fails at once must not end the queue."""
        self._waiting += items
        self.changed.emit()
        if self._current is None:
            self._start_next()

    def remove(self, index: int) -> None:
        del self._waiting[index]
        self.changed.emit()

    def move(self, index: int, delta: int) -> None:
        target = index + delta
        if 0 <= target < len(self._waiting):
            item = self._waiting.pop(index)
            self._waiting.insert(target, item)
            self.changed.emit()

    def cancel_current(self) -> None:
        self._runner.cancel()

    def shut_down(self) -> None:
        """Drops what is waiting and ends the running job, waiting for it."""
        self._waiting.clear()
        self.changed.emit()
        self._runner.stop()

    def _start_next(self) -> None:
        if not self._waiting:
            self.drained.emit()
            return
        self._current = self._waiting.pop(0)
        job, engine = self._current
        self.changed.emit()
        job.status = JobStatus.RUNNING
        job.downloaded_model = not engine.is_model_cached(job.model)
        # Recorded before anything exists on disk, so that a crash can be cleaned up after.
        self._record(self._history.add, job)
        self.job_started.emit(job)
        try:
            # Checked again: the jobs ahead may have used the space there was when this one came.
            problem = disk_shortage(job.output_dir, engine.required_disk_bytes(job))
            if problem is None:
                self._runner.start(job, engine)
        except ValueError as error:
            problem = str(error)
        if problem is not None:
            job.status = JobStatus.FAILED
            job.error = problem
            job.elapsed_s = 0.0
            self._on_job_finished(job)

    def _on_job_finished(self, job: Job) -> None:
        self._current = None
        if job.id is not None:
            self._record(self._history.update_result, job)
        self.job_finished.emit(job)
        self._start_next()

    def _record(self, write: Callable[[Job], object], job: Job) -> None:
        try:
            write(job)
        except sqlite3.Error as error:
            # Losing a record is no reason to lose the separation.
            self.log_line.emit(f"Não foi possível registrar no histórico: {error}")


_TEMP_NAME = re.compile(re.escape(TEMP_PREFIX) + "[0-9a-f]{32}")


def recover_interrupted(history: History) -> None:
    """Cleans up after a crash. Assumes this is the only instance of the app."""
    destinations = history.mark_interrupted()
    if not destinations:
        return
    _kill_orphaned_engines(destinations)
    for destination in destinations:
        for path in destination.glob(f"{TEMP_PREFIX}*"):
            if _TEMP_NAME.fullmatch(path.name) and path.is_dir() and not path.is_symlink():
                shutil.rmtree(path, ignore_errors=True)


def _kill_orphaned_engines(destinations: set[Path], proc: Path = Path("/proc")) -> None:
    """Engines have their own session, so they outlive an app that gets killed."""
    markers = [os.fsencode(destination / TEMP_PREFIX) for destination in destinations]
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            command = (entry / "cmdline").read_bytes()
            if any(marker in command for marker in markers) and os.getsid(pid) == pid:
                os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass  # it ended meanwhile, or it is not ours
