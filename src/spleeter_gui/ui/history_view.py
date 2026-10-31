"""The list of past separations, from which a folder can be opened or a job repeated."""

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from spleeter_gui.core.models import Job, JobStatus
from spleeter_gui.core.storage import History
from spleeter_gui.core.timecode import format_time
from spleeter_gui.engines.base import Engine

STATUS_LABELS = {
    JobStatus.QUEUED: "Na fila",
    JobStatus.RUNNING: "Em andamento",
    JobStatus.DONE: "Concluída",
    JobStatus.FAILED: "Falhou",
    JobStatus.CANCELLED: "Cancelada",
}
_COLUMNS = ("Data", "Música", "Modelo", "Resultado", "Tempo")


class HistoryView(QWidget):
    open_requested = Signal(object)  # Path
    mix_requested = Signal(object)  # Path
    repeat_requested = Signal(object)  # Job

    def __init__(
        self,
        history: History,
        engine_for: Callable[[Job], Engine | None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._history = history
        self._engine_for = engine_for
        self._jobs: list[Job] = []

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.verticalHeader().hide()
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.itemSelectionChanged.connect(self._update_selection)

        self._details = QLabel()
        self._details.setWordWrap(True)
        self._open = QPushButton("Abrir pasta")
        self._open.clicked.connect(self._on_open)
        self._mix = QPushButton("Abrir no mixer")
        self._mix.clicked.connect(self._on_mix)
        self._repeat = QPushButton("Repetir")
        self._repeat.clicked.connect(self._on_repeat)

        actions = QHBoxLayout()
        actions.addWidget(self._open)
        actions.addWidget(self._mix)
        actions.addWidget(self._repeat)
        actions.addStretch()

        layout = QVBoxLayout(self)
        layout.addWidget(self._table, 1)
        layout.addWidget(self._details)
        layout.addLayout(actions)
        self.refresh()

    def refresh(self) -> None:
        selected_id = job.id if (job := self._selected()) else None
        self._jobs = self._history.recent()
        self._table.setRowCount(len(self._jobs))
        for row, job in enumerate(self._jobs):
            for column, text in enumerate(_cells(job)):
                self._table.setItem(row, column, QTableWidgetItem(text))
            if job.id == selected_id:
                self._table.selectRow(row)
        self._update_selection()

    def _selected(self) -> Job | None:
        rows = self._table.selectionModel().selectedRows()
        return self._jobs[rows[0].row()] if rows else None

    def _update_selection(self) -> None:
        job = self._selected()
        self._repeat.setEnabled(job is not None and job.status is not JobStatus.RUNNING)
        has_result = job is not None and bool(job.result_dir and job.result_dir.is_dir())
        self._open.setEnabled(has_result)
        self._mix.setEnabled(has_result)
        self._details.setText(_describe(job, self._engine_for(job)) if job else "")

    def _on_open(self) -> None:
        job = self._selected()
        if job is not None and job.result_dir is not None:
            self.open_requested.emit(job.result_dir)

    def _on_mix(self) -> None:
        job = self._selected()
        if job is not None and job.result_dir is not None:
            self.mix_requested.emit(job.result_dir)

    def _on_repeat(self) -> None:
        job = self._selected()
        if job is not None:
            self.repeat_requested.emit(job)


def _cells(job: Job) -> tuple[str, str, str, str, str]:
    date = datetime.fromisoformat(job.created_at).astimezone().strftime("%d/%m/%Y %H:%M")
    elapsed = format_time(job.elapsed_s) if job.elapsed_s else ""
    return date, job.input_path.name, job.model, STATUS_LABELS[job.status], elapsed


def _describe(job: Job, engine: Engine | None) -> str:
    lines = [str(job.input_path)]
    settings = [f"Música de {format_time(job.source_duration)}"]
    lossless = engine is not None and job.codec in engine.lossless_codecs
    settings.append(job.codec.upper() if lossless else f"{job.codec.upper()} {job.bitrate}")
    if job.offset or job.duration is not None:
        settings.append(f"trecho a partir de {format_time(job.offset)}")
        settings.append(f"com {format_time(job.effective_duration)}")
    if engine is not None:
        settings += _changed_options(job, engine)
    lines.append(" · ".join(settings))
    if job.result_dir:
        lines.append(f"Saída: {job.result_dir}")
    if job.error:
        lines.append(job.error)
    return "\n".join(lines)


def _changed_options(job: Job, engine: Engine) -> list[str]:
    """What the job did differently from the engine's defaults, under the names in the form."""
    changed = []
    for spec in engine.options():
        value = job.options.get(spec.key, spec.default)
        if value != spec.default:
            changed.append(spec.label if spec.kind == "bool" else f"{spec.label}: {value}")
    return changed
