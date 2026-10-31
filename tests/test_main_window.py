import shutil
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QSettings, Qt, QUrl
from PySide6.QtGui import QDropEvent

from spleeter_gui.core import preflight
from spleeter_gui.core.models import Job, JobStatus, OptionSpec
from spleeter_gui.core.storage import History
from spleeter_gui.ui import main_window
from spleeter_gui.ui.main_window import MainWindow
from tests.conftest import WRITES_OUTPUT, FakeEngine


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def history(tmp_path):
    db = History(tmp_path / "history.db")
    yield db
    db.close()


@pytest.fixture
def make_window(qtbot, settings, history):
    def make(engine) -> MainWindow:
        window = MainWindow(engine, settings, history)
        window._confirm = lambda title, text: True
        qtbot.addWidget(window)
        return window

    return make


def select(qtbot, window: MainWindow, *paths: Path) -> None:
    window.set_inputs(list(paths))
    qtbot.waitUntil(lambda: len(window._probed) == len(paths), timeout=5000)


def separate(qtbot, window: MainWindow):
    with qtbot.waitSignal(window._queue.job_finished, timeout=20000) as blocker:
        window._separate.click()
    return blocker.args[0]


def test_separate_is_available_only_with_a_valid_song(qtbot, make_window, fake_engine, make_tone):
    window = make_window(fake_engine)
    assert not window._separate.isEnabled()
    select(qtbot, window, make_tone(seconds=2))
    assert window._separate.isEnabled()
    assert window._audio_label.text().startswith("00:02")


def test_output_defaults_to_the_song_folder(qtbot, make_window, fake_engine, make_tone):
    window = make_window(fake_engine)
    song = make_tone()
    select(qtbot, window, song)
    assert window._output.text() == str(song.parent)


def test_invalid_file_shows_the_reason_and_blocks_separation(
    qtbot, make_window, fake_engine, tmp_path
):
    path = tmp_path / "texto.mp3"
    path.write_text("não é áudio")
    window = make_window(fake_engine)
    select(qtbot, window, path)
    assert "texto.mp3" in window._problem.text()
    assert not window._separate.isEnabled()


def test_late_probe_of_a_replaced_song_is_ignored(qtbot, make_window, fake_engine, make_tone):
    window = make_window(fake_engine)
    first = make_tone("a.wav")
    second = make_tone("b.wav", seconds=3)
    select(qtbot, window, second)
    window._on_probed(first, "erro do arquivo antigo")
    assert window._ready_songs() == [second]
    assert "erro" not in window._problem.text()


def test_drop_selects_every_local_file(qtbot, make_window, fake_engine, make_tone):
    window = make_window(fake_engine)
    song = make_tone("música com espaço.wav")
    other = make_tone("outra.wav")
    mime = QMimeData()
    mime.setUrls(
        [
            QUrl("https://example.com/x.mp3"),
            QUrl.fromLocalFile(str(song)),
            QUrl.fromLocalFile(str(other)),
        ]
    )
    event = QDropEvent(
        QPoint(1, 1),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.dropEvent(event)
    assert window._selection == [song, other]
    qtbot.waitUntil(lambda: len(window._probed) == 2, timeout=5000)
    assert window._audio_label.text().startswith("2 músicas")


def test_success_offers_the_output_folder(qtbot, make_window, fake_engine, make_tone, monkeypatch):
    opened: list[QUrl] = []
    monkeypatch.setattr(main_window.QDesktopServices, "openUrl", opened.append)
    window = make_window(fake_engine)
    window.show()
    song = make_tone("música é ção.wav")
    select(qtbot, window, song)

    job = separate(qtbot, window)

    assert job.status is JobStatus.DONE
    assert window._open_result.isVisible()
    assert not window._activity.isVisible()
    assert not window._progress.isVisible()
    assert "Concluído" in window._status.text()
    assert not window._cancel.isVisible()
    window._open_result.click()
    assert opened == [QUrl.fromLocalFile(str(job.result_dir))]


def test_job_uses_what_the_form_says(qtbot, make_window, fake_engine, make_tone):
    window = make_window(fake_engine)
    select(qtbot, window, make_tone(seconds=10))
    window._options.select_model("two")
    window._options.set_codec("mp3")
    window._options.set_section("0:02", "3,5")
    window._options.option_widget("flag").setChecked(True)
    window._options.option_widget("name").setText("outro")

    job = window._build_job(window._ready_songs()[0])

    assert job.model == "two"
    assert job.codec == "mp3"
    assert job.bitrate == "320k"
    assert job.offset == 2.0
    assert job.duration == 3.5
    assert job.options == {"flag": True, "name": "outro"}


def test_empty_section_means_the_whole_song(qtbot, make_window, fake_engine, make_tone):
    window = make_window(fake_engine)
    select(qtbot, window, make_tone(seconds=2))
    job = window._build_job(window._ready_songs()[0])
    assert job.offset == 0.0
    assert job.duration is None


def test_bitrate_is_disabled_for_lossless_formats(qtbot, make_window, fake_engine):
    window = make_window(fake_engine)
    form = window._options
    form.set_codec("wav")
    assert not form.is_bitrate_enabled()
    form.set_codec("mp3")
    assert form.is_bitrate_enabled()


@pytest.mark.parametrize(
    ("offset", "duration", "message"),
    [("abc", "", "inválido"), ("", "1:2:3:4", "inválido"), ("99:00", "", "fora da música")],
)
def test_invalid_section_is_reported_and_nothing_starts(
    qtbot, make_window, fake_engine, make_tone, offset, duration, message
):
    window = make_window(fake_engine)
    select(qtbot, window, make_tone())
    window._options.set_section(offset, duration)
    window._separate.click()
    assert message in window._problem.text()
    assert not window._queue.is_active
    assert window._ready_songs()  # still selected, to correct and try again


def test_failure_shows_the_error_and_opens_the_log(qtbot, make_window, make_tone):
    window = make_window(FakeEngine("print('detalhe'); raise SystemExit(2)"))
    window.show()
    select(qtbot, window, make_tone())

    job = separate(qtbot, window)

    assert job.status is JobStatus.FAILED
    assert "detalhe" in window._status.text()
    assert window._log_section.is_open
    assert "detalhe" in window._log.toPlainText()
    assert not window._open_result.isVisible()


def test_activity_gives_way_to_real_progress(qtbot, make_window, make_tone):
    window = make_window(FakeEngine("import time; print('PROGRESS 0.25'); time.sleep(30)"))
    window.show()
    select(qtbot, window, make_tone())
    window._separate.click()
    assert window._activity.isVisible()
    qtbot.waitUntil(window._progress.isVisible, timeout=5000)
    assert not window._activity.isVisible()
    assert window._progress.value() == 25
    window._queue.shut_down()


def test_more_songs_can_be_added_while_one_runs(qtbot, make_window, make_tone):
    window = make_window(FakeEngine("import time; time.sleep(30)"))
    window.show()
    select(qtbot, window, make_tone("a.wav"))
    window._separate.click()
    assert window._queue.is_active
    assert not window._separate.isEnabled()  # nothing selected any more: no double additions
    assert window._options.isEnabled()
    assert not window._queue_panel.isVisible()

    select(qtbot, window, make_tone("b.wav"))
    assert window._separate.text() == "Adicionar à fila"
    window._separate.click()

    assert window._queue_panel.isVisible()
    assert window._queue_list.count() == 1
    assert window._queue_list.item(0).text().startswith("b.wav")
    window._queue.shut_down()


def test_several_songs_become_one_job_each(qtbot, make_window, fake_engine, make_tone, tmp_path):
    window = make_window(fake_engine)
    songs = [make_tone(f"{name}.wav") for name in "abc"]
    select(qtbot, window, *songs)
    window._output.setText(str(tmp_path / "saída"))
    finished = []
    window._queue.job_finished.connect(finished.append)
    with qtbot.waitSignal(window._queue.drained, timeout=20000):
        window._separate.click()
    assert [job.input_path for job in finished] == songs
    assert "Concluídas: 3" in window._status.text()


def test_a_section_outside_one_song_adds_nothing(qtbot, make_window, make_tone):
    window = make_window(FakeEngine("import time; time.sleep(30)"))
    select(qtbot, window, make_tone("longa.wav", seconds=5), make_tone("curta.wav", seconds=1))
    window._options.set_section("0:03", "")
    window._separate.click()
    assert "curta.wav" in window._problem.text()
    assert not window._queue.is_active


def queue_of_three(qtbot, window, make_tone):
    select(qtbot, window, *[make_tone(f"{name}.wav") for name in "abc"])
    window._separate.click()
    assert window._queue_list.count() == 2


def names_in_queue(window) -> list[str]:
    return [job.input_path.name for job in window._queue.pending]


def test_queue_can_be_reordered_and_edited(qtbot, make_window, make_tone):
    window = make_window(FakeEngine("import time; time.sleep(30)"))
    queue_of_three(qtbot, window, make_tone)

    window._queue_list.setCurrentRow(0)
    assert not window._move_up.isEnabled()
    window._move_down.click()
    assert names_in_queue(window) == ["c.wav", "b.wav"]
    assert window._queue_list.currentRow() == 1
    assert window._move_up.isEnabled()
    assert not window._move_down.isEnabled()

    window._remove.click()
    assert names_in_queue(window) == ["c.wav"]
    assert window._queue_list.currentRow() == 0
    window._queue.shut_down()


def test_cancel_goes_on_to_the_next_song(qtbot, make_window, make_tone):
    window = make_window(FakeEngine("import time; time.sleep(30)"))
    window.show()
    queue_of_three(qtbot, window, make_tone)
    with qtbot.waitSignal(window._queue.job_finished, timeout=5000) as blocker:
        window._cancel.click()
    assert blocker.args[0].status is JobStatus.CANCELLED
    assert window._queue.current.input_path.name == "b.wav"
    assert window._cancel.isEnabled()
    assert "b.wav" in window._status.text()
    window._queue.shut_down()


def test_cancel_does_not_block_the_interface(qtbot, make_window, make_tone):
    engine = FakeEngine("import signal, time; signal.signal(15, signal.SIG_IGN); time.sleep(30)")
    window = make_window(engine)
    window.show()
    select(qtbot, window, make_tone())
    window._separate.click()
    qtbot.wait(300)  # lets the script start ignoring the signal
    window._cancel.click()
    assert window._queue.is_active
    assert not window._cancel.isEnabled()
    assert window._status.text().startswith("Cancelando")
    window._queue.shut_down()


def test_failure_in_the_middle_is_summarized_at_the_end(
    qtbot, make_window, fake_engine, make_tone, monkeypatch
):
    window = make_window(fake_engine)
    window.show()
    bad = make_tone("ruim.wav")
    select(qtbot, window, make_tone("a.wav"), bad, make_tone("c.wav"))
    real = fake_engine.build_command

    def command(job, workdir):
        spec = real(job, workdir)
        if job.input_path == bad:
            spec = type(spec)([sys.executable, "-c", "print('quebrou'); raise SystemExit(4)"], {})
        return spec

    monkeypatch.setattr(fake_engine, "build_command", command)
    with qtbot.waitSignal(window._queue.drained, timeout=20000):
        window._separate.click()

    status = window._status.text()
    assert "Concluídas: 2" in status
    assert "Com falha: 1" in status
    assert "quebrou" in window._log.toPlainText()
    assert window._log_section.is_open


def test_closing_asks_first_and_can_be_refused(qtbot, make_window, make_tone):
    window = make_window(FakeEngine("import time; time.sleep(30)"))
    window.show()
    queue_of_three(qtbot, window, make_tone)
    questions = []
    window._confirm = lambda title, text: questions.append(text) or False

    window.close()

    assert "2 na fila" in questions[0]
    assert window.isVisible()
    assert window._queue.is_active
    assert len(window._queue.pending) == 2

    window._confirm = lambda title, text: True
    window.close()
    assert not window.isVisible()
    assert not window._queue.is_active
    assert window._queue.pending == []


def test_closing_while_idle_does_not_ask(qtbot, make_window, fake_engine):
    window = make_window(fake_engine)
    window._confirm = lambda title, text: pytest.fail("should not ask")
    window.show()
    window.close()


def test_missing_engine_is_explained(make_window):
    window = make_window(FakeEngine(installed=False))
    assert "setup_engines.sh fake" in window._notice.text()
    assert not window._separate.isEnabled()


def test_first_download_is_announced(qtbot, make_window, make_tone):
    window = make_window(FakeEngine("print(1)", cached=False))
    assert "10 MB" in window._notice.text()
    window._options.select_model("two")
    assert "20 MB" in window._notice.text()


def test_download_appears_in_the_status_while_running(qtbot, make_window, make_tone):
    window = make_window(FakeEngine("import time; time.sleep(30)", cached=False))
    select(qtbot, window, make_tone())
    window._separate.click()
    assert window._status.text().startswith("Baixando o modelo")
    window._queue.shut_down()


def test_not_enough_disk_blocks_the_job(qtbot, make_window, fake_engine, make_tone, monkeypatch):
    monkeypatch.setattr(preflight, "free_bytes", lambda path: 1)
    window = make_window(fake_engine)
    select(qtbot, window, make_tone())
    window._separate.click()
    assert "Espaço insuficiente" in window._problem.text()
    assert not window._queue.is_active


@pytest.mark.parametrize(("accepted", "starts"), [(True, True), (False, False)])
def test_memory_warning_lets_the_user_decide(
    qtbot, make_window, fake_engine, make_tone, monkeypatch, accepted, starts
):
    monkeypatch.setattr(main_window, "available_memory_mb", lambda: 50.0)
    window = make_window(fake_engine)
    questions: list[str] = []
    monkeypatch.setattr(window, "_confirm", lambda title, text: questions.append(text) or accepted)
    select(qtbot, window, make_tone())

    window._separate.click()

    assert "0.1 GB" in questions[0]
    assert window._queue.is_active is starts
    window._queue.shut_down()


def test_no_memory_warning_when_the_estimate_fits(
    qtbot, make_window, fake_engine, make_tone, monkeypatch
):
    monkeypatch.setattr(main_window, "available_memory_mb", lambda: 500.0)
    window = make_window(fake_engine)
    monkeypatch.setattr(window, "_confirm", lambda title, text: pytest.fail("should not ask"))
    select(qtbot, window, make_tone())
    separate(qtbot, window)


def test_preferences_survive_reopening(qtbot, make_window, fake_engine, make_tone, tmp_path):
    window = make_window(fake_engine)
    select(qtbot, window, make_tone())
    window._options.select_model("two")
    window._output.setText(str(tmp_path / "destino"))
    separate(qtbot, window)
    window.resize(700, 650)
    window.close()

    reopened = make_window(fake_engine)

    assert reopened._output.text() == str(tmp_path / "destino")
    assert reopened._options.model_info().id == "two"
    assert reopened.size().width() == 700
    assert str(window._settings.value("last_input_dir")) == str(tmp_path)


def test_memory_of_the_running_job_counts_as_available(qtbot, make_window, make_tone, monkeypatch):
    monkeypatch.setattr(main_window, "available_memory_mb", lambda: 50.0)
    engine = FakeEngine("import time; time.sleep(30)")
    window = make_window(engine)
    select(qtbot, window, make_tone("a.wav"))
    window._separate.click()  # accepted by the fixture
    window._confirm = lambda title, text: pytest.fail("the first job's memory comes back")
    select(qtbot, window, make_tone("b.wav"))
    window._separate.click()
    assert len(window._queue.pending) == 1
    window._queue.shut_down()


def history_rows(window) -> int:
    return window._history_view._table.rowCount()


def select_history_row(window, row: int = 0):
    window._tabs.setCurrentIndex(1)
    window._history_view._table.selectRow(row)


def test_history_shows_each_finished_job(qtbot, make_window, fake_engine, make_tone):
    window = make_window(fake_engine)
    window.show()
    select(qtbot, window, make_tone("música.wav"))
    job = separate(qtbot, window)

    assert history_rows(window) == 1
    table = window._history_view._table
    assert table.item(0, 1).text() == "música.wav"
    assert table.item(0, 3).text() == "Concluída"

    select_history_row(window)
    assert str(job.result_dir) in window._history_view._details.text()


def test_history_opens_the_folder_only_if_it_still_exists(
    qtbot, make_window, fake_engine, make_tone, monkeypatch
):
    opened: list[QUrl] = []
    monkeypatch.setattr(main_window.QDesktopServices, "openUrl", opened.append)
    window = make_window(fake_engine)
    select(qtbot, window, make_tone())
    job = separate(qtbot, window)

    select_history_row(window)
    window._history_view._open.click()
    assert opened == [QUrl.fromLocalFile(str(job.result_dir))]

    shutil.rmtree(job.result_dir)
    window._history_view.refresh()
    assert not window._history_view._open.isEnabled()


def test_history_keeps_the_error_of_a_failure(qtbot, make_window, make_tone):
    window = make_window(FakeEngine("print('motivo'); raise SystemExit(2)"))
    select(qtbot, window, make_tone())
    separate(qtbot, window)
    select_history_row(window)
    assert window._history_view._table.item(0, 3).text() == "Falhou"
    assert "motivo" in window._history_view._details.text()
    assert not window._history_view._open.isEnabled()


def test_repeat_runs_the_same_options_again(qtbot, make_window, fake_engine, make_tone):
    window = make_window(fake_engine)
    select(qtbot, window, make_tone(seconds=3))
    window._options.select_model("two")
    window._options.set_codec("mp3")
    window._options.set_section("1", "")
    window._options.option_widget("flag").setChecked(True)
    first = separate(qtbot, window)

    window._options.select_model("one")  # the form no longer matters
    select_history_row(window)
    with qtbot.waitSignal(window._queue.job_finished, timeout=20000) as blocker:
        window._history_view._repeat.click()

    second = blocker.args[0]
    assert window._tabs.currentIndex() == 0
    assert second.id != first.id
    assert second.status is JobStatus.DONE
    assert second.result_dir != first.result_dir
    assert (second.model, second.codec, second.offset) == ("two", "mp3", 1.0)
    assert second.options == first.options
    assert history_rows(window) == 2


def test_repeat_of_a_file_that_went_away_explains_it(qtbot, make_window, fake_engine, make_tone):
    window = make_window(fake_engine)
    song = make_tone()
    select(qtbot, window, song)
    separate(qtbot, window)
    song.unlink()

    select_history_row(window)
    window._history_view._repeat.click()

    qtbot.waitUntil(lambda: bool(window._problem.text()), timeout=5000)
    assert song.name in window._problem.text()
    assert history_rows(window) == 1


def test_estimate_appears_once_the_machine_has_a_record(
    qtbot, make_window, make_tone, history, tmp_path
):
    past = Job(
        input_path=tmp_path / "antiga.mp3",
        output_dir=tmp_path,
        engine_id="fake",
        model="one",
        source_duration=200.0,
        status=JobStatus.DONE,
        elapsed_s=100.0,
        options={"flag": False, "name": "padrão"},
    )
    history.add(past)
    window = make_window(FakeEngine("import time; time.sleep(30)"))
    select(qtbot, window, make_tone("a.wav", seconds=130), make_tone("b.wav", seconds=130))
    window._separate.click()
    assert "de cerca de 01:05" in window._status.text()
    assert "cerca de 01:05" in window._queue_list.item(0).text()
    window._queue.shut_down()


def test_history_describes_options_as_the_form_names_them(
    qtbot, make_window, fake_engine, make_tone
):
    window = make_window(fake_engine)
    select(qtbot, window, make_tone(seconds=3))
    window._options.set_codec("mp3")
    window._options.option_widget("flag").setChecked(True)
    separate(qtbot, window)
    select_history_row(window)
    details = window._history_view._details.text()
    assert "MP3 320k" in details
    assert "Opção" in details
    assert "padrão" not in details  # left at its default
    assert "flag" not in details


def two_engines(monkeypatch) -> tuple[FakeEngine, FakeEngine]:
    first, second = FakeEngine(WRITES_OUTPUT), FakeEngine(WRITES_OUTPUT)
    second.id = "other"
    second.label = "Outro"
    second.options = lambda: [OptionSpec("n", "Número", "int", 3, minimum=0, maximum=9)]
    monkeypatch.setattr(main_window, "ENGINES", {"fake": first, "other": second})
    return first, second


def make_two_engine_window(qtbot, settings, history) -> MainWindow:
    window = MainWindow(None, settings, history)
    window._confirm = lambda title, text: True
    qtbot.addWidget(window)
    return window


def test_first_engine_is_the_default_and_the_choice_is_hidden_with_only_one(
    qtbot, make_window, fake_engine, settings, history, monkeypatch
):
    window = make_window(fake_engine)
    window.show()
    assert not window._engine_box.isVisible()
    two_engines(monkeypatch)
    window = make_two_engine_window(qtbot, settings, history)
    window.show()
    assert window._engine_box.isVisible()
    assert window._engine.id == "fake"


def test_choosing_an_engine_changes_its_form_and_its_jobs(
    qtbot, settings, history, make_tone, monkeypatch
):
    two_engines(monkeypatch)
    window = make_two_engine_window(qtbot, settings, history)
    window.show()
    window.set_inputs([make_tone()])
    qtbot.waitUntil(lambda: bool(window._ready_songs()), timeout=5000)

    window._engine_box.setCurrentIndex(1)

    assert window._options.isVisible()
    assert not window._forms["fake"].isVisible()
    window._options.option_widget("n").setValue(5)
    finished = []
    window._queue.job_finished.connect(finished.append)
    with qtbot.waitSignal(window._queue.drained, timeout=20000):
        window._separate.click()
    assert finished[0].engine_id == "other"
    assert finished[0].options == {"n": 5}


def test_repeat_uses_the_engine_of_the_job(qtbot, settings, history, make_tone, monkeypatch):
    two_engines(monkeypatch)
    window = make_two_engine_window(qtbot, settings, history)
    window.set_inputs([make_tone()])
    qtbot.waitUntil(lambda: bool(window._ready_songs()), timeout=5000)
    window._engine_box.setCurrentIndex(1)
    with qtbot.waitSignal(window._queue.drained, timeout=20000):
        window._separate.click()
    window._engine_box.setCurrentIndex(0)
    window._tabs.setCurrentIndex(1)
    window._history_view._table.selectRow(0)
    with qtbot.waitSignal(window._queue.job_finished, timeout=20000) as blocker:
        window._history_view._repeat.click()
    assert blocker.args[0].engine_id == "other"


def test_time_is_shown_before_starting_and_follows_the_form(qtbot, make_window, make_tone):
    engine = FakeEngine(WRITES_OUTPUT)
    engine.seconds = 8.0
    engine.options = lambda: []
    window = make_window(engine)
    assert window._time_notice.text() == ""
    select(qtbot, window, make_tone(seconds=4))
    assert "cerca de 00:08" in window._time_notice.text()
    assert "2,0x" in window._time_notice.text()
    assert "Atenção" not in window._time_notice.text()

    window._options.set_section("abc", "")
    assert window._time_notice.text() == ""


def test_long_job_gets_a_highlighted_warning(qtbot, make_window, make_tone):
    engine = FakeEngine(WRITES_OUTPUT)
    engine.seconds = 20.0
    window = make_window(engine)
    select(qtbot, window, make_tone(seconds=2))
    assert "Atenção" in window._time_notice.text()
    assert "bold" in window._time_notice.styleSheet()


def test_real_progress_replaces_the_estimate(qtbot, make_window, make_tone):
    engine = FakeEngine("import time; print('PROGRESS 0.5'); time.sleep(30)")
    engine.seconds = 500.0
    window = make_window(engine)
    select(qtbot, window, make_tone())
    window._separate.click()
    assert "de cerca de 08:20" in window._status.text()
    window._elapsed_s = 100
    qtbot.waitUntil(lambda: window._estimate_s == 200.0, timeout=5000)
    window._show_elapsed()
    assert "de cerca de 03:20" in window._status.text()
    window._queue.shut_down()
