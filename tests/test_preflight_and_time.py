import pytest

from spleeter_gui.core.preflight import available_memory_mb, free_bytes, required_bytes
from spleeter_gui.core.timecode import format_time, parse_time


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", None),
        ("   ", None),
        ("90", 90.0),
        ("12,5", 12.5),
        ("12.5", 12.5),
        ("1:30", 90.0),
        ("1:02:03", 3723.0),
        ("0", 0.0),
    ],
)
def test_parse_time(text, expected):
    assert parse_time(text) == expected


@pytest.mark.parametrize("text", ["abc", "1:x", "1:2:3:4", "-5", "nan", "inf", "1:"])
def test_parse_time_rejects_nonsense(text):
    with pytest.raises(ValueError, match="Tempo inválido"):
        parse_time(text)


@pytest.mark.parametrize(
    ("seconds", "expected"), [(0, "00:00"), (65.9, "01:05"), (3723, "1:02:03")]
)
def test_format_time(seconds, expected):
    assert format_time(seconds) == expected


def test_lossless_output_needs_more_space_than_compressed():
    wav = required_bytes(600, 5, "wav", "320k")
    mp3 = required_bytes(600, 5, "mp3", "320k")
    assert wav > mp3 > required_bytes(600, 5, "mp3", "128k")
    assert required_bytes(600, 5, "wav", "128k") > required_bytes(600, 2, "wav", "128k")


def test_estimate_covers_a_measured_output():
    # 243.9 s in 2 stems wrote two WAV files of 43,024,970 bytes.
    assert required_bytes(243.9, 2, "wav", "128k") > 2 * 43_024_970


def test_free_space_looks_at_the_nearest_existing_folder(tmp_path):
    assert free_bytes(tmp_path / "ainda" / "não" / "existe") > 0


def test_available_memory_is_read_in_mb(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 16000000 kB\nMemAvailable:    2048000 kB\n")
    assert available_memory_mb(meminfo) == 2000.0


def test_unknown_memory_is_none(tmp_path):
    assert available_memory_mb(tmp_path / "nada") is None
