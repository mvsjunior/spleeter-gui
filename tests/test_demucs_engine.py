from pathlib import Path

import pytest

from spleeter_gui.core.models import Job
from spleeter_gui.engines import ENGINES
from spleeter_gui.engines.demucs import MODELS, OPTIONS, DemucsEngine
from spleeter_gui.engines.spleeter import SpleeterEngine

ENGINE = DemucsEngine(Path("/env"), Path("/models"))
WORKDIR = Path("/out/tmp")


def make_job(**changes) -> Job:
    fields = {
        "input_path": Path("/music/song.mp3"),
        "output_dir": Path("/out"),
        "engine_id": "demucs",
        "model": "htdemucs",
        "source_duration": 240.0,
    }
    return Job(**{**fields, **changes})


def argv_for(**changes) -> list[str]:
    return ENGINE.build_command(make_job(**changes), WORKDIR).argv


def value_of(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_spleeter_stays_the_default_engine():
    assert isinstance(next(iter(ENGINES.values())), SpleeterEngine)
    assert isinstance(ENGINES["demucs"], DemucsEngine)


def test_always_runs_on_the_cpu_writing_flat_names():
    argv = argv_for()
    assert argv[0] == "/env/bin/demucs"
    assert value_of(argv, "-n") == "htdemucs"
    assert value_of(argv, "-d") == "cpu"
    assert value_of(argv, "-o") == str(WORKDIR)
    assert value_of(argv, "--filename") == "{stem}.{ext}"


def test_environment_keeps_the_four_threads_for_real():
    env = ENGINE.build_command(make_job(), WORKDIR).env
    assert env["TORCH_HOME"] == "/models"
    assert env["OMP_NUM_THREADS"] == "4"
    assert env["MKL_NUM_THREADS"] == "4"
    assert env["MKL_DYNAMIC"] == "FALSE"


def test_defaults_match_what_demucs_would_use():
    argv = argv_for()
    assert value_of(argv, "--shifts") == "1"
    assert value_of(argv, "--overlap") == "0.25"
    assert value_of(argv, "--clip-mode") == "rescale"
    assert value_of(argv, "-j") == "1"
    assert "--segment" not in argv
    assert "--two-stems" not in argv


def test_options_reach_the_command():
    argv = argv_for(
        options={"shifts": 3, "overlap": 0.5, "segment": 5, "two_stems": "vocals",
                 "clip_mode": "clamp", "jobs": 2}
    )  # fmt: skip
    assert value_of(argv, "--shifts") == "3"
    assert value_of(argv, "--overlap") == "0.5"
    assert value_of(argv, "--segment") == "5"
    assert value_of(argv, "--two-stems") == "vocals"
    assert value_of(argv, "--clip-mode") == "clamp"
    assert value_of(argv, "-j") == "2"


@pytest.mark.parametrize(
    ("codec", "bitrate", "present", "absent"),
    [
        ("wav", "320k", [], ["--mp3", "--flac", "--mp3-bitrate"]),
        ("flac", "320k", ["--flac"], ["--mp3"]),
        ("mp3", "192k", ["--mp3"], ["--flac"]),
        ("ogg", "192k", [], ["--mp3", "--flac", "--mp3-bitrate"]),
        ("m4a", "192k", [], ["--mp3", "--flac"]),
        ("wma", "192k", [], ["--mp3", "--flac"]),
    ],
)
def test_format_flags(codec, bitrate, present, absent):
    argv = argv_for(codec=codec, bitrate=bitrate)
    assert all(flag in argv for flag in present)
    assert not any(flag in argv for flag in absent)


def test_mp3_bitrate_is_a_plain_number():
    assert value_of(argv_for(codec="mp3", bitrate="192k"), "--mp3-bitrate") == "192"


@pytest.mark.parametrize(("depth", "flag"), [("24", "--int24"), ("float", "--float32")])
def test_depth_only_applies_to_wav(depth, flag):
    assert flag in argv_for(options={"depth": depth})
    assert flag not in argv_for(codec="flac", options={"depth": depth})
    assert flag not in argv_for(codec="ogg", options={"depth": depth})


@pytest.mark.parametrize(
    "changes",
    [
        {"model": "mdx_extra"},
        {"codec": "aiff"},
        {"offset": 300.0},
        {"duration": 0.0},
        {"options": {"two_stems": "guitar"}},  # htdemucs has no such stem
        {"options": {"shifts": -1}},
        {"options": {"jobs": 0}},
        {"options": {"overlap": 1.0}},
        {"options": {"segment": 8}},  # the transformer models stop at 7.8 s
        {"options": {"clip_mode": "nada"}},
        {"options": {"depth": "8"}},
    ],
)
def test_invalid_jobs_are_rejected(changes):
    with pytest.raises(ValueError):
        argv_for(**changes)


def test_six_stem_model_can_isolate_the_guitar():
    argv = argv_for(model="htdemucs_6s", options={"two_stems": "guitar"})
    assert value_of(argv, "--two-stems") == "guitar"


@pytest.mark.parametrize(
    "name", ["Nada é Impossível.mp3", "aspas \"e\" 'mais'.mp3", "$(rm -rf) ; & *.mp3", "-traço.mp3"]
)
def test_names_reach_the_engine_as_one_intact_argument(name):
    path = Path("/música com espaço") / name
    assert argv_for(input_path=path)[-1] == str(path)


def test_relative_name_cannot_become_an_option():
    assert Path(argv_for(input_path=Path("-x.mp3"))[-1]).is_absolute()


def test_whole_song_needs_no_preparation():
    assert ENGINE.prepare_commands(make_job(), WORKDIR) == []


def test_section_is_cut_before_and_replaces_the_input():
    job = make_job(offset=30.5, duration=10.0, input_path=Path("/m/é ção.mp3"))
    steps = ENGINE.prepare_commands(job, WORKDIR)
    assert len(steps) == 1
    argv = steps[0].argv
    assert float(value_of(argv, "-ss")) == 30.5
    assert float(value_of(argv, "-t")) == 10.0
    assert value_of(argv, "-i") == "/m/é ção.mp3"
    assert ENGINE.build_command(job, WORKDIR).argv[-1] == argv[-1]
    assert Path(argv[-1]).parent == WORKDIR


def test_section_past_the_end_is_capped():
    job = make_job(source_duration=100.0, offset=80.0, duration=50.0)
    argv = ENGINE.prepare_commands(job, WORKDIR)[0].argv
    assert float(value_of(argv, "-t")) == 20.0


@pytest.mark.parametrize("codec", ["wav", "flac", "mp3"])
def test_formats_demucs_writes_need_no_conversion(codec):
    assert ENGINE.finish_commands(make_job(codec=codec), WORKDIR) == []


@pytest.mark.parametrize(
    ("codec", "encoder"), [("ogg", "libvorbis"), ("m4a", "aac"), ("wma", "wmav2")]
)
def test_other_formats_are_converted_stem_by_stem(codec, encoder):
    steps = ENGINE.finish_commands(make_job(codec=codec, bitrate="192k"), WORKDIR)
    assert len(steps) == 4
    argv = steps[0].argv
    assert value_of(argv, "-i") == str(WORKDIR / "htdemucs" / "vocals.wav")
    assert value_of(argv, "-c:a") == encoder
    assert value_of(argv, "-b:a") == "192k"
    assert argv[-1] == str(WORKDIR / f"vocals.{codec}")


def test_conversion_follows_two_stems():
    steps = ENGINE.finish_commands(make_job(codec="ogg", options={"two_stems": "bass"}), WORKDIR)
    assert [Path(step.argv[-1]).name for step in steps] == ["bass.ogg", "no_bass.ogg"]


def feed(parse, *lines: str) -> list[float]:
    return [value for line in lines if (value := parse(line)) is not None]


BAR = " {}%|███   | 1.0/9.0 [00:01<00:02,  1.0seconds/s]"


def test_progress_reads_the_bar_and_nothing_else():
    parse = ENGINE.progress_parser(make_job())
    assert parse("Separating track /music/50%|.mp3") is None
    assert parse("Selected model is a bag of 1 models.") is None
    assert parse(BAR.format(50)) == pytest.approx(0.5)
    assert parse(BAR.format(100)) == 1.0


def test_progress_of_a_model_with_four_bars_only_goes_up():
    parse = ENGINE.progress_parser(make_job(model="htdemucs_ft"))
    percents = [0, 50, 100, 100, 0, 50, 100, 0, 100, 0, 100]
    values = feed(parse, *[BAR.format(p) for p in percents])
    assert values == sorted(values)
    assert values[1] == pytest.approx(0.125)
    assert values[5] == pytest.approx(0.375)
    assert values[-1] == 1.0


def test_progress_counts_a_bar_per_shift():
    parse = ENGINE.progress_parser(make_job(options={"shifts": 2}))
    assert feed(parse, BAR.format(100), BAR.format(0), BAR.format(50)) == [0.5, 0.5, 0.75]


def test_no_shift_still_has_one_bar():
    parse = ENGINE.progress_parser(make_job(options={"shifts": 0}))
    assert parse(BAR.format(50)) == pytest.approx(0.5)


def test_each_job_gets_its_own_progress():
    first = ENGINE.progress_parser(make_job())
    first(BAR.format(100))
    first(BAR.format(0))
    assert ENGINE.progress_parser(make_job())(BAR.format(10)) == pytest.approx(0.1)


def write_stems(workdir: Path, model: str, stems, suffix: str) -> None:
    (workdir / model).mkdir(parents=True)
    for stem in stems:
        (workdir / model / f"{stem}{suffix}").write_text(stem)


def test_outputs_end_up_flat_with_no_leftovers(tmp_path):
    write_stems(tmp_path, "htdemucs", ("vocals", "drums", "bass", "other"), ".flac")
    (tmp_path / "section.wav").touch()
    outputs = ENGINE.collect_outputs(make_job(codec="flac"), tmp_path)
    assert outputs["vocals"] == tmp_path / "vocals.flac"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "bass.flac", "drums.flac", "other.flac", "vocals.flac",
    ]  # fmt: skip


def test_converted_outputs_drop_the_wav_they_came_from(tmp_path):
    stems = ("vocals", "no_vocals")
    write_stems(tmp_path, "htdemucs", stems, ".wav")
    for stem in stems:
        (tmp_path / f"{stem}.ogg").touch()
    job = make_job(codec="ogg", options={"two_stems": "vocals"})
    assert set(ENGINE.collect_outputs(job, tmp_path)) == set(stems)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["no_vocals.ogg", "vocals.ogg"]


def test_missing_stem_is_an_error(tmp_path):
    write_stems(tmp_path, "htdemucs", ("vocals",), ".wav")
    with pytest.raises(FileNotFoundError, match="drums"):
        ENGINE.collect_outputs(make_job(), tmp_path)


def test_model_counts_as_cached_only_with_all_its_files(tmp_path):
    engine = DemucsEngine(tmp_path, tmp_path)
    folder = tmp_path / "hub" / "checkpoints"
    folder.mkdir(parents=True)
    assert not engine.is_model_cached("htdemucs")
    (folder / "955717e8-8726e21a.th").touch()
    assert engine.is_model_cached("htdemucs")
    assert not engine.is_model_cached("htdemucs_6s")
    for name in ("f7e0c4bc-a", "d12395a8-b", "92cfc3b6-c"):
        (folder / f"{name}.th").touch()
    assert not engine.is_model_cached("htdemucs_ft")
    (folder / "04573f0d-d.th").touch()
    assert engine.is_model_cached("htdemucs_ft")


def test_is_installed_checks_the_executable(tmp_path):
    assert not DemucsEngine(tmp_path, tmp_path).is_installed()
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "demucs").touch()
    assert DemucsEngine(tmp_path, tmp_path).is_installed()


@pytest.mark.parametrize(
    ("model", "ratio"), [("htdemucs", 4.36), ("htdemucs_6s", 3.10), ("htdemucs_ft", 20.88)]
)
def test_estimate_starts_from_the_measurements(model, ratio):
    assert ENGINE.estimate_seconds(make_job(model=model)) == pytest.approx(240 * ratio)


def test_estimate_follows_what_changes_the_work():
    base = ENGINE.estimate_seconds(make_job())
    assert ENGINE.estimate_seconds(make_job(options={"shifts": 3})) == pytest.approx(3 * base)
    assert ENGINE.estimate_seconds(make_job(options={"shifts": 0})) == pytest.approx(base)
    assert ENGINE.estimate_seconds(make_job(options={"overlap": 0.5})) == pytest.approx(1.5 * base)
    assert ENGINE.estimate_seconds(make_job(duration=24.0)) == pytest.approx(base / 10)
    assert ENGINE.estimate_seconds(make_job(options={"segment": 5})) == pytest.approx(1.56 * base)


def test_more_jobs_are_expected_to_use_more_memory():
    one = ENGINE.estimate_peak_mb(make_job())
    assert ENGINE.estimate_peak_mb(make_job(options={"jobs": 2})) == pytest.approx(1.4 * one)


@pytest.mark.parametrize(
    ("model", "seconds", "measured_mb"),
    [
        ("htdemucs", 243.9, 1800),
        ("htdemucs", 30.0, 1200),
        ("htdemucs_6s", 243.9, 2000),
        ("htdemucs_6s", 30.0, 1400),
        ("htdemucs_ft", 30.0, 1800),
    ],
)
def test_memory_estimate_stays_near_the_measurements(model, seconds, measured_mb):
    job = make_job(model=model, source_duration=seconds)
    assert ENGINE.estimate_peak_mb(job) == pytest.approx(measured_mb, rel=0.05)


def test_disk_estimate_counts_what_exists_only_during_the_work():
    plain = ENGINE.required_disk_bytes(make_job(codec="mp3"))
    assert ENGINE.required_disk_bytes(make_job(codec="ogg")) > plain * 2  # the WAV to convert from
    assert ENGINE.required_disk_bytes(make_job(codec="mp3", offset=1.0)) > plain
    wav = ENGINE.required_disk_bytes(make_job())
    assert ENGINE.required_disk_bytes(make_job(options={"depth": "float"})) == pytest.approx(
        2 * wav
    )


def test_declared_options_are_the_ones_in_the_plan():
    assert {spec.key for spec in OPTIONS} == {
        "shifts", "overlap", "segment", "two_stems", "clip_mode", "depth", "jobs",
    }  # fmt: skip


def test_mdx_extra_stays_out_until_measured():
    assert "mdx_extra" not in {model.id for model in MODELS}
