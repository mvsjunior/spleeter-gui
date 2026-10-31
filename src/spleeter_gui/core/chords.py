"""Finds the chords of a song from its stems. Fast enough to run whenever a song is loaded."""

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

# Vocals follow the melody and drums have no key, so they only get in the way.
_NOT_HARMONY = ("vocals", "drums")
_DECIMATION = 4
_FFT_FRAMES = 4096
_HOP_FRAMES = 1024
_CHUNK_WINDOWS = 256
_HARMONY_HZ = (110.0, 2000.0)
_BASS_HZ = (40.0, 220.0)
_SMOOTHING_WINDOWS = 9  # about a second
_BASS_WEIGHT = 0.3
_CHANGE_COST = 0.7
_IN_KEY_BONUS = 0.05
_SILENCE = 0.05
_OVERTONES = ((1, 1.0), (2, 0.6), (3, 0.4), (4, 0.25), (5, 0.15))
_SHARPS = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_FLATS = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")
_FLAT_KEYS = {1, 3, 5, 8, 10}
_MAJOR_THIRD = (0, 4, 7)
_MINOR_THIRD = (0, 3, 7)
_KEY_MAJORS = (0, 5, 7)
_KEY_MINORS = (2, 4, 9)


@dataclass(frozen=True)
class Chord:
    start: float  # seconds
    root: int | None  # pitch class, with C as 0; None where nothing plays
    minor: bool = False


@dataclass(frozen=True)
class ChordChart:
    chords: tuple[Chord, ...]
    tonic: int  # of the major key, or of the relative major of a minor key

    def at(self, seconds: float) -> tuple[Chord | None, Chord | None]:
        """The chord playing at that moment and the one after it."""
        index = bisect_right(self.chords, seconds, key=lambda chord: chord.start) - 1
        current = self.chords[index] if index >= 0 else None
        following = self.chords[index + 1] if index + 1 < len(self.chords) else None
        return current, following

    def name(self, chord: Chord | None, semitones: int = 0) -> str:
        """Spelled as the key calls for: Bb in F major, but A# in B major."""
        if chord is None or chord.root is None:
            return "—"
        names = _FLATS if (self.tonic + semitones) % 12 in _FLAT_KEYS else _SHARPS
        return names[(chord.root + semitones) % 12] + ("m" if chord.minor else "")


def analyze_chords(stems: Sequence[tuple[str, np.ndarray]], sample_rate: int) -> ChordChart:
    """Takes (name, samples) pairs. Reads the bass from its own stem when there is one."""
    harmony = [samples for name, samples in stems if name not in _NOT_HARMONY]
    if not harmony:
        harmony = [samples for name, samples in stems if name != "drums"]
    if not harmony:
        return ChordChart((), 0)
    bass = [samples for name, samples in stems if name == "bass"]

    rate = sample_rate / _DECIMATION
    mix = _mono(harmony)
    if len(mix) < _FFT_FRAMES:
        return ChordChart((), 0)
    treble = _smooth(_chroma(mix, rate, _HARMONY_HZ))
    low = _smooth(_chroma(_mono(bass) if bass else mix, rate, _BASS_HZ))

    # Compressed, so that one loud note does not outweigh the rest of the chord.
    loudness = np.percentile(treble, 95)
    if loudness <= 0.0:
        return ChordChart((), 0)
    treble = np.log1p(treble / loudness * 20)
    energy = treble.sum(axis=1)

    templates, roots, minors = _templates()
    scores = _normalized(treble) @ templates.T + _BASS_WEIGHT * _normalized(low)[:, roots]
    path = _best_path(scores)
    tonic = _find_tonic(path, roots, minors)
    # A second pass that knows the key removes most of the stray chords from outside of it.
    path = _best_path(scores + _IN_KEY_BONUS * _in_key(tonic, roots, minors))

    silent = energy < energy.max() * _SILENCE
    chords: list[Chord] = []
    for window, state in enumerate(path):
        root = None if silent[window] else int(roots[state])
        minor = root is not None and bool(minors[state])
        if not chords or (chords[-1].root, chords[-1].minor) != (root, minor):
            # Each window is heard at its middle.
            start = (window * _HOP_FRAMES + _FFT_FRAMES / 2) / rate if chords else 0.0
            chords.append(Chord(start, root, minor))
    return ChordChart(tuple(chords), tonic)


def _mono(stems: Sequence[np.ndarray]) -> np.ndarray:
    """The sum in mono, at a lower rate: the notes that define a chord are below 2 kHz."""
    frames = max(len(samples) for samples in stems) // _DECIMATION
    mix = np.zeros(frames, dtype=np.float32)
    for samples in stems:
        count = len(samples) // _DECIMATION
        mix[:count] += samples[: count * _DECIMATION].reshape(count, -1).mean(axis=1)
    return mix


def _chroma(mono: np.ndarray, rate: float, band: tuple[float, float]) -> np.ndarray:
    """How strong each of the 12 pitch classes is, in each window."""
    frequencies = np.fft.rfftfreq(_FFT_FRAMES, 1 / rate)
    bins = np.flatnonzero((frequencies >= band[0]) & (frequencies <= band[1]))
    pitches = 12 * np.log2(frequencies[bins] / 440.0) + 69
    nearest = np.rint(pitches)
    weights = np.zeros((12, len(bins)), dtype=np.float32)
    weights[nearest.astype(int) % 12, np.arange(len(bins))] = (
        np.cos((pitches - nearest) * np.pi) ** 2
    )

    window = np.hanning(_FFT_FRAMES).astype(np.float32)
    windows = (len(mono) - _FFT_FRAMES) // _HOP_FRAMES + 1
    chroma = np.empty((windows, 12), dtype=np.float32)
    for first in range(0, windows, _CHUNK_WINDOWS):
        count = min(_CHUNK_WINDOWS, windows - first)
        batch = np.stack(
            [mono[start : start + _FFT_FRAMES] for start in range(
                first * _HOP_FRAMES, (first + count) * _HOP_FRAMES, _HOP_FRAMES
            )]
        )  # fmt: skip
        magnitudes = np.abs(np.fft.rfft(batch * window, axis=1))[:, bins]
        chroma[first : first + count] = magnitudes @ weights.T
    return chroma


def _smooth(chroma: np.ndarray) -> np.ndarray:
    kernel = np.ones(_SMOOTHING_WINDOWS) / _SMOOTHING_WINDOWS
    return np.stack([np.convolve(chroma[:, pitch], kernel, "same") for pitch in range(12)], axis=1)


def _normalized(chroma: np.ndarray) -> np.ndarray:
    return chroma / (np.linalg.norm(chroma, axis=1, keepdims=True) + 1e-9)


def _templates() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The 24 major and minor triads, with the overtones each note also sounds."""

    def note(pitch: int) -> np.ndarray:
        values = np.zeros(12)
        for harmonic, gain in _OVERTONES:
            values[(pitch + round(12 * np.log2(harmonic))) % 12] += gain
        return values

    rows, roots, minors = [], [], []
    for root in range(12):
        for minor, shape in ((False, _MAJOR_THIRD), (True, _MINOR_THIRD)):
            values = sum(note(root + interval) for interval in shape)
            rows.append(values / np.linalg.norm(values))
            roots.append(root)
            minors.append(minor)
    return np.array(rows), np.array(roots), np.array(minors)


def _best_path(scores: np.ndarray) -> np.ndarray:
    """The best chord per window, where every change of chord costs the same (Viterbi)."""
    windows, states = scores.shape
    best = scores[0].copy()
    came_from = np.zeros((windows, states), dtype=np.int16)
    everyone = np.arange(states)
    for window in range(1, windows):
        top = int(best.argmax())
        changed = best[top] - _CHANGE_COST
        stays = best >= changed
        came_from[window] = np.where(stays, everyone, top)
        best = np.where(stays, best, changed) + scores[window]
    path = np.empty(windows, dtype=np.int64)
    path[-1] = best.argmax()
    for window in range(windows - 1, 0, -1):
        path[window - 1] = came_from[window, path[window]]
    return path


def _in_key(tonic: int, roots: np.ndarray, minors: np.ndarray) -> np.ndarray:
    """Whether each chord belongs to the major key: I, IV, V, ii, iii and vi."""
    intervals = (roots - tonic) % 12
    return np.where(minors, np.isin(intervals, _KEY_MINORS), np.isin(intervals, _KEY_MAJORS))


def _find_tonic(path: np.ndarray, roots: np.ndarray, minors: np.ndarray) -> int:
    """The major key whose chords take up the most time."""
    time_in_state = np.bincount(path, minlength=len(roots))
    return max(range(12), key=lambda tonic: time_in_state @ _in_key(tonic, roots, minors))
