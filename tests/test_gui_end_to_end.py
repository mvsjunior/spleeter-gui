import time

import pytest
from PySide6.QtCore import QSettings, QTimer

from spleeter_gui.core.models import JobStatus
from spleeter_gui.core.probe import probe
from spleeter_gui.core.storage import History
from spleeter_gui.engines.demucs import DemucsEngine
from spleeter_gui.engines.spleeter import SpleeterEngine
from spleeter_gui.ui.main_window import MainWindow

ENGINE = SpleeterEngine()
DEMUCS = DemucsEngine()
SPLEETER_DEFAULT_CUT_S = 600


@pytest.mark.slow
@pytest.mark.skipif(not ENGINE.is_installed(), reason="run scripts/setup_engines.sh spleeter")
def test_long_song_with_accents_is_not_cut_and_the_interface_stays_responsive(
    qtbot, tmp_path, make_tone
):
    song = make_tone("canção longa é ção.mp3", seconds=SPLEETER_DEFAULT_CUT_S + 20)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    history = History(tmp_path / "history.db")
    window = MainWindow(ENGINE, settings, history)
    qtbot.addWidget(window)
    window.show()
    window.set_inputs([song])
    qtbot.waitUntil(lambda: bool(window._ready_songs()), timeout=10000)
    source_duration = probe(song).duration

    gaps = [0.0]
    last = [time.perf_counter()]

    def beat() -> None:
        now = time.perf_counter()
        gaps.append(now - last[0])
        last[0] = now

    heartbeat = QTimer()
    heartbeat.setInterval(50)
    heartbeat.timeout.connect(beat)
    heartbeat.start()

    with qtbot.waitSignal(window._queue.job_finished, timeout=900_000) as blocker:
        window._separate.click()
    heartbeat.stop()

    job = blocker.args[0]
    assert job.status is JobStatus.DONE, job.error
    for stem in ("vocals", "accompaniment"):
        stems_duration = probe(job.result_dir / f"{stem}.wav").duration
        assert stems_duration == pytest.approx(source_duration, abs=0.5)
    assert max(gaps) < 1.0, f"the interface froze for {max(gaps):.2f} s"


@pytest.mark.slow
@pytest.mark.skipif(
    not DEMUCS.is_installed() or not DEMUCS.is_model_cached("htdemucs"),
    reason="run scripts/setup_engines.sh demucs and download htdemucs",
)
def test_demucs_section_converted_to_a_format_it_cannot_write(qtbot, tmp_path, make_tone):
    song = make_tone("canção é.mp3", seconds=8)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = MainWindow(DEMUCS, settings, History(tmp_path / "history.db"))
    qtbot.addWidget(window)
    window._output.setText(str(tmp_path / "saída"))
    window._options.select_model("htdemucs")
    window._options.set_codec("ogg")
    window._options.set_section("2", "4")
    window.set_inputs([song])
    qtbot.waitUntil(lambda: bool(window._ready_songs()), timeout=10000)
    values: list[float] = []
    window._queue.progress.connect(values.append)

    with qtbot.waitSignal(window._queue.job_finished, timeout=600_000) as blocker:
        window._separate.click()

    job = blocker.args[0]
    assert job.status is JobStatus.DONE, job.error
    assert values == sorted(values) and values[-1] == 1.0
    names = sorted(path.name for path in job.result_dir.iterdir())
    assert names == ["bass.ogg", "drums.ogg", "other.ogg", "vocals.ogg"]
    assert probe(job.result_dir / "vocals.ogg").duration == pytest.approx(4.0, abs=0.1)
