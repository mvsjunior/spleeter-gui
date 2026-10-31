import os
import subprocess

import pytest

from spleeter_gui.core.models import Job
from spleeter_gui.core.probe import probe
from spleeter_gui.engines.spleeter import SpleeterEngine

ENGINE = SpleeterEngine()


@pytest.mark.slow
@pytest.mark.skipif(not ENGINE.is_installed(), reason="run scripts/setup_engines.sh spleeter")
def test_separates_a_short_excerpt_using_only_core_and_engines(tmp_path):
    source = tmp_path / "canção com 'nome' difícil.mp3"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", "8", "-ac", "2", str(source),
        ],
        check=True,
    )  # fmt: skip
    workdir = tmp_path / "work"
    workdir.mkdir()
    info = probe(source)
    job = Job(
        input_path=source,
        output_dir=tmp_path,
        engine_id=ENGINE.id,
        model="2stems",
        source_duration=info.duration,
        codec="flac",
        offset=2.0,
    )

    spec = ENGINE.build_command(job, workdir)
    result = subprocess.run(
        spec.argv,
        env={**os.environ, **spec.env},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    outputs = ENGINE.collect_outputs(job, workdir)
    assert set(outputs) == {"vocals", "accompaniment"}
    for path in outputs.values():
        assert path.suffix == ".flac"
        assert probe(path).duration == pytest.approx(job.effective_duration, abs=0.2)
