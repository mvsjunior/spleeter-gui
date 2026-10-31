import subprocess
from pathlib import Path

import numpy as np
import pytest
import sounddevice as sd

from spleeter_gui.core import audio
from spleeter_gui.core.audio import (
    SAMPLE_RATE,
    AudioError,
    ExportCancelled,
    decode,
    effective_gains,
    export_mix,
    find_stems,
    memory_needed_bytes,
    render,
    waveform_peaks,
)
from spleeter_gui.core.player import BLOCK_FRAMES, Player
from spleeter_gui.core.probe import probe


def noise(seconds: float = 1.0, level: float = 0.2, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    frames = int(seconds * SAMPLE_RATE)
    return (rng.uniform(-level, level, (frames, 2))).astype(np.float32)


def write_wav(path: Path, samples: np.ndarray) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SAMPLE_RATE), "-ac", "2",
            "-i", "pipe:0", "-c:a", "pcm_f32le", str(path),
        ],
        input=samples.tobytes(),
        check=True,
    )  # fmt: skip
    return path


class FakeStream:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.callback = kwargs["callback"]
        self.finished_callback = kwargs["finished_callback"]
        self.active = False
        self.closed = False
        self.latency = 0.05

    def start(self) -> None:
        self.active = True

    def close(self) -> None:
        self.closed = True
        self.active = False

    def pump(self, status: sd.CallbackFlags | None = None) -> np.ndarray:
        out = np.empty((BLOCK_FRAMES, 2), dtype=np.float32)
        try:
            self.callback(out, BLOCK_FRAMES, None, status or sd.CallbackFlags())
        except sd.CallbackStop:
            self.active = False
            self.finished_callback()
        return out


def make_player(stems, gains=None) -> tuple[Player, list[FakeStream], list[int]]:
    created: list[FakeStream] = []
    finished: list[int] = []

    def factory(**kwargs):
        created.append(FakeStream(**kwargs))
        return created[-1]

    gains = [1.0] * len(stems) if gains is None else gains
    return Player(stems, gains, lambda: finished.append(1), factory), created, finished


def test_decodes_stereo_float_at_the_project_rate(tmp_path, make_tone):
    samples = decode(make_tone("tom é ção.mp3", seconds=2), 2.0)
    assert samples.dtype == np.float32
    assert samples.shape[1] == 2
    assert len(samples) == pytest.approx(2 * SAMPLE_RATE, abs=SAMPLE_RATE * 0.1)
    assert np.abs(samples).max() > 0.01


def test_mono_and_other_rates_are_converted(tmp_path):
    path = tmp_path / "mono.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=440:sample_rate=22050",
         "-t", "1", "-ac", "1", str(path)],
        check=True,
    )  # fmt: skip
    samples = decode(path, 1.0)
    assert samples.shape == (SAMPLE_RATE, 2)
    assert np.array_equal(samples[:, 0], samples[:, 1])


def test_decode_keeps_the_exact_samples(tmp_path):
    original = noise()
    assert np.array_equal(decode(write_wav(tmp_path / "a.wav", original), 1.0), original)


def test_decode_failure_says_which_file(tmp_path):
    path = tmp_path / "ruim.mp3"
    path.write_text("não é áudio")
    with pytest.raises(AudioError, match="ruim.mp3"):
        decode(path, 1.0)


def test_memory_matches_the_plan_reference():
    assert memory_needed_bytes([240.0] * 5) / 1e6 == pytest.approx(423, abs=2)


def touch(folder: Path, *names: str) -> None:
    for name in names:
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()


def test_stems_come_in_a_familiar_order_and_other_files_are_ignored(tmp_path):
    touch(tmp_path, "other.mp3", "bass.mp3", "vocals.mp3", "drums.mp3", "capa.jpg", "notas.txt")
    assert list(find_stems(tmp_path)) == ["vocals", "drums", "bass", "other"]


def test_custom_name_formats_are_found(tmp_path):
    touch(tmp_path, "música/música_vocals.wav", "música/música_other.wav")
    assert set(find_stems(tmp_path)) == {"música_vocals", "música_other"}


def test_same_name_twice_does_not_lose_a_file(tmp_path):
    touch(tmp_path, "vocals.mp3", "vocals.wav")
    assert len(find_stems(tmp_path)) == 2


def test_folder_without_audio_is_explained(tmp_path):
    with pytest.raises(AudioError, match="Nenhum arquivo"):
        find_stems(tmp_path)


def test_folder_with_too_many_files_is_not_loaded(tmp_path):
    touch(tmp_path, *[f"{n}.mp3" for n in range(audio.MAX_STEMS + 1)])
    with pytest.raises(AudioError, match="não parece"):
        find_stems(tmp_path)


@pytest.mark.parametrize(
    ("muted", "soloed", "expected"),
    [
        ([False, False, False], [False, False, False], [1.0, 0.5, 0.25]),
        ([False, True, False], [False, False, False], [1.0, 0.0, 0.25]),
        ([False, False, False], [False, True, False], [0.0, 0.5, 0.0]),
        ([False, False, False], [True, False, True], [1.0, 0.0, 0.25]),
        ([True, False, False], [True, True, False], [0.0, 0.5, 0.0]),
    ],
)
def test_gains_from_volume_mute_and_solo(muted, soloed, expected):
    assert effective_gains([1.0, 0.5, 0.25], muted, soloed) == expected


def test_render_sums_the_stems_at_their_gains():
    a, b = noise(seed=1), noise(seed=2)
    out = np.empty((100, 2), dtype=np.float32)
    render(out, [a, b], 50, [1.0, 0.5], [1.0, 0.5])
    np.testing.assert_allclose(out, a[50:150] + b[50:150] * 0.5, atol=1e-6)


def test_render_ramps_when_the_gain_changes():
    one = np.ones((100, 2), dtype=np.float32)
    out = np.empty((100, 2), dtype=np.float32)
    render(out, [one], 0, [0.0], [1.0])
    assert out[0, 0] == 0.0
    assert out[-1, 0] == 1.0
    assert (np.diff(out[:, 0]) > 0).all()


def test_render_past_a_shorter_stem_leaves_silence():
    short = np.ones((10, 2), dtype=np.float32)
    out = np.empty((30, 2), dtype=np.float32)
    render(out, [short], 5, [1.0], [1.0])
    assert out[:5].sum() == 10.0
    assert out[5:].sum() == 0.0


def test_peaks_find_the_loudest_sample_of_each_part():
    samples = np.zeros((1000, 2), dtype=np.float32)
    samples[10] = [-0.8, 0.1]
    samples[950] = [0.1, 0.4]
    peaks = waveform_peaks(samples, 10)
    assert peaks[0] == pytest.approx(0.8)
    assert peaks[9] == pytest.approx(0.4)
    assert peaks[1:9].sum() == 0.0


def test_peaks_of_something_shorter_than_the_buckets():
    assert len(waveform_peaks(np.ones((3, 2), dtype=np.float32), 10)) == 10


def test_export_sounds_like_the_mix(tmp_path):
    a, b = noise(1.5, seed=1), noise(1.0, seed=2)
    result = export_mix([a, b], [1.0, 0.5], tmp_path / "mix é.wav")
    exported = decode(result.path, 2.0)
    expected = a.copy()
    expected[: len(b)] += b * 0.5
    assert len(exported) == len(a)
    np.testing.assert_allclose(exported, expected, atol=1 / 16000)
    assert result.scale == 1.0


def test_export_normalizes_only_when_it_would_clip(tmp_path):
    loud = noise(level=0.9)
    result = export_mix([loud, loud], [1.0, 1.0], tmp_path / "alto.wav")
    assert result.peak == pytest.approx(1.8, abs=0.01)
    assert result.scale == pytest.approx(0.999 / result.peak)
    exported = decode(result.path, 1.0)
    assert np.abs(exported).max() == pytest.approx(0.999, abs=0.001)
    # scaled, not clipped: the shape of the wave is intact
    np.testing.assert_allclose(exported, (loud + loud) * result.scale, atol=1 / 16000)


@pytest.mark.parametrize("suffix", sorted(audio.EXPORT_ENCODERS))
def test_every_export_format_gives_a_readable_file(tmp_path, suffix):
    result = export_mix([noise(2.0)], [1.0], tmp_path / f"mix{suffix}")
    assert probe(result.path).duration == pytest.approx(2.0, abs=0.1)


def test_unknown_format_is_refused(tmp_path):
    with pytest.raises(AudioError, match="não suportado"):
        export_mix([noise()], [1.0], tmp_path / "mix.xyz")


def test_failed_export_leaves_nothing(tmp_path):
    with pytest.raises(AudioError):
        export_mix([noise()], [1.0], tmp_path / "não existe" / "mix.wav")
    assert list(tmp_path.iterdir()) == []


def test_export_does_not_touch_an_existing_file_until_it_is_ready(tmp_path):
    target = tmp_path / "mix.wav"
    target.write_text("original")
    calls = []

    def stop_during_the_writing_pass() -> bool:
        calls.append(1)
        return len(calls) > 1  # the peak pass has one block for a second of audio

    with pytest.raises(ExportCancelled):
        export_mix([noise()], [1.0], target, should_stop=stop_during_the_writing_pass)
    assert target.read_text() == "original"
    assert [path.name for path in tmp_path.iterdir()] == ["mix.wav"]


def test_export_reports_progress_up_to_the_end(tmp_path):
    values: list[float] = []
    export_mix([noise(3.0)], [1.0], tmp_path / "mix.wav", on_progress=values.append)
    assert values == sorted(values)
    assert 0.0 <= values[0] and values[-1] < 1.0


def test_playback_sounds_like_the_export(tmp_path):
    stems = [noise(1.0, seed=1), noise(1.0, seed=2)]
    gains = [0.8, 0.3]
    player, created, _ = make_player(stems, gains)
    player.play()
    blocks = []
    while created[0].active:
        blocks.append(created[0].pump())
    played = np.concatenate(blocks)[: len(stems[0])]
    exported = decode(export_mix(stems, gains, tmp_path / "mix.wav").path, 1.0)
    # the first block fades in by design
    np.testing.assert_allclose(played[BLOCK_FRAMES:], exported[BLOCK_FRAMES:], atol=1 / 16000)


def test_stems_stay_in_sync_through_changes():
    stem = np.tile(np.arange(BLOCK_FRAMES * 4, dtype=np.float32)[:, None], (1, 2)) / 1e5
    player, created, _ = make_player([stem, stem], [0.5, 0.5])
    player.play()
    created[0].pump()
    player.set_gains([1.0, 0.0])  # same total: only a loss of sync would show
    out = created[0].pump()
    np.testing.assert_allclose(out, stem[BLOCK_FRAMES : BLOCK_FRAMES * 2], atol=1e-5)


def test_gain_change_has_no_jump():
    level = np.full((BLOCK_FRAMES * 4, 2), 0.5, dtype=np.float32)
    player, created, _ = make_player([level])
    player.play()
    created[0].pump()
    created[0].pump()
    player.set_gains([0.0])
    out = created[0].pump()
    assert out[0, 0] == pytest.approx(0.5)
    assert out[-1, 0] == pytest.approx(0.0)
    assert np.abs(np.diff(out[:, 0])).max() < 0.001
    assert (created[0].pump() == 0.0).all()


def test_pause_fades_out_keeps_the_position_and_resumes():
    level = np.full((BLOCK_FRAMES * 10, 2), 0.5, dtype=np.float32)
    player, created, finished = make_player([level])
    player.play()
    created[0].pump()
    player.pause()
    assert not player.is_playing
    out = created[0].pump()
    assert out[-1, 0] == pytest.approx(0.0)
    assert finished == [1]
    player.stream_ended()
    assert created[0].closed
    assert player.position == BLOCK_FRAMES * 2

    player.play()
    assert len(created) == 2
    out = created[1].pump()
    assert out[0, 0] == pytest.approx(0.0)  # fades back in
    assert player.position == BLOCK_FRAMES * 3


def test_play_during_the_pause_fade_goes_on_playing():
    level = np.full((BLOCK_FRAMES * 10, 2), 0.5, dtype=np.float32)
    player, created, _ = make_player([level])
    player.play()
    created[0].pump()
    player.pause()
    player.play()  # before the callback saw the pause
    assert created[0].pump()[-1, 0] == pytest.approx(0.5)
    assert created[0].active

    player.pause()
    created[0].pump()
    player.play()  # after the stream ended, before the interface heard of it
    player.stream_ended()
    assert len(created) == 2
    assert player.is_playing


def test_seek_while_playing_fades_and_jumps():
    ramp = np.tile(np.arange(BLOCK_FRAMES * 10, dtype=np.float32)[:, None], (1, 2)) / 1e5
    player, created, _ = make_player([ramp])
    player.play()
    created[0].pump()
    player.seek(BLOCK_FRAMES * 5)
    faded = created[0].pump()
    assert faded[-1, 0] == pytest.approx(0.0)
    assert player.position == BLOCK_FRAMES * 5
    out = created[0].pump()
    assert out[-1, 0] == pytest.approx(ramp[BLOCK_FRAMES * 6 - 1, 0], abs=1e-4)


def test_seek_while_stopped_and_limits():
    player, _, _ = make_player([noise()])
    player.seek(1000)
    assert player.position == 1000
    player.seek(-5)
    assert player.position == 0
    player.seek(10**9)
    assert player.position == player.frames


def test_end_of_song_stops_and_next_play_starts_over():
    stem = noise(0.1)
    player, created, finished = make_player([stem])
    player.play()
    while created[0].active:
        created[0].pump()
    assert finished == [1]
    assert player.position == player.frames
    player.stream_ended()
    assert not player.is_playing
    player.play()
    assert player.position == 0


def test_output_never_goes_past_full_scale():
    loud = np.full((BLOCK_FRAMES * 3, 2), 0.9, dtype=np.float32)
    player, created, _ = make_player([loud, loud])
    player.play()
    created[0].pump()
    assert created[0].pump().max() == 1.0


def test_underruns_are_counted():
    player, created, _ = make_player([noise()])
    player.play()
    created[0].pump()
    flags = sd.CallbackFlags()
    flags.output_underflow = True
    created[0].pump(flags)
    assert player.underruns == 1


def test_stream_asks_for_what_the_samples_are():
    player, created, _ = make_player([noise()])
    player.play()
    args = created[0].kwargs
    assert args["samplerate"] == SAMPLE_RATE
    assert args["channels"] == 2
    assert args["dtype"] == "float32"
