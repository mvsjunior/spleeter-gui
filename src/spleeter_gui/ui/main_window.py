"""The main window: choose songs and options, follow the queue, look back at the history."""

from collections import Counter
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSettings, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from spleeter_gui.core.estimate import estimate_seconds
from spleeter_gui.core.models import Job, JobStatus
from spleeter_gui.core.paths import HISTORY_PATH
from spleeter_gui.core.preflight import available_memory_mb, disk_shortage
from spleeter_gui.core.probe import AudioInfo, ProbeError, probe
from spleeter_gui.core.storage import History
from spleeter_gui.core.timecode import format_time
from spleeter_gui.engines import ENGINES
from spleeter_gui.engines.base import Engine
from spleeter_gui.runner import JobQueue
from spleeter_gui.ui.activity_bar import ActivityBar
from spleeter_gui.ui.collapsible import Collapsible
from spleeter_gui.ui.history_view import HistoryView
from spleeter_gui.ui.options import OptionsForm

AUDIO_FILTER = (
    "Áudio (*.mp3 *.wav *.flac *.ogg *.oga *.opus *.m4a *.aac *.wma *.aiff *.aif);;"
    "Todos os arquivos (*)"
)
MAX_LOG_LINES = 5000
_QUEUE_LIST_HEIGHT = 90
_MIXER_TAB = 2
_ERROR_STYLE = "color: palette(link);"
_WARNING_STYLE = "color: palette(link); font-weight: bold;"
_LONG_JOB_RATIO = 5.0
_TRUSTED_PROGRESS = 0.05


class _ProbeTask(QRunnable):
    class Signals(QObject):
        finished = Signal(object, object)  # Path, AudioInfo or the error message

    def __init__(self, paths: list[Path]) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.paths = paths
        self.signals = self.Signals()
        self.done = False

    def run(self) -> None:
        for path in self.paths:
            try:
                result: AudioInfo | str = probe(path)
            except ProbeError as error:
                result = str(error)
            self.signals.finished.emit(path, result)
        self.done = True


class MainWindow(QMainWindow):
    def __init__(
        self,
        engine: Engine | None = None,
        settings: QSettings | None = None,
        history: History | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Spleeter GUI")
        self.setAcceptDrops(True)
        self._engines = {engine.id: engine} if engine else dict(ENGINES)
        self._engine = next(iter(self._engines.values()))
        self._settings = settings or QSettings()
        self._history = history or History(HISTORY_PATH)
        self._queue = JobQueue(self._history, self)
        self._selection: list[Path] = []
        self._probed: dict[Path, AudioInfo | str] = {}
        self._probe_tasks: list[_ProbeTask] = []
        self._repeats: dict[Path, list[Job]] = {}
        self._batch: list[Job] = []
        self._result_dir: Path | None = None
        self._elapsed_s = 0
        self._estimate_s: float | None = None
        self._status_prefix = ""
        self._cancelling = False

        self._input = QLineEdit()
        self._input.setReadOnly(True)
        self._input.setPlaceholderText("Escolha arquivos ou arraste-os para esta janela")
        self._choose_input = QPushButton("Escolher…")
        self._choose_input.clicked.connect(self._on_choose_input)
        self._audio_label = QLabel()

        self._output = QLineEdit()
        self._output.setText(str(self._settings.value("last_output_dir", "")))
        self._output.textChanged.connect(self._update_state)
        self._choose_output = QPushButton("Escolher…")
        self._choose_output.clicked.connect(self._on_choose_output)

        self._engine_box = QComboBox()
        for known in self._engines.values():
            self._engine_box.addItem(known.label, known.id)
        self._engine_box.currentIndexChanged.connect(self._on_engine_changed)
        self._engine_label = QLabel("Motor:")
        self._engine_box.setVisible(len(self._engines) > 1)
        self._engine_label.setVisible(len(self._engines) > 1)

        self._forms = {key: self._make_form(known) for key, known in self._engines.items()}
        self._options = self._forms[self._engine.id]
        for form in self._forms.values():
            form.setVisible(form is self._options)

        self._notice = QLabel()
        self._notice.setWordWrap(True)
        self._time_notice = QLabel()
        self._time_notice.setWordWrap(True)
        self._problem = QLabel()
        self._problem.setWordWrap(True)
        self._problem.setStyleSheet(_ERROR_STYLE)

        self._separate = QPushButton("Separar")
        self._separate.setDefault(True)
        self._separate.clicked.connect(self._on_separate)
        self._cancel = QPushButton("Cancelar")
        self._cancel.setToolTip("Cancela a música em andamento. A fila continua na próxima.")
        self._cancel.clicked.connect(self._on_cancel)
        self._cancel.hide()
        self._open_result = QPushButton("Abrir pasta de saída")
        self._open_result.clicked.connect(self._on_open_result)
        self._open_result.hide()
        self._mix_result = QPushButton("Abrir no mixer")
        self._mix_result.clicked.connect(self._on_mix_result)
        self._mix_result.hide()

        self._queue_list = QListWidget()
        self._queue_list.setMaximumHeight(_QUEUE_LIST_HEIGHT)
        self._queue_list.currentRowChanged.connect(self._update_queue_buttons)
        self._remove = QPushButton("Remover")
        self._remove.clicked.connect(self._on_remove)
        self._move_up = QPushButton("Subir")
        self._move_up.clicked.connect(lambda: self._move_selected(-1))
        self._move_down = QPushButton("Descer")
        self._move_down.clicked.connect(lambda: self._move_selected(1))

        self._activity = ActivityBar()
        self._activity.hide()
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.hide()
        self._status = QLabel()
        self._status.setWordWrap(True)

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(MAX_LOG_LINES)
        self._log_section = Collapsible("Log", self._log)

        files = QGridLayout()
        files.addWidget(QLabel("Músicas:"), 0, 0)
        files.addWidget(self._input, 0, 1)
        files.addWidget(self._choose_input, 0, 2)
        files.addWidget(self._audio_label, 1, 1, 1, 2)
        files.addWidget(QLabel("Destino:"), 2, 0)
        files.addWidget(self._output, 2, 1)
        files.addWidget(self._choose_output, 2, 2)
        files.addWidget(self._engine_label, 3, 0)
        files.addWidget(self._engine_box, 3, 1, 1, 2)

        actions = QHBoxLayout()
        actions.addWidget(self._separate)
        actions.addWidget(self._cancel)
        actions.addWidget(self._open_result)
        actions.addWidget(self._mix_result)
        actions.addStretch()

        queue_buttons = QVBoxLayout()
        queue_buttons.addWidget(self._move_up)
        queue_buttons.addWidget(self._move_down)
        queue_buttons.addWidget(self._remove)
        queue_buttons.addStretch()
        queue_row = QHBoxLayout()
        queue_row.addWidget(self._queue_list, 1)
        queue_row.addLayout(queue_buttons)
        queue_layout = QVBoxLayout()
        queue_layout.setContentsMargins(0, 0, 0, 0)
        queue_layout.addWidget(QLabel("Na fila:"))
        queue_layout.addLayout(queue_row)
        self._queue_panel = QWidget()
        self._queue_panel.setLayout(queue_layout)
        self._queue_panel.hide()

        layout = QVBoxLayout()
        layout.addLayout(files)
        for form in self._forms.values():
            layout.addWidget(form)
        layout.addWidget(self._notice)
        layout.addWidget(self._time_notice)
        layout.addWidget(self._problem)
        layout.addLayout(actions)
        layout.addWidget(self._activity)
        layout.addWidget(self._progress)
        layout.addWidget(self._status)
        layout.addWidget(self._queue_panel)
        layout.addWidget(self._log_section, 1)
        separate_tab = QWidget()
        separate_tab.setLayout(layout)

        self._history_view = HistoryView(self._history, self._engine_for)
        self._history_view.open_requested.connect(self._open_folder)
        self._history_view.repeat_requested.connect(self._on_repeat)
        self._history_view.mix_requested.connect(self.open_in_mixer)

        self._mixer = None
        self._mixer_start = QPushButton("Abrir pasta de stems…")
        self._mixer_start.clicked.connect(self._on_choose_stems)
        self._mixer_problem = QLabel()
        self._mixer_problem.setWordWrap(True)
        self._mixer_problem.setStyleSheet(_ERROR_STYLE)
        start_layout = QVBoxLayout()
        start_layout.addWidget(QLabel("Abra uma pasta com os stems de uma música."))
        start_layout.addWidget(self._mixer_start)
        start_layout.addWidget(self._mixer_problem)
        start_layout.addStretch()
        mixer_start_page = QWidget()
        mixer_start_page.setLayout(start_layout)

        self._tabs = QTabWidget()
        self._tabs.addTab(separate_tab, "Separar")
        self._tabs.addTab(self._history_view, "Histórico")
        self._tabs.addTab(mixer_start_page, "Mixer")
        self.setCentralWidget(self._tabs)

        self._ticker = QTimer(self)
        self._ticker.setInterval(1000)
        self._ticker.timeout.connect(self._on_tick)

        self._queue.log_line.connect(self._log.appendPlainText)
        self._queue.progress.connect(self._on_progress)
        self._queue.job_started.connect(self._on_job_started)
        self._queue.job_finished.connect(self._on_job_finished)
        self._queue.changed.connect(self._refresh_queue)
        self._queue.drained.connect(self._on_drained)

        geometry = self._settings.value("window_geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(640, 620)
        self._update_state()
        self._update_queue_buttons()

    def _make_form(self, engine: Engine) -> OptionsForm:
        form = OptionsForm(engine)
        last_model = self._settings.value(f"model/{engine.id}")
        if last_model:
            form.select_model(str(last_model))
        form.changed.connect(self._update_state)
        return form

    def _on_engine_changed(self) -> None:
        self._engine = self._engines[self._engine_box.currentData()]
        self._options = self._forms[self._engine.id]
        for form in self._forms.values():
            form.setVisible(form is self._options)
        self._show_problem("")
        self._update_state()

    def set_inputs(self, paths: list[Path]) -> None:
        """Selects songs. Reading them happens off the interface thread."""
        self._selection = paths
        self._probed = {}
        self._show_problem("")
        if paths:
            self._settings.setValue("last_input_dir", str(paths[0].parent))
            if not self._output.text():
                self._output.setText(str(paths[0].parent))
            self._start_probe(paths, self._on_probed)
        self._show_selection()
        self._update_state()

    def shut_down(self) -> None:
        """Ends whatever is running. Safe to call more than once."""
        self._queue.shut_down()
        if self._mixer is not None:
            self._mixer.shut_down()
        self._ticker.stop()
        self._settings.setValue("window_geometry", self.saveGeometry())

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._queue.is_active:
            waiting = len(self._queue.pending)
            extra = f" e {waiting} na fila" if waiting else ""
            question = (
                f"Há uma separação em andamento{extra}. Fechar agora cancela tudo, "
                "e nada parcial fica no destino.\n\nFechar mesmo assim?"
            )
            if not self._confirm("Separação em andamento", question):
                event.ignore()
                return
        self.shut_down()
        super().closeEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._local_files(event):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        paths = self._local_files(event)
        if paths:
            event.acceptProposedAction()
            self.set_inputs(paths)

    @staticmethod
    def _local_files(event: QDragEnterEvent | QDropEvent) -> list[Path]:
        return [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]

    def _on_choose_input(self) -> None:
        start = str(self._settings.value("last_input_dir", ""))
        names, _ = QFileDialog.getOpenFileNames(self, "Escolher músicas", start, AUDIO_FILTER)
        if names:
            self.set_inputs([Path(name) for name in names])

    def _on_choose_output(self) -> None:
        name = QFileDialog.getExistingDirectory(self, "Escolher destino", self._output.text())
        if name:
            self._output.setText(name)

    def _start_probe(self, paths: list[Path], slot) -> None:
        self._probe_tasks = [task for task in self._probe_tasks if not task.done]
        task = _ProbeTask(paths)
        task.signals.finished.connect(slot)
        self._probe_tasks.append(task)
        QThreadPool.globalInstance().start(task)

    def _on_probed(self, path: Path, result: AudioInfo | str) -> None:
        if path not in self._selection:
            return  # other songs were chosen in the meantime
        self._probed[path] = result
        self._show_selection()
        self._update_state()

    def _ready_songs(self) -> list[Path]:
        return [p for p in self._selection if isinstance(self._probed.get(p), AudioInfo)]

    def _show_selection(self) -> None:
        count = len(self._selection)
        names = {0: "", 1: str(self._selection[0]) if count == 1 else ""}
        self._input.setText(names.get(count, f"{count} músicas selecionadas"))
        ready = [self._probed[path] for path in self._ready_songs()]
        if len(self._probed) < count:
            self._audio_label.setText("Lendo os arquivos…")
        elif count == 1 and ready:
            info = ready[0]
            self._audio_label.setText(
                f"{format_time(info.duration)} · {info.codec} · "
                f"{info.sample_rate} Hz · {info.channels} canais"
            )
        elif ready:
            total = format_time(sum(info.duration for info in ready))
            self._audio_label.setText(f"{len(ready)} músicas · {total} no total")
        else:
            self._audio_label.setText("")
        errors = [result for result in self._probed.values() if isinstance(result, str)]
        self._show_problem("\n".join(errors))

    def _update_state(self) -> None:
        active = self._queue.is_active
        installed = self._engine.is_installed()
        ready = bool(self._ready_songs()) and bool(self._output.text().strip())
        self._separate.setEnabled(installed and ready)
        self._separate.setText("Adicionar à fila" if active else "Separar")
        self._cancel.setVisible(active)
        self._cancel.setEnabled(active and not self._cancelling)

        if not installed:
            self._notice.setText(
                f"O motor {self._engine.label} não está instalado. "
                f"Instale-o com: ./scripts/setup_engines.sh {self._engine.id}"
            )
        elif not self._engine.is_model_cached(self._options.model_info().id):
            size = self._options.model_info().download_mb
            self._notice.setText(
                f"Este modelo será baixado na primeira separação ({size} MB; requer internet)."
            )
        else:
            self._notice.setText("")
        self._show_time_notice()

    def _show_time_notice(self) -> None:
        """Says how long the selected songs should take, before anything starts."""
        self._time_notice.setText("")
        try:
            jobs = [self._build_job(path) for path in self._ready_songs()]
        except ValueError:
            return  # the form is reported when the button is clicked
        estimates = [self._estimate(job, self._engine) for job in jobs]
        if not jobs or None in estimates:
            return
        total, audio = sum(estimates), sum(job.effective_duration for job in jobs)
        ratio = f"{total / audio:.1f}".replace(".", ",")
        text = f"Tempo estimado: cerca de {format_time(total)} ({ratio}x a duração do áudio)."
        long_job = total / audio > _LONG_JOB_RATIO
        if long_job:
            text = f"Atenção, separação muito lenta nesta máquina. {text}"
        self._time_notice.setStyleSheet(_WARNING_STYLE if long_job else "")
        self._time_notice.setText(text)

    def _build_job(self, path: Path) -> Job:
        """Reads the form. Anything invalid raises ValueError with a message for the user."""
        options = self._options
        job = Job(
            input_path=path,
            output_dir=Path(self._output.text().strip()),
            engine_id=self._engine.id,
            model=options.model_info().id,
            source_duration=self._probed[path].duration,
            codec=options.codec(),
            bitrate=options.bitrate(),
            offset=options.offset(),
            duration=options.duration(),
            options=options.engine_options(),
        )
        self._validate(job, self._engine)
        return job

    @staticmethod
    def _validate(job: Job, engine: Engine) -> None:
        job.effective_duration  # noqa: B018 - raises if the section is outside the song
        engine.build_command(job, job.output_dir)

    def _on_separate(self) -> None:
        self._show_problem("")
        jobs = []
        for path in self._ready_songs():
            try:
                jobs.append(self._build_job(path))
            except ValueError as error:
                self._show_problem(f"{path.name}: {error}")
                return
        if not self._accepts_resources(jobs, self._engine):
            return
        self._settings.setValue("last_output_dir", str(jobs[0].output_dir))
        self._settings.setValue(f"model/{self._engine.id}", jobs[0].model)
        # The songs are in the queue now, and a double click must not add them twice.
        self.set_inputs([])
        self._enqueue(jobs, self._engine)

    def _enqueue(self, jobs: list[Job], engine: Engine) -> None:
        if not self._queue.is_active:
            self._batch = []
            self._log.clear()
            self._set_status("")
            self._result_dir = None
            self._open_result.hide()
            self._mix_result.hide()
        self._queue.add([(job, engine) for job in jobs])
        self._update_state()

    def _accepts_resources(self, jobs: list[Job], engine: Engine) -> bool:
        needed = sum(engine.required_disk_bytes(job) for job in jobs)
        shortage = disk_shortage(jobs[0].output_dir, needed)
        if shortage:
            self._show_problem(shortage)
            return False
        return self._accepts_memory_use(jobs, engine)

    def _accepts_memory_use(self, jobs: list[Job], engine: Engine) -> bool:
        available = available_memory_mb()
        estimate = max(engine.estimate_peak_mb(job) for job in jobs)
        if available is None:
            return True
        # The running job frees its memory before the next one starts.
        available += self._queue.current_peak_mb()
        if estimate <= available:
            return True
        return self._confirm(
            "Memória insuficiente",
            f"Esta separação pode usar cerca de {estimate / 1000:.1f} GB de memória, "
            f"e há {available / 1000:.1f} GB disponíveis. Se faltar memória, o sistema pode "
            "encerrar o processo ou ficar lento.\n\n"
            "Para usar menos memória, processe um trecho ou desative o --mwf.\n\n"
            "Continuar mesmo assim?",
        )

    def _confirm(self, title: str, text: str) -> bool:
        answer = QMessageBox.warning(
            self,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _estimate(self, job: Job, engine: Engine) -> float | None:
        keys = {spec.key for spec in engine.options() if spec.affects_speed}
        past_runs = self._history.completed_runs(job.engine_id, job.model)
        measured = estimate_seconds(job, past_runs, keys)
        return measured if measured is not None else engine.estimate_seconds(job)

    def _engine_for(self, job: Job) -> Engine | None:
        return self._engines.get(job.engine_id) or ENGINES.get(job.engine_id)

    def _on_job_started(self, job: Job) -> None:
        engine = self._engine_for(job)
        self._log.appendPlainText(f"--- {job.input_path.name} ---")
        self._elapsed_s = 0
        self._estimate_s = self._estimate(job, engine) if engine else None
        self._cancelling = False
        action = "Baixando o modelo e separando" if job.downloaded_model else "Separando"
        self._status_prefix = f"{action} {job.input_path.name}"
        self._progress.hide()
        self._activity.show()
        self._ticker.start()
        self._update_state()
        self._show_elapsed()
        self._history_view.refresh()

    def _on_tick(self) -> None:
        self._elapsed_s += 1
        self._show_elapsed()

    def _show_elapsed(self) -> None:
        text = f"{self._status_prefix}… {format_time(self._elapsed_s)}"
        if self._estimate_s is not None and self._elapsed_s < self._estimate_s:
            text += f" de cerca de {format_time(self._estimate_s)}"
        self._set_status(text)

    def _on_progress(self, value: float) -> None:
        self._activity.hide()
        self._progress.show()
        self._progress.setValue(int(value * 100))
        if value >= _TRUSTED_PROGRESS and not self._cancelling:
            # What this run is showing beats any record, above all with the CPU getting hot.
            self._estimate_s = self._elapsed_s / value

    def _on_cancel(self) -> None:
        job = self._queue.current
        if job is None:
            return
        self._cancelling = True
        self._estimate_s = None
        self._status_prefix = f"Cancelando {job.input_path.name}"
        self._queue.cancel_current()
        self._update_state()
        self._show_elapsed()

    def _on_job_finished(self, job: Job) -> None:
        self._batch.append(job)
        self._ticker.stop()
        self._cancelling = False
        self._activity.hide()
        self._progress.hide()
        if job.status is JobStatus.DONE:
            self._result_dir = job.result_dir
            self._set_status(f"Concluído em {format_time(job.elapsed_s)}: {job.result_dir}")
            self._open_result.show()
            self._mix_result.show()
        elif job.status is JobStatus.FAILED:
            error = job.error or "A separação falhou."
            self._log.appendPlainText(error)
            self._set_status(error, error=True)
            self._log_section.set_open(True)
        else:
            self._set_status(f"{job.input_path.name}: cancelado.")
        self._history_view.refresh()
        self._update_state()

    def _on_drained(self) -> None:
        if len(self._batch) > 1:
            counts = Counter(job.status for job in self._batch)
            failed = counts[JobStatus.FAILED]
            self._set_status(
                f"Fila terminada. Concluídas: {counts[JobStatus.DONE]}. Com falha: {failed}. "
                f"Canceladas: {counts[JobStatus.CANCELLED]}. Detalhes na aba Histórico.",
                error=failed > 0,
            )
        self._update_state()

    def _refresh_queue(self) -> None:
        row = self._queue_list.currentRow()
        self._queue_list.clear()
        for job in self._queue.pending:
            engine = self._engine_for(job)
            estimate = self._estimate(job, engine) if engine else None
            suffix = f" · cerca de {format_time(estimate)}" if estimate is not None else ""
            self._queue_list.addItem(f"{job.input_path.name} · {job.model}{suffix}")
        self._queue_list.setCurrentRow(min(row, self._queue_list.count() - 1))
        self._queue_panel.setVisible(self._queue_list.count() > 0)
        self._update_queue_buttons()

    def _update_queue_buttons(self) -> None:
        row = self._queue_list.currentRow()
        self._remove.setEnabled(row >= 0)
        self._move_up.setEnabled(row > 0)
        self._move_down.setEnabled(0 <= row < self._queue_list.count() - 1)

    def _on_remove(self) -> None:
        row = self._queue_list.currentRow()
        if row >= 0:
            self._queue.remove(row)

    def _move_selected(self, delta: int) -> None:
        row = self._queue_list.currentRow()
        if row >= 0:
            self._queue.move(row, delta)
            self._queue_list.setCurrentRow(row + delta)

    def _on_repeat(self, job: Job) -> None:
        self._tabs.setCurrentIndex(0)
        self._show_problem("")
        # The file may have changed or gone away since, so it is read again.
        self._repeats.setdefault(job.input_path, []).append(job)
        self._start_probe([job.input_path], self._on_repeat_probed)

    def _on_repeat_probed(self, path: Path, result: AudioInfo | str) -> None:
        job = self._repeats[path].pop(0)
        if isinstance(result, str):
            self._show_problem(result)
            return
        engine = self._engine_for(job)
        if engine is None or not engine.is_installed():
            self._show_problem(f"O motor {job.engine_id} não está instalado.")
            return
        repeated = replace(
            job,
            source_duration=result.duration,
            id=None,
            status=JobStatus.QUEUED,
            result_dir=None,
            error=None,
            elapsed_s=None,
            created_at=None,
            downloaded_model=False,
        )
        try:
            self._validate(repeated, engine)
        except ValueError as error:
            self._show_problem(f"{path.name}: {error}")
            return
        if self._accepts_resources([repeated], engine):
            self._enqueue([repeated], engine)

    def open_in_mixer(self, folder: Path) -> None:
        mixer = self._ensure_mixer()
        if mixer is not None:
            self._tabs.setCurrentIndex(_MIXER_TAB)
            mixer.open_folder(folder)

    def _ensure_mixer(self):
        """Builds the mixer on first use, keeping NumPy and PortAudio out of the app's start."""
        if self._mixer is None:
            try:
                from spleeter_gui.ui.mixer import MixerPanel
            except OSError as error:  # sounddevice raises it when PortAudio is missing
                self._tabs.setCurrentIndex(_MIXER_TAB)
                self._mixer_problem.setText(
                    f"O mixer precisa da biblioteca PortAudio (libportaudio2): {error}"
                )
                return None
            self._mixer = self._mixer_factory(MixerPanel)
            current = self._tabs.currentIndex()
            page = self._tabs.widget(_MIXER_TAB)
            self._tabs.removeTab(_MIXER_TAB)
            self._tabs.insertTab(_MIXER_TAB, self._mixer, "Mixer")
            page.deleteLater()
            self._tabs.setCurrentIndex(current)
        return self._mixer

    def _mixer_factory(self, panel_class):
        return panel_class(self._settings)

    def _on_choose_stems(self) -> None:
        start = str(self._settings.value("last_stems_dir", ""))
        name = QFileDialog.getExistingDirectory(self, "Abrir pasta de stems", start)
        if name:
            self.open_in_mixer(Path(name))

    def _on_mix_result(self) -> None:
        if self._result_dir is not None:
            self.open_in_mixer(self._result_dir)

    def _on_open_result(self) -> None:
        if self._result_dir is not None:
            self._open_folder(self._result_dir)

    @staticmethod
    def _open_folder(path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _show_problem(self, text: str) -> None:
        self._problem.setText(text)
        self._problem.setVisible(bool(text))

    def _set_status(self, text: str, error: bool = False) -> None:
        self._status.setStyleSheet(_ERROR_STYLE if error else "")
        self._status.setText(text)
