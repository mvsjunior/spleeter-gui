import subprocess

import pytest

from spleeter_gui.core.probe import ProbeError, probe


@pytest.fixture
def make_audio(tmp_path):
    def make(name: str, seconds: float = 1.0):
        path = tmp_path / name
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-f", "lavfi",
                "-i", "sine=frequency=440:sample_rate=44100",
                "-t", str(seconds), "-metadata", "title=Canção", str(path),
            ],
            check=True,
        )  # fmt: skip
        return path

    return make


def test_reads_duration_and_metadata(make_audio):
    info = probe(make_audio("tone.flac", 2.0))
    assert info.duration == pytest.approx(2.0, abs=0.05)
    assert info.codec == "flac"
    assert info.sample_rate == 44100
    assert info.channels == 1
    assert "Canção" in info.tags.values()


@pytest.mark.parametrize("name", ["com espaço é ção.wav", "aspas \"e\" 'mais'.wav", "-traço.wav"])
def test_accepts_difficult_names(make_audio, tmp_path, name):
    path = make_audio("source.wav")
    target = tmp_path / name
    path.rename(target)
    assert probe(target).codec == "pcm_s16le"


def test_missing_file_has_a_clear_error(tmp_path):
    with pytest.raises(ProbeError, match="não encontrado"):
        probe(tmp_path / "nada.mp3")


def test_invalid_file_has_a_clear_error(tmp_path):
    path = tmp_path / "texto.mp3"
    path.write_text("isto não é áudio")
    with pytest.raises(ProbeError, match="texto.mp3"):
        probe(path)


def test_directory_is_rejected(tmp_path):
    with pytest.raises(ProbeError):
        probe(tmp_path)


def test_file_without_audio_has_a_clear_error(tmp_path):
    path = tmp_path / "video.mkv"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=16x16:d=1",
            "-c:v", "ffv1", str(path),
        ],
        check=True,
    )  # fmt: skip
    with pytest.raises(ProbeError, match="não tem faixa de áudio"):
        probe(path)


def test_missing_ffprobe_has_a_clear_error(make_audio, monkeypatch):
    path = make_audio("tone.wav")
    monkeypatch.setenv("PATH", "")
    with pytest.raises(ProbeError, match="ffprobe não está instalado"):
        probe(path)
