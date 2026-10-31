from pathlib import Path

import pytest

from spleeter_gui.core.models import Job, JobStatus
from spleeter_gui.core.storage import History


@pytest.fixture
def history(tmp_path):
    db = History(tmp_path / "sub" / "history.db")
    yield db
    db.close()


def make_job(name: str = "música é ção.mp3") -> Job:
    return Job(
        input_path=Path("/music") / name,
        output_dir=Path("/out"),
        engine_id="spleeter",
        model="4stems",
        source_duration=731.7,
        codec="mp3",
        bitrate="320k",
        offset=10.5,
        duration=60.0,
        options={"mwf": True, "filename_format": "{instrument}.{codec}"},
    )


def test_add_fills_id_and_round_trips_every_field(history):
    job = make_job()
    job_id = history.add(job)
    assert job.id == job_id
    assert job.created_at
    assert history.get(job_id) == job


def test_duration_until_the_end_stays_none(history):
    job = make_job()
    job.duration = None
    assert history.get(history.add(job)).duration is None


def test_update_result_saves_the_outcome(history):
    job = make_job()
    history.add(job)
    job.status = JobStatus.DONE
    job.result_dir = Path("/out/música-4stems")
    job.elapsed_s = 91.67
    history.update_result(job)
    assert history.get(job.id) == job


def test_update_result_saves_a_failure(history):
    job = make_job()
    history.add(job)
    job.status = JobStatus.FAILED
    job.error = "ffmpeg: arquivo inválido"
    history.update_result(job)
    stored = history.get(job.id)
    assert stored.status is JobStatus.FAILED
    assert stored.error == job.error
    assert stored.result_dir is None


def test_update_requires_a_stored_job(history):
    with pytest.raises(ValueError):
        history.update_result(make_job())


def test_missing_job_is_none(history):
    assert history.get(999) is None


def test_recent_returns_newest_first_up_to_the_limit(history):
    ids = [history.add(make_job(f"{n}.mp3")) for n in range(3)]
    assert [job.id for job in history.recent()] == ids[::-1]
    assert [job.id for job in history.recent(limit=2)] == ids[:0:-1]


def test_data_survives_reopening(tmp_path):
    path = tmp_path / "history.db"
    first = History(path)
    job_id = first.add(make_job())
    first.close()
    second = History(path)
    assert second.get(job_id).model == "4stems"
    second.close()


def test_awkward_text_is_stored_literally(history):
    job = make_job("'; DROP TABLE jobs; --.mp3")
    assert history.get(history.add(job)).input_path == job.input_path


def finished(name: str = "a.mp3", **changes) -> Job:
    job = make_job(name)
    job.status = JobStatus.DONE
    job.elapsed_s = 100.0
    for key, value in changes.items():
        setattr(job, key, value)
    return job


def test_completed_runs_ignore_other_models_failures_and_downloads(history):
    good = finished()
    history.add(good)
    history.add(finished(model="2stems"))
    history.add(finished(status=JobStatus.FAILED))
    history.add(finished(engine_id="demucs"))
    history.add(finished(downloaded_model=True))
    assert [job.id for job in history.completed_runs("spleeter", "4stems")] == [good.id]


def test_mark_interrupted_closes_only_running_jobs(history):
    running = make_job()
    running.status = JobStatus.RUNNING
    running.output_dir = Path("/destino/da/execução")
    history.add(running)
    done = finished()
    history.add(done)

    assert history.mark_interrupted() == {Path("/destino/da/execução")}

    stored = history.get(running.id)
    assert stored.status is JobStatus.FAILED
    assert "Interrompida" in stored.error
    assert history.get(done.id).status is JobStatus.DONE
    assert history.mark_interrupted() == set()


def test_damaged_file_is_set_aside_instead_of_stopping_the_app(tmp_path):
    path = tmp_path / "history.db"
    path.write_bytes(b"isto nao e um banco de dados" * 100)
    history = History(path)
    assert history.recent() == []
    assert history.get(history.add(make_job())).model == "4stems"
    history.close()
    assert (tmp_path / "history.db.corrompido").is_file()
