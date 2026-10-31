"""The mixer. Loaded only when first used, because NumPy alone would slow down the app's start."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sounddevice as sd
from PySide6.QtCore import QObject, QRunnable, QSettings, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from spleeter_gui.core.audio import (
    EXPORT_BITRATE,
    EXPORT_ENCODERS,
    SAMPLE_RATE,
    AudioError,
    ExportCancelled,
    ExportResult,
    decode,
    effective_gains,
    export_mix,
    find_stems,
    memory_needed_bytes,
    waveform_peaks,
)
from spleeter_gui.core.player import Player
from spleeter_gui.core.preflight import available_memory_mb, disk_shortage, required_bytes
from spleeter_gui.core.probe import ProbeError, probe
from spleeter_gui.core.timecode import format_time
from spleeter_gui.ui.waveform import WaveformView

STEM_LABELS = {
    "vocals": "Voz",
    "drums": "Bateria",
    "bass": "Baixo",
    "guitar": "Guitarra",
    "piano": "Piano",
    "other": "Outros",
    "accompaniment": "Acompanhamento",
}
_LOSSY = f"{EXPORT_BITRATE.removesuffix('k')} kbps"
EXPORT_FILTERS = {
    "WAV, 16 bits (*.wav)": ".wav",
    "FLAC (*.flac)": ".flac",
    f"MP3, {_LOSSY} (*.mp3)": ".mp3",
    f"OGG, {_LOSSY} (*.ogg)": ".ogg",
    f"M4A, {_LOSSY} (*.m4a)": ".m4a",
}
_PEAK_BUCKETS = 2000
_POSITION_TICKS_PER_SECOND = 10
_REFRESH_MS = 100


@dataclass
class _Stem:
    name: str
    path: Path
    samples: np.ndarray
    peaks: np.ndarray


class _Worker(QRunnable):
    """Runs `work` off the interface thread. Each signal says which worker sent it."""

    class Signals(QObject):
        progress = Signal(object, float)
        finished = Signal(object, object, object)  # worker, result, error message

    def __init__(self, work: Callable[[Callable[[float], None]], object]) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.signals = self.Signals()
        self.done = False
        self._work = work

    def run(self) -> None:
        result, error = None, None
        try:
            result = self._work(lambda value: self.signals.progress.emit(self, value))
        except (AudioError, ProbeError, OSError) as failure:
            error = str(failure)
        except ExportCancelled:
            pass
        self._work = None  # it can refer to the stems, which must go when the song is closed
        self.signals.finished.emit(self, result, error)
        self.done = True


def _scan(folder: Path) -> list[tuple[str, Path, float]]:
    return [(name, path, probe(path).duration) for name, path in find_stems(folder).items()]


def _load(found: list[tuple[str, Path, float]], report: Callable[[float], None]) -> list[_Stem]:
    stems = []
    for index, (name, path, duration) in enumerate(found):
        report(index / len(found))
        samples = decode(path, duration)
        stems.append(_Stem(name, path, samples, waveform_peaks(samples, _PEAK_BUCKETS)))
    return stems


def _stem_label(name: str) -> str:
    if name.startswith("no_") and name[3:] in STEM_LABELS:
        return f"Sem {STEM_LABELS[name[3:]].lower()}"
    return STEM_LABELS.get(name, name)


class _StemRow:
    """The controls of one stem. They sit in the panel's grid, so that columns line up."""

    def __init__(self, stem: _Stem, on_change: Callable[[], None]) -> None:
        self.label = QLabel(_stem_label(stem.name))
        self.label.setToolTip(str(stem.path))
        self.mute = QCheckBox("Mudo")
        self.mute.toggled.connect(on_change)
        self.solo = QCheckBox("Solo")
        self.solo.toggled.connect(on_change)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(100)
        self.volume.setMaximumWidth(110)
        self.volume.setToolTip("Volume: 100% é o nível original do stem")
        self.volume.valueChanged.connect(on_change)
        self.wave = WaveformView(stem.peaks)
        self.widgets = (self.label, self.mute, self.solo, self.volume, self.wave)


class MixerPanel(QWidget):
    _stream_finished = Signal()

    def __init__(
        self,
        settings: QSettings,
        stream_factory: Callable[..., object] = sd.OutputStream,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._stream_factory = stream_factory
        self._workers: list[_Worker] = []
        self._scanner: _Worker | None = None
        self._loader: _Worker | None = None
        self._exporter: _Worker | None = None
        self._stop_export = False
        self._folder: Path | None = None
        self._stems: list[_Stem] = []
        self._rows: list[_StemRow] = []
        self._player: Player | None = None

        self._open = QPushButton("Abrir pasta de stems…")
        self._open.clicked.connect(self._on_choose_folder)
        self._title = QLabel("Abra uma pasta com os stems de uma música.")
        self._title.setWordWrap(True)
        self._close = QPushButton("Fechar")
        self._close.setToolTip("Fecha a música e libera a memória que ela ocupa")
        self._close.clicked.connect(self.close_song)

        self._play = QPushButton("Reproduzir")
        self._play.clicked.connect(self._on_play_pause)
        self._position = QSlider(Qt.Orientation.Horizontal)
        self._position.valueChanged.connect(self._on_position_changed)
        self._position.sliderReleased.connect(self._on_position_released)
        self._time = QLabel()
        self._export = QPushButton("Exportar mistura…")
        self._export.clicked.connect(self._on_export)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.hide()
        self._status = QLabel()
        self._status.setWordWrap(True)

        self._grid = QGridLayout()
        self._grid.setColumnStretch(4, 1)
        self._controls = QWidget()
        controls = QVBoxLayout(self._controls)
        controls.setContentsMargins(0, 0, 0, 0)
        transport = QHBoxLayout()
        transport.addWidget(self._play)
        transport.addWidget(self._position, 1)
        transport.addWidget(self._time)
        transport.addWidget(self._export)
        controls.addLayout(transport)
        controls.addLayout(self._grid, 1)
        self._controls.hide()

        top = QHBoxLayout()
        top.addWidget(self._open)
        top.addWidget(self._title, 1)
        top.addWidget(self._close)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self._controls, 1)
        layout.addWidget(self._progress)
        layout.addWidget(self._status)
        layout.addStretch()

        self._refresh = QTimer(self)
        self._refresh.setInterval(_REFRESH_MS)
        self._refresh.timeout.connect(self._show_position)
        self._stream_finished.connect(self._on_stream_finished)
        self._space = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self._space.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._space.activated.connect(self._on_play_pause)
        self._update_state()

    def open_folder(self, folder: Path) -> None:
        """Reads the stems of a folder, off the interface thread."""
        self.close_song()
        self._folder = folder
        self._settings.setValue("last_stems_dir", str(folder.parent))
        self._title.setText(folder.name)
        self._title.setToolTip(str(folder))
        self._set_status("Lendo a pasta…")
        self._scanner = self._start_worker(lambda report: _scan(folder), self._on_scanned)
        self._update_state()

    def close_song(self) -> None:
        """Stops playing and gives the memory back. Work in progress on it is ignored."""
        self._refresh.stop()
        self._scanner = None
        self._loader = None
        if self._player is not None:
            self._player.close()
            self._player = None
        for row in self._rows:
            for widget in row.widgets:
                self._grid.removeWidget(widget)
                widget.deleteLater()
        self._rows = []
        self._stems = []
        self._folder = None
        self._title.setText("Abra uma pasta com os stems de uma música.")
        self._title.setToolTip("")
        self._controls.hide()
        self._progress.hide()
        self._set_status("")
        self._update_state()

    def shut_down(self) -> None:
        self._stop_export = True
        self.close_song()

    def _start_worker(self, work, slot) -> _Worker:
        self._workers = [worker for worker in self._workers if not worker.done]
        worker = _Worker(work)
        worker.signals.finished.connect(slot)
        worker.signals.progress.connect(self._on_worker_progress)
        self._workers.append(worker)
        QThreadPool.globalInstance().start(worker)
        return worker

    def _on_choose_folder(self) -> None:
        start = str(self._settings.value("last_stems_dir", ""))
        name = QFileDialog.getExistingDirectory(self, "Abrir pasta de stems", start)
        if name:
            self.open_folder(Path(name))

    def _on_scanned(self, worker: _Worker, found, error: str | None) -> None:
        if worker is not self._scanner:
            return  # closed or replaced in the meantime
        self._scanner = None
        if error is not None:
            self._fail_loading(error)
            return
        if not self._accepts_memory_use(memory_needed_bytes([item[2] for item in found])):
            self.close_song()
            return
        self._set_status(f"Carregando {len(found)} stems…")
        self._progress.setValue(0)
        self._progress.show()
        self._loader = self._start_worker(lambda report: _load(found, report), self._on_loaded)
        self._update_state()

    def _fail_loading(self, error: str) -> None:
        self.close_song()
        self._set_status(error, error=True)

    def _accepts_memory_use(self, needed: int) -> bool:
        available = available_memory_mb()
        if available is None or needed / 1e6 <= available:
            return True
        return self._confirm(
            f"Carregar estes stems ocupa cerca de {needed / 1e9:.1f} GB de memória, e há "
            f"{available / 1000:.1f} GB disponíveis.\n\nContinuar mesmo assim?"
        )

    def _confirm(self, text: str) -> bool:
        answer = QMessageBox.warning(
            self,
            "Memória insuficiente",
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _on_loaded(self, worker: _Worker, stems: list[_Stem] | None, error: str | None) -> None:
        if worker is not self._loader:
            return
        self._loader = None
        self._progress.hide()
        if error is not None:
            self._fail_loading(error)
            return
        self._stems = stems
        for index, stem in enumerate(stems):
            row = _StemRow(stem, self._on_controls_changed)
            row.wave.seek_requested.connect(self._seek_fraction)
            for column, widget in enumerate(row.widgets):
                self._grid.addWidget(widget, index, column)
            self._grid.setRowStretch(index, 1)
            self._rows.append(row)
        self._player = Player(
            [stem.samples for stem in stems],
            self._gains(),
            self._stream_finished.emit,
            self._stream_factory,
        )
        self._position.setRange(0, self._seconds(self._player.frames) * _POSITION_TICKS_PER_SECOND)
        self._set_position_display(0)
        self._controls.show()
        self._set_status("")
        self._update_state()

    @staticmethod
    def _seconds(frames: int) -> int:
        return round(frames / SAMPLE_RATE)

    def _gains(self) -> list[float]:
        return effective_gains(
            [row.volume.value() / 100 for row in self._rows],
            [row.mute.isChecked() for row in self._rows],
            [row.solo.isChecked() for row in self._rows],
        )

    def _on_controls_changed(self) -> None:
        gains = self._gains()
        if self._player is not None:
            self._player.set_gains(gains)
        for row, gain in zip(self._rows, gains, strict=True):
            row.wave.set_dimmed(gain == 0.0)
        self._update_state()

    def _on_play_pause(self) -> None:
        player = self._player
        if player is None:
            return
        if player.is_playing:
            player.pause()
            self._refresh.stop()
        else:
            try:
                player.play()
            except sd.PortAudioError as error:
                player.close()
                self._set_status(f"Não foi possível usar a saída de som: {error}", error=True)
                return
            self._set_status("")
            self._refresh.start()
        self._update_state()

    def _on_stream_finished(self) -> None:
        player = self._player
        if player is None:
            return
        player.stream_ended()
        if not player.is_playing:
            self._refresh.stop()
            self._show_position(player.position)
        self._update_state()

    def _show_position(self, frame: int | None = None) -> None:
        if self._player is None or self._position.isSliderDown():
            return
        self._set_position_display(self._player.heard_position if frame is None else frame)

    def _set_position_display(self, frame: int) -> None:
        total = max(self._player.frames, 1)
        self._position.blockSignals(True)
        self._position.setValue(round(frame / SAMPLE_RATE * _POSITION_TICKS_PER_SECOND))
        self._position.blockSignals(False)
        self._show_time(frame / SAMPLE_RATE)
        for row in self._rows:
            row.wave.set_cursor(frame / total)

    def _show_time(self, seconds: float) -> None:
        total = self._player.frames / SAMPLE_RATE
        self._time.setText(f"{format_time(seconds)} / {format_time(total)}")

    def _on_position_changed(self, value: int) -> None:
        if self._position.isSliderDown():
            self._show_time(value / _POSITION_TICKS_PER_SECOND)  # jumps when the drag ends
        else:
            self._seek_ticks(value)

    def _on_position_released(self) -> None:
        self._seek_ticks(self._position.value())

    def _seek_ticks(self, value: int) -> None:
        self._seek_frame(round(value / _POSITION_TICKS_PER_SECOND * SAMPLE_RATE))

    def _seek_fraction(self, fraction: float) -> None:
        if self._player is not None:
            self._seek_frame(round(fraction * self._player.frames))

    def _seek_frame(self, frame: int) -> None:
        if self._player is not None:
            self._player.seek(frame)
            self._set_position_display(min(frame, self._player.frames))

    def _on_export(self) -> None:
        folder = self._folder
        start = str(folder.parent / f"{folder.name}-mistura.wav")
        name, chosen = QFileDialog.getSaveFileName(
            self, "Exportar mistura", start, ";;".join(EXPORT_FILTERS)
        )
        if name:
            self.export_to(Path(name), EXPORT_FILTERS.get(chosen, ".wav"))

    def export_to(self, path: Path, default_suffix: str = ".wav") -> None:
        if path.suffix.lower() not in EXPORT_ENCODERS:
            path = path.with_name(path.name + default_suffix)
        duration = self._player.frames / SAMPLE_RATE
        codec = path.suffix.lower().lstrip(".")
        shortage = disk_shortage(path.parent, required_bytes(duration, 1, codec, EXPORT_BITRATE))
        if shortage:
            self._set_status(shortage, error=True)
            return
        samples = [stem.samples for stem in self._stems]
        gains = self._gains()
        self._stop_export = False
        self._set_status(f"Exportando {path.name}…")
        self._progress.setValue(0)
        self._progress.show()
        self._exporter = self._start_worker(
            lambda report: export_mix(samples, gains, path, report, lambda: self._stop_export),
            self._on_exported,
        )
        self._update_state()

    def _on_exported(self, worker: _Worker, result: ExportResult | None, error: str | None) -> None:
        if worker is not self._exporter:
            return
        self._exporter = None
        self._progress.hide()
        if error is not None:
            self._set_status(error, error=True)
        elif result is not None:
            text = f"Mistura exportada: {result.path}"
            if result.scale != 1.0:
                reduction = -20 * np.log10(result.scale)
                text += f" (volume reduzido em {reduction:.1f} dB para evitar distorção)"
            self._set_status(text)
        self._update_state()

    def _on_worker_progress(self, worker: _Worker, value: float) -> None:
        if worker is self._loader or worker is self._exporter:
            self._progress.setValue(int(value * 100))

    def _update_state(self) -> None:
        loading = self._scanner is not None or self._loader is not None
        loaded = self._player is not None
        self._close.setVisible(loaded or loading)
        self._open.setEnabled(not loading)
        self._play.setText("Pausar" if loaded and self._player.is_playing else "Reproduzir")
        audible = any(gain > 0.0 for gain in self._gains())
        self._export.setEnabled(loaded and audible and self._exporter is None)
        self._export.setToolTip("" if audible else "Todos os stems estão em silêncio.")

    def _set_status(self, text: str, error: bool = False) -> None:
        self._status.setStyleSheet("color: palette(link);" if error else "")
        self._status.setText(text)
