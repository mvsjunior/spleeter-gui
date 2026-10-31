import gc
import subprocess
import sys
import weakref
from pathlib import Path

import numpy as np
import pytest
import sounddevice as sd
from PySide6.QtCore import QPoint, QSettings, Qt

from spleeter_gui.core.audio import SAMPLE_RATE, decode, export_mix
from spleeter_gui.core.models import JobStatus
from spleeter_gui.core.storage import History
from spleeter_gui.ui.main_window import MainWindow
from spleeter_gui.ui.mixer import MixerPanel
from tests.conftest import WRITES_OUTPUT, FakeEngine
from tests.test_audio import FakeStream, noise, write_wav


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def streams():
    return []


@pytest.fixture
def panel(qtbot, settings, streams):
    def factory(**kwargs):
        streams.append(FakeStream(**kwargs))
        return streams[-1]

    widget = MixerPanel(settings, factory)
    widget._confirm = lambda text: True
    qtbot.addWidget(widget)
    yield widget
    widget.shut_down()


@pytest.fixture
def stems_folder(tmp_path) -> Path:
    folder = tmp_path / "música é-2stems"
    folder.mkdir()
    write_wav(folder / "vocals.wav", noise(2.0, seed=1))
    write_wav(folder / "accompaniment.wav", noise(2.0, seed=2))
    return folder


def chord_song() -> np.ndarray:
    """C major and then G major, for three seconds each."""

    def triad(*midi_notes: int) -> np.ndarray:
        t = np.arange(SAMPLE_RATE * 3) / SAMPLE_RATE
        hz = 440.0 * 2 ** ((np.array(midi_notes)[:, np.newaxis] - 69) / 12)
        wave = (0.2 * np.sin(2 * np.pi * hz * t)).sum(axis=0).astype(np.float32)
        return np.stack([wave, wave], axis=1)

    return np.concatenate([triad(48, 52, 55, 60), triad(55, 59, 62, 67)])


def load(qtbot, panel: MixerPanel, folder: Path) -> None:
    panel.open_folder(folder)
    qtbot.waitUntil(lambda: panel._player is not None, timeout=10000)


def test_opening_loads_each_stem_with_its_controls(qtbot, panel, stems_folder):
    panel.show()
    assert not panel._controls.isVisible()
    load(qtbot, panel, stems_folder)
    assert panel._controls.isVisible()
    assert [row.label.text() for row in panel._rows] == ["Voz", "Acompanhamento"]
    assert panel._time.text() == "00:00 / 00:02"
    assert panel._title.text() == stems_folder.name


def test_two_stem_results_are_named_for_people(qtbot, panel, tmp_path):
    folder = tmp_path / "dois"
    folder.mkdir()
    write_wav(folder / "vocals.wav", noise(1.0))
    write_wav(folder / "no_vocals.wav", noise(1.0))
    load(qtbot, panel, folder)
    assert sorted(row.label.text() for row in panel._rows) == ["Sem voz", "Voz"]


def test_six_stems_all_get_controls(qtbot, panel, tmp_path):
    folder = tmp_path / "seis"
    folder.mkdir()
    for name in ("vocals", "drums", "bass", "guitar", "piano", "other"):
        write_wav(folder / f"{name}.wav", noise(1.0))
    load(qtbot, panel, folder)
    labels = [row.label.text() for row in panel._rows]
    assert labels == ["Voz", "Bateria", "Baixo", "Guitarra", "Piano", "Outros"]


def test_folder_without_audio_is_explained(qtbot, panel, tmp_path):
    panel.open_folder(tmp_path)
    qtbot.waitUntil(lambda: "Nenhum arquivo" in panel._status.text(), timeout=5000)
    assert panel._player is None


def test_damaged_stem_is_explained(qtbot, panel, stems_folder):
    (stems_folder / "drums.wav").write_text("não é áudio")
    panel.open_folder(stems_folder)
    qtbot.waitUntil(lambda: "drums.wav" in panel._status.text(), timeout=10000)
    assert panel._player is None


def test_memory_warning_can_stop_the_loading(qtbot, panel, stems_folder, monkeypatch):
    monkeypatch.setattr("spleeter_gui.ui.mixer.available_memory_mb", lambda: 0.1)
    questions = []
    panel._confirm = lambda text: questions.append(text) or False
    panel.open_folder(stems_folder)
    qtbot.waitUntil(lambda: bool(questions), timeout=5000)
    assert "GB" in questions[0]
    assert panel._loader is None
    assert panel._player is None


def test_opening_another_folder_ignores_the_first(qtbot, panel, stems_folder, tmp_path):
    other = tmp_path / "outra"
    other.mkdir()
    write_wav(other / "bass.wav", noise(1.0))
    panel.open_folder(stems_folder)
    load(qtbot, panel, other)
    qtbot.wait(300)
    assert [row.label.text() for row in panel._rows] == ["Baixo"]


def test_closing_during_the_loading_is_safe(qtbot, panel, stems_folder):
    panel.open_folder(stems_folder)
    panel.close_song()
    qtbot.wait(500)
    assert panel._player is None
    assert panel._rows == []


def test_closing_gives_everything_back(qtbot, panel, stems_folder, streams):
    load(qtbot, panel, stems_folder)
    panel._play.click()
    panel.close_song()
    assert streams[0].closed
    assert panel._stems == []
    assert panel._rows == []
    assert not panel._refresh.isActive()
    assert panel._grid.count() == 0


def test_controls_reach_the_player(qtbot, panel, stems_folder):
    load(qtbot, panel, stems_folder)
    vocals, accompaniment = panel._rows
    vocals.volume.setValue(50)
    assert panel._player._target_gains == [0.5, 1.0]
    accompaniment.mute.setChecked(True)
    assert panel._player._target_gains == [0.5, 0.0]
    accompaniment.mute.setChecked(False)
    vocals.solo.setChecked(True)
    assert panel._player._target_gains == [0.5, 0.0]


def test_silent_stem_is_dimmed(qtbot, panel, stems_folder):
    load(qtbot, panel, stems_folder)
    panel._rows[0].solo.setChecked(True)
    assert panel._rows[1].wave._dimmed
    assert not panel._rows[0].wave._dimmed


def test_play_pause_and_button_text(qtbot, panel, stems_folder, streams):
    load(qtbot, panel, stems_folder)
    panel._play.click()
    assert panel._play.text() == "Pausar"
    assert panel._refresh.isActive()
    streams[0].pump()
    panel._play.click()
    assert panel._play.text() == "Reproduzir"
    assert not panel._refresh.isActive()
    with qtbot.waitSignal(panel._stream_finished, timeout=2000):
        streams[0].pump()
    qtbot.waitUntil(lambda: streams[0].closed, timeout=2000)


def test_space_key_is_play_and_pause(qtbot, panel, stems_folder):
    panel.show()
    qtbot.waitExposed(panel)
    load(qtbot, panel, stems_folder)
    panel.activateWindow()
    panel.setFocus()
    qtbot.keyClick(panel, Qt.Key.Key_Space)
    assert panel._player.is_playing


def test_end_of_song_goes_back_to_play(qtbot, panel, stems_folder, streams):
    load(qtbot, panel, stems_folder)
    panel._play.click()
    while streams[0].active:
        streams[0].pump()
    qtbot.waitUntil(lambda: panel._play.text() == "Reproduzir", timeout=2000)
    assert panel._time.text().startswith("00:02")
    assert not panel._refresh.isActive()


def test_missing_sound_device_is_explained(qtbot, settings, stems_folder):
    def no_device(**kwargs):
        raise sd.PortAudioError("sem dispositivo")

    panel = MixerPanel(settings, no_device)
    qtbot.addWidget(panel)
    load(qtbot, panel, stems_folder)
    panel._play.click()
    assert "saída de som" in panel._status.text()
    assert panel._play.text() == "Reproduzir"


def test_position_slider_seeks_when_released_not_during_the_drag(qtbot, panel, stems_folder):
    load(qtbot, panel, stems_folder)
    slider = panel._position
    slider.setSliderDown(True)
    slider.setValue(10)
    assert panel._player.position == 0
    assert panel._time.text().startswith("00:01")
    slider.setSliderDown(False)
    panel._on_position_released()
    assert panel._player.position == SAMPLE_RATE


def test_clicking_a_waveform_seeks(qtbot, panel, stems_folder):
    panel.resize(600, 400)
    panel.show()
    qtbot.waitExposed(panel)
    load(qtbot, panel, stems_folder)
    qtbot.waitUntil(lambda: panel._rows[0].wave.width() > 100, timeout=2000)
    wave = panel._rows[0].wave
    qtbot.mouseClick(wave, Qt.MouseButton.LeftButton, pos=QPoint(wave.width() // 2, 5))
    assert panel._player.position == pytest.approx(panel._player.frames / 2, rel=0.02)
    assert panel._rows[1].wave._cursor_x > 0


def test_waveform_paints_at_any_size(qtbot, panel, stems_folder):
    panel.show()
    qtbot.waitExposed(panel)
    load(qtbot, panel, stems_folder)
    wave = panel._rows[0].wave
    for width in (120, 700, 3000):  # fewer or more pixels than buckets
        wave.resize(width, 40)
        assert not wave.grab().isNull()


def wait_exported(qtbot, panel) -> None:
    qtbot.waitUntil(lambda: panel._exporter is None, timeout=20000)


def test_export_follows_the_controls(qtbot, panel, stems_folder, tmp_path):
    load(qtbot, panel, stems_folder)
    panel._rows[0].volume.setValue(50)
    panel._rows[1].mute.setChecked(True)
    target = tmp_path / "mistura é.wav"
    panel.export_to(target)
    wait_exported(qtbot, panel)
    assert str(target) in panel._status.text()
    np.testing.assert_allclose(decode(target, 2.0), noise(2.0, seed=1) * 0.5, atol=1 / 16000)


def test_export_mentions_a_reduction(qtbot, panel, tmp_path):
    folder = tmp_path / "alto"
    folder.mkdir()
    for name in ("a", "b"):
        write_wav(folder / f"{name}.wav", noise(1.0, level=0.9))
    load(qtbot, panel, folder)
    panel.export_to(tmp_path / "x.wav")
    wait_exported(qtbot, panel)
    assert "reduzido" in panel._status.text()


def test_export_gets_the_chosen_format_when_the_name_has_none(qtbot, panel, stems_folder, tmp_path):
    load(qtbot, panel, stems_folder)
    panel.export_to(tmp_path / "mistura", ".flac")
    wait_exported(qtbot, panel)
    assert (tmp_path / "mistura.flac").is_file()


def test_export_failure_is_explained(qtbot, panel, stems_folder, tmp_path):
    load(qtbot, panel, stems_folder)
    panel.export_to(tmp_path / "não existe" / "x.wav")
    wait_exported(qtbot, panel)
    assert "exportação falhou" in panel._status.text()


def test_not_enough_disk_blocks_the_export(qtbot, panel, stems_folder, tmp_path, monkeypatch):
    monkeypatch.setattr("spleeter_gui.core.preflight.free_bytes", lambda path: 1)
    load(qtbot, panel, stems_folder)
    panel.export_to(tmp_path / "x.wav")
    assert "Espaço insuficiente" in panel._status.text()
    assert panel._exporter is None


def test_only_one_export_at_a_time_and_none_of_silence(qtbot, panel, stems_folder, tmp_path):
    load(qtbot, panel, stems_folder)
    panel.export_to(tmp_path / "x.wav")
    assert not panel._export.isEnabled()
    wait_exported(qtbot, panel)
    assert panel._export.isEnabled()
    for row in panel._rows:
        row.mute.setChecked(True)
    assert not panel._export.isEnabled()


def test_playback_goes_on_during_an_export(qtbot, panel, stems_folder, streams, tmp_path):
    load(qtbot, panel, stems_folder)
    panel._play.click()
    panel.export_to(tmp_path / "x.wav")
    streams[0].pump()
    wait_exported(qtbot, panel)
    streams[0].pump()
    assert panel._player.is_playing


@pytest.fixture
def window(qtbot, settings, tmp_path):
    history = History(tmp_path / "history.db")
    created = MainWindow(FakeEngine(WRITES_OUTPUT), settings, history)
    created._confirm = lambda title, text: True
    created._mixer_factory = lambda panel_class: panel_class(
        settings, lambda **kw: FakeStream(**kw)
    )
    qtbot.addWidget(created)
    yield created
    history.close()


def test_mixer_is_not_built_until_it_is_needed(window):
    assert window._mixer is None
    assert window._tabs.tabText(2) == "Mixer"


def test_history_opens_a_result_in_the_mixer(qtbot, window, stems_folder, make_tone):
    window.set_inputs([make_tone()])
    qtbot.waitUntil(lambda: bool(window._ready_songs()), timeout=5000)
    with qtbot.waitSignal(window._queue.job_finished, timeout=20000) as blocker:
        window._separate.click()
    assert blocker.args[0].status is JobStatus.DONE
    window._tabs.setCurrentIndex(1)
    window._history_view._table.selectRow(0)
    window._history_view._mix.click()
    assert window._tabs.currentIndex() == 2
    assert window._tabs.currentWidget() is window._mixer
    assert window._tabs.count() == 3
    # the fake engine writes no real audio, so reaching the file is what can be checked here
    qtbot.waitUntil(lambda: "a.wav" in window._mixer._status.text(), timeout=10000)


def test_finished_job_offers_the_mixer(qtbot, window, make_tone):
    window.show()
    window.set_inputs([make_tone()])
    qtbot.waitUntil(lambda: bool(window._ready_songs()), timeout=5000)
    with qtbot.waitSignal(window._queue.job_finished, timeout=20000):
        window._separate.click()
    assert window._mix_result.isVisible()
    window._mix_result.click()
    # the fake engine writes no real audio, so reaching the file is what can be checked here
    qtbot.waitUntil(lambda: "a.wav" in window._mixer._status.text(), timeout=10000)


def test_closing_the_app_stops_the_sound(qtbot, window, stems_folder):
    window.open_in_mixer(stems_folder)
    qtbot.waitUntil(lambda: window._mixer._player is not None, timeout=10000)
    window._mixer._play.click()
    stream = window._mixer._player._stream
    window.close()
    assert stream.closed


def test_nothing_holds_on_to_the_samples_after_closing(
    qtbot, panel, stems_folder, streams, tmp_path
):
    load(qtbot, panel, stems_folder)
    panel._play.click()
    panel.export_to(tmp_path / "x.wav")
    wait_exported(qtbot, panel)
    samples = [weakref.ref(stem.samples) for stem in panel._stems]

    panel.close_song()
    streams.clear()  # this test's fake stream would hold its callback, unlike a closed real one
    qtbot.wait(50)  # the rows are deleted by the event loop
    gc.collect()

    assert all(ref() is None for ref in samples)


def test_memory_goes_back_when_closing_a_song(tmp_path):
    code = f"""
import numpy as np, gc
from pathlib import Path
from spleeter_gui.core.audio import decode
def rss():
    with open("/proc/self/status") as f:
        return [int(l.split()[1]) for l in f if l.startswith("VmRSS")][0] / 1024
base = rss()
samples = decode(Path({str(tmp_path / "x.wav")!r}), 60.0)
loaded = rss()
del samples; gc.collect()
print(loaded - base, rss() - base)
"""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=r=44100:a=0.1",
         "-t", "60", "-ac", "2", str(tmp_path / "x.wav")],
        check=True,
    )  # fmt: skip
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    loaded, after = map(float, out.stdout.split())
    assert loaded == pytest.approx(60 * SAMPLE_RATE * 8 / 2**20, rel=0.1)
    assert after < 20


def test_key_buttons_change_the_key_and_stop_at_the_limits(qtbot, panel, stems_folder):
    load(qtbot, panel, stems_folder)
    assert panel._key.text() == "original"
    panel._key_up.click()
    assert panel._key.text() == "+1"
    assert panel._player._semitones == 1
    for _ in range(8):
        panel._key_up.click()
    assert panel._key.text() == "+6"
    assert not panel._key_up.isEnabled()
    for _ in range(12):
        panel._key_down.click()
    assert panel._key.text() == "-6"
    assert not panel._key_down.isEnabled()
    assert panel._key_up.isEnabled()


def test_closing_a_song_puts_the_key_back(qtbot, panel, stems_folder):
    load(qtbot, panel, stems_folder)
    panel._key_up.click()
    panel.close_song()
    load(qtbot, panel, stems_folder)
    assert panel._key.text() == "original"
    assert panel._player._semitones == 0


def test_chord_on_screen_follows_the_position_and_the_key(qtbot, panel, tmp_path):
    folder = tmp_path / "acordes"
    folder.mkdir()
    write_wav(folder / "other.wav", chord_song())
    load(qtbot, panel, folder)
    assert panel._chord.text() == "C"
    assert panel._next_chord.text() == "depois: G"
    panel._seek_frame(SAMPLE_RATE * 5)
    assert panel._chord.text() == "G"
    assert panel._next_chord.text() == ""
    panel._key_up.click()
    panel._key_up.click()
    assert panel._chord.text() == "A"


def test_noise_or_drums_show_no_chord_without_failing(qtbot, panel, tmp_path):
    folder = tmp_path / "bateria"
    folder.mkdir()
    write_wav(folder / "drums.wav", noise(2.0))
    load(qtbot, panel, folder)
    assert panel._chord.text() == "—"
    assert panel._next_chord.text() == ""


def test_export_uses_the_key_in_use(qtbot, panel, stems_folder, tmp_path):
    load(qtbot, panel, stems_folder)
    panel._key_up.click()
    plain = tmp_path / "direta.wav"
    export_mix([stem.samples for stem in panel._stems], panel._gains(), plain)
    target = tmp_path / "no tom.wav"
    panel.export_to(target)
    qtbot.waitUntil(lambda: panel._exporter is None, timeout=20000)
    assert target.is_file()
    assert not np.array_equal(decode(target, 3.0), decode(plain, 3.0))


def test_key_buttons_do_not_take_the_space_bar(qtbot, panel, stems_folder):
    panel.show()
    load(qtbot, panel, stems_folder)
    panel._key_up.click()
    assert panel._key_up.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert panel._key_down.focusPolicy() == Qt.FocusPolicy.NoFocus
