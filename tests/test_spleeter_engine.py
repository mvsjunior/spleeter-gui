from itertools import product
from pathlib import Path

import pytest

from spleeter_gui.core.models import Job
from spleeter_gui.engines import ENGINES
from spleeter_gui.engines.spleeter import CODECS, LOSSLESS_CODECS, MODELS, SpleeterEngine

ENGINE = SpleeterEngine(Path("/env"), Path("/models"))
WORKDIR = Path("/out/tmp")


def make_job(**changes) -> Job:
    fields = {
        "input_path": Path("/music/song.mp3"),
        "output_dir": Path("/out"),
        "engine_id": "spleeter",
        "model": "2stems",
        "source_duration": 240.0,
    }
    return Job(**{**fields, **changes})


def argv_for(**changes) -> list[str]:
    return ENGINE.build_command(make_job(**changes), WORKDIR).argv


def value_of(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


@pytest.mark.parametrize(
    ("total", "offset", "expected"),
    [
        (240.0, 0.0, 240.0),
        (731.703923, 0.0, 731.703923),  # past the 600 s the CLI would cut at
        (731.703923, 100.5, 631.203923),
        (5000.0, 4999.0, 1.0),
    ],
)
def test_duration_is_always_total_minus_offset(total, offset, expected):
    argv = argv_for(source_duration=total, offset=offset)
    assert float(value_of(argv, "-d")) == pytest.approx(expected)


def test_chosen_duration_is_sent_when_it_fits():
    assert float(value_of(argv_for(duration=30.0), "-d")) == 30.0


def test_chosen_duration_is_capped_at_the_end_of_the_file():
    argv = argv_for(source_duration=100.0, offset=80.0, duration=50.0)
    assert float(value_of(argv, "-d")) == 20.0


def test_offset_is_sent_only_when_set():
    assert "-s" not in argv_for()
    assert float(value_of(argv_for(offset=12.5), "-s")) == 12.5


@pytest.mark.parametrize(
    "changes",
    [
        {"offset": 240.0},
        {"offset": 300.0},
        {"offset": -1.0},
        {"duration": 0.0},
        {"duration": -5.0},
        {"source_duration": 0.0},
        {"model": "3stems"},
        {"codec": "aiff"},
    ],
)
def test_invalid_jobs_are_rejected(changes):
    with pytest.raises(ValueError):
        argv_for(**changes)


@pytest.mark.parametrize(
    "name",
    [
        "Nada é Impossível - Quatro Por Um.mp3",
        "com espaços  duplos.wav",
        "aspas \"duplas\" e 'simples'.flac",
        "$(rm -rf) ; & | *.mp3",
        "-começa com traço.mp3",
    ],
)
def test_names_reach_the_engine_as_one_intact_argument(name):
    path = Path("/música com espaço") / name
    argv = argv_for(input_path=path)
    assert argv[-1] == str(path)
    assert argv.count(str(path)) == 1


def test_relative_name_starting_with_a_dash_cannot_become_an_option():
    assert Path(argv_for(input_path=Path("-x.mp3"))[-1]).is_absolute()


@pytest.mark.parametrize(
    ("model", "codec", "bitrate", "mwf"),
    list(product([m.id for m in MODELS], CODECS, ["128k", "320k"], [False, True])),
)
def test_all_option_combinations(model, codec, bitrate, mwf):
    argv = argv_for(model=model, codec=codec, bitrate=bitrate, options={"mwf": mwf})
    assert argv[:2] == ["/env/bin/spleeter", "separate"]
    assert value_of(argv, "-p") == f"spleeter:{model}"
    assert value_of(argv, "-c") == codec
    assert value_of(argv, "-o") == str(WORKDIR)
    assert ("--mwf" in argv) is mwf
    if codec in LOSSLESS_CODECS:
        assert "-b" not in argv
    else:
        assert value_of(argv, "-b") == bitrate


def test_filename_format_defaults_to_one_flat_file_per_stem():
    assert value_of(argv_for(), "-f") == "{instrument}.{codec}"
    custom = argv_for(options={"filename_format": "{filename}_{instrument}.{codec}"})
    assert value_of(custom, "-f") == "{filename}_{instrument}.{codec}"


@pytest.mark.parametrize(
    "bad",
    ["{nope}.{codec}", "{instrument", "{0}.{codec}", "fixo.{codec}", "/etc/{instrument}.wav",
     "../{instrument}.{codec}"],
)  # fmt: skip
def test_unsafe_or_invalid_filename_format_is_rejected(bad):
    with pytest.raises(ValueError):
        argv_for(options={"filename_format": bad})


def test_declared_options_are_all_understood():
    keys = {spec.key for spec in ENGINE.options()}
    assert keys == {"mwf", "verbose", "filename_format"}


def test_16khz_variants_share_the_downloaded_weights(tmp_path):
    engine = SpleeterEngine(tmp_path, tmp_path)
    assert not engine.is_model_cached("2stems-16kHz")
    (tmp_path / "2stems").mkdir()
    assert not engine.is_model_cached("2stems")  # an interrupted download has no probe file
    (tmp_path / "2stems" / ".probe").touch()
    assert engine.is_model_cached("2stems")
    assert engine.is_model_cached("2stems-16kHz")
    assert not engine.is_model_cached("4stems")


@pytest.mark.parametrize(
    ("model", "seconds", "mwf", "measured_mb"),
    [
        ("2stems", 731.7, False, 6876),
        ("5stems", 731.7, False, 11127),
        ("5stems", 243.9, True, 8156),
        ("4stems", 243.9, False, 4217),
    ],
)
def test_memory_estimate_stays_near_the_measurements(model, seconds, mwf, measured_mb):
    job = make_job(model=model, source_duration=seconds, options={"mwf": mwf})
    assert ENGINE.estimate_peak_mb(job) == pytest.approx(measured_mb, rel=0.05)


def test_memory_estimate_follows_the_chosen_section():
    whole = make_job(model="5stems", source_duration=700.0)
    section = make_job(model="5stems", source_duration=700.0, duration=30.0)
    assert ENGINE.estimate_peak_mb(section) < ENGINE.estimate_peak_mb(whole)


def test_verbose_is_opt_in():
    assert "--verbose" not in argv_for()
    assert "--verbose" in argv_for(options={"verbose": True})


def test_environment_points_to_models_and_limits_threads():
    env = ENGINE.build_command(make_job(), WORKDIR).env
    assert env["MODEL_PATH"] == "/models"
    assert env["OMP_NUM_THREADS"] == "2"
    assert env["TF_NUM_INTRAOP_THREADS"] == "2"


def test_build_command_does_not_touch_the_disk(tmp_path):
    missing = tmp_path / "does" / "not" / "exist"
    ENGINE.build_command(make_job(input_path=missing / "a.mp3"), missing)
    assert list(tmp_path.iterdir()) == []


def test_progress_is_never_invented():
    parse = ENGINE.progress_parser(make_job())
    assert parse("INFO:spleeter:File vocals.wav written succesfully") is None


def test_collect_outputs_maps_each_stem(tmp_path):
    for stem in ("vocals", "accompaniment"):
        (tmp_path / f"{stem}.mp3").touch()
    outputs = ENGINE.collect_outputs(make_job(codec="mp3"), tmp_path)
    assert outputs == {
        "vocals": tmp_path / "vocals.mp3",
        "accompaniment": tmp_path / "accompaniment.mp3",
    }


def test_collect_outputs_follows_a_custom_format(tmp_path):
    job = make_job(options={"filename_format": "{filename}/{instrument}.{codec}"})
    (tmp_path / "song").mkdir()
    (tmp_path / "song" / "vocals.wav").touch()
    (tmp_path / "song" / "accompaniment.wav").touch()
    assert ENGINE.collect_outputs(job, tmp_path)["vocals"] == tmp_path / "song" / "vocals.wav"


def test_missing_stem_is_an_error(tmp_path):
    (tmp_path / "vocals.wav").touch()
    with pytest.raises(FileNotFoundError, match="accompaniment"):
        ENGINE.collect_outputs(make_job(), tmp_path)


def test_is_installed_checks_the_executable(tmp_path):
    assert not SpleeterEngine(tmp_path, tmp_path).is_installed()
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "spleeter").touch()
    assert SpleeterEngine(tmp_path, tmp_path).is_installed()


def test_engine_is_registered():
    assert isinstance(ENGINES["spleeter"], SpleeterEngine)
