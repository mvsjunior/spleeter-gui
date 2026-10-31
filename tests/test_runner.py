import os
import signal
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from spleeter_gui import runner as runner_module
from spleeter_gui.core import preflight
from spleeter_gui.core.models import CommandSpec, Job, JobStatus
from spleeter_gui.core.storage import History
from spleeter_gui.runner import (
    TEMP_PREFIX,
    JobQueue,
    JobRunner,
    recover_interrupted,
    result_dir_for,
)
from tests.conftest import WRITES_OUTPUT, FakeEngine


def make_job(tmp_path: Path, name: str = "música é ção.mp3", model: str = "one") -> Job:
    return Job(
        input_path=tmp_path / name,
        output_dir=tmp_path / "saída",
        engine_id="fake",
        model=model,
        source_duration=10.0,
    )


def script(body: str) -> str:
    return textwrap.dedent(body)


def run(qtbot, tmp_path, engine, job=None) -> tuple[Job, list[str], list[float]]:
    job = job or make_job(tmp_path)
    runner = JobRunner()
    lines: list[str] = []
    values: list[float] = []
    runner.log_line.connect(lines.append)
    runner.progress.connect(values.append)
    with qtbot.waitSignal(runner.job_finished, timeout=20000):
        runner.start(job, engine)
    assert not runner.is_running
    return job, lines, values


def temp_dirs(job: Job) -> list[Path]:
    return list(job.output_dir.glob(f"{TEMP_PREFIX}*"))


def test_success_moves_the_result_to_its_named_folder(qtbot, tmp_path, fake_engine):
    job, _, _ = run(qtbot, tmp_path, fake_engine)
    assert job.status is JobStatus.DONE
    assert job.error is None
    assert job.elapsed_s > 0
    assert job.result_dir == job.output_dir / "música é ção-one"
    assert (job.result_dir / "a.wav").is_file()
    assert temp_dirs(job) == []


def test_result_folder_gets_a_suffix_instead_of_overwriting(qtbot, tmp_path, fake_engine):
    first, _, _ = run(qtbot, tmp_path, fake_engine)
    (first.result_dir / "a.wav").write_text("original")
    second, _, _ = run(qtbot, tmp_path, fake_engine)
    third, _, _ = run(qtbot, tmp_path, fake_engine)
    assert second.result_dir.name == "música é ção-one-2"
    assert third.result_dir.name == "música é ção-one-3"
    assert (first.result_dir / "a.wav").read_text() == "original"


def test_very_long_name_still_fits_the_file_system(tmp_path):
    job = make_job(tmp_path, name="é" * 120 + ".mp3")
    assert len(result_dir_for(job).name.encode()) < 255


def test_engine_receives_its_environment_at_low_priority(qtbot, tmp_path):
    engine = FakeEngine(
        script(f"""
        import os
        print(os.environ["FAKE_ENGINE_VAR"], os.nice(0))
        {WRITES_OUTPUT}
        """)
    )
    _, lines, _ = run(qtbot, tmp_path, engine)
    assert lines == ["ok 10"]


def test_engine_has_no_input_to_wait_for(qtbot, tmp_path):
    engine = FakeEngine(
        script(f"""
        import sys
        print(repr(sys.stdin.read()))
        {WRITES_OUTPUT}
        """)
    )
    job, lines, _ = run(qtbot, tmp_path, engine)
    assert job.status is JobStatus.DONE
    assert lines == ["''"]


def test_log_handles_stderr_carriage_returns_invalid_bytes_and_no_final_newline(qtbot, tmp_path):
    engine = FakeEngine(
        script(f"""
        import sys
        {WRITES_OUTPUT}
        sys.stderr.write("erro\\n")
        sys.stderr.flush()
        sys.stdout.buffer.write(b"10%\\r20%\\rinv\\xffalido\\n\\nfim sem quebra")
        """)
    )
    _, lines, _ = run(qtbot, tmp_path, engine)
    assert lines == ["erro", "10%", "20%", "inv�alido", "fim sem quebra"]


def test_progress_comes_from_the_engine(qtbot, tmp_path):
    engine = FakeEngine(
        script(f"""
        print("PROGRESS 0.5")
        print("texto")
        {WRITES_OUTPUT}
        """)
    )
    _, _, values = run(qtbot, tmp_path, engine)
    assert values == [0.5]


def test_nonzero_exit_fails_with_the_last_line_and_cleans_up(qtbot, tmp_path):
    engine = FakeEngine(
        script(f"""
        import sys
        {WRITES_OUTPUT}
        print("motivo do erro")
        sys.exit(3)
        """)
    )
    job, _, _ = run(qtbot, tmp_path, engine)
    assert job.status is JobStatus.FAILED
    assert "código 3" in job.error
    assert "motivo do erro" in job.error
    assert job.result_dir is None
    assert list(job.output_dir.iterdir()) == []


def test_killed_process_mentions_memory(qtbot, tmp_path):
    engine = FakeEngine("import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
    job, _, _ = run(qtbot, tmp_path, engine)
    assert job.status is JobStatus.FAILED
    assert "memória" in job.error
    assert temp_dirs(job) == []


def test_clean_exit_without_output_is_a_failure(qtbot, tmp_path):
    job, _, _ = run(qtbot, tmp_path, FakeEngine("pass"))
    assert job.status is JobStatus.FAILED
    assert "Stem não gerado" in job.error
    assert temp_dirs(job) == []


def test_missing_executable_is_a_failure(qtbot, tmp_path, fake_engine, monkeypatch):
    monkeypatch.setattr(
        fake_engine,
        "build_command",
        lambda job, workdir: type("Spec", (), {"argv": ["/nao/existe"], "env": {}})(),
    )
    job, _, _ = run(qtbot, tmp_path, fake_engine)
    assert job.status is JobStatus.FAILED
    assert temp_dirs(job) == []


def test_unusable_destination_is_a_failure(qtbot, tmp_path, fake_engine):
    (tmp_path / "arquivo").write_text("")
    job = make_job(tmp_path)
    job.output_dir = tmp_path / "arquivo" / "saída"
    job, _, _ = run(qtbot, tmp_path, fake_engine, job)
    assert job.status is JobStatus.FAILED
    assert "pasta de saída" in job.error


def test_invalid_options_raise_before_anything_is_created(
    qtbot, tmp_path, fake_engine, monkeypatch
):
    def reject(job, workdir):
        raise ValueError("inválido")

    monkeypatch.setattr(fake_engine, "build_command", reject)
    job = make_job(tmp_path)
    runner = JobRunner()
    with pytest.raises(ValueError):
        runner.start(job, fake_engine)
    assert not runner.is_running
    assert not job.output_dir.exists()


def test_second_job_is_refused_while_one_runs(qtbot, tmp_path):
    engine = FakeEngine("import time; time.sleep(30)")
    runner = JobRunner()
    runner.start(make_job(tmp_path), engine)
    with pytest.raises(RuntimeError):
        runner.start(make_job(tmp_path), engine)
    runner.stop()


def test_stop_ends_the_whole_tree_and_leaves_nothing(qtbot, tmp_path):
    marker = tmp_path / "pid"
    engine = FakeEngine(
        script(f"""
        import pathlib, subprocess, time
        child = subprocess.Popen(["sleep", "300"])
        pathlib.Path({str(marker)!r}).write_text(str(child.pid))
        time.sleep(300)
        """)
    )
    job = make_job(tmp_path)
    runner = JobRunner()
    finished: list[Job] = []
    runner.job_finished.connect(finished.append)
    runner.start(job, engine)
    qtbot.waitUntil(lambda: marker.exists() and bool(marker.read_text()), timeout=10000)
    child = marker.read_text()

    runner.stop()

    assert finished == [job]
    assert job.status is JobStatus.CANCELLED
    assert list(job.output_dir.iterdir()) == []
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if subprocess.run(["ps", "-o", "stat=", "-p", child], capture_output=True).returncode:
            break
        time.sleep(0.05)
    else:
        pytest.fail("the child process survived")


def test_stop_without_a_job_does_nothing(qtbot):
    JobRunner().stop()


def wait_gone(pid: str, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = subprocess.run(["ps", "-o", "stat=", "-p", pid], capture_output=True, text=True)
        if state.returncode or state.stdout.startswith("Z"):
            return True
        time.sleep(0.05)
    return False


def test_cancel_returns_at_once_and_reports_later(qtbot, tmp_path):
    runner = JobRunner()
    job = make_job(tmp_path)
    runner.start(job, FakeEngine("import time; time.sleep(30)"))
    with qtbot.waitSignal(runner.job_finished, timeout=5000):
        runner.cancel()
        assert runner.is_running
    assert job.status is JobStatus.CANCELLED
    assert list(job.output_dir.iterdir()) == []


def test_engine_that_ignores_the_request_is_killed(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module, "_STOP_TIMEOUT_MS", 300)
    marker = tmp_path / "ready"
    engine = FakeEngine(
        script(f"""
        import pathlib, signal, time
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        pathlib.Path({str(marker)!r}).touch()
        time.sleep(30)
        """)
    )
    runner = JobRunner()
    job = make_job(tmp_path)
    runner.start(job, engine)
    qtbot.waitUntil(marker.exists, timeout=5000)
    with qtbot.waitSignal(runner.job_finished, timeout=5000):
        runner.cancel()
    assert job.status is JobStatus.CANCELLED
    assert list(job.output_dir.iterdir()) == []


def test_crash_leaves_no_helper_process_behind(qtbot, tmp_path):
    marker = tmp_path / "pid"
    engine = FakeEngine(
        script(f"""
        import os, pathlib, signal, subprocess
        child = subprocess.Popen(["sleep", "300"])
        pathlib.Path({str(marker)!r}).write_text(str(child.pid))
        os.kill(os.getpid(), signal.SIGKILL)
        """)
    )
    job, _, _ = run(qtbot, tmp_path, engine)
    assert job.status is JobStatus.FAILED
    assert wait_gone(marker.read_text())


@pytest.fixture
def history(tmp_path):
    db = History(tmp_path / "history.db")
    yield db
    db.close()


@pytest.fixture
def queue(history):
    jobs = JobQueue(history)
    yield jobs
    jobs.shut_down()


def run_all(qtbot, queue: JobQueue, items: list[tuple[Job, FakeEngine]]) -> list[Job]:
    finished: list[Job] = []
    queue.job_finished.connect(finished.append)
    with qtbot.waitSignal(queue.drained, timeout=30000):
        queue.add(items)
    return finished


def test_queue_runs_in_order_one_at_a_time(qtbot, tmp_path, queue, fake_engine):
    jobs = [make_job(tmp_path, f"{n}.mp3") for n in "abc"]
    active: list[bool] = []
    queue.job_started.connect(lambda job: active.append(queue._runner.is_running))
    finished = run_all(qtbot, queue, [(job, fake_engine) for job in jobs])
    assert finished == jobs
    assert [job.status for job in jobs] == [JobStatus.DONE] * 3
    assert active == [False] * 3
    assert not queue.is_active


def test_failure_does_not_stop_the_next_jobs(qtbot, tmp_path, queue, fake_engine):
    jobs = [make_job(tmp_path, f"{n}.mp3") for n in "abc"]
    engines = [fake_engine, FakeEngine("raise SystemExit(1)"), fake_engine]
    run_all(qtbot, queue, list(zip(jobs, engines, strict=True)))
    assert [job.status for job in jobs] == [JobStatus.DONE, JobStatus.FAILED, JobStatus.DONE]


def test_invalid_job_does_not_stop_the_next_ones(qtbot, tmp_path, queue, fake_engine, monkeypatch):
    broken = FakeEngine()

    def reject(job, workdir):
        raise ValueError("opção inválida")

    monkeypatch.setattr(broken, "build_command", reject)
    jobs = [make_job(tmp_path, f"{n}.mp3") for n in "ab"]
    run_all(qtbot, queue, [(jobs[0], broken), (jobs[1], fake_engine)])
    assert jobs[0].status is JobStatus.FAILED
    assert jobs[0].error == "opção inválida"
    assert jobs[1].status is JobStatus.DONE


def test_job_that_runs_out_of_disk_fails_and_the_queue_goes_on(
    qtbot, tmp_path, queue, fake_engine, monkeypatch
):
    monkeypatch.setattr(preflight, "free_bytes", lambda path: 1)
    job = make_job(tmp_path)
    run_all(qtbot, queue, [(job, fake_engine)])
    assert job.status is JobStatus.FAILED
    assert "Espaço insuficiente" in job.error
    assert not job.output_dir.exists()


def test_cancel_only_ends_the_current_job(qtbot, tmp_path, queue, fake_engine):
    slow = FakeEngine("import time; time.sleep(30)")
    first, second = make_job(tmp_path, "a.mp3"), make_job(tmp_path, "b.mp3")
    queue.add([(first, slow), (second, fake_engine)])
    assert queue.current is first
    assert queue.pending == [second]
    with qtbot.waitSignal(queue.drained, timeout=20000):
        queue.cancel_current()
    assert first.status is JobStatus.CANCELLED
    assert second.status is JobStatus.DONE


def test_waiting_jobs_can_be_moved_and_removed(tmp_path, queue):
    slow = FakeEngine("import time; time.sleep(30)")
    jobs = [make_job(tmp_path, f"{n}.mp3") for n in "abcd"]
    queue.add([(job, slow) for job in jobs])
    names = lambda: [job.input_path.stem for job in queue.pending]  # noqa: E731
    assert names() == ["b", "c", "d"]
    queue.move(2, -1)
    assert names() == ["b", "d", "c"]
    queue.move(0, -1)
    queue.move(2, 1)
    assert names() == ["b", "d", "c"]
    queue.remove(1)
    assert names() == ["b", "c"]


def test_shut_down_ends_everything_and_starts_nothing(qtbot, tmp_path, queue):
    slow = FakeEngine("import time; time.sleep(30)")
    jobs = [make_job(tmp_path, f"{n}.mp3") for n in "ab"]
    queue.add([(job, slow) for job in jobs])
    queue.shut_down()
    assert not queue.is_active
    assert queue.pending == []
    assert jobs[0].status is JobStatus.CANCELLED
    assert jobs[1].status is JobStatus.QUEUED


def test_history_follows_the_job(qtbot, tmp_path, queue, history, fake_engine):
    job = make_job(tmp_path)
    seen: list[JobStatus] = []
    queue.job_started.connect(lambda started: seen.append(history.get(started.id).status))
    run_all(qtbot, queue, [(job, fake_engine)])
    assert seen == [JobStatus.RUNNING]
    stored = history.get(job.id)
    assert stored.status is JobStatus.DONE
    assert stored.result_dir == job.result_dir
    assert stored.elapsed_s == job.elapsed_s


def test_jobs_never_started_stay_out_of_the_history(tmp_path, queue, history):
    slow = FakeEngine("import time; time.sleep(30)")
    queue.add([(make_job(tmp_path, f"{name}.mp3"), slow) for name in "ab"])
    queue.shut_down()
    assert [job.input_path.name for job in history.recent()] == ["a.mp3"]


def test_first_download_is_recorded_so_it_does_not_count_as_speed(qtbot, tmp_path, queue, history):
    engine = FakeEngine(WRITES_OUTPUT, cached=False)
    job = make_job(tmp_path)
    run_all(qtbot, queue, [(job, engine)])
    assert history.get(job.id).downloaded_model
    assert history.completed_runs("fake", "one") == []


def test_history_failure_does_not_stop_the_queue(qtbot, tmp_path, queue, fake_engine, monkeypatch):
    def broken(job):
        raise sqlite3.OperationalError("disco cheio")

    monkeypatch.setattr(queue._history, "add", broken)
    lines: list[str] = []
    queue.log_line.connect(lines.append)
    job = make_job(tmp_path)
    run_all(qtbot, queue, [(job, fake_engine)])
    assert job.status is JobStatus.DONE
    assert any("disco cheio" in line for line in lines)


def interrupted_job(history: History, destination: Path) -> Job:
    job = make_job(destination)
    job.output_dir = destination
    job.status = JobStatus.RUNNING
    history.add(job)
    return job


def test_recovery_cleans_only_what_the_interrupted_job_left(history, tmp_path):
    destination = tmp_path / "destino"
    job = interrupted_job(history, destination)
    leftover = destination / f"{TEMP_PREFIX}{'a' * 32}"
    leftover.mkdir(parents=True)
    (leftover / "vocals.wav").write_text("parcial")
    result = destination / "música-2stems"
    result.mkdir()
    similar = destination / f"{TEMP_PREFIX}meu"
    similar.mkdir()
    untouched = tmp_path / "outro" / f"{TEMP_PREFIX}{'b' * 32}"
    untouched.mkdir(parents=True)

    recover_interrupted(history)

    assert not leftover.exists()
    assert result.is_dir()
    assert similar.is_dir()
    assert untouched.is_dir()
    assert history.get(job.id).status is JobStatus.FAILED


def test_recovery_ends_the_engine_that_outlived_the_app(history, tmp_path):
    destination = tmp_path / "destino"
    interrupted_job(history, destination)
    workdir = destination / f"{TEMP_PREFIX}{'c' * 32}"
    workdir.mkdir(parents=True)
    orphan = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "-o", str(workdir)],
        start_new_session=True,
    )
    other = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "-o", "/outro/destino"],
        start_new_session=True,
    )
    try:
        time.sleep(0.2)
        recover_interrupted(history)
        assert orphan.wait(timeout=5) == -signal.SIGKILL
        assert other.poll() is None
        assert not workdir.exists()
    finally:
        os.killpg(other.pid, signal.SIGKILL)
        other.wait()
        orphan.kill()


def test_recovery_with_nothing_interrupted_touches_nothing(history, tmp_path, monkeypatch):
    def fail(destinations):
        pytest.fail("no process scan on a normal start")

    monkeypatch.setattr(runner_module, "_kill_orphaned_engines", fail)
    recover_interrupted(history)


class StepsEngine(FakeEngine):
    """Records each step's order in a file, like an engine with work before and after."""

    def __init__(self, log: Path, script_body: str = WRITES_OUTPUT, after_code: int = 0) -> None:
        super().__init__(script_body)
        self.log = log
        self.after_code = after_code

    def _step(self, name: str, code: int = 0) -> CommandSpec:
        body = f"open({str(self.log)!r}, 'a').write({name!r}); raise SystemExit({code})"
        return CommandSpec([sys.executable, "-c", body], {})

    def prepare_commands(self, job, workdir):
        return [self._step("antes ")]

    def finish_commands(self, job, workdir):
        return [self._step("depois1 ", self.after_code), self._step("depois2 ")]


def test_steps_run_in_order_around_the_engine(qtbot, tmp_path):
    log = tmp_path / "log"
    job, _, _ = run(qtbot, tmp_path, StepsEngine(log))
    assert job.status is JobStatus.DONE
    assert log.read_text() == "antes depois1 depois2 "
    assert (job.result_dir / "a.wav").is_file()


def test_a_failing_step_ends_the_job_and_leaves_nothing(qtbot, tmp_path):
    log = tmp_path / "log"
    job, _, _ = run(qtbot, tmp_path, StepsEngine(log, after_code=3))
    assert job.status is JobStatus.FAILED
    assert "código 3" in job.error
    assert log.read_text() == "antes depois1 "
    assert temp_dirs(job) == []


def test_cancel_during_the_engine_skips_the_steps_after_it(qtbot, tmp_path):
    log = tmp_path / "log"
    runner = JobRunner()
    job = make_job(tmp_path)
    runner.start(job, StepsEngine(log, "import time; time.sleep(30)"))
    qtbot.waitUntil(lambda: log.exists() and bool(log.read_text()), timeout=5000)
    with qtbot.waitSignal(runner.job_finished, timeout=5000):
        runner.cancel()
    assert job.status is JobStatus.CANCELLED
    assert log.read_text() == "antes "


def test_progress_lines_stay_out_of_the_log(qtbot, tmp_path):
    engine = FakeEngine(
        script(f"""
        print("PROGRESS 0.5")
        print("texto")
        {WRITES_OUTPUT}
        """)
    )
    _, lines, values = run(qtbot, tmp_path, engine)
    assert lines == ["texto"]
    assert values == [0.5]
