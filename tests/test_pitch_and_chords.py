import numpy as np
import pytest

from spleeter_gui.core.audio import CHANNELS, SAMPLE_RATE, decode, export_mix
from spleeter_gui.core.chords import Chord, ChordChart, analyze_chords
from spleeter_gui.core.pitch import PitchShifter
from spleeter_gui.core.player import SHIFTED_LATENCY_SECONDS
from tests.test_audio import make_player, write_wav

C, E, F, G, A, B_FLAT = 0, 4, 5, 7, 9, 10
_MIDI_C3 = 48


def tone(hz: float, seconds: float, level: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    wave = (level * np.sin(2 * np.pi * hz * t)).astype(np.float32)
    return np.stack([wave, wave], axis=1)


def dominant_hz(samples: np.ndarray) -> float:
    mono = samples[:, 0] * np.hanning(len(samples))
    spectrum = np.abs(np.fft.rfft(mono))
    return float(np.fft.rfftfreq(len(mono), 1 / SAMPLE_RATE)[spectrum.argmax()])


def shifted(samples: np.ndarray, semitones: int, frames: int, block: int = 1000) -> np.ndarray:
    shifter = PitchShifter(semitones, SAMPLE_RATE, CHANNELS)
    position = 0

    def feed(target: np.ndarray) -> None:
        nonlocal position
        chunk = samples[position : position + len(target)]
        target.fill(0.0)
        target[: len(chunk)] = chunk
        position += len(target)

    out = np.empty((frames, CHANNELS), dtype=np.float32)
    for start in range(0, frames, block):
        shifter.read(out[start : start + block], feed)
    return out


@pytest.mark.parametrize("semitones", [12, 2, -5])
def test_shifter_moves_the_pitch(semitones):
    out = shifted(tone(440.0, 3.0), semitones, SAMPLE_RATE * 2)
    assert dominant_hz(out[SAMPLE_RATE // 2 :]) == pytest.approx(
        440.0 * 2 ** (semitones / 12), rel=0.01
    )


def test_shifter_output_lines_up_with_its_input():
    onset = SAMPLE_RATE
    samples = np.concatenate([np.zeros((onset, 2), dtype=np.float32), tone(440.0, 1.0)])
    out = shifted(samples, 3, onset + SAMPLE_RATE // 2)
    heard = np.flatnonzero(np.abs(out[:, 0]) > 0.1)[0]
    assert abs(heard - onset) < SAMPLE_RATE * 0.03


def test_shifter_reports_what_it_has_not_given_back():
    shifter = PitchShifter(2, SAMPLE_RATE, CHANNELS, input_frames=2048)
    out = np.empty((2048, CHANNELS), dtype=np.float32)
    shifter.read(out, lambda block: block.fill(0.0))
    assert shifter.delay >= 0
    shifter.reset()
    assert shifter.delay == 0


def test_player_is_untouched_until_the_key_changes():
    samples = tone(440.0, 1.0)
    player, streams, _ = make_player([samples])
    player.play()
    streams[0].pump()
    block = streams[0].pump()
    assert np.array_equal(block, samples[len(block) : 2 * len(block)])
    assert player._shifter is None


def test_player_plays_in_the_new_key_and_returns_without_skipping():
    player, streams, _ = make_player([tone(440.0, 6.0)])
    player.play()
    streams[0].pump()
    before = player.position

    player.set_semitones(12)
    streams[0].pump()  # fades out and stops, to start again with a larger buffer
    player.stream_ended()
    assert player.position == before + 2048
    assert player.is_playing
    assert len(streams) == 2
    assert streams[1].kwargs["latency"] == SHIFTED_LATENCY_SECONDS
    blocks = [streams[1].pump() for _ in range(40)]
    assert dominant_hz(np.concatenate(blocks[10:])) == pytest.approx(880.0, rel=0.01)
    assert player.heard_position < player.position

    player.set_semitones(0)
    streams[1].pump()
    player.stream_ended()
    assert streams[2].kwargs["latency"] == "high"
    position = player.position
    block = streams[2].pump()
    assert player.position == position + len(block)
    assert dominant_hz(np.concatenate([streams[2].pump() for _ in range(10)])) == pytest.approx(
        440.0, rel=0.01
    )


def test_changing_the_key_while_pausing_does_not_resume():
    player, streams, _ = make_player([tone(440.0, 2.0)])
    player.play()
    streams[0].pump()
    player.pause()
    player.set_semitones(3)
    streams[0].pump()
    player.stream_ended()
    assert not player.is_playing
    assert len(streams) == 1


def test_key_set_before_playing_needs_no_restart():
    player, streams, _ = make_player([tone(440.0, 2.0)])
    player.set_semitones(3)
    player.play()
    assert streams[0].kwargs["latency"] == SHIFTED_LATENCY_SECONDS
    streams[0].pump()
    assert streams[0].active


def test_changing_between_two_keys_fades_without_a_bump():
    player, streams, _ = make_player([tone(440.0, 6.0)])
    player.set_semitones(2)
    player.play()
    for _ in range(5):
        streams[0].pump()
    heard = player.heard_position
    player.set_semitones(12)
    blocks = [streams[0].pump() for _ in range(40)]
    assert max(np.abs(block).max() for block in blocks) < 0.34
    assert dominant_hz(np.concatenate(blocks[20:])) == pytest.approx(880.0, rel=0.01)
    assert player.heard_position >= heard


def test_seek_in_another_key_goes_where_it_was_asked():
    player, streams, _ = make_player([tone(440.0, 6.0)])
    player.set_semitones(5)
    player.play()
    streams[0].pump()
    player.seek(SAMPLE_RATE * 3)
    streams[0].pump()
    assert player.position == SAMPLE_RATE * 3
    assert player._shifter.delay == 0


def test_pause_in_another_key_resumes_from_what_was_heard():
    player, streams, _ = make_player([tone(440.0, 6.0)])
    player.set_semitones(5)
    player.play()
    for _ in range(10):
        streams[0].pump()
    heard = 10 * 2048
    player.pause()
    streams[0].pump()
    player.stream_ended()
    assert player.position == heard + 2048


def test_song_in_another_key_ends_and_keeps_its_last_frames():
    samples = tone(440.0, 1.0)
    player, streams, _ = make_player([samples])
    player.set_semitones(2)
    player.play()
    blocks = []
    while streams[0].active:
        blocks.append(streams[0].pump())
    assert player.position == player.frames
    assert sum(map(len, blocks)) >= len(samples)
    last_sound = np.flatnonzero(np.abs(np.concatenate(blocks)[:, 0]) > 0.05)[-1]
    assert last_sound > len(samples) - SAMPLE_RATE * 0.1


def test_export_in_another_key(tmp_path):
    samples = tone(440.0, 3.0)
    path = tmp_path / "mistura.wav"
    export_mix([samples], [1.0], path, semitones=12)
    out = decode(path, 3.0)
    assert len(out) == len(samples)
    assert dominant_hz(out[SAMPLE_RATE:]) == pytest.approx(880.0, rel=0.01)


def test_export_in_another_key_does_not_distort(tmp_path):
    path = tmp_path / "alto.wav"
    result = export_mix([tone(440.0, 2.0, level=0.9)] * 2, [1.0, 1.0], path, semitones=3)
    assert result.scale < 1.0
    assert np.abs(decode(path, 2.0)).max() <= 1.0


def note_hz(pitch: int, octave: int) -> float:
    return 440.0 * 2 ** ((_MIDI_C3 + 12 * octave + pitch - 69) / 12)


def triad(root: int, minor: bool, seconds: float) -> np.ndarray:
    third = 3 if minor else 4
    return sum(tone(note_hz(root + interval, 1), seconds, 0.2) for interval in (0, third, 7, 12))


def bass_note(root: int, seconds: float) -> np.ndarray:
    return tone(note_hz(root, -1), seconds, 0.3)


PROGRESSION = ((C, False), (A, True), (F, False), (G, False))


def song(seconds_each: float = 3.0) -> tuple[np.ndarray, np.ndarray]:
    harmony = np.concatenate([triad(r, m, seconds_each) for r, m in PROGRESSION])
    bass = np.concatenate([bass_note(r, seconds_each) for r, _ in PROGRESSION])
    return harmony, bass


def named(chart: ChordChart) -> list[str]:
    return [chart.name(chord) for chord in chart.chords]


def test_finds_a_known_progression_and_when_it_changes():
    harmony, bass = song()
    chart = analyze_chords([("other", harmony), ("bass", bass)], SAMPLE_RATE)
    assert named(chart) == ["C", "Am", "F", "G"]
    assert chart.tonic == C
    for chord, expected in zip(chart.chords[1:], (3.0, 6.0, 9.0), strict=True):
        assert chord.start == pytest.approx(expected, abs=0.4)


def test_vocals_and_drums_do_not_change_the_answer():
    harmony, bass = song()
    rng = np.random.default_rng(1)
    drums = (rng.standard_normal(harmony.shape) * 0.5).astype(np.float32)
    vocals = tone(note_hz(B_FLAT, 1), len(harmony) / SAMPLE_RATE, 0.5)
    chart = analyze_chords(
        [("vocals", vocals), ("drums", drums), ("other", harmony), ("bass", bass)], SAMPLE_RATE
    )
    assert named(chart) == ["C", "Am", "F", "G"]


def test_works_without_a_bass_stem():
    harmony, bass = song()
    chart = analyze_chords(
        [("vocals", harmony * 0), ("accompaniment", harmony + bass)], SAMPLE_RATE
    )
    assert named(chart) == ["C", "Am", "F", "G"]


def test_a_vocals_only_folder_still_gets_chords_but_drums_never_do():
    harmony, _ = song()
    assert named(analyze_chords([("vocals", harmony)], SAMPLE_RATE))
    assert analyze_chords([("drums", harmony)], SAMPLE_RATE).chords == ()


def test_silence_and_very_short_audio_have_no_chords():
    silence = np.zeros((SAMPLE_RATE, 2), dtype=np.float32)
    assert analyze_chords([("other", silence)], SAMPLE_RATE).chords == ()
    assert analyze_chords([("other", silence[:100])], SAMPLE_RATE).chords == ()
    assert analyze_chords([], SAMPLE_RATE).chords == ()


def test_a_silent_passage_shows_no_chord():
    harmony = triad(C, False, 3.0)
    gap = np.zeros((SAMPLE_RATE * 3, 2), dtype=np.float32)
    chart = analyze_chords([("other", np.concatenate([harmony, gap, harmony]))], SAMPLE_RATE)
    assert [chart.name(c) for c in chart.chords] == ["C", "—", "C"]


@pytest.mark.parametrize(
    ("root", "minor", "tonic", "semitones", "expected"),
    [
        (B_FLAT, False, F, 0, "Bb"),  # flats in F major
        (10, False, 11, 0, "A#"),  # sharps in B major
        (C, False, C, 2, "D"),
        (A, True, C, 2, "Bm"),
        (C, False, C, -1, "B"),
        (C, False, C, 1, "Db"),  # C becomes Db major
    ],
)
def test_names_follow_the_key(root, minor, tonic, semitones, expected):
    chart = ChordChart((Chord(0.0, root, minor),), tonic)
    assert chart.name(chart.chords[0], semitones) == expected


def test_chart_says_what_plays_and_what_comes_next():
    first, second = Chord(0.0, C), Chord(4.0, G)
    chart = ChordChart((first, second), C)
    assert chart.at(0.0) == (first, second)
    assert chart.at(3.9) == (first, second)
    assert chart.at(4.0) == (second, None)
    assert chart.at(1000.0) == (second, None)
    assert ChordChart((), 0).at(1.0) == (None, None)


def test_saved_song_analysis_runs_on_real_files(tmp_path):
    harmony, _ = song(1.5)
    path = write_wav(tmp_path / "other.wav", harmony)
    chart = analyze_chords([("other", decode(path, 6.0))], SAMPLE_RATE)
    assert named(chart) == ["C", "Am", "F", "G"]
