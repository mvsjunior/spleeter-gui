from pathlib import Path

import pytest

from spleeter_gui.core.estimate import MIN_SECONDS, estimate_seconds
from spleeter_gui.core.models import Job


def make_job(seconds: float = 240.0, elapsed: float | None = None, **options) -> Job:
    return Job(
        input_path=Path("/a.mp3"),
        output_dir=Path("/out"),
        engine_id="spleeter",
        model="2stems",
        source_duration=seconds,
        elapsed_s=elapsed,
        options=options,
    )


def test_no_history_gives_no_estimate():
    assert estimate_seconds(make_job(), [], set()) is None


def test_scales_with_the_duration_of_the_song():
    past = [make_job(100.0 * 2, elapsed=100.0)]
    assert estimate_seconds(make_job(400.0), past, set()) == pytest.approx(200.0)


def test_one_hot_run_does_not_move_the_estimate():
    past = [make_job(elapsed=e) for e in (100.0, 100.0, 300.0)]
    assert estimate_seconds(make_job(), past, set()) == pytest.approx(100.0)


def test_only_the_most_recent_runs_count():
    past = [make_job(elapsed=100.0) for _ in range(5)] + [make_job(elapsed=900.0) for _ in range(9)]
    assert estimate_seconds(make_job(), past, set()) == pytest.approx(100.0)


def test_short_runs_say_nothing_of_the_speed():
    past = [make_job(MIN_SECONDS - 1, elapsed=500.0)]
    assert estimate_seconds(make_job(), past, set()) is None


def test_short_sections_get_no_estimate():
    job = make_job()
    job.duration = 30.0
    assert estimate_seconds(job, [make_job(elapsed=100.0)], set()) is None


def test_only_runs_with_the_same_speed_options_count():
    past = [make_job(elapsed=400.0, mwf=True, verbose=True), make_job(elapsed=100.0, mwf=False)]
    job = make_job(mwf=True, verbose=False)
    assert estimate_seconds(job, past, {"mwf"}) == pytest.approx(400.0)
    assert estimate_seconds(job, past[1:], {"mwf"}) is None
